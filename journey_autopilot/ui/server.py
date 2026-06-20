"""Web app server — FastAPI app with a JSON API and DB Navigator-style UI.

Start:
    python run_onboarding.py            # http://127.0.0.1:8000
    # or: uvicorn journey_autopilot.ui.server:app --reload

This is the presentation layer. The onboarding *logic* (simulated DB
accounts/trips and the SQLite profile store) lives in
``journey_autopilot.onboarding`` and is imported here. The chat endpoint
(``/api/chat``) runs the same ReAct orchestrator as ``run_demo.py``.

What's simulated here (and why) is documented in the Context Record: DB
(Deutsche Bahn) offers no official API for account login / ticket import,
and Microsoft OAuth and SMS sending require registered apps or a gateway
contract. The flows are therefore built with real UX but simulated backends —
the API contracts match what a real integration would need to deliver.

Live data: the home station search (`/api/stations`) uses the db_service
sidecar (real DB station data) and falls back to a static list of major
stations without it.
"""

from __future__ import annotations

import re
import secrets
import random
import threading
from datetime import date, timedelta

from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pathlib import Path
from pydantic import BaseModel

# Onboarding logic ("the functions") lives in a separate package; the UI only
# imports it. The chat module is local to this UI package.
from journey_autopilot.onboarding import accounts, store
from journey_autopilot.rerouting import db_api
from . import chat

app = FastAPI(title="Journey Autopilot — Web App", version="0.1.0")

_STATIC = Path(__file__).resolve().parent / "static"

# In-memory sessions: token -> user_id. Deliberately without persistence for
# the single-user prototype; a restart simply means "log in again".
_SESSIONS: dict[str, str] = {}

# Pending SMS verifications: user_id -> (phone, code)
_PENDING_PHONE: dict[str, tuple[str, str]] = {}

# Pending Outlook device-code flows: user_id -> {thread, result, error,
# device_code (dict with user_code/verification_uri/expires_at) or None}
# The thread runs get_token() (blocking); /status checks if it's done.
_OUTLOOK_AUTH: dict[str, dict] = {}


# --- Request models ---------------------------------------------------------------


class LoginRequest(BaseModel):
    email: str
    password: str


class PhoneStartRequest(BaseModel):
    phone: str


class PhoneConfirmRequest(BaseModel):
    code: str


class OutlookConnectRequest(BaseModel):
    consent: bool = False


class ChatRequest(BaseModel):
    message: str
    session_id: str | None = None
    trip: dict | None = None


# --- Auth helpers ---------------------------------------------------------------------


def _user_id(authorization: str | None) -> str:
    """Resolves the bearer token to a user_id, otherwise 401."""
    if authorization and authorization.startswith("Bearer "):
        token = authorization.removeprefix("Bearer ")
        user_id = _SESSIONS.get(token)
        if user_id:
            return user_id
    raise HTTPException(status_code=401, detail="You're not signed in.")


# --- DB account: login & trip import -----------------------------------------------------


@app.post("/api/auth/db-login")
def db_login(body: LoginRequest) -> dict:
    """Simulated bahn.de login. Imports booked trips right away."""
    account = accounts.authenticate(body.email, body.password)
    if account is None:
        raise HTTPException(
            status_code=401,
            detail="That email or password isn't right. Demo login: lucas.wild@example.com / demo123",
        )

    store.upsert_user(account)
    trips = accounts.booked_trips(account["user_id"])
    store.save_trips(account["user_id"], trips)
    profile = store.update_profile(account["user_id"], {"connections": {"db_account": True}})

    token = secrets.token_urlsafe(24)
    _SESSIONS[token] = account["user_id"]
    return {"token": token, "account": account, "trips": trips, "profile": profile}


@app.get("/api/me")
def me(authorization: str | None = Header(default=None)) -> dict:
    user_id = _user_id(authorization)
    return {
        "account": store.get_account(user_id),
        "profile": store.get_profile(user_id),
        "trips": store.get_trips(user_id),
    }


@app.get("/api/trips")
def trips(authorization: str | None = Header(default=None)) -> dict:
    user_id = _user_id(authorization)
    return {"trips": store.get_trips(user_id)}


# --- Mobile number: SMS verification (simulated) ------------------------------------------


@app.post("/api/verify/phone/start")
def phone_start(body: PhoneStartRequest, authorization: str | None = Header(default=None)) -> dict:
    user_id = _user_id(authorization)
    phone = re.sub(r"[^\d+]", "", body.phone)
    if not re.fullmatch(r"\+?\d{8,15}", phone):
        raise HTTPException(status_code=422, detail="Please enter a valid mobile number (e.g. +49 151 12345678).")

    code = f"{random.randint(0, 9999):04d}"
    _PENDING_PHONE[user_id] = (phone, code)
    # Simulated sending: in the real system the code would go out via an SMS
    # gateway. In demo mode we return it directly so the flow stays demoable.
    return {"sent": True, "phone": phone, "demo_code": code}


@app.post("/api/verify/phone/confirm")
def phone_confirm(body: PhoneConfirmRequest, authorization: str | None = Header(default=None)) -> dict:
    user_id = _user_id(authorization)
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


