"""SQLite store for profiles, connections, and imported trips.

Persistence follows the README's target picture: SQLite for preferences, hard
constraints, and trip history. Deliberately standard-library only
(``sqlite3``), so the ADK side (``journey_autopilot.tools``) can read without
a FastAPI dependency.

Profile and trips are stored as JSON blobs — in the prototype the fields
still change often, and a rigid column schema would just generate migrations.

Path configurable via ``JA_DB_PATH``, defaulting to ``data/journey_autopilot.db``
in the project directory (covered by ``.gitignore`` via ``*.db``).
"""

from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = Path(os.getenv("JA_DB_PATH", _PROJECT_ROOT / "data" / "journey_autopilot.db"))

_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    user_id     TEXT PRIMARY KEY,
    email       TEXT NOT NULL,
    account     TEXT NOT NULL,   -- JSON: DB account (name, BahnCard, BahnBonus, ...)
    created_at  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS profiles (
    user_id     TEXT PRIMARY KEY REFERENCES users(user_id) ON DELETE CASCADE,
    profile     TEXT NOT NULL,   -- JSON: preferences, constraints, connections
    updated_at  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS trips (
    trip_id     TEXT NOT NULL,
    user_id     TEXT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    trip        TEXT NOT NULL,   -- JSON: imported booking
    imported_at TEXT NOT NULL,
    PRIMARY KEY (trip_id, user_id)
);
CREATE TABLE IF NOT EXISTS complaints (
    complaint_id TEXT NOT NULL,
    user_id      TEXT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    complaint    TEXT NOT NULL,   -- JSON: draft/submitted passenger-rights claim
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL,
    PRIMARY KEY (complaint_id, user_id)
);
"""

# Empty profile with all fields and sensible defaults. The UI fills this in
# step by step; the agent tools can rely on the structure.
DEFAULT_PROFILE: dict = {
    "preferences": {
        "travel_class": 2,                # 1 | 2
        "seat_location": "window",        # window | aisle | any
        "seat_area": "open_plan",         # open_plan | compartment | any
        "quiet_zone": False,              # prefer quiet zone
        "speed_vs_comfort": 50,           # 0 = max. comfort ... 100 = max. speed
        "max_transfers": 2,
        "min_transfer_minutes": 8,
    },
    "home": {
        "home_station": None,             # {"id": EVA, "name": ...}
        "latest_arrival_home": "23:00",   # latest acceptable arrival home
        "hotel_ok": True,                 # hotel instead of overnight travel acceptable
        "taxi_ok": True,                  # taxi for the last mile acceptable
    },
    "mobility": {
        "car_sharing_ok": True,           # Flinkster / car sharing acceptable as reroute alternative
        "bike_sharing_ok": True,          # Call-a-Bike / bike sharing acceptable as reroute alternative
    },
    "notifications": {
        "phone": None,
        "phone_verified": False,
        "channels": ["push"],             # push | whatsapp | email
        "quiet_hours": {"from": "22:00", "to": "06:30"},
    },
    "connections": {
        "db_account": False,
        "outlook": False,
        "outlook_email": None,            # actual signed-in MS account (real device-code flow)
        "outlook_name": None,
    },
    "autonomy": "approve_each",           # notify_only | approve_each | auto_within_limits
    # Policy / veto gate — read by journey_autopilot.policy.resolve(). The
    # onboarding "autonomy" choice seeds global_autonomy_level; the advanced
    # settings screen can pin per-write-tool overrides (auto | ask, plus
    # "ask_over_threshold" for booking). Empty write_tools => fall back to the
    # config/policy.yaml defaults shifted by the global level.
    "policy": {
        "global_autonomy_level": "balanced",   # conservative | balanced | aggressive
        "book_cost_threshold_eur": 50,
        "write_tools": {},                      # e.g. {"book_hotel": "ask"}
    },
    "onboarding_completed": False,
}


def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(_SCHEMA)
    return conn


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _merge(base: dict, update: dict) -> dict:
    """Recursive merge — the UI sends only the changed fields per step."""
    merged = dict(base)
    for key, value in update.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _merge(merged[key], value)
        else:
            merged[key] = value
    return merged


# --- User & profile -------------------------------------------------------------


def upsert_user(account: dict) -> None:
    """Creates the user on first login (including an empty profile)."""
    with _connect() as conn:
        conn.execute(
            "INSERT INTO users (user_id, email, account, created_at) VALUES (?,?,?,?) "
            "ON CONFLICT(user_id) DO UPDATE SET account = excluded.account",
            (account["user_id"], account["email"], json.dumps(account), _now()),
        )
        conn.execute(
            "INSERT OR IGNORE INTO profiles (user_id, profile, updated_at) VALUES (?,?,?)",
            (account["user_id"], json.dumps(DEFAULT_PROFILE), _now()),
        )


def get_account(user_id: str) -> dict | None:
    with _connect() as conn:
        row = conn.execute(
            "SELECT account FROM users WHERE user_id = ?", (user_id,)
        ).fetchone()
    return json.loads(row[0]) if row else None


def get_profile(user_id: str) -> dict | None:
    with _connect() as conn:
        row = conn.execute(
            "SELECT profile FROM profiles WHERE user_id = ?", (user_id,)
        ).fetchone()
    if row is None:
        return None
    # Layer in defaults so older profiles don't miss newer fields.
    return _merge(DEFAULT_PROFILE, json.loads(row[0]))


def update_profile(user_id: str, patch: dict) -> dict:
    """Merges a partial patch into the profile and returns the new state."""
    current = get_profile(user_id) or DEFAULT_PROFILE
    merged = _merge(current, patch)
    with _connect() as conn:
        conn.execute(
            "INSERT INTO profiles (user_id, profile, updated_at) VALUES (?,?,?) "
            "ON CONFLICT(user_id) DO UPDATE SET profile = excluded.profile, "
            "updated_at = excluded.updated_at",
            (user_id, json.dumps(merged), _now()),
        )
    return merged


def delete_user(user_id: str) -> None:
    """GDPR deletion: completely remove user, profile, and trips."""
    with _connect() as conn:
        conn.execute("DELETE FROM users WHERE user_id = ?", (user_id,))


# --- Trips ------------------------------------------------------------------------


def save_trips(user_id: str, trips: list[dict]) -> None:
    with _connect() as conn:
        for trip in trips:
            conn.execute(
                "INSERT INTO trips (trip_id, user_id, trip, imported_at) VALUES (?,?,?,?) "
                "ON CONFLICT(trip_id, user_id) DO UPDATE SET trip = excluded.trip",
                (trip["trip_id"], user_id, json.dumps(trip), _now()),
            )


def delete_trips(user_id: str, trip_ids: list[str]) -> None:
    """Remove specific trips for a user (e.g. stale demo imports at re-login)."""
    if not trip_ids:
        return
    with _connect() as conn:
        conn.executemany(
            "DELETE FROM trips WHERE user_id = ? AND trip_id = ?",
            [(user_id, trip_id) for trip_id in trip_ids],
        )


def get_trips(user_id: str) -> list[dict]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT trip FROM trips WHERE user_id = ?", (user_id,)
        ).fetchall()
    trips = [json.loads(row[0]) for row in rows]
    trips.sort(key=lambda t: t.get("planned_departure") or "")
    return trips


def delete_trip(user_id: str, trip_id: str) -> None:
    """Remove a single imported/added trip for a user."""
    with _connect() as conn:
        conn.execute(
            "DELETE FROM trips WHERE user_id = ? AND trip_id = ?",
            (user_id, trip_id),
        )


# --- Complaints (passenger-rights drafts) -----------------------------------------


def _complaint_row(complaint_id: str, payload: dict, created_at: str, updated_at: str) -> dict:
    return {
        "complaint_id": complaint_id,
        **payload,
        "created_at": created_at,
        "updated_at": updated_at,
    }


def create_complaint(user_id: str, complaint_id: str, payload: dict) -> dict:
    now = _now()
    with _connect() as conn:
        conn.execute(
            "INSERT INTO complaints (complaint_id, user_id, complaint, created_at, updated_at) "
            "VALUES (?,?,?,?,?)",
            (complaint_id, user_id, json.dumps(payload), now, now),
        )
    return _complaint_row(complaint_id, payload, now, now)


def get_complaints(user_id: str) -> list[dict]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT complaint_id, complaint, created_at, updated_at FROM complaints "
            "WHERE user_id = ? ORDER BY created_at DESC",
            (user_id,),
        ).fetchall()
    out = []
    for complaint_id, blob, created_at, updated_at in rows:
        out.append(_complaint_row(complaint_id, json.loads(blob), created_at, updated_at))
    return out


def get_complaint(user_id: str, complaint_id: str) -> dict | None:
    with _connect() as conn:
        row = conn.execute(
            "SELECT complaint_id, complaint, created_at, updated_at FROM complaints "
            "WHERE user_id = ? AND complaint_id = ?",
            (user_id, complaint_id),
        ).fetchone()
    if row is None:
        return None
    return _complaint_row(row[0], json.loads(row[1]), row[2], row[3])


def find_open_complaint(user_id: str, trip_id: str, travel_date: str) -> dict | None:
    """Return an existing draft/submitted claim for the same trip and date."""
    for item in get_complaints(user_id):
        if item.get("trip_id") != trip_id:
            continue
        if item.get("travel_date") != travel_date:
            continue
        if item.get("status") in ("draft", "submitted"):
            return item
    return None


def update_complaint(user_id: str, complaint_id: str, patch: dict) -> dict | None:
    current = get_complaint(user_id, complaint_id)
    if current is None:
        return None
    payload = {k: v for k, v in current.items() if k not in ("complaint_id", "created_at", "updated_at")}
    payload = _merge(payload, patch)
    if patch.get("status") == "submitted" and not payload.get("submitted_at"):
        payload["submitted_at"] = _now()
    now = _now()
    created_at = current["created_at"]
    with _connect() as conn:
        conn.execute(
            "UPDATE complaints SET complaint = ?, updated_at = ? "
            "WHERE user_id = ? AND complaint_id = ?",
            (json.dumps(payload), now, user_id, complaint_id),
        )
    return _complaint_row(complaint_id, payload, created_at, now)


def any_profile() -> dict | None:
    """Profile of the most recently onboarded user — for the agent tools that
    (still) run without a login context and need the one profile in the
    single-user prototype."""
    with _connect() as conn:
        row = conn.execute(
            "SELECT user_id, profile FROM profiles ORDER BY updated_at DESC LIMIT 1"
        ).fetchone()
    if row is None:
        return None
    profile = _merge(DEFAULT_PROFILE, json.loads(row[1]))
    profile["user_id"] = row[0]
    return profile
