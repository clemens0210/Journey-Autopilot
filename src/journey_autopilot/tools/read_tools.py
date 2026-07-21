"""Function tools for the agents.

In ADK, a typed Python function with a docstring is enough: the framework wraps
it automatically into a FunctionTool and derives the parameter schema from the
type hints and docstring. Therefore docstrings and types here are not decoration
but part of the API that the LLM sees.

DB-related functions are live-first via `integrations.db_ops` and fall back to
`mock_data` when the sidecar is unavailable. Calendar and demo account data
keep the same presentation-safe fallback pattern.
"""

from __future__ import annotations

import logging
import os
import re
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any


from .. import mock_data, risk
from . import risk_model
from ..config import REROUTE_MAX_ADDED_DELAY_MINUTES, REROUTE_MAX_OPTIONS
from ..errors import with_resilience, with_resilience_async
from ..integrations.rights_rag.rights_service import calculate_compensation
from ..integrations import db_ops as db_api
from ..integrations import hotels as hotels_api
from ..integrations import stations

logger = logging.getLogger(__name__)


def _calendar_configured() -> bool:
    """Return True if MS Entra credentials are present in the environment."""
    return bool(os.getenv("MS_ENTRA_CLIENT_ID"))


def _profile_connections() -> dict:
    """Read ``profile.connections`` from the onboarding store ({} on any failure).

    Single accessor for the store's connections blob — shared by the
    Outlook-connected check and the self-organized contact resolution so the
    lookup pattern lives in one place.
    """
    try:
        from journey_autopilot.persistence import store
        from journey_autopilot.request_context import current_user_id

        user_id = current_user_id.get()
        profile = store.get_profile(user_id) if user_id else store.any_profile()
        return (profile or {}).get("connections", {}) or {}
    except Exception:
        return {}


def _outlook_connected() -> bool:
    """Return True if the user connected Outlook during onboarding.

    Checks ``profile.connections.outlook`` in the onboarding store. This
    prevents the agent from triggering a blocking device-code flow when
    the user skipped Outlook — the token cache is only populated after a
    successful web-based device-code login.
    """
    return bool(_profile_connections().get("outlook"))


def calendar_connected() -> bool:
    """True when a real calendar is available (Entra creds + Outlook connected).

    The Planner's instruction provider reads this at call time: without a
    connected calendar the calendar steps are dropped from the prompt entirely,
    so no LLM round-trip (and no mock-calendar check) is spent on appointments
    the user never provided.
    """
    return _calendar_configured() and _outlook_connected()


# Internal alias Microsoft consumer accounts report as the organizer address
# of self-created events (e.g. outlook_45A79CDF4502E0CF@outlook.com). Graph's
# sendMail accepts it, but it is NOT a routable inbox — mail silently goes
# nowhere. Never use it as a recipient.
PSEUDO_OUTLOOK_ALIAS_RE = re.compile(r"^outlook_[0-9A-F]{8,}@outlook\.com$", re.IGNORECASE)


def _resolve_self_organized_contacts(events: list[dict]) -> None:
    """Replace the organizer contact of self-organized events in place.

    For events the connected user created themself, Graph reports the
    non-routable ``outlook_<hex>@outlook.com`` alias (see
    ``PSEUDO_OUTLOOK_ALIAS_RE``) as organizer address. The organizer IS the
    connected account, so substitute the real email/name stored during
    onboarding (``profile.connections.outlook_email``). Events organized by
    others are left untouched.
    """
    connections = _profile_connections()
    email = connections.get("outlook_email")
    if not email:
        return
    for event in events:
        if event.get("self_organized"):
            event["organizer_email"] = email
            event["organizer_name"] = connections.get("outlook_name") or event.get(
                "organizer_name"
            )


