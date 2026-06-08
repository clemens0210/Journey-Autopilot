"""Microsoft Graph API client using the official msgraph-sdk.

Provides a thin wrapper that queries /me/calendar/events filtered by date
and returns the raw Event model objects for the mapper to convert.
"""

from __future__ import annotations

from azure.identity import DeviceCodeCredential
from msgraph import GraphServiceClient
from msgraph.generated.users.item.calendar.events.events_request_builder import (
    EventsRequestBuilder,
)
from kiota_abstractions.base_request_configuration import RequestConfiguration

from .auth import SCOPES, acquire_credential


def _build_client(credential: DeviceCodeCredential) -> GraphServiceClient:
    """Build a GraphServiceClient backed by a DeviceCodeCredential."""
    return GraphServiceClient(credential, scopes=SCOPES)


async def get_events(date: str, user_email: str | None = None) -> list:
    """Fetch calendar events for a specific date via Microsoft Graph.

    Uses the msgraph-sdk to call /me/calendar/events (or
    /users/{email}/calendar/events) filtered to the given date.

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

    start_filter = f"{date}T00:00:00"
    end_filter = f"{date}T23:59:59"

    query_params = EventsRequestBuilder.EventsRequestBuilderGetQueryParameters(
        filter=(
            f"start/dateTime ge '{start_filter}' "
            f"and end/dateTime le '{end_filter}'"
        ),
        top=50,
    )
    config = RequestConfiguration(
        query_parameters=query_params,
        headers={"Prefer": 'outlook.timezone="Europe/Berlin"'},
    )

    try:
        if user_email:
            result = await client.users.by_user_id(user_email).calendar.events.get(
                request_configuration=config,
            )
        else:
            result = await client.me.calendar.events.get(
                request_configuration=config,
            )
        return result.value if result and result.value else []
    except Exception:
        return []
