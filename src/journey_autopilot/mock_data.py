"""Mocked data for the prototype — loaded from JSON fixtures.

Deliberate decision (see CONTEXT_RECORD.md / ADR 0005): we have no real DB API
access, so all live-ops data is simulated. The fixtures now live as JSON under
``data/fixtures/`` and are loaded here into the module-level names the tools and
scenarios already import (``DEMO_TRIP``, ``LIVE_TRIP_STATUS``, ...). Keeping the
names stable means consumers (``tools/read_tools.py``, ``scenarios/``, the
WhatsApp event) don't change.

Pick a fixture set with the ``JA_FIXTURES`` environment variable (default
``happy_path``); this is the swap point that lets the edge-case / failure-case
scenarios run against their own dataset without touching code. Later, a real API
(or an MCP server) replaces the read tools — the interface stays the same.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

_FIXTURES_DIR = Path(__file__).resolve().parent / "data" / "fixtures"
_ACTIVE = os.getenv("JA_FIXTURES", "happy_path")


def _load_fixtures(name: str) -> dict:
    """Load the named fixture set from ``data/fixtures/<name>.json``."""
    path = _FIXTURES_DIR / f"{name}.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        available = ", ".join(sorted(p.stem for p in _FIXTURES_DIR.glob("*.json"))) or "none"
        raise FileNotFoundError(
            f"Fixture set '{name}' not found at {path}. "
            f"Set JA_FIXTURES to one of: {available}."
        ) from exc


def _by_route(records: list[dict], value_key: str) -> dict:
    """Rebuild a {(origin, destination): value} map from a list of records.

    JSON has no tuple keys, so route-keyed maps are stored as a list of
    ``{"origin": ..., "destination": ..., <value_key>: ...}`` records.
    """
    return {(r["origin"], r["destination"]): r[value_key] for r in records}


_FX = _load_fixtures(_ACTIVE)

# --- Demo trip + live ops (Monitoring) ----------------------------------------
DEMO_TRIP: dict = _FX["demo_trip"]
LIVE_TRIP_STATUS: dict = _FX["live_trip_status"]
NETWORK_DISRUPTIONS: dict = _FX["network_disruptions"]

# --- Planner knowledge --------------------------------------------------------
REROUTE_OPTIONS: dict = _by_route(_FX["reroute_options"], "options")
USER_CALENDAR: dict = _FX["user_calendar"]
PASSENGER_RIGHTS: list = _FX["passenger_rights"]

# --- Risk knowledge (pre-trip): delay history + scheduled connection ----------
# Fallbacks for the Monitoring Agent's pre-trip risk path when the db_service
# sidecar is unavailable, so the agent sees the same fields whether the data is
# live or simulated.
CONNECTION_DELAY_HISTORY: dict = _by_route(_FX["connection_delay_history"], "stats")
PLANNED_CONNECTIONS: dict = _by_route(_FX["planned_connections"], "connection")


# --- WhatsApp communicator: event fields derived from the active scenario -----
# Real phone numbers come from .env at runtime; ``recipients`` is populated by
# the scenario from DEMO_TRAVELER_NUMBER / DEMO_CLIENT_NUMBER etc.


def _demo_event_fields() -> dict:
    """Build the DisruptionEvent fields from the loaded fixture (scenario-agnostic)."""
    route = (DEMO_TRIP["origin"], DEMO_TRIP["destination"])
    reroutes = REROUTE_OPTIONS.get(route, [])
    first = reroutes[0] if reroutes else None

    date = DEMO_TRIP.get("planned_arrival", "")[:10]
    meetings = USER_CALENDAR.get(date, [])
    meeting_time = meetings[0]["start"][11:16] if meetings else ""

    status = LIVE_TRIP_STATUS.get(DEMO_TRIP["trip_id"], {})
    if first is not None:
        reroute_summary = (
            f"Change at Erfurt to ICE 1008 toward Berlin "
            f"(arrival {first['new_arrival'][11:16]}, +{first['added_delay_minutes']} min)"
        )
    else:
        reroute_summary = "No reroute available."

    return {
        "traveler_name": DEMO_TRIP["passenger"],
        "original_train": DEMO_TRIP["train"],
        "delay_minutes": status.get("current_delay_minutes", 0),
        "reroute_summary": reroute_summary,
        "meeting_time_original": meeting_time,
        # Appointment holds — the reroute arrival is before the meeting start.
        "meeting_time_new": meeting_time,
    }


DEMO_EVENT_FIELDS: dict = _demo_event_fields()
