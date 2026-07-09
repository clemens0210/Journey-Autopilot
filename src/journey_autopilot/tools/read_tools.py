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

import os
import threading
from datetime import datetime, timezone
from typing import Any

from . import risk_model

from .. import mock_data
from ..errors import with_resilience, with_resilience_async
from ..integrations.rights_rag.rights_service import calculate_compensation
from ..integrations import db_ops as db_api
from ..integrations import stations


_STATION_ALIASES: dict[str, str] = {
    "münchen hbf": "Munich Hbf",
    "münchen hauptbahnhof": "Munich Hbf",
    "munich hauptbahnhof": "Munich Hbf",
    "berlin hauptbahnhof": "Berlin Hbf",
    "berlin hbf": "Berlin Hbf",
    "frankfurt hbf": "Frankfurt Hbf",
    "frankfurt(main)hbf": "Frankfurt Hbf",
}


def _normalize_station(name: str) -> str:
    return _STATION_ALIASES.get(name.strip().lower(), name)


def _calendar_configured() -> bool:
    """Return True if MS Entra credentials are present in the environment."""
    return bool(os.getenv("MS_ENTRA_CLIENT_ID"))


def _outlook_connected() -> bool:
    """Return True if the user connected Outlook during onboarding.

    Checks ``profile.connections.outlook`` in the onboarding store. This
    prevents the agent from triggering a blocking device-code flow when
    the user skipped Outlook — the token cache is only populated after a
    successful web-based device-code login.
    """
    try:
        from journey_autopilot.persistence import store

        profile = store.any_profile()
        return bool(profile and profile.get("connections", {}).get("outlook"))
    except Exception:
        return False


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
    options = db_api.normalize_journeys(payload)
    train = str(trip.get("train") or "")
    for option in options:
        if train and train in option.get("trains", []):
            return option
    return options[0] if options else None


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
    if not arrival_iso:
        return False
    try:
        arrival = datetime.fromisoformat(arrival_iso)
    except ValueError:
        return False
    now = datetime.now(arrival.tzinfo) if arrival.tzinfo else datetime.now()
    return arrival <= now


def get_live_trip_status(trip_id: str) -> dict:
    """Returns the current live status of a train journey.

    Args:
        trip_id: The trip ID, e.g. "DB-2026-0603-MUC-BLN".

    Returns:
        A dict with current delay, trend, position, known incidents,
        connection risk, and ``source``. Contains "error" only if neither live
        data nor the demo fallback knows the trip.
    """
    trip = _find_trip_context(trip_id)
    if trip is not None:
        try:
            option = _journey_for_trip(trip)
            if option:
                delay = option.get("arrival_delay_minutes")
                if delay is None:
                    delay = option.get("departure_delay_minutes") or 0
                delay_int = round(delay)
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
                    "connection_risk": (
                        "Arrival delay may affect onward plans."
                        if delay_int >= 15
                        else "No elevated connection risk visible from DB live data."
                    ),
                    "planned_departure": option.get("planned_departure") or trip.get("planned_departure"),
                    "planned_arrival": option.get("planned_arrival") or trip.get("planned_arrival"),
                    "estimated_arrival": option.get("arrival") or option.get("planned_arrival"),
                    "arrived": _arrival_in_past(
                        option.get("arrival") or option.get("planned_arrival")
                    ),
                    "platform_changes": option.get("platform_changes", []),
                    "data_timestamp": datetime.now(timezone.utc).isoformat(),
                    "source": "db_service_live",
                }
        except db_api.DBServiceError:
            pass
        except Exception:
            pass

    status = mock_data.LIVE_TRIP_STATUS.get(trip_id)
    if status is not None:
        return {**status, "source": "mock_live_status"}
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
    max_results: int = 5,
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
        number of transfers, added delay, price if available, and ``source``.
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
            live_options.append(
                {
                    "option_id": option.get("option_id"),
                    "description": option.get("description"),
                    "departure": option.get("departure") or option.get("planned_departure"),
                    "new_arrival": arrival,
                    "transfers": option.get("transfers", 0),
                    "added_delay_minutes": round(added_delay),
                    "comfort": "; ".join(comfort_parts) or "Live DB connection",
                    "price_eur": option.get("price_eur"),
                    "trains": option.get("trains", []),
                    "remarks": option.get("remarks", []),
                    "source": "db_service_live",
                }
            )
        if live_options:
            return {
                "origin": origin,
                "destination": destination,
                "options": live_options,
                "source": "db_service_live",
            }
    except db_api.DBServiceError:
        pass
    except Exception:
        pass

    origin_norm = _normalize_station(origin)
    destination_norm = _normalize_station(destination)
    options = mock_data.REROUTE_OPTIONS.get((origin_norm, destination_norm), [])
    if options:
        return {
            "origin": origin,
            "destination": destination,
            "options": [{**option, "source": "mock_reroute_options"} for option in options],
            "source": "mock_reroute_options",
        }
    return {
        "origin": origin,
        "destination": destination,
        "options": [],
        "source": "none",
        "error": "No reroute options available for this route.",
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

    async def _primary() -> dict:
        from ..integrations.outlook import get_calendar_events

        events = await get_calendar_events(date, user_email)
        return {"date": date, "events": events, "source": "outlook"}

    def _fallback() -> dict:
        return {"date": date, "events": mock_events, "source": "mock (Graph-Fallback)"}

    outcome = await with_resilience_async(_primary, _fallback, tool="get_user_calendar")
    result = outcome.value
    if outcome.failure is not None:  # Graph access failed -> surface why
        result["error"] = outcome.failure["fallback_taken"]
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
    """Returns the historical punctuality reference for a connection (monthly archive).

    The reliable baseline for the risk assessment: how punctually do trains of
    this type arrive at the destination station over SEVERAL MONTHS? The source
    is a real delay archive (piebro/deutsche-bahn-data, DB data, CC BY 4.0),
    pre-aggregated into metrics per station and train type. Complements
    ``get_connection_delay_history`` (only the last few hours, current situation):
    the archive provides the long-term normal case, the live history today's
    situation.

    Args:
        origin: Departure station (context only; the arrival at the destination is scored).
        destination: Destination station, e.g. "Berlin Hbf".
        train: Optional train name (e.g. "ICE 1006") — determines the train type.

    Returns:
        A dict with ``sample_count``, mean/median/p90 delay, punctuality rate,
        cancellation rate, the ``basis`` used (train type), the covered ``months``
        and ``source="db_history_archive"``. Contains "error" if the station is
        not in the archive.
    """
    ref = risk_model.historical_reference(destination, train=train)
    if ref is None:
        return {
            "origin": origin,
            "destination": destination,
            "error": "No historical reference available for this destination station.",
        }
    ref["origin"] = origin
    return ref


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
