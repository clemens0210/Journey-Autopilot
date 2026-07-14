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

import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from msgraph.generated.models.event import Event

logger = logging.getLogger(__name__)

DEFAULT_LOCATION = "No location"
HARD_CONSTRAINT_CATEGORY = "Journey-Autopilot/Hard"

# The whole app speaks naive Europe/Berlin wall time (DB times, mock calendar,
# the conflict checks in read_tools). Graph, however, returns event times in
# whatever timezone applies to the request — UTC by default, or the timezone
# from the ``Prefer: outlook.timezone`` header when it is honored. Trusting the
# header alone shifted events by the UTC offset (2h in summer) whenever it was
# dropped, so the conversion is done explicitly here from the ``time_zone``
# Graph reports alongside each timestamp.
_APP_TZ = ZoneInfo("Europe/Berlin")

# Windows timezone ids Graph commonly reports -> IANA names zoneinfo knows.
_WINDOWS_TZ = {
    "utc": "UTC",
    "w. europe standard time": "Europe/Berlin",
    "central europe standard time": "Europe/Berlin",
    "central european standard time": "Europe/Berlin",
    "romance standard time": "Europe/Paris",
    "gmt standard time": "Europe/London",
}


def _to_app_wall_time(date_time: str, time_zone: str | None) -> str:
    """Convert a Graph timestamp to naive Europe/Berlin, minute precision.

    ``date_time`` is Graph's naive string (e.g. "2026-07-14T12:00:00.0000000"),
    ``time_zone`` the timezone it is expressed in ("UTC", "Europe/Berlin",
    "W. Europe Standard Time", ...). Unknown zones are assumed to already be
    app-local — better a correct no-op than a wrong double shift.
    """
    raw = (date_time or "")[:19]
    tz_name = (time_zone or "").strip()
    key = _WINDOWS_TZ.get(tz_name.lower(), tz_name)
    try:
        source = ZoneInfo(key) if key else _APP_TZ
    except Exception:
        logger.warning("unknown Graph timezone %r — assuming Europe/Berlin", time_zone)
        source = _APP_TZ
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        return raw[:16]
    local = dt.replace(tzinfo=source).astimezone(_APP_TZ)
    return local.strftime("%Y-%m-%dT%H:%M")


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
            start = _to_app_wall_time(event.start.date_time, event.start.time_zone)

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