def _parse_datetime(value: str | None) -> datetime | None:
    """Parse ISO timestamps from DB/mock data, tolerating trailing ``Z``."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _minutes_between(start: str | None, end: str | None) -> int | None:
    start_dt = _parse_datetime(start)
    end_dt = _parse_datetime(end)
    if start_dt is None or end_dt is None:
        return None
    # Stored trips use naive German-local times while the live sidecar returns
    # offset-aware ones; on a mix, compare wall clocks (both are German local).
    if (start_dt.tzinfo is None) != (end_dt.tzinfo is None):
        start_dt = start_dt.replace(tzinfo=None)
        end_dt = end_dt.replace(tzinfo=None)
    return round((end_dt - start_dt).total_seconds() / 60)


def _find_trip_context(trip_id: str) -> dict | None:
    """Find imported trip metadata for a tool call that only receives trip_id."""
    try:
        from journey_autopilot.persistence import store

        profile = store.any_profile()
        if profile is not None:
            for trip in store.get_trips(profile["user_id"]):
                if trip.get("trip_id") == trip_id:
                    return trip
    except Exception:
        pass

    if mock_data.DEMO_TRIP.get("trip_id") == trip_id:
        return mock_data.DEMO_TRIP
    if trip_id in mock_data.LIVE_TRIP_STATUS:
        return mock_data.DEMO_TRIP
    return None


def _journey_for_trip(trip: dict) -> dict | None:
    """Live search result for exactly the booked itinerary, or ``None``.

    Strict on purpose: matching only by origin/destination (or first train)
    would return a *different* connection — e.g. a later journey via another
    hub — and its delays/transfers would then be presented as the user's trip.
    """
    origin = trip.get("origin")
    destination = trip.get("destination")
    if not origin or not destination:
        return None
    from_eva = stations.resolve_eva(origin)
    to_eva = stations.resolve_eva(destination)
    if from_eva is None or to_eva is None:
        raise db_api.DBServiceError(
            f"Station not resolvable (origin={origin!r}, destination={destination!r})."
        )
    payload = db_api.journeys(
        from_eva,
        to_eva,
        departure=trip.get("planned_departure"),
        results=5,
        tickets=True,
    )
    return db_api.match_booked_journey(trip, db_api.normalize_journeys(payload))


def _trip_position(option: dict) -> str | None:
    for leg in option.get("legs") or []:
        delay = leg.get("arrival_delay_minutes")
        if delay and delay > 0:
            origin = leg.get("origin") or "unknown origin"
            destination = leg.get("destination") or "unknown destination"
            return f"between {origin} and {destination}"
    return None


def _locate_next_leg(option: dict) -> tuple[dict, str, datetime] | None:
    """Find the first not-yet-completed leg and how the traveler relates to it.

    Shared walk used by ``_next_boardable_station`` and
    ``_earliest_reroute_departure`` so the "where/when is the traveler" state
    machine lives in one place. Returns ``(leg, phase, now)`` where ``phase``
    is one of:

    - ``"cancelled"``: the leg never ran (regardless of its scheduled window
      being in the past or future) — the traveler is still at its origin.
    - ``"in_transit"``: the traveler is currently riding this leg.
    - ``"not_started"``: the leg hasn't departed yet — the traveler is still
      at its origin.

    Returns ``None`` if every leg is already completed or none carry a usable
    time. Live times win over planned times.
    """
    for leg in option.get("legs") or []:
        departure = _parse_datetime(leg.get("departure") or leg.get("planned_departure"))
        arrival = _parse_datetime(leg.get("arrival") or leg.get("planned_arrival"))
        anchor = arrival or departure
        if anchor is None:
            continue
        now = datetime.now(anchor.tzinfo) if anchor.tzinfo else datetime.now()
        # A cancelled leg was never boardable, no matter how far in the past its
        # scheduled arrival now is — the traveler could not have ridden it, so
        # it is always the next unfinished leg once reached in this order
        # (earlier, uncancelled legs are skipped as usual by falling through
        # below once their own arrival/departure are in the past).
        if leg.get("cancelled"):
            return leg, "cancelled", now
        if arrival is not None and arrival >= now:
            if departure is not None and departure <= now:
                return leg, "in_transit", now
            return leg, "not_started", now
        if departure is not None and departure >= now:
            return leg, "not_started", now
    return None


def _next_boardable_station(option: dict) -> str | None:
    """Best station from which an en-route traveler can start a new search.

    If the traveler is currently on a leg, its destination is the next place
    they can change trains. If the next leg has not started (or was
    cancelled), its origin is still boardable.
    """
    located = _locate_next_leg(option)
    if located is None:
        return None
    leg, phase, _now = located
    return leg.get("destination") if phase == "in_transit" else leg.get("origin")


def _earliest_reroute_departure(option: dict) -> str | None:
    """Live time paired with ``_next_boardable_station`` for a new search."""
    located = _locate_next_leg(option)
    if located is None:
        return None
    leg, phase, now = located
    departure_raw = leg.get("departure") or leg.get("planned_departure")
    if phase == "cancelled":
        # Before its planned start, keep that intended start time; once that
        # time has passed, search from the current time.
        departure = _parse_datetime(departure_raw)
        if departure is not None and departure > now:
            return departure_raw
        return now.replace(microsecond=0).isoformat()
    if phase == "in_transit":
        return leg.get("arrival") or leg.get("planned_arrival")
    return departure_raw


def _region_anchor(region: str) -> str | None:
    anchors = {
        "bavaria": "Munich Hbf",
        "bayern": "Munich Hbf",
        "berlin": "Berlin Hbf",
        "nrw": "Cologne Hbf",
        "north rhine-westphalia": "Cologne Hbf",
        "hessen": "Frankfurt (Main) Hbf",
        "hamburg": "Hamburg Hbf",
    }
    return anchors.get(region.lower())


# --- In-process capture of the last reroute result (for the chat UI) -------
# The Orchestrator wraps the Planner in a base ``AgentTool``, which runs the
# sub-agent in its own runner and returns only the merged final *text*. The
# ``find_reroute_options`` function_response therefore never reaches the
# top-level event stream that ``ui.chat`` iterates, so the browser can't see
# the structured option list via ADK events.
#
# Workaround: the tools stash structured planning state here while they run
# (same process), and ``ui.chat.chat_turn`` reads only the finalized shortlist
# after the run. Discovery candidates and constraint-breaking fallbacks remain
# separate so raw tool results can never become selectable UI cards. Workspaces
# are request-scoped; direct scenario calls use one prototype fallback scope.
_REROUTE_WORKSPACES: dict[tuple[str, str], dict] = {}
_DEFAULT_REROUTE_SCOPE = ("prototype", "default")


def _reroute_scope(user_id: str | None = None, session_id: str | None = None) -> tuple[str, str]:
    if user_id and session_id:
        return user_id, session_id
    try:
        from journey_autopilot.request_context import current_session_id, current_user_id

        context_user = current_user_id.get()
        context_session = current_session_id.get()
        if context_user and context_session:
            return context_user, context_session
    except Exception:
        pass
    # Only direct scenario/demo calls (which never bind request_context) are
    # expected to land here. A real chat turn always binds both vars before
    # any tool runs (see ui.chat._run_turn), so reaching this fallback there
    # would mean two real requests share one in-process discovery workspace —
    # log it so that regression is observable instead of silently mixing state.
    logger.warning(
        "reroute discovery workspace fell back to the shared prototype scope "
        "(no request identity bound) — expected only for direct scenario calls"
    )
    return _DEFAULT_REROUTE_SCOPE


def last_reroute_options(
    user_id: str | None = None, session_id: str | None = None
) -> dict | None:
    """Return this turn's reroute workspace, or ``None``.

    ``options`` is intentionally empty until ``finalize_reroute_options`` has
    applied every hard constraint. ``candidate_options`` is internal planning
    state and must never be rendered as selectable UI cards.
    """
    return _REROUTE_WORKSPACES.get(_reroute_scope(user_id, session_id))


def clear_reroute_options(
    user_id: str | None = None, session_id: str | None = None
) -> None:
    """Reset the request-scoped workspace at the start/end of a chat turn."""
    _REROUTE_WORKSPACES.pop(_reroute_scope(user_id, session_id), None)


def _rebuild_reroute_stash() -> None:
    """Rebuild flattened planning fields from the explicit family entries."""
    workspace = last_reroute_options()
    if workspace is None:
        return
    candidate_options: list[dict] = []
    fallback_options: list[dict] = []
    rejected_options: list[dict] = []
    rejected_summary: dict[str, int] = {}
    for family_data in workspace.get("families", {}).values():
        candidate_options.extend(family_data.get("options") or [])
        fallback_options.extend(family_data.get("fallback_options") or [])
        rejected_options.extend(family_data.get("rejected_options") or [])
        for reason, count in (family_data.get("rejected_summary") or {}).items():
            rejected_summary[reason] = rejected_summary.get(reason, 0) + int(count)

    workspace["candidate_options"] = candidate_options
    workspace["fallback_options"] = fallback_options
    workspace["rejected_options"] = rejected_options
    workspace["rejected_summary"] = rejected_summary

    # Any corrected family invalidates calendar verdicts and the prior UI
    # shortlist. A later calendar/finalize call rebuilds both against this batch.
    workspace["calendar_checked"] = False
    workspace["calendar_verdicts"] = {}
    workspace["finalized"] = False
    workspace["options"] = []
    workspace["recommended_option_id"] = None


def _stash_options(
    options: list[dict],
    *,
    family: str,
    origin: str = "",
    destination: str = "",
    source: str = "",
    fallback_options: list[dict] | None = None,
    rejected_options: list[dict] | None = None,
    rejected_summary: dict[str, int] | None = None,
) -> list[dict]:
    """Replace one explicit mode family in the turn-local planning workspace.

    Replacement happens even for an empty list. The ``mobility`` family owns
    both C# and B# options, so a car-only re-run removes stale bikes as well.
    """
    scope = _reroute_scope()
    workspace = _REROUTE_WORKSPACES.get(scope)
    if workspace is None:
        workspace = {
            "origin": origin,
            "destination": destination,
            "families": {},
            "candidate_options": [],
            "options": [],
            "fallback_options": [],
            "source": source,
            "finalized": False,
        }
        _REROUTE_WORKSPACES[scope] = workspace
    family_data = {
        "origin": origin,
        "destination": destination,
        "source": source,
        "options": list(options),
        "fallback_options": list(fallback_options or []),
        "rejected_options": list(rejected_options or []),
        "rejected_summary": dict(rejected_summary or {}),
    }
    workspace["families"][family] = family_data

    # Prefer the train route as the aggregate route; otherwise use the most
    # recently replaced family. Per-option source remains authoritative in UI.
    route_data = workspace["families"].get("train") or family_data
    workspace["origin"] = route_data.get("origin", "")
    workspace["destination"] = route_data.get("destination", "")
    workspace["source"] = route_data.get("source", "")
    _rebuild_reroute_stash()
    return options


def _board_warnings(board: Any) -> list[dict]:
    entries = []
    if isinstance(board, dict):
        entries = board.get("departures") or board.get("arrivals") or []
    elif isinstance(board, list):
        entries = board

    disruptions: list[dict] = []
    seen: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        line = (entry.get("line") or {}).get("name") or entry.get("direction") or "DB"
        for remark in entry.get("remarks") or []:
            if not isinstance(remark, dict):
                continue
            if remark.get("type") not in ("status", "warning"):
                continue
            text = (remark.get("summary") or remark.get("text") or "").strip()
            if not text or text in seen:
                continue
            seen.add(text)
            disruptions.append(
                {
                    "line": line,
                    "type": text,
                    "severity": "unknown",
                    "expected_resolution": None,
                }
            )
    return disruptions[:5]


# --- Monitoring tools ---------------------------------------------------------


def _arrival_in_past(arrival_iso: str | None) -> bool:
    """True if the (estimated) arrival lies in the past — i.e. the trip is over.

    The live DB feed has no explicit "arrived" flag (unlike the mock fixture,
    where scripts/simulate_arrival.py sets one), so it is derived from the
    arrival timestamp. Complaint drafting and the Monitoring agent's
    EN ROUTE/ARRIVED status both depend on this field being present.
    """
    arrival = _parse_datetime(arrival_iso)
    if arrival is None:
        return False
    # Offset-aware timestamps (the live sidecar's format, including a trailing
    # "Z") compare as instants; genuinely naive ones are German-local wall clock
    # (see _minutes_between) and compare against the local clock.
    now = datetime.now(arrival.tzinfo) if arrival.tzinfo else datetime.now()
    return arrival <= now


def _overall_level(forecasts: list[dict]) -> str:
    """Worst per-leg forecast level as the trip's risk band (LOW/MEDIUM/HIGH).

    Ranked explicitly — max() on the raw strings would sort alphabetically
    ("low" > "high"), inverting the result.
    """
    rank = {"low": 0, "medium": 1, "high": 2}
    if not forecasts:
        return "LOW"
    worst = max(
        (f.get("level", "low") for f in forecasts),
        key=lambda lvl: rank.get(lvl, 0),
    )
    return worst.upper()


def _booked_risk_legs(trip: dict) -> list[dict]:
    """The booked trip's own legs in the risk module's shape (no live delay).

    Used when live data for the exact booked connection is unavailable, so the
    forecast still describes the user's actual itinerary instead of nothing.
    """
    booked = trip.get("legs") or []
    if not booked:
        if not (trip.get("origin") and trip.get("destination")):
            return []
        booked = [
            {
                "train": trip.get("train"),
                "origin": trip.get("origin"),
                "destination": trip.get("destination"),
                "planned_departure": trip.get("planned_departure"),
                "planned_arrival": trip.get("planned_arrival"),
            }
        ]
    return [
        {
            "train": leg.get("train"),
            "origin": {"name": leg.get("origin"), "planned": leg.get("planned_departure")},
            "destination": {"name": leg.get("destination"), "planned": leg.get("planned_arrival")},
            "current_delay_minutes": 0,
        }
        for leg in booked
    ]


def get_live_trip_status(trip_id: str) -> dict:
    """Returns the current live status of a train journey with risk forecasts.

    Args:
        trip_id: The trip ID, e.g. "DB-2026-0603-MUC-BLN".

    Returns:
        A dict with current delay, trend, position, known incidents,
        connection risk, risk forecasts, and ``source``. When ``source`` is
        ``db_history_forecast`` no live data was available for the exact booked
        connection — the numbers are the historical forecast for the booked
        legs and ``current_delay_minutes`` is null (see ``note``). Contains
        "error" only if the trip is entirely unknown.
    """
    trip = _find_trip_context(trip_id)

    # A scripted status in the fixture wins over the live search: the demo
    # trip's dates are rebased to "today", so a live lookup could otherwise
    # find the real train and silently replace the scripted disruption —
    # making the canonical demo non-deterministic. Trips without a scripted
    # status (e.g. self-booked BK-… connections) stay live-first.
    scripted = mock_data.LIVE_TRIP_STATUS.get(trip_id)

    if trip is not None and scripted is None:
        try:
            option = _journey_for_trip(trip)
            if option:
                delay = option.get("arrival_delay_minutes")
                if delay is None:
                    delay = option.get("departure_delay_minutes") or 0
                delay_int = round(delay)

                # Adapt the sidecar's flat legs (origin/destination are plain
                # strings, times in separate keys) into the shape the risk module
                # expects: origin/destination as dicts with ``name`` and
                # ``planned``, plus the leg's own live arrival delay. Passing the
                # flat legs directly would crash ``connection_risks`` (it reads
                # ``leg["destination"]["planned"]``).
                risk_legs: list[dict] = []
                for leg in option.get("legs") or []:
                    leg_delay = leg.get("arrival_delay_minutes")
                    if leg_delay is None:
                        leg_delay = leg.get("departure_delay_minutes") or 0
                    risk_legs.append(
                        {
                            "train": leg.get("train"),
                            "origin": {"name": leg.get("origin"), "planned": leg.get("planned_departure")},
                            "destination": {"name": leg.get("destination"), "planned": leg.get("planned_arrival")},
                            "current_delay_minutes": round(leg_delay or 0),
                        }
                    )

                # Historical forecast + transfer-miss warnings from the risk module.
                forecasts = risk.forecast_trip(trip, risk_legs)
                for leg, forecast in zip(risk_legs, forecasts):
                    leg["forecast"] = forecast
                connection_warnings = risk.connection_risks(risk_legs)
                # A definitively missed transfer breaks the itinerary: there is
                # no "stay aboard" arrival anymore, so the journey's reported
                # arrival (which assumes every transfer is still made) must not
                # be handed on as an ETA — downstream it would become the
                # baseline that rejects every real reroute as slower.
                missed_transfers = risk.missed_connections(risk_legs)
                itinerary_broken = bool(missed_transfers)
                risk_level = "HIGH" if itinerary_broken else _overall_level(forecasts)

                incidents = [
                    {"type": text, "location": trip.get("destination"), "impact": "DB live remark"}
                    for text in option.get("remarks") or []
                ]
                if option.get("platform_changes"):
                    incidents.extend(
                        {
                            "type": "Platform change",
                            "location": change.get("train"),
                            "impact": (
                                f"Platform {change.get('planned_platform')} -> "
                                f"{change.get('platform')}"
                            ),
                        }
                        for change in option["platform_changes"]
                    )

                return {
                    "trip_id": trip_id,
                    "train": option.get("train") or trip.get("train"),
                    "current_delay_minutes": delay_int,
                    "trend": "unknown",
                    "current_position": _trip_position(option),
                    "next_boardable_station": _next_boardable_station(option),
                    "earliest_reroute_departure": _earliest_reroute_departure(option),
                    "incidents": incidents,
                    "connection_risk": " ".join(missed_transfers + connection_warnings) or (
                        "Arrival delay may affect onward plans."
                        if delay_int >= 15
                        else "No elevated connection risk visible from DB live data."
                    ),
                    "risk_level": risk_level,
                    "forecasts": forecasts,
                    "legs": risk_legs,
                    "planned_departure": option.get("planned_departure") or trip.get("planned_departure"),
                    "planned_arrival": option.get("planned_arrival") or trip.get("planned_arrival"),
                    "itinerary_broken": itinerary_broken,
                    "estimated_arrival": (
                        None if itinerary_broken
                        else option.get("arrival") or option.get("planned_arrival")
                    ),
                    # Only the *estimated* arrival confirms the trip is over; a
                    # scheduled planned_arrival passing while a train is delayed
                    # must NOT read as "arrived" (would draft a premature claim).
                    "arrived": _arrival_in_past(option.get("arrival")),
                    "platform_changes": option.get("platform_changes", []),
                    "data_timestamp": datetime.now(timezone.utc).isoformat(),
                    "source": "db_service_live",
                }
        except db_api.DBServiceError:
            pass
        except Exception:
            pass

    if scripted is not None:
        result = {**scripted, "source": "mock_live_status"}
        # The fixture's en-route state carries neither the planned times nor a
        # risk band — fill both so the agent has a real ETA anchor (planned
        # arrival + delay) instead of guessing, and a deterministic band.
        if trip is not None:
            result.setdefault("planned_departure", trip.get("planned_departure"))
            result.setdefault("planned_arrival", trip.get("planned_arrival"))
        result.setdefault("next_boardable_station", None)
        result.setdefault("earliest_reroute_departure", None)
        result.setdefault("itinerary_broken", False)
        delay = result.get("current_delay_minutes") or 0
        result.setdefault(
            "risk_level",
            "HIGH" if result["itinerary_broken"] or delay >= 30
            else "MEDIUM" if delay >= 10 else "LOW",
        )
        if result["itinerary_broken"]:
            # A scripted missed transfer means the booked itinerary cannot be
            # completed — there is no stay-aboard ETA to synthesize, and a
            # fixture-supplied one would poison the reroute baseline.
            result["estimated_arrival"] = None
        else:
            planned_eta = _parse_datetime(result.get("planned_arrival"))
            if planned_eta is not None:
                result.setdefault(
                    "estimated_arrival", (planned_eta + timedelta(minutes=delay)).isoformat()
                )
        if "arrived" not in result:
            # Derive arrival from the delayed ETA (planned arrival + current
            # delay, both German-local wall clock). Once that instant has
            # passed, the delay is final — a scripted en-route status must not
            # keep a long-finished trip "running" forever. An explicit
            # ``arrived`` in the fixture (scripts/simulate_arrival.py) wins.
            eta = _parse_datetime(result.get("planned_arrival"))
            if eta is not None:
                result["arrived"] = _arrival_in_past(
                    (eta + timedelta(minutes=delay)).isoformat()
                )
        return result

    # Trip is known but the exact booked connection had no live match (and no
    # simulated status exists): answer from the booked legs' historical
    # forecast instead of erroring — but clearly flagged, never as live data.
    if trip is not None:
        risk_legs = _booked_risk_legs(trip)
        if risk_legs:
            forecasts = risk.forecast_trip(trip, risk_legs)
            for leg, forecast in zip(risk_legs, forecasts):
                leg["forecast"] = forecast
            connection_warnings = risk.connection_risks(risk_legs)
            # No live data exists for this trip. If its planned arrival lies
            # comfortably in the past (3h margin covers even heavy delays),
            # the trip is over — report ARRIVED so past trips are never
            # monitored/rerouted as if they were still running. The final
            # delay stays unknown (None): a compensation claim needs a
            # confirmed delay, which this path cannot provide.
            planned_arrival = _parse_datetime(trip.get("planned_arrival"))
            long_past = planned_arrival is not None and _arrival_in_past(
                (planned_arrival + timedelta(hours=3)).isoformat()
            )
            note = (
                "Live status for the exact booked connection is currently "
                "unavailable; this assessment is the historical forecast for "
                "the booked legs only."
            )
            if long_past:
                note = (
                    "This trip's planned arrival lies well in the past — the "
                    "trip has concluded. No live data is available anymore, so "
                    "the actually experienced final delay is UNKNOWN (a "
                    "compensation claim cannot be assessed from this result)."
                )
            return {
                "trip_id": trip_id,
                "train": trip.get("train"),
                "current_delay_minutes": None,
                "trend": "unknown",
                "current_position": None,
                "next_boardable_station": None,
                "earliest_reroute_departure": None,
                "incidents": [],
                "connection_risk": " ".join(connection_warnings)
                or "No live data — no connection risk visible from the historical forecast.",
                "risk_level": "LOW" if long_past else _overall_level(forecasts),
                "forecasts": forecasts,
                "legs": risk_legs,
                "planned_departure": trip.get("planned_departure"),
                "planned_arrival": trip.get("planned_arrival"),
                "arrived": long_past,
                "note": note,
                "data_timestamp": datetime.now(timezone.utc).isoformat(),
                "source": "db_history_forecast",
            }

    return {"trip_id": trip_id, "source": "none", "error": f"No trip data found for trip_id '{trip_id}'."}


def get_network_disruptions(region: str) -> dict:
    """Returns the current network-wide disruption status for a region.

    Args:
        region: Region in lowercase, e.g. "bavaria" or "berlin".

    Returns:
        A dict with the list of active disruptions for the region and ``source``.
    """
    anchor = _region_anchor(region)
    if anchor:
        try:
            eva = stations.resolve_eva(anchor)
            if eva:
                disruptions = _board_warnings(db_api.departures(eva, duration=60, results=80))
                if disruptions:
                    return {
                        "region": region,
                        "anchor_station": anchor,
                        "disruptions": disruptions,
                        "source": "db_service_live",
                    }
        except db_api.DBServiceError:
            pass
        except Exception:
            pass

    disruptions = mock_data.NETWORK_DISRUPTIONS.get(region.lower(), [])
    return {"region": region, "disruptions": disruptions, "source": "mock_region"}


# --- Planner tools ------------------------------------------------------------


_REROUTE_PREFERENCE_DEFAULTS = {"max_transfers": 2, "min_transfer_minutes": 8, "speed_vs_comfort": 50}


def _sanitize_reroute_preferences(raw_prefs: dict | None) -> dict:
    """Clamp a raw ``profile.preferences`` blob to safe deterministic bounds.

    Pure function over an already-fetched preferences dict, so a caller that
    already has the profile in hand (e.g. ``finalize_reroute_options``) can
    sanitize it without a second store round-trip.
    """
    prefs = raw_prefs or {}
    try:
        return {
            "max_transfers": max(
                0, int(prefs.get("max_transfers", _REROUTE_PREFERENCE_DEFAULTS["max_transfers"]))
            ),
            "min_transfer_minutes": max(
                0,
                int(
                    prefs.get(
                        "min_transfer_minutes", _REROUTE_PREFERENCE_DEFAULTS["min_transfer_minutes"]
                    )
                ),
            ),
            "speed_vs_comfort": min(
                100,
                max(
                    0,
                    int(
                        prefs.get(
                            "speed_vs_comfort", _REROUTE_PREFERENCE_DEFAULTS["speed_vs_comfort"]
                        )
                    ),
                ),
            ),
        }
    except (TypeError, ValueError):
        return dict(_REROUTE_PREFERENCE_DEFAULTS)


def _profile_reroute_preferences() -> dict:
    """Read and sanitize the current request's deterministic reroute preferences."""
    try:
        from journey_autopilot.persistence import store
        from journey_autopilot.request_context import current_user_id

        user_id = current_user_id.get()
        profile = store.get_profile(user_id) if user_id else store.any_profile()
        return _sanitize_reroute_preferences((profile or {}).get("preferences"))
    except Exception:
        return dict(_REROUTE_PREFERENCE_DEFAULTS)