@app.delete("/api/verify/phone")
def phone_remove(authorization: str | None = Header(default=None)) -> dict:
    """Remove the verified phone number from the profile."""
    user_id = _user_id(authorization)
    _PENDING_PHONE.pop(user_id, None)
    profile = store.update_profile(
        user_id, {"notifications": {"phone": None, "phone_verified": False}}
    )
    return {"removed": True, "profile": profile}


# --- Outlook calendar (device-code flow or simulated consent) ----------------------------


def _outlook_preview_dates() -> list[str]:
    """Dates to fetch for the post-connect preview — pinned to the demo scenario.

    Lucas' first trip (DEMO_DATE) and the workshop date (today+12d) match the
    events the simulated path returns, so the real and simulated previews look
    the same.
    """
    from journey_autopilot.onboarding.accounts import DEMO_DATE

    d3 = date.today() + timedelta(days=12)
    return [DEMO_DATE.isoformat(), d3.isoformat()]


@app.post("/api/connect/outlook/start")
def outlook_start(authorization: str | None = Header(default=None)) -> dict:
    """Begin the Outlook connection flow.

    If MS_ENTRA_CLIENT_ID is configured, starts the real device-code flow in a
    background thread and returns the user code + verification URL for the
    browser to display. The frontend polls ``/api/connect/outlook/status``.

    Without Entra credentials, returns ``{"mode": "simulated"}`` so the UI can
    fall back to the existing simulated consent dialog.
    """
    user_id = _user_id(authorization)

    # Cancel any previous pending flow for this user
    _OUTLOOK_AUTH.pop(user_id, None)

    try:
        from journey_autopilot.calendar import create_device_credential, is_outlook_configured
    except ImportError:
        return {"mode": "simulated"}

    if not is_outlook_configured():
        return {"mode": "simulated"}

    # Real device-code flow — capture the code via prompt_callback, poll in a thread
    auth_state: dict = {"thread": None, "result": None, "error": None, "device_code": None}
    _OUTLOOK_AUTH[user_id] = auth_state

    def prompt_callback(verification_uri: str, user_code: str, expires_on) -> None:
        auth_state["device_code"] = {
            "user_code": user_code,
            "verification_uri": verification_uri,
            "expires_at": expires_on.isoformat() if hasattr(expires_on, "isoformat") else str(expires_on),
        }

    from journey_autopilot.calendar.auth import SCOPES

    def _run_device_flow() -> None:
        try:
            cred = create_device_credential(prompt_callback)
            auth_state["result"] = cred.get_token(*SCOPES)
        except Exception as exc:
            auth_state["error"] = str(exc)

    t = threading.Thread(target=_run_device_flow, daemon=True)
    auth_state["thread"] = t
    t.start()

    # Wait briefly for prompt_callback to fire (it runs as soon as the device
    # code is received, before polling starts)
    t.join(timeout=5)

    dc = auth_state.get("device_code")
    if dc:
        return {"mode": "device_code", **dc}
    # prompt_callback didn't fire in time — frontend will see "pending" and
    # can retry /start or show an error
    return {"mode": "device_code", "user_code": None, "verification_uri": None, "pending": True}


@app.get("/api/connect/outlook/status")
async def outlook_status(authorization: str | None = Header(default=None)) -> dict:
    """Poll the Outlook device-code flow state.

    Returns ``{"status": "pending" | "complete" | "expired" | "error"}``.
    On ``complete`` the profile flag is set and real calendar events are
    fetched for the preview.
    """
    user_id = _user_id(authorization)
    auth_state = _OUTLOOK_AUTH.get(user_id)

    if auth_state is None:
        # No pending flow — check if already connected
        profile = store.get_profile(user_id)
        if profile and profile.get("connections", {}).get("outlook"):
            return {"status": "complete", "events": _outlook_events_or_fetch(user_id)}
        return {"status": "none"}

    thread: threading.Thread | None = auth_state["thread"]

    if thread is not None and thread.is_alive():
        dc = auth_state.get("device_code") or {}
        return {
            "status": "pending",
            "user_code": dc.get("user_code"),
            "verification_uri": dc.get("verification_uri"),
        }

    # Thread finished — check result
    if auth_state.get("error"):
        err = auth_state["error"]
        _OUTLOOK_AUTH.pop(user_id, None)
        return {"status": "error", "error": err}

    if auth_state.get("result"):
        _OUTLOOK_AUTH.pop(user_id, None)
        profile = store.update_profile(user_id, {"connections": {"outlook": True}})
        events = await _fetch_outlook_preview(user_id)
        return {"status": "complete", "profile": profile, "events": events}

    # Thread died without result or error (e.g. timeout)
    _OUTLOOK_AUTH.pop(user_id, None)
    return {"status": "expired"}


