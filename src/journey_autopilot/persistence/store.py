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
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .. import trip_status

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
CREATE TABLE IF NOT EXISTS reroute_proposals (
    proposal_id       TEXT PRIMARY KEY,
    user_id           TEXT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    session_id        TEXT NOT NULL,
    trip_id           TEXT NOT NULL,
    proposal          TEXT NOT NULL,   -- JSON: finalized options + constraint evidence
    selected_option_id TEXT,
    status            TEXT NOT NULL,   -- active | selected | executed | expired | superseded
    created_at        TEXT NOT NULL,
    expires_at        TEXT NOT NULL,
    updated_at        TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_reroute_proposals_session
    ON reroute_proposals (user_id, session_id, trip_id, updated_at DESC);
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
        # All channels on by default; WhatsApp only actually delivers once a
        # phone number is confirmed (the chat alert degrades to a hint toast).
        "channels": ["push", "whatsapp", "email"],
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
            # ``status`` is derived on the way out (get_trips) and must never be
            # written back: a persisted phase is stale the moment the clock moves
            # past it, and would then outrank the live derivation everywhere.
            payload = {key: value for key, value in trip.items() if key != "status"}
            conn.execute(
                "INSERT INTO trips (trip_id, user_id, trip, imported_at) VALUES (?,?,?,?) "
                "ON CONFLICT(trip_id, user_id) DO UPDATE SET trip = excluded.trip",
                (trip["trip_id"], user_id, json.dumps(payload), _now()),
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
    """Stored trips, each tagged with its current lifecycle ``status``.

    The phase is attached here rather than at each call site so every reader —
    the trip list, the login/``/api/me`` bootstrap, the booking response and the
    agent's own trip lookup — agrees without any of them having to remember to
    compute it. It is the schedule-derived (cheap, no I/O) precision; the
    trip-detail endpoint and the Monitoring tool refine it from live data. See
    ``trip_status``.
    """
    with _connect() as conn:
        rows = conn.execute(
            "SELECT trip FROM trips WHERE user_id = ?", (user_id,)
        ).fetchall()
    trips = [json.loads(row[0]) for row in rows]
    trips.sort(key=lambda t: t.get("planned_departure") or "")
    return [{**trip, "status": trip_status.from_schedule(trip)} for trip in trips]


def delete_trip(user_id: str, trip_id: str) -> None:
    """Remove a single imported/added trip for a user."""
    with _connect() as conn:
        conn.execute(
            "DELETE FROM trips WHERE user_id = ? AND trip_id = ?",
            (user_id, trip_id),
        )


# --- Finalized reroute proposals -------------------------------------------------


# Single source of truth for the reroute_proposals column order: every SELECT
# against this table is built from this tuple, and _proposal_row unpacks rows
# by zipping against the same tuple — so a future column reorder can never
# silently desync the SQL from the row-to-dict marshalling (unlike a bare
# positional tuple-unpack living in a different function from the SELECT).
_PROPOSAL_COLUMNS = (
    "proposal_id", "user_id", "session_id", "trip_id", "proposal",
    "selected_option_id", "status", "created_at", "expires_at", "updated_at",
)
_PROPOSAL_SELECT = f"SELECT {', '.join(_PROPOSAL_COLUMNS)} FROM reroute_proposals"


# The live DB sidecar is the only non-reproducible reroute source: those cards
# reflect a single moment (delays, seat availability) and must be revalidated —
# and re-searched if stale — before booking. Every other ("mock_*"/offline)
# source is static and reproducible, so such a proposal never goes stale: the
# shown card stays bookable for the whole demo regardless of the TTL or a
# supersession. See _proposal_row (expiry) and select/claim (status).
_LIVE_PROPOSAL_SOURCE = "db_service_live"


def _proposal_is_offline(payload: dict | None) -> bool:
    """True if the shortlist came from static/mock data, not the live sidecar."""
    return (payload or {}).get("source") != _LIVE_PROPOSAL_SOURCE


def _proposal_row(row: tuple) -> dict:
    data = dict(zip(_PROPOSAL_COLUMNS, row))
    payload = json.loads(data["proposal"])
    expires_dt = datetime.fromisoformat(data["expires_at"])
    # Offline/mock shortlists are reproducible and never expire; only live
    # sidecar proposals age out after their TTL.
    expired = (expires_dt <= datetime.now(timezone.utc)) and not _proposal_is_offline(payload)
    status = data["status"]
    return {
        "proposal_id": data["proposal_id"],
        "user_id": data["user_id"],
        "session_id": data["session_id"],
        "trip_id": data["trip_id"],
        "proposal": payload,
        "selected_option_id": data["selected_option_id"],
        "status": "expired" if expired and status in ("active", "selected") else status,
        "created_at": data["created_at"],
        "expires_at": data["expires_at"],
        "updated_at": data["updated_at"],
        "expired": expired,
    }


def save_reroute_proposal(
    user_id: str,
    session_id: str,
    trip_id: str,
    proposal: dict,
    *,
    ttl_seconds: int = 300,
) -> dict:
    """Persist one finalized shortlist and supersede the prior active version."""
    now_dt = datetime.now(timezone.utc)
    now = now_dt.isoformat()
    expires = (now_dt + timedelta(seconds=max(30, int(ttl_seconds)))).isoformat()
    proposal_id = f"RP-{secrets.token_hex(8)}"
    trip_id = str(trip_id or "")
    with _connect() as conn:
        conn.execute(
            "UPDATE reroute_proposals SET status = 'superseded', updated_at = ? "
            "WHERE user_id = ? AND session_id = ? AND trip_id = ? "
            "AND status IN ('active', 'selected')",
            (now, user_id, session_id, trip_id),
        )
        conn.execute(
            "INSERT INTO reroute_proposals "
            "(proposal_id, user_id, session_id, trip_id, proposal, selected_option_id, "
            "status, created_at, expires_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                proposal_id,
                user_id,
                session_id,
                trip_id,
                json.dumps(proposal),
                None,
                "active",
                now,
                expires,
                now,
            ),
        )
    return get_reroute_proposal(user_id, proposal_id)  # type: ignore[return-value]


def get_reroute_proposal(user_id: str, proposal_id: str) -> dict | None:
    """Return a proposal owned by the user, including its computed expiry state."""
    with _connect() as conn:
        row = conn.execute(
            f"{_PROPOSAL_SELECT} WHERE user_id = ? AND proposal_id = ?",
            (user_id, proposal_id),
        ).fetchone()
        if row is None:
            return None
        item = _proposal_row(row)
        if item["expired"]:
            conn.execute(
                "UPDATE reroute_proposals SET status = 'expired', updated_at = ? "
                "WHERE user_id = ? AND proposal_id = ? AND status IN ('active', 'selected')",
                (_now(), user_id, proposal_id),
            )
    return item


def get_active_reroute_proposal(
    user_id: str, session_id: str, trip_id: str | None = None
) -> dict | None:
    """Return the newest unexpired active/selected proposal for this chat."""
    query = (
        f"{_PROPOSAL_SELECT} WHERE user_id = ? AND session_id = ? "
        "AND status IN ('active', 'selected')"
    )
    params: list[str] = [user_id, session_id]
    if trip_id is not None:
        query += " AND trip_id = ?"
        params.append(str(trip_id or ""))
    query += " ORDER BY updated_at DESC"
    result: dict | None = None
    expired_ids: list[str] = []
    with _connect() as conn:
        rows = conn.execute(query, params).fetchall()
        for row in rows:
            item = _proposal_row(row)
            if item["expired"]:
                expired_ids.append(item["proposal_id"])
                continue
            if result is None:
                result = item
        if expired_ids:
            now = _now()
            conn.executemany(
                "UPDATE reroute_proposals SET status = 'expired', updated_at = ? "
                "WHERE user_id = ? AND proposal_id = ? AND status IN ('active', 'selected')",
                [(now, user_id, proposal_id) for proposal_id in expired_ids],
            )
    return result


def select_reroute_option(
    user_id: str,
    session_id: str,
    proposal_id: str,
    option_id: str,
) -> dict:
    """Select only an eligible option from an owned, active proposal."""
    item = get_reroute_proposal(user_id, proposal_id)
    if item is None or item["session_id"] != session_id:
        return {"error": "Reroute proposal not found for this chat."}
    # Offline/mock shortlists never expire and survive a supersession (their
    # options are reproducible); live ones must still be unexpired and active.
    if _proposal_is_offline(item.get("proposal")):
        if item["status"] == "executed":
            return {"error": "That reroute proposal was already used."}
    elif item["expired"] or item["status"] not in ("active", "selected"):
        return {"error": "That reroute proposal is no longer active. Run a fresh search."}
    option = next(
        (
            candidate
            for candidate in item["proposal"].get("options") or []
            if candidate.get("option_id") == option_id
            and candidate.get("eligible") is not False
            and candidate.get("selectable") is not False
        ),
        None,
    )
    if option is None:
        return {"error": f"Option {option_id} is not selectable in this proposal."}
    now = _now()
    with _connect() as conn:
        conn.execute(
            "UPDATE reroute_proposals SET selected_option_id = ?, status = 'selected', "
            "updated_at = ? WHERE user_id = ? AND proposal_id = ?",
            (option_id, now, user_id, proposal_id),
        )
    item["selected_option_id"] = option_id
    item["selected_option"] = option
    item["status"] = "selected"
    item["updated_at"] = now
    return item


def set_reroute_proposal_status(user_id: str, proposal_id: str, status: str) -> None:
    """Update lifecycle state after an authoritative execution decision."""
    if status not in ("active", "selected", "executed", "expired", "superseded"):
        raise ValueError(f"Unsupported reroute proposal status: {status}")
    with _connect() as conn:
        conn.execute(
            "UPDATE reroute_proposals SET status = ?, updated_at = ? "
            "WHERE user_id = ? AND proposal_id = ?",
            (status, _now(), user_id, proposal_id),
        )


def claim_reroute_proposal_execution(
    user_id: str, proposal_id: str, option_id: str
) -> bool:
    """Atomically consume a selected proposal exactly once.

    Live proposals must still be unexpired and active/selected. Offline/mock
    proposals are reproducible and stay claimable regardless of TTL or
    supersession — only a second execution of the same proposal is refused.
    """
    now = _now()
    with _connect() as conn:
        row = conn.execute(
            f"{_PROPOSAL_SELECT} WHERE user_id = ? AND proposal_id = ?",
            (user_id, proposal_id),
        ).fetchone()
        if row is None:
            return False
        if _proposal_is_offline(_proposal_row(row)["proposal"]):
            cursor = conn.execute(
                "UPDATE reroute_proposals SET status = 'executed', updated_at = ? "
                "WHERE user_id = ? AND proposal_id = ? AND selected_option_id = ? "
                "AND status != 'executed'",
                (now, user_id, proposal_id, option_id),
            )
        else:
            cursor = conn.execute(
                "UPDATE reroute_proposals SET status = 'executed', updated_at = ? "
                "WHERE user_id = ? AND proposal_id = ? AND selected_option_id = ? "
                "AND status IN ('active', 'selected') AND expires_at > ?",
                (now, user_id, proposal_id, option_id, now),
            )
    return cursor.rowcount == 1


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