def _profile_max_transfers(default: int = 2) -> int:
    """Backward-compatible accessor used by focused checks and callers."""
    return _profile_reroute_preferences().get("max_transfers", default)


def _reroute_sort_key(opt: dict) -> tuple:
    """Order discovery candidates by earliest arrival, then transfers/cost.

    Live arrivals are uniformly tz-aware within one search; the tz is stripped so
    a stray naive value cannot raise on comparison (wall-clock ordering, like
    ``_minutes_between``).
    """
    arr = _parse_datetime(opt.get("new_arrival"))
    arr_key = arr.replace(tzinfo=None) if arr else datetime.max
    cost = _option_cost(opt)
    return (
        arr_key,
        opt.get("transfers") or 0,
        cost if cost is not None else float("inf"),
        opt.get("added_delay_minutes") or 0,
    )


def _option_cost(opt: dict) -> float | None:
    """Comparable cost when known; prefer actual added cost over quoted fare."""
    value = opt.get("added_cost_eur")
    if value is None:
        value = opt.get("price_eur")
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _apply_cost_contract(option: dict) -> None:
    """Make known, estimated, and unknown incremental cost unambiguous."""
    status = option.get("cost_status")
    if status in ("known", "estimate", "unknown"):
        return
    if option.get("added_cost_eur") is not None:
        option["cost_status"] = "known"
        return
    mode = option.get("mode", "train")
    if mode in ("car_sharing", "bike_sharing") and option.get("price_eur") is not None:
        option["added_cost_eur"] = option["price_eur"]
        option["cost_status"] = "estimate"
        return
    if mode == "hotel" and option.get("price_per_night_eur") is not None:
        try:
            option["added_cost_eur"] = round(
                float(option["price_per_night_eur"])
                * max(1, int(option.get("nights") or 1)),
                2,
            )
        except (TypeError, ValueError):
            pass
        else:
            option["cost_status"] = "estimate"
            return
    option["added_cost_eur"] = None
    option["cost_status"] = "unknown"


def _tool_visible_option(option: dict) -> dict:
    """Remove provider-only identifiers from agent/browser-facing payloads."""
    return {key: value for key, value in option.items() if not key.startswith("_provider_")}


def _minimum_transfer_buffer(opt: dict) -> int | None:
    """Smallest live transfer buffer across an option's ride legs."""
    legs = opt.get("legs") or []
    buffers: list[int] = []
    for leg, following in zip(legs, legs[1:]):
        buffer_minutes = _minutes_between(leg.get("arrival"), following.get("departure"))
        if buffer_minutes is not None:
            buffers.append(buffer_minutes)
    return min(buffers) if buffers else None


def _before(value: str | None, threshold: str | None) -> bool:
    """Wall-clock comparison for German-local DB timestamps."""
    value_dt = _parse_datetime(value)
    threshold_dt = _parse_datetime(threshold)
    if value_dt is None or threshold_dt is None:
        return False
    return value_dt.replace(tzinfo=None) < threshold_dt.replace(tzinfo=None)


def _dominates(a: dict, b: dict) -> bool:
    """True if option ``a`` makes ``b`` pointless: ``a`` departs no earlier,
    arrives no later, has no more transfers, and is no more expensive. At least
    one dimension must be strictly better. Unknown-vs-known cost is deliberately
    incomparable so a potentially cheaper route is never discarded.
    """
    a_dep, b_dep = _parse_datetime(a.get("departure")), _parse_datetime(b.get("departure"))
    a_arr, b_arr = _parse_datetime(a.get("new_arrival")), _parse_datetime(b.get("new_arrival"))
    if a_dep is None or b_dep is None or a_arr is None or b_arr is None:
        return False
    # Wall-clock compare (strip tz; all live times are German-local).
    a_dep, b_dep = a_dep.replace(tzinfo=None), b_dep.replace(tzinfo=None)
    a_arr, b_arr = a_arr.replace(tzinfo=None), b_arr.replace(tzinfo=None)
    a_tr, b_tr = a.get("transfers") or 0, b.get("transfers") or 0
    a_cost, b_cost = _option_cost(a), _option_cost(b)
    if (a_cost is None) != (b_cost is None):
        return False
    cost_no_worse = True if a_cost is None else a_cost <= b_cost
    cost_better = False if a_cost is None else a_cost < b_cost
    no_worse = a_dep >= b_dep and a_arr <= b_arr and a_tr <= b_tr and cost_no_worse
    strictly_better = a_dep > b_dep or a_arr < b_arr or a_tr < b_tr or cost_better
    return no_worse and strictly_better


