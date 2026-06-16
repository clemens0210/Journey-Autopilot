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

from . import mock_data
from .passenger_rights.rag_store import FahrgastrechteRAG
from .passenger_rights.rights_service import calculate_compensation
from .calendar import get_calendar_events


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

    if _calendar_configured():
        try:
            events = await get_calendar_events(date, user_email)
            return {"date": date, "events": events, "source": "outlook"}
        except Exception as exc:
            return {
                "date": date,
                "events": mock_events,
                "source": "mock (Graph-Fallback)",
                "error": str(exc),
            }

    return {"date": date, "events": mock_events, "source": "mock"}


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
        rag = FahrgastrechteRAG()
        chunks = rag.retrieve_for_case(
            delay_minutes=delay_minutes,
            ticket_type=ticket_type,
            bahncard_type=bahncard_type,
        )
        legal_context = "\n\n--- Next Section ---\n".join(chunks)
    except Exception as e:
        legal_context = f"Knowledge base temporarily unavailable: {e}"

    return {
        **compensation,
        "legal_context": legal_context,
    }
