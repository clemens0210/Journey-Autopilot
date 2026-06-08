"""Mapping from Microsoft Graph SDK Event models to the internal calendar schema.

The internal schema is what mock_data.USER_CALENDAR already produces and
what the Planner Agent expects:

    {
        "title": str,
        "location": str,
        "start": str,         # ISO datetime, timezone stripped
        "hard_constraint": bool,
    }
"""

from __future__ import annotations

from msgraph.generated.models.event import Event

DEFAULT_LOCATION = "Kein Ort"
HARD_CONSTRAINT_CATEGORY = "Journey-Autopilot/Hard"


def graph_events_to_internal(graph_events: list[Event]) -> list[dict]:
    """Convert a list of msgraph Event model objects to the internal format.

    Args:
        graph_events: Event model objects from the Graph SDK (e.g. from
            client.me.calendar.events.get().value).

    Returns:
        A list of dicts with keys: title, location, start, hard_constraint.
    """
    result: list[dict] = []
    for event in graph_events:
        title = event.subject or "Kein Titel"

        location = DEFAULT_LOCATION
        if event.location and event.location.display_name:
            location = event.location.display_name

        start = ""
        if event.start and event.start.date_time:
            dt = event.start.date_time
            start = dt[:16] if len(dt) >= 16 else dt

        categories: list[str] = event.categories or []
        is_hard = HARD_CONSTRAINT_CATEGORY in categories

        result.append(
            {
                "title": title,
                "location": location,
                "start": start,
                "hard_constraint": is_hard,
            }
        )
    return result