def _select_diverse_options(options: list[dict], max_options: int) -> list[dict]:
    """Cap a shortlist while preserving fastest, simplest, and cheapest choices."""
    if max_options <= 0 or not options:
        return []
    ranked = sorted(options, key=_reroute_sort_key)
    selected: list[dict] = []

    def add(option: dict | None) -> None:
        if option is not None and option not in selected and len(selected) < max_options:
            selected.append(option)

    add(ranked[0])
    add(min(ranked, key=lambda o: (o.get("transfers") or 0, _reroute_sort_key(o))))
    priced = [o for o in ranked if _option_cost(o) is not None]
    if priced:
        add(min(priced, key=lambda o: (_option_cost(o), _reroute_sort_key(o))))
    buffered = [o for o in ranked if o.get("minimum_transfer_minutes") is not None]
    if buffered:
        add(max(buffered, key=lambda o: o["minimum_transfer_minutes"]))
    for option in ranked:
        add(option)
    return sorted(selected, key=_reroute_sort_key)


def _prune_reroute_options(
    options: list[dict],
    *,
    max_transfers: int,
    min_transfer_minutes: int,
    max_added_delay_minutes: int,
    max_options: int,
    earliest_departure: str = "",
    current_arrival: str = "",
) -> dict:
    """Split raw live journeys into eligible, fallback, and rejected buckets."""
    eligible: list[dict] = []
    invalid: list[dict] = []
    rejected: list[dict] = []
    summary: dict[str, int] = {}

    def reject(option: dict, reasons: list[str], *, fallback: bool) -> None:
        item = {**option, "eligible": False, "selectable": False, "constraint_violations": reasons}
        (invalid if fallback else rejected).append(item)
        for reason in reasons:
            summary[reason] = summary.get(reason, 0) + 1

    for raw in options:
        option = dict(raw)
        option["minimum_transfer_minutes"] = _minimum_transfer_buffer(option)
        fatal_reasons: list[str] = []
        constraint_reasons: list[str] = []
        if option.get("cancelled"):
            fatal_reasons.append("cancelled")
        if earliest_departure and _before(option.get("departure"), earliest_departure):
            fatal_reasons.append("already_departed")
        if option.get("new_arrival") is None:
            fatal_reasons.append("missing_arrival")
        if (option.get("transfers") or 0) > max_transfers:
            constraint_reasons.append("too_many_transfers")
        transfer_buffer = option.get("minimum_transfer_minutes")
        if transfer_buffer is not None and transfer_buffer < min_transfer_minutes:
            constraint_reasons.append("transfer_too_short")

        # A current ETA is the decision baseline: a reroute that arrives later is
        # not useful. Callers omit current_arrival when the itinerary is broken
        # (missed transfer) — every option is then judged on its own merits.
        # Without it, fall back to the schedule-relative safety bound — and if
        # that too is unknown (no original_arrival to compare against), fall
        # back further to the option's own live-reported delay so an option
        # with no delay information at all is never silently treated as safe.
        saved = option.get("minutes_saved_vs_current_plan")
        if current_arrival and saved is not None:
            if saved < 0:
                constraint_reasons.append("slower_than_current_plan")
        else:
            effective_added_delay = option.get("added_delay_minutes")
            if effective_added_delay is None:
                effective_added_delay = option.get("live_delay_minutes")
            if effective_added_delay is not None and effective_added_delay > max_added_delay_minutes:
                constraint_reasons.append("excessive_added_delay")

        if fatal_reasons:
            reject(option, fatal_reasons + constraint_reasons, fallback=False)
        elif constraint_reasons:
            reject(option, constraint_reasons, fallback=True)
        else:
            option.update(eligible=True, selectable=True, constraint_violations=[])
            eligible.append(option)

    dominated: list[dict] = []
    survivors: list[dict] = []
    for option in eligible:
        if any(other is not option and _dominates(other, option) for other in eligible):
            dominated.append(
                {**option, "eligible": False, "selectable": False, "constraint_violations": ["dominated"]}
            )
            summary["dominated"] = summary.get("dominated", 0) + 1
        else:
            survivors.append(option)

    shortlist = _select_diverse_options(survivors, max_options)
    for option in survivors:
        if option not in shortlist:
            rejected.append(
                {**option, "eligible": False, "selectable": False, "constraint_violations": ["shortlist_cap"]}
            )
            summary["shortlist_cap"] = summary.get("shortlist_cap", 0) + 1
    rejected.extend(dominated)

    # Keep at most one least-bad, non-fatal route for explanation/constraint
    # relaxation. It is never placed in the selectable ``options`` list.
    fallback_options = sorted(invalid, key=_reroute_sort_key)[:1] if not shortlist else []
    return {
        "options": shortlist,
        "fallback_options": fallback_options,
        "rejected_options": rejected + ([o for o in invalid if o not in fallback_options]),
        "rejected_summary": summary,
        "raw_count": len(options),
    }


def find_reroute_options(
    origin: str,
    destination: str,
    departure: str = "",
    original_arrival: str = "",
    current_arrival: str = "",
    max_results: int = 8,
) -> dict:
    """Finds alternative connections (reroute options) between two stations.

    Args:
        origin: Departure station, e.g. "Munich Hbf".
        destination: Destination station, e.g. "Berlin Hbf".
        departure: Earliest boardable departure time (ISO timestamp). En route,
            pair this with the next boardable station as ``origin``.
        original_arrival: Original scheduled arrival, used only for the
            schedule-relative arrival delta.
        current_arrival: Current estimated arrival if the traveler stays on the
            disrupted itinerary. Options arriving later become non-selectable
            fallbacks; options arriving earlier expose minutes saved. Leave
            EMPTY when the itinerary is broken (a transfer already missed,
            ``itinerary_broken`` on the live status): staying aboard has no
            arrival time then, and a phantom baseline would demote every real
            alternative as "slower than doing nothing".
        max_results: Candidate-pool size before deterministic pruning (1..12).

    Returns:
        A dict with the list of possible reroutes including new arrival time,
        number of transfers, added delay, price if available, per-leg ``legs``
        (train, change stations, departure/arrival times — cite the change
        stations and transfer times when presenting an option), and ``source``.
    """
    # Scripted reroutes win over the live sidecar — same rationale as
    # get_live_trip_status: a demo route carries curated fixture options (the
    # happy-path R1 free / R2 via Leipzig) that tell one deterministic story.
    # A live search from the same station would return real, ever-changing
    # trains and silently break the canonical demo. Routes the fixture does NOT
    # curate return nothing here and fall through to the live-first path below,
    # so self-booked / non-demo trips stay live exactly as before.
    curated = mock_data.lookup_route(mock_data.REROUTE_OPTIONS, origin, destination)
    if curated:
        return _mock_reroute_result(origin, destination, curated)

    try:
        preferences = _profile_reroute_preferences()
        max_results = max(1, min(int(max_results), 12))
        from_eva = stations.resolve_eva(origin)
        to_eva = stations.resolve_eva(destination)
        if from_eva is None or to_eva is None:
            raise db_api.DBServiceError(
                f"Station not resolvable (origin={origin!r}, destination={destination!r})."
            )
        query_preferences: dict[str, Any] = {
            "transferTime": preferences["min_transfer_minutes"],
        }
        # Keep the DB candidate pool broader than the transfer preference. The
        # deterministic split below needs to distinguish "no compliant train"
        # from "no train at all" and retain one disabled least-bad fallback.
        payload = db_api.journeys(
            from_eva,
            to_eva,
            departure=departure or None,
            results=max_results,
            tickets=True,
            **query_preferences,
        )
        live_options = []
        for option in db_api.normalize_journeys(payload)[:max_results]:
            arrival = option.get("arrival") or option.get("planned_arrival")
            added_delay = _minutes_between(original_arrival, arrival)
            minutes_saved = _minutes_between(arrival, current_arrival)
            comfort_parts = []
            if option.get("price_eur") is not None:
                comfort_parts.append(f"Price: {option['price_eur']} EUR")
            if option.get("platform_changes"):
                comfort_parts.append("Platform change reported")
            # Compact per-leg itinerary so the UI (and the Planner's answer)
            # can show the change stations and the transfer time at each stop.
            legs = [
                {
                    "train": leg.get("train"),
                    "origin": leg.get("origin"),
                    "destination": leg.get("destination"),
                    "departure": leg.get("departure") or leg.get("planned_departure"),
                    "arrival": leg.get("arrival") or leg.get("planned_arrival"),
                    "planned_departure": leg.get("planned_departure"),
                    "planned_arrival": leg.get("planned_arrival"),
                    "departure_delay_minutes": leg.get("departure_delay_minutes"),
                    "arrival_delay_minutes": leg.get("arrival_delay_minutes"),
                    "cancelled": bool(leg.get("cancelled")),
                }
                for leg in option.get("legs") or []
                if leg.get("train")
            ]
            live_options.append(
                {
                    "option_id": option.get("option_id"),
                    "_provider_refresh_token": option.get("refresh_token"),
                    "mode": "train",
                    "description": option.get("description"),
                    "origin": origin,
                    "destination": destination,
                    "departure": option.get("departure") or option.get("planned_departure"),
                    "new_arrival": arrival,
                    "transfers": option.get("transfers", 0),
                    "added_delay_minutes": round(added_delay) if added_delay is not None else None,
                    "live_delay_minutes": option.get("arrival_delay_minutes"),
                    "minutes_saved_vs_current_plan": (
                        round(minutes_saved) if minutes_saved is not None else None
                    ),
                    "current_plan_arrival": current_arrival or None,
                    "comfort": "; ".join(comfort_parts) or "Live DB connection",
                    "price_eur": option.get("price_eur"),
                    "added_cost_eur": None,
                    "cost_status": "unknown",
                    "trains": option.get("trains", []),
                    "legs": legs,
                    "cancelled": bool(option.get("cancelled")),
                    "remarks": option.get("remarks", []),
                    "source": "db_service_live",
                }
            )
        if live_options:
            # Deterministic pre-filter: the sidecar returns the next N departures
            # verbatim, so without this the UI floods with the user's own delayed
            # train, later runs of the same line, and slow many-transfer routings.
            pruned = _prune_reroute_options(
                live_options,
                max_transfers=preferences["max_transfers"],
                min_transfer_minutes=preferences["min_transfer_minutes"],
                max_added_delay_minutes=REROUTE_MAX_ADDED_DELAY_MINUTES,
                max_options=REROUTE_MAX_OPTIONS,
                earliest_departure=departure,
                current_arrival=current_arrival,
            )
            logger.info(
                "reroute discovery: route=%s -> %s raw=%d eligible=%d fallback=%d rejected=%s",
                origin,
                destination,
                pruned["raw_count"],
                len(pruned["options"]),
                len(pruned["fallback_options"]),
                pruned["rejected_summary"],
            )
            live_options = pruned["options"]
            fallback_options = pruned["fallback_options"]
            # Renumber after pruning. R1 is the earliest discovery candidate;
            # final recommendation/order is assigned after profile/calendar checks.
            for i, opt in enumerate(live_options, start=1):
                opt["option_id"] = f"R{i}"
            for i, opt in enumerate(fallback_options, start=1):
                opt["option_id"] = f"R{i}"
            _stash_options(
                live_options,
                family="train",
                origin=origin,
                destination=destination,
                source="db_service_live",
                fallback_options=fallback_options,
                rejected_options=pruned["rejected_options"],
                rejected_summary=pruned["rejected_summary"],
            )
            return {
                "origin": origin,
                "destination": destination,
                "options": [_tool_visible_option(option) for option in live_options],
                "fallback_options": [
                    _tool_visible_option(option) for option in fallback_options
                ],
                "rejected_summary": pruned["rejected_summary"],
                "raw_count": pruned["raw_count"],
                "source": "db_service_live",
            }
    except db_api.DBServiceError:
        pass  # sidecar down/blocked — expected, fall back to mock quietly
    except Exception:
        # A bug in the live path (not a sidecar outage) — fall back too, but
        # leave a trace: this once hid a TypeError as "sidecar unavailable".
        logger.warning("find_reroute_options live path failed", exc_info=True)

    options = mock_data.lookup_route(mock_data.REROUTE_OPTIONS, origin, destination)
    if options:
        return _mock_reroute_result(origin, destination, options)
    _stash_options([], family="train", origin=origin, destination=destination, source="none")
    return {
        "origin": origin,
        "destination": destination,
        "options": [],
        "fallback_options": [],
        "source": "none",
        "error": "No reroute options available for this route.",
    }


