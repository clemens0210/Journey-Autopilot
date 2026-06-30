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


def _norm(name: str) -> str:
    """Normalize a station/location name for case-insensitive matching."""
    return " ".join(name.strip().lower().split())


# German <-> English aliases for the stations in the demo fixtures so the LLM
# can use either spelling and the lookup still hits.
_STATION_ALIASES: dict[str, str] = {
    # Bare city names (LLM sometimes drops "Hbf") + German umlaut forms.
    # Case / whitespace variants are already handled by the _norm scan in
    # lookup_route / lookup_location, so those don't need entries here.
    "munich":    "Munich Hbf",   "münchen": "Munich Hbf",  "münchen hbf": "Munich Hbf",
    "berlin":    "Berlin Hbf",
    "köln":      "Köln Hbf",     "cologne": "Köln Hbf",    "koeln": "Köln Hbf",
    "frankfurt": "Frankfurt (Main) Hbf",
    "hamburg":   "Hamburg Hbf",
    "nürnberg":  "Nürnberg Hbf", "nuremberg": "Nürnberg Hbf",
    "stuttgart": "Stuttgart Hbf",
    "bonn":      "Bonn Hbf",
    "hannover":  "Hannover Hbf",
    "düsseldorf": "Düsseldorf Hbf", "dusseldorf": "Düsseldorf Hbf",
}


def _canonical(name: str) -> str:
    """Return the fixture-canonical spelling for a station name.

    Tries: exact match → alias table → strips ' Hbf' suffix fallback. If none
    matches, returns the input unchanged.
    """
    return _STATION_ALIASES.get(_norm(name), name)


def _by_route(records: list[dict], value_key: str) -> dict:
    """Rebuild a {(origin, destination): value} map from a list of records.

    JSON has no tuple keys, so route-keyed maps are stored as a list of
    ``{"origin": ..., "destination": ..., <value_key>: ...}`` records.
    """
    return {(r["origin"], r["destination"]): r[value_key] for r in records}


def lookup_route(table: dict, origin: str, destination: str) -> list:
    """Case-insensitive route lookup against a ``_by_route`` table.

    Tries the exact key first, then the alias-canonical key, then a
    case-insensitive scan so that LLM-generated variants like 'München Hbf'
    or 'munich hbf' still hit the right fixture entry.
    """
    hit = table.get((origin, destination))
    if hit is not None:
        return hit
    co, cd = _canonical(origin), _canonical(destination)
    hit = table.get((co, cd))
    if hit is not None:
        return hit
    no, nd = _norm(origin), _norm(destination)
    for (ko, kd), v in table.items():
        if _norm(ko) == no and _norm(kd) == nd:
            return v
    return []


def lookup_location(table: dict, location: str) -> list:
    """Case-insensitive single-key lookup (for FLINKSTER_OPTIONS, PARTNER_HOTELS).

    Tries exact → alias-canonical → case-insensitive scan.
    """
    hit = table.get(location)
    if hit is not None:
        return hit
    cl = _canonical(location)
    hit = table.get(cl)
    if hit is not None:
        return hit
    nl = _norm(location)
    for k, v in table.items():
        if _norm(k) == nl:
            return v
    return []


_FX = _load_fixtures(_ACTIVE)

# --- Demo trip + live ops (Monitoring) ----------------------------------------
DEMO_TRIP: dict = _FX["demo_trip"]
LIVE_TRIP_STATUS: dict = _FX["live_trip_status"]
NETWORK_DISRUPTIONS: dict = _FX["network_disruptions"]

# --- Planner knowledge --------------------------------------------------------
REROUTE_OPTIONS: dict = _by_route(_FX["reroute_options"], "options")
USER_CALENDAR: dict = _FX["user_calendar"]
PASSENGER_RIGHTS: list = _FX["passenger_rights"]

# --- DB-ecosystem alternatives (Flinkster, Call-a-Bike, partner hotels) ------
# Keyed by location name (station / city). Missing sections default to empty
# dicts so fixtures that predate this feature still load without error.
FLINKSTER_OPTIONS: dict = {r["location"]: r["options"] for r in _FX.get("flinkster_options", [])}
CALLABIKE_OPTIONS: dict = {r["location"]: r["options"] for r in _FX.get("callabike_options", [])}
PARTNER_HOTELS: dict = {r["location"]: r["hotels"] for r in _FX.get("partner_hotels", [])}

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
