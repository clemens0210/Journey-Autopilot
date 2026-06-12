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


def get_passenger_rights(delay_minutes: int) -> dict:
    """Determines the passenger rights/compensation tier for a delay.

    Args:
        delay_minutes: Expected arrival delay in minutes.

    Returns:
        A dict with the applicable compensation (or a note that
        below the threshold there is no entitlement).
    """
    applicable = [
        rule
        for rule in mock_data.PASSENGER_RIGHTS
        if delay_minutes >= rule["min_delay_minutes"]
    ]
    if not applicable:
        return {
            "delay_minutes": delay_minutes,
            "compensation": "Under 60 minutes — no compensation entitlement.",
        }
    best = max(applicable, key=lambda rule: rule["min_delay_minutes"])
    return {"delay_minutes": delay_minutes, "compensation": best["compensation"]}
