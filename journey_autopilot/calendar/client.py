"""Microsoft Graph API client using the official msgraph-sdk.

Queries /me/calendarView (or /users/{email}/calendarView) — the proper
endpoint for date-range calendar queries that expands recurring events.
"""

from __future__ import annotations

from azure.identity import DeviceCodeCredential
from msgraph import GraphServiceClient
from msgraph.generated.users.item.calendar_view.calendar_view_request_builder import (
    CalendarViewRequestBuilder,
)
from kiota_abstractions.base_request_configuration import RequestConfiguration
from kiota_abstractions.headers_collection import HeadersCollection

from .auth import SCOPES, acquire_credential


def _build_client(credential: DeviceCodeCredential) -> GraphServiceClient:
    """Build a GraphServiceClient backed by a DeviceCodeCredential."""
    return GraphServiceClient(credential, scopes=SCOPES)


async def get_events(date: str, user_email: str | None = None) -> list:
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

    Returns:
        A list of msgraph Event model objects. Returns [] on error.
    """
    credential = acquire_credential()
    client = _build_client(credential)

    start_dt = f"{date}T00:00:00"
    end_dt = f"{date}T23:59:59"

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
