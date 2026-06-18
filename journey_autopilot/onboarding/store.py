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
    "notifications": {
        "phone": None,
        "phone_verified": False,
        "channels": ["push"],             # push | whatsapp | email
        "quiet_hours": {"from": "22:00", "to": "06:30"},
    },
    "connections": {
        "db_account": False,
        "outlook": False,
    },
    "autonomy": "approve_each",           # notify_only | approve_each | auto_within_limits
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


def get_trips(user_id: str) -> list[dict]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT trip FROM trips WHERE user_id = ?", (user_id,)
        ).fetchall()
    trips = [json.loads(row[0]) for row in rows]
    trips.sort(key=lambda t: t.get("planned_departure") or "")
    return trips


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
