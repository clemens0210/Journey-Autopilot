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
import re
from datetime import date, datetime, timedelta
from pathlib import Path

_FIXTURES_DIR = Path(__file__).resolve().parent / "data" / "fixtures"
_ACTIVE = os.getenv("JA_FIXTURES", "happy_path")

# The fixtures are authored against one fixed anchor day (the demo trip's
# departure date). At load time every date in the fixture is shifted so that
# the anchor lands on today + JA_DEMO_OFFSET_DAYS (default 0 = today). This
# keeps the demo evergreen: the canonical trip is never silently "three weeks
# in the past", the calendar clash sits on the actual travel day, and the
# reroute arrivals stay consistent with the live status. A second pass
# (``_anchor_times_to_start``) then shifts the wall-clock TIMES once per
# process so the demo trip departs JA_DEMO_TRIP_LEAD_MIN minutes before the
# app was started.
_DEMO_OFFSET_DAYS = int(os.getenv("JA_DEMO_OFFSET_DAYS", "0"))

# Matches the date part of bare dates AND ISO datetimes. The lookahead accepts
# a following "T" explicitly: a plain trailing \b would fail between the day
# and the "T" (both word characters), silently leaving every datetime in the
# fixture un-rebased — the demo trip then sits weeks in the past while
# date-keyed maps (user_calendar) DO shift.
_DATE_RE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})(?=T|\b)")


def _shift_dates(node, delta: timedelta):
    """Recursively shift every ISO date (YYYY-MM-DD) in strings/keys by delta.

    Handles bare dates, ISO datetimes, and date-keyed maps (user_calendar).
    Ids like "DB-2026-0619-MUC-BLN" don't match the pattern and stay stable.
    """

    def _shift_str(s: str) -> str:
        def repl(m: re.Match) -> str:
            try:
                shifted = date(int(m[1]), int(m[2]), int(m[3])) + delta
            except ValueError:
                return m[0]
            return shifted.isoformat()

        return _DATE_RE.sub(repl, s)

    if isinstance(node, str):
        return _shift_str(node)
    if isinstance(node, list):
        return [_shift_dates(item, delta) for item in node]
    if isinstance(node, dict):
        return {_shift_str(k) if isinstance(k, str) else k: _shift_dates(v, delta) for k, v in node.items()}
    return node


def _rebase_fixture(fx: dict) -> dict:
    """Shift the whole fixture so the demo trip departs today (+ offset)."""
    anchor_iso = (fx.get("demo_trip", {}).get("planned_departure") or "")[:10]
    try:
        anchor = date.fromisoformat(anchor_iso)
    except ValueError:
        return fx  # no parseable anchor — leave the fixture as authored
    delta = (date.today() + timedelta(days=_DEMO_OFFSET_DAYS)) - anchor
    if not delta:
        return fx
    return _shift_dates(fx, delta)


# --- Start-relative time anchoring --------------------------------------------
# The date rebase puts the demo trip on "today", but its wall-clock times were
# authored for one specific morning (10:02 departure, "now" ≈ 11:32). So the
# scenario plays correctly at ANY start time, the whole fixture timeline is
# shifted ONCE per process: the demo trip then departed JA_DEMO_TRIP_LEAD_MIN
# minutes (default 90) before the app was started — the presenter is always
# sitting in the delayed train mid-journey, the missed transfer lies just
# behind, the line reopening and both reroutes just ahead, and the hard
# meeting ~4.5 h in the future. Every fixture datetime moves by the same
# delta, so all relative gaps stay exactly as authored. Set
# JA_DEMO_TRIP_LEAD_MIN=0 to keep the authored wall-clock times.

_DATETIME_RE = re.compile(r"\b(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2})?)\b")