def _mock_reroute_result(origin: str, destination: str, options: list[dict]) -> dict:
    """Build the reroute payload from curated fixture options (scripted / offline).

    Used both when a route has scripted options (scripted-wins, sidecar up) and
    as the fallback when the live sidecar is unreachable. The curated options
    already carry authored arrival/delay/cost fields, so no pruning or
    schedule-relative recomputation is applied — the fixture is the source of
    truth for these routes.
    """
    mock_options = [
        {
            **option,
            "mode": option.get("mode", "train"),
            "origin": origin,
            "destination": destination,
            "eligible": True,
            "selectable": True,
            "source": "mock_reroute_options",
        }
        for option in options
    ]
    for option in mock_options:
        _apply_cost_contract(option)
    _stash_options(
        mock_options,
        family="train",
        origin=origin,
        destination=destination,
        source="mock_reroute_options",
    )
    return {
        "origin": origin,
        "destination": destination,
        "options": mock_options,
        "source": "mock_reroute_options",
    }


def find_mobility_alternatives(
    location: str,
    destination: str = "",
    leg: str = "last_mile",
    max_results: int = 4,
) -> dict:
    """Finds Flinkster (car sharing) and Call-a-Bike (bike sharing) options near a station.

    Call this ONLY when no train option from ``find_reroute_options`` is viable —
    i.e. all train alternatives miss the hard-constraint deadline, exceed
    ``preferences.max_transfers``, or no train options were returned at all.
    Only call when ``profile.mobility.car_sharing_ok`` or
    ``profile.mobility.bike_sharing_ok`` is True (or the ``mobility`` section is
    absent from the profile, in which case both are assumed True by default).

    Swap point for a future real integration: replace the mock lookups below with
    DB Connect / Flinkster API calls keyed on the station's coordinates.

    Args:
        location: Station or city where the traveler is stranded, e.g. "Munich Hbf".
        destination: Target destination — used for context and drive-time estimates.
        leg: "last_mile" for short local connections, "bridging" for longer legs.
        max_results: Maximum number of alternatives to return in total.

    Returns:
        A dict with ``flinkster`` (car-sharing options, option_id C#) and
        ``callabike`` (bike-sharing options, option_id B#) lists. Each option
        carries: ``mode`` ("car_sharing" / "bike_sharing"), ``option_id``,
        ``description``, ``pickup`` location, ``distance_km``,
        ``est_duration_minutes``, ``new_arrival`` (ISO string or null),
        ``price_eur``, ``remarks``, and ``source`` ("mock_flinkster" /
        "mock_callabike"). Also includes ``source`` on the top-level dict.
    """
    max_results = max(1, min(int(max_results), 8))
    flinkster = [
        {
            **o,
            "origin": location,
            "destination": destination,
            "eligible": True,
            "selectable": True,
            "source": "mock_flinkster",
        }
        for o in mock_data.lookup_location(mock_data.FLINKSTER_OPTIONS, location)[:max_results]
    ]
    callabike = [
        {
            **o,
            "origin": location,
            "destination": destination,
            "eligible": True,
            "selectable": True,
            "source": "mock_callabike",
        }
        for o in mock_data.lookup_location(mock_data.CALLABIKE_OPTIONS, location)[:max_results]
    ]
    all_options: list[dict] = []
    for index in range(max(len(flinkster), len(callabike))):
        for group in (flinkster, callabike):
            if index < len(group) and len(all_options) < max_results:
                all_options.append(group[index])
    kept_ids = {o.get("option_id") for o in all_options}
    flinkster = [o for o in flinkster if o.get("option_id") in kept_ids]
    callabike = [o for o in callabike if o.get("option_id") in kept_ids]
    _stash_options(
        all_options,
        family="mobility",
        origin=location,
        destination=destination,
        source="mock_mobility" if all_options else "none",
    )
    return {
        "location": location,
        "destination": destination,
        "leg": leg,
        "flinkster": flinkster,
        "callabike": callabike,
        "source": "mock_mobility" if all_options else "none",
    }


def find_partner_hotels(
    location: str,
    check_in_date: str,
    max_results: int = 4,
) -> dict:
    """Finds hotel options near a stranded station for an overnight stay.

    Call this ONLY when no same-day option (train or mobility) can get the
    traveler to their destination, AND ``profile.home.hotel_ok`` is True.
    Covers the overnight case — traveler cannot reach the destination today.

    Live-first: real hotels near the station via OpenStreetMap
    (``integrations.hotels``), sorted by distance. Hotel prices are not shown
    because the live source cannot check rates. Falls back to the mock
    partner-hotel list when the live lookup fails or finds nothing.

    Args:
        location: Station or city near which to search — pass where the traveler
            ACTUALLY IS: the origin/start city before departure, the current
            stranded station en route, or the destination only when they can
            still arrive today (just too late), e.g. "Munich Hbf".
        check_in_date: Planned check-in date in "YYYY-MM-DD" format.
        max_results: Maximum number of hotels to return.

    Returns:
        A dict with a ``hotels`` list (option_id H#). Each hotel carries:
        ``mode`` ("hotel"), ``option_id``, ``name``, ``description``,
        ``distance_to_station_km``, ``price_per_night_eur``, ``check_in_date``,
        ``nights``, ``remarks``, and ``source`` ("osm_hotels_live" or
        "mock_hotels").
    """
    outcome = with_resilience(
        lambda: hotels_api.find_hotels_near_station(location, max_results=max_results),
        lambda: [
            {**h, "source": "mock_hotels"}
            for h in mock_data.lookup_location(mock_data.PARTNER_HOTELS, location)[:max_results]
        ],
        tool="find_partner_hotels",
        accept=lambda hs: bool(hs),
    )
    hotels = [
        {
            **h,
            "origin": location,
            "destination": location,
            "check_in_date": check_in_date,
            "eligible": True,
            "selectable": True,
        }
        for h in outcome.value
    ]
    source = hotels[0]["source"] if hotels else "none"
    _stash_options(
        hotels,
        family="hotel",
        origin=location,
        destination=location,
        source=source,
    )
    return {
        "location": location,
        "check_in_date": check_in_date,
        "hotels": hotels,
        "source": source,
    }


async def get_user_calendar(date: str, user_email: str | None = None) -> dict:
    """Reads the user's calendar appointments for a given date.

    Needed to check hard deadlines (e.g. an on-site meeting) against
    reroute options.

    Uses Outlook/Microsoft Graph **only when both** Entra credentials are
    present in .env (MS_ENTRA_CLIENT_ID, MS_ENTRA_TENANT_ID) **and** the user
    has connected Outlook during onboarding (``profile.connections.outlook``).
    This prevents the agent from triggering a blocking device-code flow
    mid-chat when the user skipped Outlook. Without a connected Outlook,
    falls back to mock data.

    Args:
        date: Date in "YYYY-MM-DD" format, e.g. "2026-06-03".
        user_email: Optional email of another user whose calendar
            should be queried. If omitted, the authenticated user's
            own calendar is used.

    Returns:
        A dict with the list of appointments. ``hard_constraint=True`` marks
        non-negotiable appointments. Contains ``source`` ("outlook", "mock", or
        "mock (Graph-Fallback)") and optionally ``error`` on failed
        Graph access (mock data was used as fallback in that case).
    """
    mock_events = mock_data.USER_CALENDAR.get(date, [])

    if not (_calendar_configured() and _outlook_connected()):
        return {"date": date, "events": mock_events, "source": "mock"}

    # Short-lived cache: the Planner reads the calendar once for the overview
    # and once more per reroute option (get_calendar_conflicts) — all within
    # one planning run and all for the same date. Serving those from a 60s
    # cache turns N+1 Graph round-trips into one; failed fetches are never
    # cached so a Graph hiccup can recover on the next call.
    try:
        from journey_autopilot.request_context import current_user_id

        request_user_id = current_user_id.get()
    except Exception:
        request_user_id = None
    cache_key = (request_user_id, date, user_email)
    cached = _CALENDAR_CACHE.get(cache_key)
    if cached is not None and time.monotonic() - cached[0] < _CALENDAR_CACHE_TTL_S:
        return {**cached[1], "events": list(cached[1]["events"])}

    async def _primary() -> dict:
        from ..integrations.outlook import get_calendar_events

        events = await get_calendar_events(date, user_email)
        _resolve_self_organized_contacts(events)
        return {"date": date, "events": events, "source": "outlook"}

    def _fallback() -> dict:
        return {"date": date, "events": mock_events, "source": "mock (Graph-Fallback)"}

    outcome = await with_resilience_async(_primary, _fallback, tool="get_user_calendar")
    result = outcome.value
    if outcome.failure is not None:  # Graph access failed -> surface why
        result["error"] = outcome.failure["fallback_taken"]
    else:
        _CALENDAR_CACHE[cache_key] = (time.monotonic(), result)
    return result


# Minutes planned for getting from the arrival station to an appointment —
# the same assumption the Planner uses when gating reroute options.
CALENDAR_TRAVEL_BUFFER_MINUTES = 30

# Live-calendar cache: (date, user_email) -> (monotonic timestamp, result).
# 60 seconds spans one planning run (overview + per-option conflict checks)
# without holding stale data across chat turns.
_CALENDAR_CACHE: dict[tuple[str | None, str, str | None], tuple[float, dict]] = {}
_CALENDAR_CACHE_TTL_S = 60

_TIME_ONLY_RE = re.compile(r"^\d{1,2}:\d{2}$")


def _parse_trip_time(date: str, value: str | None) -> datetime | None:
    """Parse "HH:MM" (combined with ``date``) or an ISO datetime to naive local time.

    Calendar events and DB times are all Europe/Berlin wall time in this app
    (the Graph client requests that timezone, the mapper strips the offset), so
    any offset is dropped rather than converted.
    """
    if not value:
        return None
    value = value.strip()
    if _TIME_ONLY_RE.match(value):
        value = f"{date}T{value}:00"
    dt = _parse_datetime(value)
    return dt.replace(tzinfo=None) if dt is not None else None


