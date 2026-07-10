"""Mapping from Microsoft Graph SDK Event models to the internal calendar schema.

The internal schema is what mock_data.USER_CALENDAR already produces and
what the Planner Agent expects:

    {
        "title": str,
        "location": str,
        "start": str,             # ISO datetime, timezone stripped
        "hard_constraint": bool,
        "organizer_name": str | None,
        "organizer_email": str | None,
        "attendee_emails": list[str],
        "self_organized": bool,
    }

The contact fields let the Communicator notify a meeting counterpart when a
trip disruption endangers the appointment. ``attendee_emails`` excludes the
organizer (Graph lists the organizer among the attendees as well).

``self_organized`` (Graph ``isOrganizer``) matters for consumer accounts:
for events the signed-in user created, Graph reports an internal alias
(``outlook_<hex>@outlook.com``) as the organizer address — NOT a routable
inbox. ``read_tools.get_user_calendar`` uses the flag to substitute the
connected account's real email.
"""

from __future__ import annotations

from msgraph.generated.models.event import Event

DEFAULT_LOCATION = "No location"
HARD_CONSTRAINT_CATEGORY = "Journey-Autopilot/Hard"


def graph_events_to_internal(graph_events: list[Event]) -> list[dict]:
    """Convert a list of msgraph Event model objects to the internal format.

    Args:
        graph_events: Event model objects from the Graph SDK (e.g. from
            client.me.calendar.events.get().value).

    Returns:
        A list of dicts with keys: title, location, start, hard_constraint,
        organizer_name, organizer_email, attendee_emails.
    """
    result: list[dict] = []
    for event in graph_events:
        title = event.subject or "No title"

        location = DEFAULT_LOCATION
        if event.location and event.location.display_name:
            location = event.location.display_name

        start = ""
        if event.start and event.start.date_time:
            dt = event.start.date_time
            start = dt[:16] if len(dt) >= 16 else dt

        categories: list[str] = event.categories or []
        is_hard = HARD_CONSTRAINT_CATEGORY in categories

        organizer_name: str | None = None
        organizer_email: str | None = None
        if event.organizer and event.organizer.email_address:
            organizer_name = event.organizer.email_address.name
            organizer_email = event.organizer.email_address.address

        attendee_emails: list[str] = []
        for attendee in event.attendees or []:
            address = (
                attendee.email_address.address if attendee.email_address else None
            )
            if address and address != organizer_email:
                attendee_emails.append(address)

        result.append(
            {
                "title": title,
                "location": location,
                "start": start,
                "hard_constraint": is_hard,
                "organizer_name": organizer_name,
                "organizer_email": organizer_email,
                "attendee_emails": attendee_emails,
                "self_organized": bool(event.is_organizer),
            }
        )
    return result
