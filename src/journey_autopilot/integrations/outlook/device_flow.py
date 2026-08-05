"""Interactive Outlook connect — the device-code flow and its pending state.

The onboarding "Connect Outlook" step is the only place in the system that
authenticates a *human* against Microsoft, and almost everything awkward about
that is Microsoft's, not the web app's. It therefore lives here rather than in
the endpoint:

- ``authenticate()`` blocks until the user types the code at microsoft.com, so
  the flow runs on a worker thread while the browser polls;
- a pending flow outlives the request that started it, so it is per-user module
  state keyed by ``user_id``;
- the ``Mail.Send`` scope may be unconsentable in a given app registration.
  That surfaces as specific AADSTS codes which have to be caught and retried
  with calendar-only scopes, or connecting a calendar would fail over a mail
  permission nobody asked for yet;
- right after a device flow, MSAL's silent path is unreliable and re-entering
  it would start a *second* interactive flow that hangs the request — so the
  follow-up Graph calls reuse the just-issued token verbatim via
  :class:`StaticTokenCredential`.

``ui/routes/connect.py`` calls only :func:`start`, :func:`poll` and
:func:`forget`, and persists what comes back. It knows none of the above.

No azure type crosses this boundary — both entry points return plain dicts:

    start(user_id)        -> {"mode": "simulated" | "cached" | "device_code", ...}
    await poll(user_id)   -> {"status": "none" | "pending" | "complete"
                                        | "error" | "expired", ...}

On ``complete``, ``events`` is ``None`` when Microsoft Graph could not be
reached — the caller decides what to show instead (the onboarding route
substitutes simulated events so the wizard never dead-ends on a preview).
"""

from __future__ import annotations

import logging
import threading
from datetime import date, timedelta

# Timestamped device-flow lines on stderr, visible in the terminal running
# ``python run_onboarding.py``. The handler is attached by the web app (see
# ui/server.py) so importing this module configures nothing.
logger = logging.getLogger("journey_autopilot.outlook")

# Pending flows: user_id -> {thread, result, error, device_code}. The worker
# thread blocks on ``authenticate()``; ``poll`` reads the outcome it parks here.
_PENDING: dict[str, dict] = {}

# AADSTS codes that mean the Mail.Send scope could not be consented (the app
# registration lacks the permission, or admin consent was withheld). Connecting
# a calendar must not break on those — see the retry in ``_run_device_flow``.
_CONSENT_ERROR_CODES = ("AADSTS65001", "AADSTS70011", "AADSTS650053")

# How far ahead the post-connect preview looks. Two weeks of the REAL connected
# calendar, so the preview reflects the signed-in account rather than pinned
# demo days.
_PREVIEW_DAYS = 14


def forget(user_id: str) -> None:
    """Drop any pending flow for this user (disconnect, or starting over)."""
    _PENDING.pop(user_id, None)


