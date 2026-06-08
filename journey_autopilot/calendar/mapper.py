"""Mapping from Microsoft Graph event format to the internal calendar schema.

The internal schema is defined by what mock_data.USER_CALENDAR already produces
and what the Planner Agent expects:

    {
        "title": str,
        "location": str,
        "start": str,         # ISO datetime, timezone stripped
        "hard_constraint": bool,
    }
"""

from __future__ import annotations

DEFAULT_LOCATION = "Kein Ort"

HARD_CONSTRAINT_CATEGORY = "Journey-Autopilot/Hard"


def graph_events_to_internal(graph_events: list[dict]) -> list[dict]:
    """Convert a list of Microsoft Graph event dicts to the internal format.

    Args:
        graph_events: Raw event dicts from the Graph API /calendarView endpoint.

    Returns:
        A list of dicts with keys: title, location, start, hard_constraint.
    """
    result: list[dict] = []
    for event in graph_events:
        title = event.get("subject", "Kein Titel")
        location = _extract_location(event)
        start = _extract_start(event)
        is_hard = _is_hard_constraint(event)

        result.append(
            {
                "title": title,
                "location": location,
                "start": start,
                "hard_constraint": is_hard,
            }
        )
    return result


def _extract_location(event: dict) -> str:
    location_obj = event.get("location", {}) or {}
    return location_obj.get("displayName", DEFAULT_LOCATION)


def _extract_start(event: dict) -> str:
    start_obj = event.get("start", {}) or {}
    dt = start_obj.get("dateTime", "")
    if dt and len(dt) >= 16:
        return dt[:16]
    return dt


def _is_hard_constraint(event: dict) -> bool:
    categories: list[str] = event.get("categories", []) or []
    return HARD_CONSTRAINT_CATEGORY in categories
