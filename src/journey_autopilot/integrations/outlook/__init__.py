"""Outlook Calendar integration for Journey Autopilot.

Public API:

    get_calendar_events(date, user_email=None, credential=None) -> list[dict]
    get_calendar_events_range(start_date, end_date, user_email=None, credential=None) -> list[dict]
    is_outlook_configured() -> bool
    create_device_credential(prompt_callback, timeout=900) -> DeviceCodeCredential
    StaticTokenCredential(access_token) -> TokenCredential
    clear_token_cache() -> bool

Returns calendar events in the internal format expected by the Planner Agent.
Handles authentication (device-code flow via azure.identity with persistent
token caching) and data mapping internally. Uses the official msgraph-sdk.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .auth import (
    SCOPES,
    acquire_credential,
    clear_token_cache,
    create_device_credential,
    is_outlook_configured,
)
from .client import StaticTokenCredential, get_events, get_events_range
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