def start(user_id: str) -> dict:
    """Begin connecting Outlook, and report how the frontend should proceed.

    Three outcomes, all reported as ``mode``:

    - ``"simulated"`` — no MS Entra credentials configured. The caller should
      fall back to the simulated consent dialog.
    - ``"cached"`` — a login from an earlier session (a previous onboarding run
      or ``python scripts/check_outlook.py --login``) was still valid and was
      reused silently. No device-code round trip; :func:`poll` completes
      immediately. This is what lets a live demo pre-authorize Outlook before
      the wizard starts.
    - ``"device_code"`` — a real device flow is now running on a worker thread.
      The returned ``user_code`` / ``verification_uri`` go on screen and the
      frontend polls :func:`poll`. ``pending: True`` means the code had not
      arrived yet within the startup grace period; poll for it.
    """
    _PENDING.pop(user_id, None)

    try:
        from .auth import (
            CALENDAR_WRITE_SCOPES,
            MAIL_SCOPES,
            SCOPES,
            acquire_credential,
            create_device_credential,
            is_outlook_configured,
            save_authentication_record,
        )
    except ImportError:
        return {"mode": "simulated"}

    if not is_outlook_configured():
        return {"mode": "simulated"}

    try:
        token = acquire_credential().get_token(*SCOPES)  # silent or raises
        _PENDING[user_id] = {
            "thread": None, "result": token, "error": None, "device_code": None,
        }
        logger.info("cached login reused — connected silently, no device flow")
        return {"mode": "cached"}
    except Exception as exc:
        logger.info(
            "no usable cached login (%s: %s) — starting device flow",
            type(exc).__name__, exc,
        )

    auth_state: dict = {"thread": None, "result": None, "error": None, "device_code": None}
    _PENDING[user_id] = auth_state

    def prompt_callback(verification_uri: str, user_code: str, expires_on) -> None:
        logger.info(
            "device code received — user_code=%s, verification_uri=%s",
            user_code, verification_uri,
        )
        auth_state["device_code"] = {
            "user_code": user_code,
            "verification_uri": verification_uri,
            "expires_at": expires_on.isoformat() if hasattr(expires_on, "isoformat") else str(expires_on),
        }

    def _run_device_flow() -> None:
        try:
            cred = create_device_credential(prompt_callback)
            # authenticate() runs the device flow AND returns the
            # AuthenticationRecord — the account metadata a fresh credential
            # needs to find the cached token later. Persisting it is what lets
            # the agent tools (acquire_credential) read the calendar silently
            # instead of falling back to mock with AuthenticationRequiredError.
            # CALENDAR_WRITE_SCOPES (calendar read+write + Mail.Send) so one
            # consent covers reading the calendar, rescheduling appointments,
            # and sending the approved notice email. Falls back in two steps
            # if the app registration doesn't expose the newer permission(s)
            # yet, so connecting a calendar never breaks over a permission
            # nobody asked for.
            try:
                record = cred.authenticate(scopes=CALENDAR_WRITE_SCOPES)
            except Exception as exc:
                if not any(code in str(exc) for code in _CONSENT_ERROR_CODES):
                    raise
                logger.warning(
                    "Calendars.ReadWrite consent unavailable (%s) — retrying "
                    "with calendar-read + Mail.Send scopes; rescheduling will "
                    "stay disabled (simulated) until the app registration "
                    "adds Calendars.ReadWrite.",
                    exc,
                )
                try:
                    record = cred.authenticate(scopes=MAIL_SCOPES)
                except Exception as exc2:
                    if not any(code in str(exc2) for code in _CONSENT_ERROR_CODES):
                        raise
                    logger.warning(
                        "Mail.Send consent unavailable (%s) — retrying with "
                        "calendar-only scopes; the notice-email send will "
                        "stay disabled until the app registration adds "
                        "Mail.Send.",
                        exc2,
                    )
                    record = cred.authenticate(scopes=SCOPES)
            save_authentication_record(record)
            # Same instance, record in memory -> silent; token for the preview.
            auth_state["result"] = cred.get_token(*SCOPES)
            logger.info("device flow completed — token acquired, auth record saved")
        except Exception as exc:
            logger.error("device flow failed: %s", exc, exc_info=True)
            auth_state["error"] = str(exc)

    t = threading.Thread(target=_run_device_flow, daemon=True, name="outlook-device-flow")
    auth_state["thread"] = t
    t.start()

    # Wait briefly for prompt_callback to fire (before the blocking poll starts)
    t.join(timeout=5)

    dc = auth_state.get("device_code")
    if dc:
        return {"mode": "device_code", **dc}
    logger.warning("device code not received within 5s — returning pending")
    return {"mode": "device_code", "user_code": None, "verification_uri": None, "pending": True}


async def poll(user_id: str) -> dict:
    """Report the state of this user's pending flow, one ``status`` per case.

    - ``"none"`` — nothing in flight. The caller decides whether that means
      "never started" or "already connected in an earlier session".
    - ``"pending"`` — the worker is still waiting on the user; the device code
      is echoed back so a reloaded page can re-display it.
    - ``"error"`` / ``"expired"`` — the flow ended without a usable token.
    - ``"complete"`` — carries ``account`` (the ACTUAL signed-in Microsoft
      identity, ``{"email", "name"}``, so the UI stops showing the demo one)
      and ``events`` (the calendar preview, or ``None`` if Graph failed).

    Every terminal status clears the pending flow, so polling again returns
    ``"none"`` rather than replaying an outcome the caller already persisted.
    """
    auth_state = _PENDING.get(user_id)
    if auth_state is None:
        return {"status": "none"}

    thread: threading.Thread | None = auth_state["thread"]
    dc = auth_state.get("device_code") or {}

    if thread is not None and thread.is_alive():
        return {
            "status": "pending",
            "user_code": dc.get("user_code"),
            "verification_uri": dc.get("verification_uri"),
        }

    if auth_state.get("error"):
        _PENDING.pop(user_id, None)
        return {"status": "error", "error": auth_state["error"]}

    if auth_state.get("result"):
        access_token = auth_state["result"]
        _PENDING.pop(user_id, None)
        from . import StaticTokenCredential, get_signed_in_user

        credential = StaticTokenCredential(access_token)

        identity = {"email": None, "name": None}
        try:
            identity = await get_signed_in_user(credential=credential)
        except Exception as exc:
            logger.warning(
                "could not read signed-in user (%s: %s)", type(exc).__name__, exc
            )

        events = await _fetch_preview(credential)
        logger.info(
            "outlook connected as %s — %d preview events",
            identity.get("email") or "<unknown>",
            len(events) if events else 0,
        )
        return {"status": "complete", "account": identity, "events": events}

    logger.warning("device flow ended without a token — status=expired")
    _PENDING.pop(user_id, None)
    return {"status": "expired"}


async def _fetch_preview(credential) -> list[dict] | None:
    """Real calendar events for the post-connect preview, or None if Graph failed.

    Returning None rather than raising keeps the decision with the caller: the
    onboarding step shows simulated events instead, so a Graph hiccup never
    blocks the wizard on a screen that is only a preview.
    """
    try:
        from . import get_calendar_events_range

        today = date.today()
        end = today + timedelta(days=_PREVIEW_DAYS)
        return await get_calendar_events_range(today.isoformat(), end.isoformat(),
                                               credential=credential)
    except Exception as exc:
        logger.warning(
            "Graph preview failed (%s: %s) — caller falls back to simulated events",
            type(exc).__name__, exc,
        )
        return None