def _classify_window_conflicts(
    events: list[dict],
    date: str,
    planned_departure: str = "",
    expected_arrival: str = "",
    latest_arrival: str = "",
) -> dict | None:
    """Classify calendar events against one trip window (shared core).

    Returns ``None`` when no usable arrival estimate was given; otherwise a
    dict with ``conflicts`` (events tagged ``during_trip`` /
    ``at_risk_if_delayed``), ``hard_conflicts``, ``unparsed_events``, and
    ``departure_known``. Used by ``get_calendar_conflicts`` (one window) and
    ``check_options_against_calendar`` (one window per reroute option).
    """
    departure = _parse_trip_time(date, planned_departure)
    expected = _parse_trip_time(date, expected_arrival)
    latest = _parse_trip_time(date, latest_arrival)
    if expected is None and latest is None:
        return None
    expected = expected or latest
    latest = max(latest, expected) if latest else expected  # invariant: latest >= expected

    buffer = timedelta(minutes=CALENDAR_TRAVEL_BUFFER_MINUTES)
    conflicts: list[dict] = []
    unparsed = 0
    for event in events:
        start = _parse_trip_time(date, event.get("start"))
        if start is None:
            unparsed += 1
            continue
        if departure is not None and start < departure:
            continue  # before the trip — unaffected
        if start < expected + buffer:
            clash = "during_trip"
        elif start < latest + buffer:
            clash = "at_risk_if_delayed"
        else:
            continue  # reachable even in the unfavorable case
        conflicts.append({**event, "clash": clash})
    return {
        "conflicts": conflicts,
        "hard_conflicts": sum(1 for c in conflicts if c.get("hard_constraint")),
        "unparsed_events": unparsed,
        "departure_known": departure is not None,
    }


async def get_calendar_conflicts(
    date: str,
    planned_departure: str = "",
    expected_arrival: str = "",
    latest_arrival: str = "",
    user_email: str | None = None,
) -> dict:
    """Checks the user's calendar for appointments that clash with a trip.

    Deterministically compares each appointment on the travel date against a
    trip window — use it to gate a planned trip or a single reroute option on
    the appointments the traveler would miss because of the arrival time or
    its delay. Reads the same calendar source as ``get_user_calendar``
    (Outlook when connected, mock otherwise).

    Classification per appointment (a travel buffer of 30 minutes from the
    station to the appointment is applied):
      - ``during_trip``: starts after the departure but before the expected
        arrival + buffer — missed even without additional delay.
      - ``at_risk_if_delayed``: reachable at the expected arrival, but missed
        at the unfavorable (latest) arrival.
      - Appointments before the departure or reachable even in the unfavorable
        case are counted as clear and not listed.

    Args:
        date: Travel date in "YYYY-MM-DD" format, e.g. "2026-06-19".
        planned_departure: Planned departure — ISO datetime or "HH:MM".
        expected_arrival: Typical expected arrival (planned arrival + expected
            delay) — ISO datetime or "HH:MM".
        latest_arrival: Unfavorable-case arrival (e.g. planned arrival + p90
            delay) — ISO datetime or "HH:MM". Optional; defaults to
            ``expected_arrival``.
        user_email: Optional email of another user whose calendar to query.

    Returns:
        A dict with ``conflicts`` (each clashing appointment incl. its
        ``clash`` kind and ``hard_constraint`` flag), ``hard_conflicts``
        (count of clashing non-negotiable appointments), ``events_checked``,
        ``clear_events``, ``buffer_minutes``, and the calendar ``source``.
        Contains "error" if no usable arrival estimate was provided.
    """
    calendar = await get_user_calendar(date, user_email)
    events = calendar.get("events", [])

    classified = _classify_window_conflicts(
        events, date, planned_departure, expected_arrival, latest_arrival
    )
    if classified is None:
        return {
            "date": date,
            "events_checked": len(events),
            "error": (
                "No usable arrival estimate — pass expected_arrival (and ideally "
                "latest_arrival) as ISO datetime or HH:MM."
            ),
            "source": calendar.get("source"),
        }
    conflicts = classified["conflicts"]
    unparsed = classified["unparsed_events"]

    result = {
        "date": date,
        "trip_window": {
            "planned_departure": planned_departure or None,
            "expected_arrival": expected_arrival or None,
            "latest_arrival": latest_arrival or None,
        },
        "buffer_minutes": CALENDAR_TRAVEL_BUFFER_MINUTES,
        "events_checked": len(events),
        "conflicts": conflicts,
        "hard_conflicts": classified["hard_conflicts"],
        "clear_events": len(events) - len(conflicts) - unparsed,
        "source": calendar.get("source"),
    }
    if unparsed:
        result["unparsed_events"] = unparsed
    if not classified["departure_known"] and events:
        result["warning"] = (
            "planned_departure was not provided — appointments before the "
            "trip could not be excluded and may be misclassified as "
            "during_trip. Pass the departure time for a reliable result."
        )
    if calendar.get("error"):
        result["calendar_error"] = calendar["error"]
    return result


async def check_options_against_calendar(
    date: str,
    planned_departure: str = "",
    user_email: str | None = None,
) -> dict:
    """Checks ALL reroute options found this turn against the calendar at once.

    Call this ONCE after ``find_reroute_options`` (and any ecosystem tools)
    instead of checking options one by one with ``get_calendar_conflicts``.
    It reads the options gathered in this planning turn, fetches the calendar
    a single time, and returns a per-option verdict.

    Args:
        date: Travel date in "YYYY-MM-DD" format, e.g. "2026-06-19".
        planned_departure: The trip's planned departure (ISO datetime or
            "HH:MM") — used for options that carry no departure of their own,
            so appointments before the trip are not misclassified.
        user_email: Optional email of another user whose calendar to query.

    Returns:
        A dict with ``option_verdicts`` — one entry per option:
        ``option_id``, ``mode``, ``viable`` (True = no hard-constraint clash),
        ``hard_conflicts``, and the clashing appointments (incl. their
        ``hard_constraint`` flag and organizer/attendee contacts). Options
        without a same-day arrival (e.g. hotels) return ``viable: null`` with
        a note. Also contains ``hard_constraint_appointments`` (all
        non-negotiable appointments that day), ``events_checked``,
        ``buffer_minutes``, and the calendar ``source``.
    """
    if not calendar_connected():
        stashed = last_reroute_options()
        if stashed is not None:
            stashed["calendar_checked"] = True
            stashed["calendar_verdicts"] = {}
        return {
            "calendar_connected": False,
            "checked": False,
            "note": (
                "No calendar is connected — appointment deadlines cannot be "
                "checked. Treat options as free of calendar conflicts."
            ),
        }

    stashed = last_reroute_options()
    if stashed is None:
        return {
            "date": date,
            "error": (
                "No reroute options were gathered this turn — call "
                "find_reroute_options first, then this check."
            ),
        }
    options = stashed.get("candidate_options") or []
    if not options:
        stashed["calendar_checked"] = True
        stashed["calendar_verdicts"] = {}
        return {
            "calendar_connected": True,
            "date": date,
            "checked": True,
            "option_verdicts": [],
            "note": "No eligible reroute candidates to check; widen the search or finalize fallbacks.",
        }

    calendar = await get_user_calendar(date, user_email)
    events = calendar.get("events", [])

    verdicts: list[dict] = []
    for option in options:
        entry: dict = {
            "option_id": option.get("option_id"),
            "mode": option.get("mode", "train"),
        }
        arrival = option.get("new_arrival")
        if not arrival:
            entry.update(
                viable=None,
                note=(
                    "No same-day arrival time (e.g. overnight hotel) — not "
                    "checkable against the travel day's appointments."
                ),
            )
            verdicts.append(entry)
            continue
        classified = _classify_window_conflicts(
            events,
            date,
            planned_departure=option.get("departure") or planned_departure,
            expected_arrival=arrival,
        )
        # classified can't be None here: arrival is a non-empty expected_arrival.
        entry.update(
            new_arrival=arrival,
            viable=classified["hard_conflicts"] == 0,
            hard_conflicts=classified["hard_conflicts"],
            conflicts=classified["conflicts"],
        )
        verdicts.append(entry)

    result = {
        "calendar_connected": True,
        "date": date,
        "buffer_minutes": CALENDAR_TRAVEL_BUFFER_MINUTES,
        "events_checked": len(events),
        "hard_constraint_appointments": [
            e for e in events if e.get("hard_constraint")
        ],
        "option_verdicts": verdicts,
        "source": calendar.get("source"),
    }
    if calendar.get("error"):
        result["calendar_error"] = calendar["error"]
    stashed["calendar_checked"] = True
    stashed["calendar_verdicts"] = {
        entry.get("option_id"): entry for entry in verdicts if entry.get("option_id")
    }
    stashed["calendar_result"] = result
    return result


def _station_key(value: Any) -> str:
    if isinstance(value, dict):
        value = value.get("name")
    return " ".join(str(value or "").casefold().split())


def _time_after_home_limit(arrival: datetime | None, departure: datetime | None, limit: str) -> bool:
    """True if `arrival`'s wall-clock time is after "HH:MM" `limit`.

    Also true if `arrival` falls on a later calendar date than `departure`
    (an overnight arrival is "after" any same-day cutoff regardless of the
    clock reading). `limit` must already be validated as "HH:MM"; an
    unparseable `arrival` is treated as "not after" (nothing to compare).
    """
    if arrival is None:
        return False
    limit_hour, limit_minute = (int(part) for part in limit.split(":"))
    if departure is not None and arrival.replace(tzinfo=None).date() > departure.replace(tzinfo=None).date():
        return True
    return arrival.hour * 60 + arrival.minute > limit_hour * 60 + limit_minute


def _arrives_after_home_limit(option: dict, profile: dict) -> bool:
    """Apply latest-arrival-home only when the option actually ends at home."""
    home = profile.get("home") or {}
    home_station = _station_key(home.get("home_station"))
    if not home_station or _station_key(option.get("destination")) != home_station:
        return False
    limit = str(home.get("latest_arrival_home") or "").strip()
    if not re.fullmatch(r"\d{2}:\d{2}", limit):
        return False
    arrival = _parse_datetime(option.get("new_arrival"))
    departure = _parse_datetime(option.get("departure"))
    return _time_after_home_limit(arrival, departure, limit)