def _shift_datetimes(node, delta: timedelta):
    """Recursively shift every ISO datetime in strings/keys by delta.

    Bare dates (no time part) are left alone — day-granular values like the
    ``user_calendar`` keys are re-derived afterwards from the shifted event
    times instead.
    """

    def _shift_str(s: str) -> str:
        def repl(m: re.Match) -> str:
            try:
                dt = datetime.fromisoformat(m[1])
            except ValueError:
                return m[0]
            return (dt + delta).isoformat(timespec="seconds")

        return _DATETIME_RE.sub(repl, s)

    if isinstance(node, str):
        return _shift_str(node)
    if isinstance(node, list):
        return [_shift_datetimes(item, delta) for item in node]
    if isinstance(node, dict):
        return {_shift_str(k) if isinstance(k, str) else k: _shift_datetimes(v, delta) for k, v in node.items()}
    return node


def _anchor_times_to_start(fx: dict) -> tuple[dict, timedelta]:
    """Shift all fixture datetimes so the demo trip departed LEAD minutes ago.

    The target departure is rounded down to a 5-minute grid so the shifted
    timetable still looks like a timetable. Returns the shifted fixture and
    the applied delta — ``onboarding/accounts.py`` adds the same delta to its
    composed times so bookings/calendar stay on the fixture's clock.
    """
    try:
        lead = int(os.getenv("JA_DEMO_TRIP_LEAD_MIN", "90"))
    except ValueError:
        lead = 0
    if lead <= 0:
        return fx, timedelta(0)
    try:
        dep = datetime.fromisoformat(fx.get("demo_trip", {}).get("planned_departure") or "")
    except ValueError:
        return fx, timedelta(0)
    target = datetime.now() - timedelta(minutes=lead)
    target = target.replace(minute=target.minute - target.minute % 5, second=0, microsecond=0)
    delta = target - dep
    if not delta:
        return fx, delta
    shifted = _shift_datetimes(fx, delta)
    # Day-keyed calendar: a shift across midnight can move events to another
    # date — rebuild the keys from each event's (shifted) start.
    calendar = shifted.get("user_calendar")
    if isinstance(calendar, dict):
        rekeyed: dict = {}
        for events in calendar.values():
            for event in events or []:
                key = (event.get("start") or "")[:10]
                rekeyed.setdefault(key, []).append(event)
        shifted["user_calendar"] = rekeyed
    return shifted, delta


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
    "nürnberg hbf": "Nürnberg Hbf", "nuremberg hbf": "Nürnberg Hbf",
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


_FX = _rebase_fixture(_load_fixtures(_ACTIVE))

# The (rebased) demo travel day — single source of truth for everything that
# must sit on the same day as the demo trip (simulated bookings, calendar).
# Captured BEFORE the start-relative time shift: accounts.py composes its
# wall-clock times as "DEMO_DAY at HH:MM plus DEMO_TIME_SHIFT", which equals
# the fixture arithmetic exactly, even if the shifted departure crosses
# midnight.
DEMO_DAY: date = date.fromisoformat(_FX["demo_trip"]["planned_departure"][:10])

# Applied to every fixture datetime so the demo trip departed
# JA_DEMO_TRIP_LEAD_MIN minutes before this process started (see
# _anchor_times_to_start). Exported for accounts.py.
_FX, DEMO_TIME_SHIFT = _anchor_times_to_start(_FX)

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
    status = LIVE_TRIP_STATUS.get(DEMO_TRIP["trip_id"], {})
    # Curated reroutes are keyed by the station the traveler actually rebooks
    # from (the en-route next boardable station, e.g. Nuremberg), not the trip
    # origin — that's where R1/R2 board. Fall back to the trip origin for a
    # pre-trip scenario with no en-route status.
    boarding = status.get("next_boardable_station") or DEMO_TRIP["origin"]
    # lookup_route (not a bare dict .get) so the English "Nuremberg Hbf" from
    # the live status aliases onto the German-canonical fixture key.
    reroutes = lookup_route(REROUTE_OPTIONS, boarding, DEMO_TRIP["destination"])
    first = reroutes[0] if reroutes else None

    date = DEMO_TRIP.get("planned_arrival", "")[:10]
    meetings = USER_CALENDAR.get(date, [])
    meeting_time = meetings[0]["start"][11:16] if meetings else ""

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
