"""Microsoft Graph API client using the official msgraph-sdk.

Queries /me/calendarView (or /users/{email}/calendarView) — the proper
endpoint for date-range calendar queries that expands recurring events.
"""

from __future__ import annotations

from azure.core.credentials import AccessToken, TokenCredential
from msgraph import GraphServiceClient
from msgraph.generated.users.item.calendar_view.calendar_view_request_builder import (
    CalendarViewRequestBuilder,
)
from kiota_abstractions.base_request_configuration import RequestConfiguration
from kiota_abstractions.headers_collection import HeadersCollection

from .auth import SCOPES, acquire_credential


class StaticTokenCredential:
    """Minimal ``TokenCredential`` returning a fixed ``AccessToken``.

    The onboarding web device-code flow acquires a token in a background
    thread, then the /status handler calls Graph on the event loop. Letting
    the msgraph-sdk call ``get_token()`` on the original
    ``DeviceCodeCredential`` again triggers an MSAL silent-auth lookup that
    fails right after a device flow — kicking off an unwanted second
    interactive device flow (printed to stderr, never answered, hangs the
    request).

    Injecting the freshly-acquired token via this static credential sidesteps
    MSAL entirely: the SDK gets the bearer token it needs without touching
    the cache. The token is short-lived (~1h) so this is strictly for the
    immediate post-connect preview; the agent's later ``acquire_credential()``
    path keeps the real credential + persistent cache.
    """

    def __init__(self, access_token: AccessToken) -> None:
        self._access_token = access_token

    def get_token(self, *scopes: str, **kwargs) -> AccessToken:
        return self._access_token


def _build_client(credential: TokenCredential) -> GraphServiceClient:
    """Build a GraphServiceClient backed by any TokenCredential."""
    return GraphServiceClient(credential, scopes=SCOPES)


async def _fetch_calendar_view(
    client: GraphServiceClient,
    start_dt: str,
    end_dt: str,
    user_email: str | None = None,
) -> list:
    """Single Graph calendarView call with start/end datetimes."""
    query_params = CalendarViewRequestBuilder.CalendarViewRequestBuilderGetQueryParameters(
        start_date_time=start_dt,
        end_date_time=end_dt,
        top=50,
    )
    headers = HeadersCollection()
    headers.try_add("Prefer", 'outlook.timezone="Europe/Berlin"')
    config = RequestConfiguration(
        query_parameters=query_params,
        headers=headers,
    )

    if user_email:
        result = await client.users.by_user_id(user_email).calendar_view.get(
            request_configuration=config,
        )
    else:
        result = await client.me.calendar_view.get(
            request_configuration=config,
        )

    return result.value if result and result.value else []


async def get_events(
    date: str,
    user_email: str | None = None,
    credential: TokenCredential | None = None,
) -> list:
    """Fetch calendar events for a specific date via Microsoft Graph.

    Uses the msgraph-sdk to call /me/calendarView (or
    /users/{email}/calendarView) with startDateTime/endDateTime query
    parameters — the correct endpoint for date-range calendar queries.

    Auth happens lazily — the device-code flow triggers only when the
    SDK first calls get_token() and the persistent cache has no valid token.

    Args:
        date: Date string in ISO format, e.g. "2026-06-03".
        user_email: If provided, query that user's calendar (requires
            appropriate delegated permissions). If None, queries the
            authenticated user.
        credential: Optional ``TokenCredential`` to reuse. If None, a
            fresh one is built via ``acquire_credential()``. Reusing the
            credential that just completed the web device flow avoids a
            second interactive flow when the persistent cache hasn't flushed
            the new token yet.

    Returns:
        A list of msgraph Event model objects. Returns [] on error.
    """
    credential = credential or acquire_credential()
    client = _build_client(credential)

    return await _fetch_calendar_view(
        client, f"{date}T00:00:00", f"{date}T23:59:59", user_email
    )


async def get_events_range(
    start_date: str,
    end_date: str,
    user_email: str | None = None,
    credential: TokenCredential | None = None,
) -> list:
    """Fetch calendar events for a date range (inclusive) via Microsoft Graph.

    A single calendarView call spanning ``start_date`` to ``end_date`` — more
    efficient than one call per day. Used by the onboarding "Connect Outlook"
    step to show a multi-day preview after a successful login.

    Args:
        start_date: ISO date string, e.g. "2026-06-19".
        end_date: ISO date string, e.g. "2026-07-01".
        user_email: Optional email of another user whose calendar to query.
        credential: Optional ``TokenCredential`` to reuse — typically a
            :class:`StaticTokenCredential` wrapping the ``AccessToken``
            just produced by the web device-code flow. See
            :func:`get_events` for why this matters.

    Returns:
        A list of msgraph Event model objects (may contain duplicates from
        recurring series — the mapper handles them).
    """
    credential = credential or acquire_credential()
    client = _build_client(credential)

    return await _fetch_calendar_view(
        client, f"{start_date}T00:00:00", f"{end_date}T23:59:59", user_email
    )