def _mode_eligibility_violations(
    option: dict,
    *,
    preferences: dict,
    mobility: dict,
    home: dict,
    recompute_transfer_buffer: bool,
) -> list[str]:
    """Shared per-mode transfer/cancellation/mobility/hotel eligibility rules.

    Used by both ``finalize_reroute_options`` (discovery-time) and
    ``write_tools._profile_constraint_violations`` (execution-time
    revalidation) so the two can't silently diverge on what counts as an
    eligible option. Calendar verdicts and live-freshness checks (e.g.
    already-departed) are caller-specific and stay out of this function.
    """
    reasons: list[str] = []
    mode = option.get("mode", "train")
    if mode == "train":
        if option.get("cancelled"):
            reasons.append("cancelled")
        try:
            max_transfers = max(0, int(preferences.get("max_transfers", 2)))
            min_transfer = max(0, int(preferences.get("min_transfer_minutes", 8)))
        except (TypeError, ValueError):
            max_transfers, min_transfer = 2, 8
        if (option.get("transfers") or 0) > max_transfers:
            reasons.append("too_many_transfers")
        if recompute_transfer_buffer or option.get("minimum_transfer_minutes") is None:
            option["minimum_transfer_minutes"] = _minimum_transfer_buffer(option)
        buffer = option.get("minimum_transfer_minutes")
        if buffer is not None and buffer < min_transfer:
            reasons.append("transfer_too_short")
    elif mode == "car_sharing" and not mobility.get("car_sharing_ok", True):
        reasons.append("car_sharing_disabled")
    elif mode == "bike_sharing" and not mobility.get("bike_sharing_ok", True):
        reasons.append("bike_sharing_disabled")
    elif mode == "hotel" and not home.get("hotel_ok", True):
        reasons.append("hotel_disabled")
    return reasons


def _profile_rank_score(option: dict, *, earliest_arrival: datetime | None, preferences: dict) -> float:
    """Deterministic profile score; lower is better."""
    speed = preferences.get("speed_vs_comfort", 50) / 100.0
    arrival = _parse_datetime(option.get("new_arrival"))
    arrival_penalty = 24 * 60.0
    if arrival is not None and earliest_arrival is not None:
        arrival_penalty = max(
            0.0,
            (arrival.replace(tzinfo=None) - earliest_arrival.replace(tzinfo=None)).total_seconds()
            / 60.0,
        )
    elif option.get("mode") == "hotel":
        arrival_penalty = 48 * 60.0

    transfers = float(option.get("transfers") or 0)
    time_weight = 0.5 + speed
    transfer_weight = 45.0 - 35.0 * speed
    cost = _option_cost(option)
    cost_penalty = (cost or 0.0) * 0.05
    buffer = option.get("minimum_transfer_minutes")
    buffer_penalty = 0.0
    if buffer is not None:
        safe_buffer = preferences.get("min_transfer_minutes", 8) + 5
        buffer_penalty = max(0, safe_buffer - buffer) * (2.0 - speed)
    return round(
        arrival_penalty * time_weight
        + transfers * transfer_weight
        + cost_penalty
        + buffer_penalty,
        2,
    )


def _select_final_diverse(options: list[dict], max_options: int) -> list[dict]:
    """Recommended option first, while preserving distinct useful trade-offs."""
    if not options:
        return []
    max_options = max(1, max_options)
    ranked = sorted(options, key=lambda o: (o.get("ranking_score", float("inf")), _reroute_sort_key(o)))
    selected: list[dict] = []

    def add(option: dict | None) -> None:
        if option is not None and option not in selected and len(selected) < max_options:
            selected.append(option)

    add(ranked[0])
    timed = [o for o in ranked if _parse_datetime(o.get("new_arrival")) is not None]
    if timed:
        add(min(timed, key=_reroute_sort_key))
    trains = [o for o in ranked if o.get("mode", "train") == "train"]
    if trains:
        add(min(trains, key=lambda o: (o.get("transfers") or 0, _reroute_sort_key(o))))
    priced = [o for o in ranked if _option_cost(o) is not None]
    if priced:
        add(min(priced, key=lambda o: (_option_cost(o), o.get("ranking_score", float("inf")))))
    for option in ranked:
        add(option)
    return selected


def finalize_reroute_options(max_options: int = REROUTE_MAX_OPTIONS) -> dict:
    """Finalize the selectable reroute cards after all discovery/calendar calls.

    This is the only tool allowed to populate the UI-facing ``options`` list.
    It reapplies profile hard limits, ranks by the speed-vs-comfort preference,
    preserves fastest/simple/cheap alternatives, and keeps constraint-breaking
    fallbacks (cancelled, too many transfers, mode disabled, arrives after the
    traveler's own latest-arrival-home time) separate and non-selectable.

    A hard-constraint CALENDAR clash is treated differently: it does not
    disqualify an option (the traveler can still take a train that arrives
    late for one appointment) — merged verdicts instead annotate the option
    with ``calendar_clash`` so the Planner can recommend rescheduling that
    appointment and notifying its contact, while still presenting the option
    as bookable. Hotels are gated purely on whether any reachable option's
    predicted arrival still beats ``home.latest_arrival_home`` — never on a
    calendar clash alone.

    Args:
        max_options: Maximum number of selectable cards across all modes.

    Returns:
        ``options`` (eligible/selectable), ``fallback_options`` (disabled), the
        recommended option id, and a rejection summary. Call this after the
        calendar check and after any mobility/hotel widening.
    """
    stashed = last_reroute_options()
    if stashed is None:
        return {"error": "No reroute search has run in this turn."}
    candidates = [dict(option) for option in stashed.get("candidate_options") or []]
    if calendar_connected() and candidates and not stashed.get("calendar_checked"):
        return {
            "error": (
                "Calendar is connected but the current candidate batch has not been checked. "
                "Call check_options_against_calendar after the last discovery tool, then finalize again."
            )
        }

    profile = get_user_profile()
    if profile.get("error"):
        profile = {
            "preferences": dict(_REROUTE_PREFERENCE_DEFAULTS),
            "home": {},
            "mobility": {"car_sharing_ok": True, "bike_sharing_ok": True},
        }
    # Sanitize/clamp the preferences already fetched above (malformed or
    # out-of-range values) instead of re-reading the profile from the store a
    # second time — do not overwrite `profile` with the raw sanitized blob.
    preferences = _sanitize_reroute_preferences(profile.get("preferences"))
    mobility = profile.get("mobility") or {}
    home = profile.get("home") or {}
    verdicts = stashed.get("calendar_verdicts") or {}
    rejected_summary: dict[str, int] = {}
    fallback_pool: list[dict] = []
    for family_data in stashed.get("families", {}).values():
        fallback_pool.extend(dict(option) for option in family_data.get("fallback_options") or [])
        for reason, count in (family_data.get("rejected_summary") or {}).items():
            rejected_summary[reason] = rejected_summary.get(reason, 0) + int(count)
    eligible: list[dict] = []

    for option in candidates:
        _apply_cost_contract(option)
        reasons = _mode_eligibility_violations(
            option,
            preferences=preferences,
            mobility=mobility,
            home=home,
            recompute_transfer_buffer=False,
        )

        # A hard-constraint calendar clash does NOT make the option unbookable —
        # the traveler can still take it, just late for that appointment. Surface
        # the clash (so the Planner can recommend rescheduling + notifying
        # participants) instead of hiding a perfectly reachable option.
        verdict = verdicts.get(option.get("option_id"))
        if verdict and verdict.get("viable") is False:
            option["calendar_clash"] = {
                "hard_conflicts": verdict.get("hard_conflicts"),
                "conflicts": verdict.get("conflicts"),
            }
        if _arrives_after_home_limit(option, profile):
            reasons.append("after_latest_arrival_home")

        if reasons:
            fallback_pool.append(
                {**option, "eligible": False, "selectable": False, "constraint_violations": reasons}
            )
            for reason in reasons:
                rejected_summary[reason] = rejected_summary.get(reason, 0) + 1
        else:
            option.update(eligible=True, selectable=True, constraint_violations=[])
            eligible.append(option)

    # Hotels are a genuine last resort: only worth suggesting when nothing that
    # actually reaches the destination gets the traveler home before the
    # profile's latest-arrival-home cutoff — never merely because a reachable
    # option misses a meeting (see calendar_clash above) or exceeds the
    # transfer preference (that's already excluded via constraint_violations).
    limit = str(home.get("latest_arrival_home") or "").strip()
    non_hotel_options = [option for option in eligible if option.get("mode") != "hotel"]
    if re.fullmatch(r"\d{2}:\d{2}", limit):
        known_arrivals = [
            (_parse_datetime(option.get("new_arrival")), _parse_datetime(option.get("departure")))
            for option in non_hotel_options
            if option.get("new_arrival")
        ]
        hotel_needed = all(
            _time_after_home_limit(arrival, departure, limit) for arrival, departure in known_arrivals
        )
    else:
        # No configured cutoff — fall back to the previous rule: any reachable
        # non-hotel option makes hotels redundant.
        hotel_needed = not non_hotel_options

    if not hotel_needed:
        omitted_hotels = sum(option.get("mode") == "hotel" for option in eligible)
        eligible = [option for option in eligible if option.get("mode") != "hotel"]
        if omitted_hotels:
            rejected_summary["hotel_not_needed"] = (
                rejected_summary.get("hotel_not_needed", 0) + omitted_hotels
            )

    arrivals = [_parse_datetime(o.get("new_arrival")) for o in eligible]
    earliest_arrival = min(
        (arrival for arrival in arrivals if arrival is not None),
        key=lambda dt: dt.replace(tzinfo=None),
        default=None,
    )
    for option in eligible:
        option["ranking_score"] = _profile_rank_score(
            option, earliest_arrival=earliest_arrival, preferences=preferences
        )

    selected = _select_final_diverse(eligible, min(max(1, int(max_options)), REROUTE_MAX_OPTIONS))
    omitted = len(eligible) - len(selected)
    if omitted:
        rejected_summary["final_shortlist_cap"] = rejected_summary.get("final_shortlist_cap", 0) + omitted
    for rank, option in enumerate(selected, start=1):
        option["rank"] = rank
        option["recommended"] = rank == 1
    recommended_option_id = selected[0].get("option_id") if selected else None

    # Fallbacks are useful only when there is no selectable plan. Never expose
    # more than one, and never let it pass the UI's click gate.
    fallback_options: list[dict] = []
    if not selected and fallback_pool:
        fallback = sorted(fallback_pool, key=_reroute_sort_key)[0]
        fallback_options = [{**fallback, "eligible": False, "selectable": False}]

    stashed["options"] = selected
    stashed["fallback_options"] = fallback_options
    stashed["recommended_option_id"] = recommended_option_id
    stashed["rejected_summary"] = rejected_summary
    stashed["finalized"] = True
    logger.info(
        "reroute finalized: candidates=%d selectable=%d fallback=%d recommended=%s rejected=%s",
        len(candidates),
        len(selected),
        len(fallback_options),
        recommended_option_id,
        rejected_summary,
    )
    return {
        "options": [_tool_visible_option(option) for option in selected],
        "fallback_options": [
            _tool_visible_option(option) for option in fallback_options
        ],
        "recommended_option_id": recommended_option_id,
        "candidate_count": len(candidates),
        "rejected_summary": rejected_summary,
        "source": stashed.get("source"),
    }


def get_user_profile() -> dict:
    """Reads the user's personal preference profile from onboarding.

    Contains class, seat preferences, the speed-vs-comfort tradeoff (0 = maximum
    comfort, 100 = fastest arrival), maximum number of transfers, home station,
    latest return time, and the autonomy level. Reroute options should be
    evaluated against this profile.

    Returns:
        A dict with the profile, or with "error" if onboarding has not been
        completed yet.
    """
    try:
        # Lazy import: keeps the ADK package independent of the persistence
        # layer (SQLite store) as long as the tool is not called.
        from journey_autopilot.persistence import store
        from journey_autopilot.request_context import current_user_id

        user_id = current_user_id.get()
        profile = store.get_profile(user_id) if user_id else store.any_profile()
    except Exception as exc:  # persistence layer / DB not available
        return {"error": f"Profile not readable: {exc}"}
    if profile is None:
        return {"error": "No user profile available — onboarding has not been completed yet."}
    return profile


