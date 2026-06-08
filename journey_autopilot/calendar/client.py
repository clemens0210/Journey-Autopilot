"""Microsoft Graph API client for Outlook calendar operations.

Provides a lightweight wrapper around the Graph /v1.0/me/calendarView and
/v1.0/users/{email}/calendarView endpoints using httpx.
"""

from __future__ import annotations

from msgraph import GraphServiceClient
import httpx

GRAPH_BASE = "https://graph.microsoft.com/v1.0"


def _build_headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Prefer": 'outlook.timezone="Europe/Berlin"',
    }


def _paginate(client: httpx.Client, url: str, headers: dict[str, str]) -> list[dict]:
    """Follow @odata.nextLink to collect all events across pages."""
    events: list[dict] = []
    while url:
        resp = client.get(url, headers=headers)
        resp.raise_for_status()
        body = resp.json()
        events.extend(body.get("value", []))
        url = body.get("@odata.nextLink", "")
    return events


def get_calendar_view(
    token: str,
    date: str,
    user_email: str | None = None,
) -> list[dict]:
    """Fetch calendar events for a specific date from Microsoft Graph.

    Args:
        token: A valid OAuth 2.0 access token.
        date: Date string in ISO format, e.g. "2026-06-03".
        user_email: If provided, query that user's calendar (requires
            appropriate permissions). If None, queries the authenticated user.

    Returns:
        A list of Graph event dicts. Returns [] if no events or on error.
    """
    start = f"{date}T00:00:00Z"
    end = f"{date}T23:59:59Z"

    if user_email:
        url = (
            f"{GRAPH_BASE}/users/{user_email}/calendarView"
            f"?startDateTime={start}&endDateTime={end}&$top=50"
        )
    else:
        url = (
            f"{GRAPH_BASE}/me/calendarView"
            f"?startDateTime={start}&endDateTime={end}&$top=50"
        )

    headers = _build_headers(token)
    with httpx.Client(timeout=httpx.Timeout(30.0)) as client:
        if resp := _try_request(client, url, headers):
            return _paginate(client, url, headers)
        return []


def reauth_needed(status_code: int) -> bool:
    """Return True if the HTTP status indicates a need for re-authentication."""
    return status_code == 401


def _try_request(
    client: httpx.Client,
    url: str,
    headers: dict[str, str],
) -> bool:
    """Probe the endpoint to check connectivity and token validity.

    Returns True if the endpoint is reachable with the current token.
    """
    try:
        resp = client.get(url, headers=headers)
        return resp.status_code != 401
    except httpx.RequestError:
        return False
