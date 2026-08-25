"""Outlook Calendar integration for Journey Autopilot.

Public API:

    get_calendar_events(date, user_email=None, credential=None) -> list[dict]
    get_calendar_events_range(start_date, end_date, user_email=None, credential=None) -> list[dict]
    get_signed_in_user(credential=None) -> dict   # {"email", "name"} of the connected MS account
    send_notice_email(to_address, subject, body, credential=None) -> None  # Mail.Send scope
    reschedule_calendar_event(event_id, start=None, end=None, user_email=None, credential=None) -> None  # Calendars.ReadWrite scope
    is_outlook_configured() -> bool
    create_device_credential(prompt_callback, timeout=900) -> DeviceCodeCredential
    StaticTokenCredential(access_token) -> TokenCredential
    clear_token_cache() -> bool

Returns calendar events in the internal format expected by the Planner Agent.
Handles authentication (device-code flow via azure.identity with persistent
token caching) and data mapping internally. Uses the official msgraph-sdk.

The *interactive* half — connecting a human account from the onboarding wizard
— lives in :mod:`.device_flow` (``start`` / ``poll`` / ``forget``). It is
imported on demand rather than re-exported here, because the web layer is its
only caller and everything else in this package works with an already-connected
account.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .auth import (
    clear_token_cache,
    create_device_credential,
    is_outlook_configured,
)
from .client import (
    StaticTokenCredential,
    get_events,
    get_events_range,
    get_signed_in_user,
    send_mail,
    update_event,
)
from .mapper import graph_events_to_internal

if TYPE_CHECKING:
    from azure.core.credentials import TokenCredential


async def get_calendar_events(
    date: str,
    user_email: str | None = None,
    credential: TokenCredential | None = None,
) -> list[dict]:
    """Fetch and map Outlook calendar events for a given date.

    Orchestrates the full pipeline: authenticate → query Graph → map to
    internal event format.

    Args:
        date: ISO date string, e.g. "2026-06-03".
        user_email: Optional email of another user whose calendar to query.
            Requires appropriate Graph permissions. Defaults to the
            authenticated user's own calendar.
        credential: Optional ``TokenCredential`` to reuse instead of
            building a new one. The onboarding web flow passes a
            :class:`StaticTokenCredential` wrapping the ``AccessToken`` that
            the just-completed device-code login returned, so the preview
            fetch doesn't trigger a second interactive MSAL flow.

    """
    raw_events = await get_events(date, user_email, credential=credential)
    return graph_events_to_internal(raw_events)


async def get_calendar_events_range(
    start_date: str,
    end_date: str,
    user_email: str | None = None,
    credential: TokenCredential | None = None,
) -> list[dict]:
    """Fetch and map Outlook calendar events for a date range (inclusive).

    Used by the onboarding "Connect Outlook" step to show a multi-day preview
    after a successful device-code login.

    Args:
        start_date: ISO date string, e.g. "2026-06-19".
        end_date: ISO date string, e.g. "2026-07-01".
        user_email: Optional email of another user whose calendar to query.
        credential: Optional ``TokenCredential`` to reuse — typically a
            :class:`StaticTokenCredential` wrapping the ``AccessToken`` just
            produced by the web device-code flow. See
            :func:`get_calendar_events` for why this matters.
    """
    raw_events = await get_events_range(start_date, end_date, user_email, credential=credential)
    return graph_events_to_internal(raw_events)


async def send_notice_email(
    to_address: str,
    subject: str,
    body: str,
    credential: TokenCredential | None = None,
) -> None:
    """Send a plain-text notice email from the connected Microsoft account.

    Thin wrapper over :func:`client.send_mail`. Requires the ``Mail.Send``
    scope — logins made before that scope was introduced must reconnect
    Outlook once (the interactive flows request ``MAIL_SCOPES``).

    Args:
        to_address: Recipient email address (e.g. the organizer of a
            clashing calendar appointment).
        subject: Mail subject line.
        body: Plain-text mail body.
        credential: Optional ``TokenCredential`` to reuse.
    """
    await send_mail(to_address, subject, body, credential=credential)


async def reschedule_calendar_event(
    event_id: str,
    start: str | None = None,
    end: str | None = None,
    user_email: str | None = None,
    credential: TokenCredential | None = None,
) -> None:
    """Move a calendar appointment on the connected Microsoft account.

    Thin wrapper over :func:`client.update_event`. Requires the
    ``Calendars.ReadWrite`` scope — logins consented before rescheduling
    existed (calendar-read-only, or calendar-read + Mail.Send) must reconnect
    Outlook once (the interactive flow requests ``CALENDAR_WRITE_SCOPES``).

    Args:
        event_id: Graph id of the appointment to move.
        start: New start as a naive local datetime string
            ("YYYY-MM-DDTHH:MM:SS"). Omitted leaves the start unchanged.
        end: New end, same format. Omitted leaves the end unchanged.
        user_email: Optional email of another user's calendar to update.
        credential: Optional ``TokenCredential`` to reuse.
    """
    await update_event(event_id, start, end, user_email=user_email, credential=credential)