def get_upcoming_trips() -> dict:
    """Returns the user's upcoming trips imported during onboarding.

    Returns:
        A dict with the list of monitored trips (trip_id, origin, destination,
        train, scheduled times). Falls back to the demo trip if onboarding has
        not been completed yet.
    """
    try:
        from journey_autopilot.persistence import store

        profile = store.any_profile()
        if profile is not None:
            return {"trips": store.get_trips(profile["user_id"])}
    except Exception:
        pass
    return {"trips": [mock_data.DEMO_TRIP], "note": "Fallback: demo trip (no onboarding profile)."}


# get_passenger_rights is a Planner tool, and the Planner is only reachable
# through an AgentTool (the orchestrator calls it as a nested agent). ADK's
# AgentTool runs the wrapped agent in its own internal Runner and forwards
# only the agent's final merged *text* to the parent — the Planner's own
# tool-call results (get_passenger_rights included) never reach the
# top-level event stream that ui.chat iterates. Scanning that trace for a
# "get_passenger_rights" entry therefore never matches.
#
# Workaround (same pattern as the request-scoped reroute workspace on
# the rerouting branch): the tool stashes its result here while it runs
# (same process), and the caller reads it after the run instead of the
# trace. Safe for the single-user prototype (the chat UI's busy guard
# prevents concurrent turns). ui.chat.chat_turn clears the slot at the start
# of each turn so a stale result from a previous turn is never reused.
_LAST_RIGHTS: dict | None = None


def last_passenger_rights() -> dict | None:
    """Returns the most recent get_passenger_rights result, or None."""
    return _LAST_RIGHTS


def clear_passenger_rights() -> None:
    """Reset the in-process slot — called at the start of each chat turn."""
    global _LAST_RIGHTS
    _LAST_RIGHTS = None


def get_passenger_rights(
    delay_minutes: int,
    ticket_type: str = "einzelticket",
    price_paid: float = 0.0,
    travel_class: int = 2,
    bahncard_type: str = "keine",
) -> dict:
    """Determines passenger rights and calculates the concrete compensation claim.

    Combines two sources:
      1. Deterministic rule logic (rights_service) → exact EUR amount
      2. RAG search in ChromaDB → legal context chunks from bahn.de

    Args:
        delay_minutes:  Expected arrival delay at destination in minutes.
        ticket_type:    Ticket type: "einzelticket" | "zeitkarte_fv" |
                        "zeitkarte_nv" | "bc100" | "deutschland_ticket".
        price_paid:     Ticket price paid in EUR (relevant for single tickets).
        travel_class:   Travel class, 1 or 2 (default: 2).
        bahncard_type:  User's BahnCard: "keine" | "bc25" | "bc50" | "bc100".

    Returns:
        Dict with calculated compensation claim, legal context chunks, and
        the bahn.de source URLs those chunks came from (``legal_sources``).
    """
    # 1. Deterministic calculation — no LLM, no network
    compensation = calculate_compensation(
        delay_minutes=delay_minutes,
        ticket_type=ticket_type,
        price_paid=price_paid,
        travel_class=travel_class,
        bahncard_type=bahncard_type,
    )

    # 2. RAG context for the agent — semantically matching chunks, with the
    # bahn.de page each one came from so the claim can cite a source.
    try:
        rag = _get_or_build_rag()
        chunks = rag.retrieve_for_case(
            delay_minutes=delay_minutes,
            ticket_type=ticket_type,
            bahncard_type=bahncard_type,
        )
        legal_context = "\n\n--- Next Section ---\n".join(c["text"] for c in chunks)
        legal_sources = sorted({c["source"] for c in chunks if c.get("source")})
    except Exception:
        legal_context = "Knowledge base temporarily unavailable."
        legal_sources = []

    result = {
        "delay_minutes": delay_minutes,
        **compensation,
        "legal_context": legal_context,
        "legal_sources": legal_sources,
    }
    global _LAST_RIGHTS
    _LAST_RIGHTS = result
    return result


# Constructing FahrgastrechteRAG imports sentence_transformers + torch, which
# takes ~20-40s the first time in a fresh process — almost entirely Python
# import overhead (proven by timing it directly), not model download or
# inference. Left to happen on the first real get_passenger_rights() call,
# that cost lands squarely in the middle of a chat turn. Instead, kick it off
# in the background as soon as this module loads (i.e. as soon as a chat turn
# starts building the agent graph) so it runs concurrently with the ReAct
# loop's own LLM latency — by the time the Planner actually calls
# get_passenger_rights, the model is very likely already warm.
_RAG_LOCK = threading.Lock()


def _get_or_build_rag():
    """Returns the cached FahrgastrechteRAG singleton, building it if needed."""
    rag = getattr(get_passenger_rights, "_rag", None)
    if rag is not None:
        return rag
    with _RAG_LOCK:
        rag = getattr(get_passenger_rights, "_rag", None)
        if rag is None:
            from ..integrations.rights_rag.rag_store import FahrgastrechteRAG

            rag = FahrgastrechteRAG()
            setattr(get_passenger_rights, "_rag", rag)
        return rag


def _warm_rag_in_background() -> None:
    try:
        _get_or_build_rag()
    except Exception:
        pass  # get_passenger_rights() retries synchronously and surfaces the real error


threading.Thread(target=_warm_rag_in_background, daemon=True).start()


# --- Risk tools (pre-trip delay assessment) -----------------------------------


def get_connection_delay_reference(origin: str, destination: str, train: str = "") -> dict:
    """Returns the pre-trip risk forecast (historical baseline) for a connection.

    Scores how delay-prone the connection normally is, from the historical DB
    punctuality archive (piebro/deutsche-bahn-data) via the risk module — the
    reliable "normal case" for the pre-trip assessment. Pair it with
    ``get_connection_delay_history`` (today's situation) and
    ``get_planned_connection`` (the scheduled-arrival ETA anchor).

    Args:
        origin: Departure station (context only; the arrival at the destination is scored).
        destination: Destination station, e.g. "Berlin Hbf".
        train: Optional train name (e.g. "ICE 1006") — determines the train type;
            omitted falls back to the station-wide baseline.

    Returns:
        A dict with ``risk_level`` (LOW/MEDIUM/HIGH), ``risk_score`` (0-100),
        ``expected_delay_minutes``, ``confidence`` (from sample size), ``factors``
        (a plain-language note), and ``source``. Returns an error if the route
        cannot be forecasted.
    """
    try:
        trip = {"origin": origin, "destination": destination, "train": train}
        # forecast_leg scores from destination + train type; times are not read.
        legs = [
            {
                "origin": {"name": origin},
                "destination": {"name": destination},
                "train": train,
                "current_delay_minutes": 0,
            }
        ]

        forecasts = risk.forecast_trip(trip, legs)
        if forecasts:
            forecast = forecasts[0]
            return {
                "origin": origin,
                "destination": destination,
                "train": train,
                "risk_level": forecast.get("level", "medium").upper(),
                "risk_score": forecast.get("risk_score", 50),
                "expected_delay_minutes": forecast.get("expected_delay_minutes", 0),
                "confidence": forecast.get("confidence", 0.5),
                "factors": forecast.get("factors", []),
                "source": forecast.get("source", "db_history"),
            }
        else:
            return {
                "origin": origin,
                "destination": destination,
                "error": "Could not compute risk forecast for this connection.",
            }
    except Exception as e:
        return {
            "origin": origin,
            "destination": destination,
            "error": f"Risk forecast error: {e}",
        }


def _connection_delay_history(
    origin: str, destination: str, train: str = "", *, details: bool = False
) -> dict:
    """Shared live/fallback resolution for the connection delay history.

    Live sidecar first (arrival board at the destination), simulated history on
    failure or an empty sample. ``details=True`` asks the live source for the
    individual considered arrivals (``samples``) — used by the verbose risk
    scenario; the agent-facing tool keeps it off to stay context-lean.
    """
    def _primary() -> dict:
        stats = risk_model.connection_delay_history(
            origin, destination, train=train, details=details
        )
        stats["source"] = "db_service_live"
        return stats

    def _fallback() -> dict:
        mock = mock_data.CONNECTION_DELAY_HISTORY.get((origin, destination))
        if mock is None:
            return {
                "origin": origin,
                "destination": destination,
                "error": "No delay history available for this connection.",
            }
        result = dict(mock)
        result.update(
            {"origin": origin, "destination": destination, "train": train or None, "source": "mock_history"}
        )
        return result

    # An empty sample (sample_count == 0) counts as a miss, just like an
    # unreachable sidecar.
    return with_resilience(
        _primary,
        _fallback,
        tool="get_connection_delay_history",
        accept=lambda r: r.get("sample_count", 0) > 0,
    ).value


def get_connection_delay_history(origin: str, destination: str, train: str = "") -> dict:
    """Returns delay metrics for a connection from past data.

    The data basis for the upfront risk assessment: how punctually have the
    trains on this connection arrived in the past? First tries real DB data via
    the db_service sidecar (arrival board at the destination); if the sidecar is
    unreachable or returns no sample, a simulated history is used. The ``source``
    field makes transparent where the numbers come from.

    Args:
        origin: Departure station, e.g. "Munich Hbf".
        destination: Destination station, e.g. "Berlin Hbf".
        train: Optional train name (e.g. "ICE 1006"), context only.

    Returns:
        A dict with ``sample_count``, mean/median/p90 delay, punctuality rate,
        cancellations, most common causes, and ``source`` ("db_service_live" |
        "mock_history"). Contains "error" if neither live nor mock data is
        available for the connection.
    """
    return _connection_delay_history(origin, destination, train)


def get_planned_connection(origin: str, destination: str, departure: str = "") -> dict:
    """Returns the planned connection (scheduled times) as the anchor for the ETA.

    The risk module needs the scheduled arrival time to derive the expected
    arrival (ETA = scheduled arrival + expected delay). Tries real DB data via
    the db_service sidecar; otherwise falls back to simulated scheduled times.

    Args:
        origin: Departure station, e.g. "Munich Hbf".
        destination: Destination station, e.g. "Berlin Hbf".
        departure: Optional departure time (ISO "YYYY-MM-DDTHH:MM:SS"); empty =
            next connection.

    Returns:
        A dict with ``train``, ``planned_departure``, ``planned_arrival``,
        ``transfers``, any real-time arrival delay, and ``source``. Contains
        "error" if no connection was found.
    """
    def _primary() -> dict | None:
        conn = risk_model.scheduled_connection(origin, destination, departure or None)
        if conn:
            conn["source"] = "db_service_live"
        return conn  # None (no journey found) is rejected -> fall back

    def _fallback() -> dict:
        mock = mock_data.PLANNED_CONNECTIONS.get((origin, destination))
        if mock is None:
            return {
                "origin": origin,
                "destination": destination,
                "error": "No planned connection found for this route.",
            }
        result = dict(mock)
        result.update({"origin": origin, "destination": destination, "source": "mock_planned"})
        return result

    return with_resilience(_primary, _fallback, tool="get_planned_connection").value
