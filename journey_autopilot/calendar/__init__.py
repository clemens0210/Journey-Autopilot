"""Outlook Calendar integration for Journey Autopilot.

Public API:

    get_calendar_events(date, user_email=None) -> list[dict]
    get_calendar_events_range(start_date, end_date, user_email=None) -> list[dict]
    is_outlook_configured() -> bool
    create_device_credential(prompt_callback, timeout=900) -> DeviceCodeCredential
    clear_token_cache() -> bool

Returns calendar events in the internal format expected by the Planner Agent.
Handles authentication (device-code flow via azure.identity with persistent
token caching) and data mapping internally. Uses the official msgraph-sdk.
"""

from __future__ import annotations

from .auth import (
    SCOPES,
    acquire_credential,
    clear_token_cache,
    create_device_credential,
    is_outlook_configured,
)
from .client import get_events, get_events_range
from .mapper import graph_events_to_internal


async def get_calendar_events(date: str, user_email: str | None = None) -> list[dict]:
    """Fetch and map Outlook calendar events for a given date.

    Orchestrates the full pipeline: authenticate → query Graph → map to
    internal event format.

    Args:
        date: ISO date string, e.g. "2026-06-03".
        user_email: Optional email of another user whose calendar to query.
            Requires appropriate Graph permissions. Defaults to the
            authenticated user's own calendar.

    """
    raw_events = await get_events(date, user_email)
    return graph_events_to_internal(raw_events)


async def get_calendar_events_range(
    start_date: str, end_date: str, user_email: str | None = None
) -> list[dict]:
    """Fetch and map Outlook calendar events for a date range (inclusive).

    Used by the onboarding "Connect Outlook" step to show a multi-day preview
    after a successful device-code login.

    Args:
        start_date: ISO date string, e.g. "2026-06-19".
        end_date: ISO date string, e.g. "2026-07-01".
        user_email: Optional email of another user whose calendar to query.
    """
    raw_events = await get_events_range(start_date, end_date, user_email)
    return graph_events_to_internal(raw_events)
