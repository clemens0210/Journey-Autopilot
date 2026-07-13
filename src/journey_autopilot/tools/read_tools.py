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

        return (store.any_profile() or {}).get("connections", {}) or {}
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
# Workaround: the tool stashes its result here while it runs (same process),
# and ``ui.chat.chat_turn`` reads it after the run. This is safe for the
# single-user prototype (the chat UI's ``busy`` guard prevents concurrent
# turns). ``chat_turn`` clears the slot at the start of each turn so stale
# options from a previous turn are never shown.
_LAST_REROUTE: dict | None = None


def last_reroute_options() -> dict | None:
    """Returns the accumulated option list for this turn, or ``None``.

    Shape: ``{"origin", "destination", "options", "source"}`` where ``options``
    may contain train, car-sharing, bike-sharing, and hotel entries from multiple
    tool calls within the same Planner turn.
    """
    return _LAST_REROUTE


def clear_reroute_options() -> None:
    """Reset the in-process slot — called at the start of each chat turn."""
    global _LAST_REROUTE
    _LAST_REROUTE = None


def _stash_options(
    options: list[dict],
    *,
    origin: str = "",
    destination: str = "",
    source: str = "",
) -> list[dict]:
    """Append new options to the in-process slot; return the list unchanged.

    Options whose ``option_id`` already exists in the slot are skipped so
    repeated calls (train first, then ecosystem) merge without duplicates.
    """
    global _LAST_REROUTE
    if _LAST_REROUTE is None:
        _LAST_REROUTE = {
            "origin": origin,
            "destination": destination,
            "options": [],
            "source": source,
        }
    seen_ids = {o.get("option_id") for o in _LAST_REROUTE["options"]}
    for opt in options:
        oid = opt.get("option_id")
        if oid not in seen_ids:
            _LAST_REROUTE["options"].append(opt)
            seen_ids.add(oid)
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
                risk_level = _overall_level(forecasts)

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
                    "incidents": incidents,
                    "connection_risk": " ".join(connection_warnings) or (
                        "Arrival delay may affect onward plans."
                        if delay_int >= 15
                        else "No elevated connection risk visible from DB live data."
                    ),
                    "risk_level": risk_level,
                    "forecasts": forecasts,
                    "legs": risk_legs,
                    "planned_departure": option.get("planned_departure") or trip.get("planned_departure"),
                    "planned_arrival": option.get("planned_arrival") or trip.get("planned_arrival"),
                    "estimated_arrival": option.get("arrival") or option.get("planned_arrival"),
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
        delay = result.get("current_delay_minutes") or 0
        result.setdefault(
            "risk_level", "HIGH" if delay >= 30 else "MEDIUM" if delay >= 10 else "LOW"
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


def find_reroute_options(
    origin: str,
    destination: str,
    departure: str = "",
    original_arrival: str = "",
    max_results: int = 8,
) -> dict:
    """Finds alternative connections (reroute options) between two stations.

    Args:
        origin: Departure station, e.g. "Munich Hbf".
        destination: Destination station, e.g. "Berlin Hbf".
        departure: Optional planned departure time (ISO "YYYY-MM-DDTHH:MM:SS").
        original_arrival: Optional original planned arrival time, used to
            compute added delay.
        max_results: Maximum number of live options to return.

    Returns:
        A dict with the list of possible reroutes including new arrival time,
        number of transfers, added delay, price if available, per-leg ``legs``
        (train, change stations, departure/arrival times — cite the change
        stations and transfer times when presenting an option), and ``source``.
    """
    try:
        from_eva = stations.resolve_eva(origin)
        to_eva = stations.resolve_eva(destination)
        if from_eva is None or to_eva is None:
            raise db_api.DBServiceError(
                f"Station not resolvable (origin={origin!r}, destination={destination!r})."
            )
        payload = db_api.journeys(
            from_eva,
            to_eva,
            departure=departure or None,
            results=max_results,
            tickets=True,
        )
        live_options = []
        for option in db_api.normalize_journeys(payload)[:max_results]:
            arrival = option.get("arrival") or option.get("planned_arrival")
            added_delay = _minutes_between(original_arrival, arrival)
            if added_delay is None:
                added_delay = option.get("arrival_delay_minutes") or 0
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
                }
                for leg in option.get("legs") or []
                if leg.get("train")
            ]
            live_options.append(
                {
                    "option_id": option.get("option_id"),
                    "mode": "train",
                    "description": option.get("description"),
                    "departure": option.get("departure") or option.get("planned_departure"),
                    "new_arrival": arrival,
                    "transfers": option.get("transfers", 0),
                    "added_delay_minutes": round(added_delay),
                    "comfort": "; ".join(comfort_parts) or "Live DB connection",
                    "price_eur": option.get("price_eur"),
                    "trains": option.get("trains", []),
                    "legs": legs,
                    "remarks": option.get("remarks", []),
                    "source": "db_service_live",
                }
            )
        if live_options:
            _stash_options(live_options, origin=origin, destination=destination, source="db_service_live")
            return {
                "origin": origin,
                "destination": destination,
                "options": live_options,
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
        mock_options = [{**option, "mode": option.get("mode", "train"), "source": "mock_reroute_options"} for option in options]
        _stash_options(mock_options, origin=origin, destination=destination, source="mock_reroute_options")
        return {
            "origin": origin,
            "destination": destination,
            "options": mock_options,
            "source": "mock_reroute_options",
        }
    return {
        "origin": origin,
        "destination": destination,
        "options": [],
        "source": "none",
        "error": "No reroute options available for this route.",
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
    flinkster = [
        {**o, "source": "mock_flinkster"}
        for o in mock_data.lookup_location(mock_data.FLINKSTER_OPTIONS, location)[:max_results]
    ]
    callabike = [
        {**o, "source": "mock_callabike"}
        for o in mock_data.lookup_location(mock_data.CALLABIKE_OPTIONS, location)[:max_results]
    ]
    all_options = flinkster + callabike
    if all_options:
        _stash_options(all_options, origin=location, destination=destination, source="mock_mobility")
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
        location: Station or city near which to search (typically the destination
            or the stranded intermediate stop), e.g. "Berlin Hbf".
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
    hotels = [{**h, "check_in_date": check_in_date} for h in outcome.value]
    source = hotels[0]["source"] if hotels else "none"
    if hotels:
        _stash_options(hotels, origin=location, destination=location, source=source)
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
    cache_key = (date, user_email)
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
_CALENDAR_CACHE: dict[tuple[str, str | None], tuple[float, dict]] = {}
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
        return {
            "calendar_connected": False,
            "checked": False,
            "note": (
                "No calendar is connected — appointment deadlines cannot be "
                "checked. Treat options as free of calendar conflicts."
            ),
        }

    stashed = last_reroute_options()
    options = (stashed or {}).get("options") or []
    if not options:
        return {
            "date": date,
            "error": (
                "No reroute options were gathered this turn — call "
                "find_reroute_options first, then this check."
            ),
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
    return result


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

        profile = store.any_profile()
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
# Workaround (same pattern as find_reroute_options' _LAST_REROUTE stash on
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
        Dict with calculated compensation claim and legal context.
    """
    # 1. Deterministic calculation — no LLM, no network
    compensation = calculate_compensation(
        delay_minutes=delay_minutes,
        ticket_type=ticket_type,
        price_paid=price_paid,
        travel_class=travel_class,
        bahncard_type=bahncard_type,
    )

    # 2. RAG context for the agent — semantically matching chunks
    try:
        rag = _get_or_build_rag()
        chunks = rag.retrieve_for_case(
            delay_minutes=delay_minutes,
            ticket_type=ticket_type,
            bahncard_type=bahncard_type,
        )
        legal_context = "\n\n--- Next Section ---\n".join(chunks)
    except Exception:
        legal_context = "Knowledge base temporarily unavailable."

    result = {
        "delay_minutes": delay_minutes,
        **compensation,
        "legal_context": legal_context,
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
