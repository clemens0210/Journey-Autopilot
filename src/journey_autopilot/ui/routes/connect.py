"""Connecting the two external channels: the mobile number and Outlook.

Both are "prove you own this account" steps in onboarding, and both keep a
little pending state between two requests — the SMS code here, the device-code
flow inside ``integrations.outlook.device_flow``.

The Outlook routes deliberately hold no Microsoft knowledge: they call
``start`` / ``poll`` / ``forget`` and persist what comes back. Everything about
MSAL, scopes, consent errors and worker threads lives in the integration.
"""

from __future__ import annotations

import random
import re

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel

from journey_autopilot.onboarding import accounts
from journey_autopilot.persistence import store

from .deps import current_user_id

router = APIRouter(tags=["connect"])

# Pending SMS verifications: user_id -> (phone, code)
_PENDING_PHONE: dict[str, tuple[str, str]] = {}


class PhoneStartRequest(BaseModel):
    phone: str


class PhoneConfirmRequest(BaseModel):
    code: str


class OutlookConnectRequest(BaseModel):
    consent: bool = False


def forget_pending_phone(user_id: str) -> None:
    """Drop a half-finished SMS verification (account deletion, disconnect)."""
    _PENDING_PHONE.pop(user_id, None)


# --- Mobile number: SMS verification (simulated) ------------------------------------------


@router.post("/api/verify/phone/start")
def phone_start(body: PhoneStartRequest, authorization: str | None = Header(default=None)) -> dict:
    user_id = current_user_id(authorization)
    phone = re.sub(r"[^\d+]", "", body.phone)
    if not re.fullmatch(r"\+?\d{8,15}", phone):
        raise HTTPException(status_code=422, detail="Please enter a valid mobile number (e.g. +49 151 12345678).")

    code = f"{random.randint(0, 9999):04d}"
    _PENDING_PHONE[user_id] = (phone, code)
    # Deliver the code to the actual number via Twilio WhatsApp (degrades to a
    # demo no-op if Twilio isn't configured), AND still return it so the on-screen
    # demo display keeps working.
    from journey_autopilot.integrations import whatsapp

    delivery = whatsapp.send_verification_code(phone, code)
    return {"sent": True, "phone": phone, "demo_code": code, "delivery": delivery}


@router.post("/api/verify/phone/confirm")
def phone_confirm(body: PhoneConfirmRequest, authorization: str | None = Header(default=None)) -> dict:
    user_id = current_user_id(authorization)
    pending = _PENDING_PHONE.get(user_id)
    if pending is None:
        raise HTTPException(status_code=409, detail="No code was requested.")
    phone, code = pending
    if body.code.strip() != code:
        raise HTTPException(status_code=422, detail="That code isn't right. Please try again.")

    del _PENDING_PHONE[user_id]
    profile = store.update_profile(
        user_id, {"notifications": {"phone": phone, "phone_verified": True}}
    )
    return {"verified": True, "profile": profile}


@router.delete("/api/verify/phone")
def phone_remove(authorization: str | None = Header(default=None)) -> dict:
    """Remove the verified phone number from the profile."""
    user_id = current_user_id(authorization)
    forget_pending_phone(user_id)
    profile = store.update_profile(
        user_id, {"notifications": {"phone": None, "phone_verified": False}}
    )
    return {"removed": True, "profile": profile}


# --- Outlook calendar (device-code flow or simulated consent) ----------------------------


def _device_flow():
    """The Outlook device-code flow, or None if the integration isn't installed.

    The only Microsoft-specific thing left in this layer, and it is a
    deployment question rather than a protocol one: without azure-identity /
    msgraph the UI falls back to the simulated consent dialog.
    """
    try:
        from journey_autopilot.integrations.outlook import device_flow
    except ImportError:
        return None
    return device_flow


@router.post("/api/connect/outlook/start")
def outlook_start(authorization: str | None = Header(default=None)) -> dict:
    """Begin the Outlook connection flow.

    Returns the integration's ``mode`` verbatim: ``"cached"`` (an earlier login
    was reused — poll ``/status``, it completes immediately), ``"device_code"``
    (show ``user_code`` + ``verification_uri`` and poll), or ``"simulated"``
    (no Entra credentials — fall back to the simulated consent dialog).
    """
    user_id = current_user_id(authorization)
    flow = _device_flow()
    if flow is None:
        return {"mode": "simulated"}
    return flow.start(user_id)


@router.get("/api/connect/outlook/status")
async def outlook_status(authorization: str | None = Header(default=None)) -> dict:
    """Poll the Outlook device-code flow state.

    Returns ``{"status": "pending" | "complete" | "expired" | "error"}``.
    On ``complete`` the profile flag is set — including the ACTUAL signed-in
    Microsoft identity, so the UI stops showing the demo account — and the
    calendar preview is returned. A Graph failure is not fatal: the integration
    reports ``events: None`` and the simulated events stand in, because this
    screen is a preview and must never dead-end the wizard.
    """
    user_id = current_user_id(authorization)
    flow = _device_flow()
    result = await flow.poll(user_id) if flow is not None else {"status": "none"}

    if result["status"] == "none":
        # Nothing in flight — but the user may have connected in an earlier
        # session, in which case the browser expects the preview, not "none".
        profile = store.get_profile(user_id)
        if profile and profile.get("connections", {}).get("outlook"):
            return {"status": "complete", "events": accounts.outlook_events(user_id)}
        return {"status": "none"}

    if result["status"] != "complete":
        return result

    identity = result.get("account") or {}
    profile = store.update_profile(
        user_id,
        {
            "connections": {
                "outlook": True,
                "outlook_email": identity.get("email"),
                "outlook_name": identity.get("name"),
            }
        },
    )
    events = result.get("events")
    if events is None:
        events = accounts.outlook_events(user_id)
    return {
        "status": "complete",
        "profile": profile,
        "events": events,
        "account": identity,
    }


@router.post("/api/connect/outlook")
async def connect_outlook(body: OutlookConnectRequest, authorization: str | None = Header(default=None)) -> dict:
    """Simulated Outlook consent (fallback when Entra is not configured).

    When ``MS_ENTRA_CLIENT_ID`` is set, the frontend should use
    ``/api/connect/outlook/start`` + ``/status`` instead. This endpoint keeps
    the simulated demo flow working.
    """
    user_id = current_user_id(authorization)
    if not body.consent:
        raise HTTPException(status_code=422, detail="Please grant consent to continue.")
    profile = store.update_profile(user_id, {"connections": {"outlook": True}})
    return {"connected": True, "events": accounts.outlook_events(user_id), "profile": profile}


@router.delete("/api/connect/outlook")
def disconnect_outlook(authorization: str | None = Header(default=None)) -> dict:
    """Disconnect Outlook: flip the profile flag and best-effort clear the token cache."""
    user_id = current_user_id(authorization)
    flow = _device_flow()
    if flow is not None:
        flow.forget(user_id)
    profile = store.update_profile(
        user_id,
        {"connections": {"outlook": False, "outlook_email": None, "outlook_name": None}},
    )

    cleared = False
    try:
        from journey_autopilot.integrations.outlook import clear_token_cache

        cleared = clear_token_cache()
    except Exception:
        pass  # best-effort

    return {"connected": False, "profile": profile, "token_cache_cleared": cleared}
