"""Microsoft Graph API client using the official msgraph-sdk.

Queries /me/calendarView (or /users/{email}/calendarView) — the proper
endpoint for date-range calendar queries that expands recurring events.
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from azure.core.credentials import AccessToken, TokenCredential
from msgraph import GraphRequestAdapter, GraphServiceClient
from msgraph.generated.users.item.calendar_view.calendar_view_request_builder import (
    CalendarViewRequestBuilder,
)
from kiota_abstractions.base_request_configuration import RequestConfiguration
from kiota_abstractions.headers_collection import HeadersCollection
from kiota_authentication_azure.azure_identity_authentication_provider import (
    AzureIdentityAuthenticationProvider,
)

from .auth import CALENDAR_WRITE_SCOPES, MAIL_SCOPES, SCOPES, acquire_credential

# Calendar windows are meant in German local time (the app's wall clock).
# Graph interprets naive startDateTime/endDateTime values as UTC, which shifts
# the queried day by the UTC offset — so the boundaries are sent with an
# explicit Europe/Berlin offset instead.
_APP_TZ = ZoneInfo("Europe/Berlin")


def _local_iso(naive_iso: str) -> str:
    """'2026-07-14T00:00:00' -> '2026-07-14T00:00:00+02:00' (Europe/Berlin)."""
    return datetime.fromisoformat(naive_iso).replace(tzinfo=_APP_TZ).isoformat()


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


def _build_client(
    credential: TokenCredential, scopes: list[str] | None = None
) -> GraphServiceClient:
    """Build a GraphServiceClient backed by any TokenCredential.

    CAE (Continuous Access Evaluation) is disabled explicitly: kiota's auth
    provider defaults to ``is_cae_enabled=True``, which makes azure-identity
    look for tokens in a SEPARATE CAE token cache file — but the onboarding
    device-code flow populates only the non-CAE cache. With the default,
    every SDK call fails silent auth (``AuthenticationRequiredError``) even
    though a valid cached token exists.

    Args:
        scopes: Scopes the client requests tokens for. Defaults to the
            calendar-read ``SCOPES``; the send-mail path passes
            ``MAIL_SCOPES``.
    """
    auth_provider = AzureIdentityAuthenticationProvider(
        credential, scopes=scopes or SCOPES, is_cae_enabled=False
    )
    return GraphServiceClient(request_adapter=GraphRequestAdapter(auth_provider))


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


async def get_signed_in_user(credential: TokenCredential | None = None) -> dict:
    """Fetch the signed-in user's identity (email + name) via Graph ``/me``.

    Lets the app show and use the ACTUAL connected Microsoft account rather than
    a hardcoded demo email. Requires the ``User.Read`` scope (see auth.SCOPES).

    Args:
        credential: Optional ``TokenCredential`` to reuse — typically the
            :class:`StaticTokenCredential` wrapping the token from the web
            device-code flow, so this doesn't trigger a second interactive flow.

    Returns:
        ``{"email": str | None, "name": str | None}``. For personal accounts
        ``mail`` may be empty, so ``userPrincipalName`` is used as a fallback.
    """
    credential = credential or acquire_credential()
    client = _build_client(credential)

    user = await client.me.get()
    if user is None:
        return {"email": None, "name": None}

    email = getattr(user, "mail", None) or getattr(user, "user_principal_name", None)
    return {"email": email, "name": getattr(user, "display_name", None)}


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
        client, _local_iso(f"{date}T00:00:00"), _local_iso(f"{date}T23:59:59"), user_email
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
        client, _local_iso(f"{start_date}T00:00:00"), _local_iso(f"{end_date}T23:59:59"), user_email
    )


async def send_mail(
    to_address: str,
    subject: str,
    body: str,
    credential: TokenCredential | None = None,
) -> None:
    """Send a plain-text email from the connected account via Graph ``/me/sendMail``.

    Requires the ``Mail.Send`` delegated scope. Logins made before that scope
    was added have not consented to it — the silent token request then raises
    ``AuthenticationRequiredError`` (calendar reads are unaffected; they use
    the narrower ``SCOPES``). The fix is a one-time Outlook reconnect, which
    requests ``MAIL_SCOPES``.

    Args:
        to_address: Recipient email address.
        subject: Mail subject line.
        body: Plain-text mail body.
        credential: Optional ``TokenCredential`` to reuse; defaults to the
            silent cached-login credential.

    Raises:
        Exception: Graph/auth errors propagate — the calling write tool turns
            them into a user-facing result.
    """
    from msgraph.generated.models.body_type import BodyType
    from msgraph.generated.models.email_address import EmailAddress
    from msgraph.generated.models.item_body import ItemBody
    from msgraph.generated.models.message import Message
    from msgraph.generated.models.recipient import Recipient
    from msgraph.generated.users.item.send_mail.send_mail_post_request_body import (
        SendMailPostRequestBody,
    )

    credential = credential or acquire_credential()
    client = _build_client(credential, scopes=MAIL_SCOPES)

    request_body = SendMailPostRequestBody(
        message=Message(
            subject=subject,
            body=ItemBody(content_type=BodyType.Text, content=body),
            to_recipients=[Recipient(email_address=EmailAddress(address=to_address))],
        ),
        save_to_sent_items=True,
    )
    await client.me.send_mail.post(request_body)


async def update_event(
    event_id: str,
    start: str | None = None,
    end: str | None = None,
    user_email: str | None = None,
    credential: TokenCredential | None = None,
) -> None:
    """Move a calendar appointment via Graph ``PATCH /me/events/{id}``.

    Requires the ``Calendars.ReadWrite`` delegated scope (see
    ``CALENDAR_WRITE_SCOPES``). A login consented only for the narrower
    ``Calendars.Read`` (calendar reads and mail keep working) raises
    ``AuthenticationRequiredError`` on the silent token request here — the
    same failure mode ``send_mail`` hits for an unconsented ``Mail.Send``. The
    fix is a one-time Outlook reconnect.

    Args:
        event_id: Graph event id (the ``id`` field ``get_events``/mapper
            already exposes to the write path).
        start: New start as a naive local datetime string
            ("YYYY-MM-DDTHH:MM:SS"), sent with the app's Europe/Berlin
            timezone. Omitted leaves the start unchanged.
        end: New end, same format. The caller is expected to always supply
            both together when shifting the appointment (Graph does not
            infer a new end from a moved start), but either can be omitted to
            leave that side untouched.
        user_email: Optional email of another user's calendar to update.
        credential: Optional ``TokenCredential`` to reuse; defaults to the
            silent cached-login credential.

    Raises:
        Exception: Graph/auth errors propagate — the calling write tool turns
            them into a user-facing result.
    """
    from msgraph.generated.models.date_time_time_zone import DateTimeTimeZone
    from msgraph.generated.models.event import Event

    credential = credential or acquire_credential()
    client = _build_client(credential, scopes=CALENDAR_WRITE_SCOPES)

    request_body = Event()
    if start:
        request_body.start = DateTimeTimeZone(date_time=start, time_zone="Europe/Berlin")
    if end:
        request_body.end = DateTimeTimeZone(date_time=end, time_zone="Europe/Berlin")

    if user_email:
        await client.users.by_user_id(user_email).events.by_event_id(event_id).patch(
            request_body
        )
    else:
        await client.me.events.by_event_id(event_id).patch(request_body)