async def _fetch_outlook_preview(user_id: str) -> list[dict]:
    """Fetch real Outlook events for the pinned demo dates.

    Falls back to simulated events on any Graph error so the preview never
    breaks the onboarding flow.
    """
    try:
        from journey_autopilot.calendar import get_calendar_events_range

        dates = _outlook_preview_dates()
        events = await get_calendar_events_range(dates[0], dates[-1])
        return events
    except Exception:
        return accounts.outlook_events(user_id)


def _outlook_events_or_fetch(user_id: str) -> list[dict]:
    """Synchronous fallback for already-connected users without a pending flow."""
    return accounts.outlook_events(user_id)


@app.post("/api/connect/outlook")
async def connect_outlook(body: OutlookConnectRequest, authorization: str | None = Header(default=None)) -> dict:
    """Simulated Outlook consent (fallback when Entra is not configured).

    When ``MS_ENTRA_CLIENT_ID`` is set, the frontend should use
    ``/api/connect/outlook/start`` + ``/status`` instead. This endpoint keeps
    the simulated demo flow working.
    """
    user_id = _user_id(authorization)
    if not body.consent:
        raise HTTPException(status_code=422, detail="Please grant consent to continue.")
    profile = store.update_profile(user_id, {"connections": {"outlook": True}})
    return {"connected": True, "events": accounts.outlook_events(user_id), "profile": profile}


@app.delete("/api/connect/outlook")
def disconnect_outlook(authorization: str | None = Header(default=None)) -> dict:
    """Disconnect Outlook: flip the profile flag and best-effort clear the token cache."""
    user_id = _user_id(authorization)
    _OUTLOOK_AUTH.pop(user_id, None)
    profile = store.update_profile(user_id, {"connections": {"outlook": False}})

    cleared = False
    try:
        from journey_autopilot.calendar import clear_token_cache

        cleared = clear_token_cache()
    except Exception:
        pass  # best-effort

    return {"connected": False, "profile": profile, "token_cache_cleared": cleared}


# --- Profile ------------------------------------------------------------------------------


@app.get("/api/profile")
def get_profile(authorization: str | None = Header(default=None)) -> dict:
    user_id = _user_id(authorization)
    return {"profile": store.get_profile(user_id)}


@app.put("/api/profile")
def put_profile(patch: dict, authorization: str | None = Header(default=None)) -> dict:
    """Partial patch: the UI sends only the changed fields per onboarding step."""
    user_id = _user_id(authorization)
    # Only change connection/verification status via the dedicated endpoints.
    patch.pop("connections", None)
    if isinstance(patch.get("notifications"), dict):
        patch["notifications"].pop("phone_verified", None)
    return {"profile": store.update_profile(user_id, patch)}


@app.post("/api/onboarding/complete")
def complete_onboarding(authorization: str | None = Header(default=None)) -> dict:
    user_id = _user_id(authorization)
    return {"profile": store.update_profile(user_id, {"onboarding_completed": True})}


@app.delete("/api/profile")
def delete_profile(authorization: str | None = Header(default=None)) -> dict:
    """GDPR deletion: completely remove account, profile, and imported trips."""
    user_id = _user_id(authorization)
    store.delete_user(user_id)
    _PENDING_PHONE.pop(user_id, None)
    for token, uid in list(_SESSIONS.items()):
        if uid == user_id:
            del _SESSIONS[token]
    return {"deleted": True}


# --- Station search (real DB data via sidecar, with fallback) --------------------------------


@app.get("/api/stations")
def stations(query: str = "") -> dict:
    query = query.strip()
    if len(query) < 2:
        return {"stations": [], "source": "none"}
    try:
        hits = [
            {"id": item["id"], "name": item["name"]}
            for item in db_api.normalize_locations(db_api.locations(query, results=6))
        ]
        return {"stations": hits, "source": "db-live"}
    except db_api.DBServiceError:
        needle = query.lower()
        hits = [s for s in accounts.FALLBACK_STATIONS if needle in s["name"].lower()]
        return {"stations": hits[:6], "source": "fallback"}


# --- Chat (runs the ReAct orchestrator, like run_demo.py) --------------------------------


@app.post("/api/chat")
async def chat_endpoint(
    body: ChatRequest, authorization: str | None = Header(default=None)
) -> dict:
    """Drives the ReAct orchestrator from the chat UI.

    Clicking a trip opens a chat; each message is handed to ``root_agent``
    (the same orchestrator ``run_demo.py`` uses). On the first message the
    selected trip is added as context so the orchestrator monitors it. The
    agent/tool trace and the final answer are returned for display.

    ADK + a configured Uni-GPT backend (.env) are required here; errors are
    returned as ``error`` (HTTP 200) so the chat UI can show them inline.
    """
    _user_id(authorization)  # chat is behind the login like the rest of the API
    try:
        return await chat.chat_turn(body.session_id, body.message, body.trip)
    except Exception as exc:  # surfaced inline in the chat instead of a 500
        return {
            "session_id": body.session_id,
            "reply": None,
            "trace": [],
            "error": f"{type(exc).__name__}: {exc}",
        }


# --- Static UI ----------------------------------------------------------------------------


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    return FileResponse(_STATIC / "index.html")


app.mount("/static", StaticFiles(directory=_STATIC), name="static")
