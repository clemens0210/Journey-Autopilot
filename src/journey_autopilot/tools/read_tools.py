"""Function tools for the agents.

In ADK, a typed Python function with a docstring is enough — the framework wraps
it automatically into a FunctionTool and derives the parameter schema from the
type hints + docstring. Therefore docstrings and types here are not decoration
but part of the API that the LLM sees.

All functions currently read from `mock_data` — they are the insertion points
for real DB/calendar/RAG sources.
"""

from __future__ import annotations

import os

from . import risk_model

from .. import mock_data
from ..errors import with_resilience, with_resilience_async
from ..integrations.rights_rag.rag_store import FahrgastrechteRAG
from ..integrations.rights_rag.rights_service import calculate_compensation
from ..integrations.outlook import get_calendar_events


def _calendar_configured() -> bool:
    """Return True if MS Entra credentials are present in the environment."""
    return bool(os.getenv("MS_ENTRA_CLIENT_ID"))


# --- Monitoring tools ---------------------------------------------------------


def get_live_trip_status(trip_id: str) -> dict:
    """Returns the current live status of a train journey.

    Args:
        trip_id: The trip ID, e.g. "DB-2026-0603-MUC-BLN".

    Returns:
        A dict with current delay, trend, position, known incidents,
        and connection risk. Contains "error" if the trip is unknown.
    """
    status = mock_data.LIVE_TRIP_STATUS.get(trip_id)
    if status is None:
        return {"error": f"No live data found for trip_id '{trip_id}'."}
    return status


def get_network_disruptions(region: str) -> dict:
    """Returns the current network-wide disruption status for a region.

    Args:
        region: Region in lowercase, e.g. "bavaria" or "berlin".

    Returns:
        A dict with the list of active disruptions for the region.
    """
    disruptions = mock_data.NETWORK_DISRUPTIONS.get(region.lower(), [])
    return {"region": region, "disruptions": disruptions}


# --- Planner tools ------------------------------------------------------------


def find_reroute_options(origin: str, destination: str) -> dict:
    """Finds alternative connections (reroute options) between two stations.

    Args:
        origin: Departure station, e.g. "Munich Hbf".
        destination: Destination station, e.g. "Berlin Hbf".

    Returns:
        A dict with the list of possible reroutes including new arrival time,
        number of transfers, and added delay.
    """
    options = mock_data.REROUTE_OPTIONS.get((origin, destination), [])
    return {"origin": origin, "destination": destination, "options": options}


async def get_user_calendar(date: str, user_email: str | None = None) -> dict:
    """Reads the user's calendar appointments for a given date.

    Needed to check hard deadlines (e.g. an on-site meeting) against
    reroute options.

    Uses Outlook/Microsoft Graph when Entra credentials are present in .env
    (MS_ENTRA_CLIENT_ID, MS_ENTRA_TENANT_ID). Without configuration,
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

    if not _calendar_configured():
        return {"date": date, "events": mock_events, "source": "mock"}

    async def _primary() -> dict:
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
        rag = getattr(get_passenger_rights, "_rag", None)
        if rag is None:
            rag = FahrgastrechteRAG()
            setattr(get_passenger_rights, "_rag", rag)
        chunks = rag.retrieve_for_case(
            delay_minutes=delay_minutes,
            ticket_type=ticket_type,
            bahncard_type=bahncard_type,
        )
        legal_context = "\n\n--- Next Section ---\n".join(chunks)
    except Exception:
        legal_context = "Knowledge base temporarily unavailable."

    return {
        **compensation,
        "legal_context": legal_context,
    }


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
    def _primary() -> dict:
        stats = risk_model.connection_delay_history(origin, destination, train=train)
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

    # Live sidecar, else simulated history. An empty sample (sample_count == 0)
    # counts as a miss, just like an unreachable sidecar.
    return with_resilience(
        _primary,
        _fallback,
        tool="get_connection_delay_history",
        accept=lambda r: r.get("sample_count", 0) > 0,
    ).value


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
