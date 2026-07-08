"""Web app server — FastAPI app with a JSON API and DB Navigator-style UI.

Start:
    python run_onboarding.py            # http://127.0.0.1:8000
    # or: uvicorn journey_autopilot.ui.server:app --reload

This is the presentation layer. The onboarding *logic* (simulated DB
accounts/trips and the SQLite profile store) lives in
``journey_autopilot.onboarding`` and is imported here. The chat endpoint
(``/api/chat``) runs the same ReAct orchestrator as ``scenarios/happy_path.py``.

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

import hashlib
import logging
import re
import secrets
import random
import sys
import threading
from datetime import date, timedelta
from typing import TYPE_CHECKING

from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pathlib import Path
from pydantic import BaseModel

# Onboarding logic ("the functions") lives in a separate package; the UI only
# imports it. The chat module is local to this UI package.
from journey_autopilot import mock_data, risk
from journey_autopilot.onboarding import accounts
from journey_autopilot.persistence import store
from journey_autopilot.integrations import db_ops as db_api
from journey_autopilot.integrations import stations as db_api_stations
from . import chat

if TYPE_CHECKING:
    from azure.core.credentials import AccessToken, TokenCredential

# Logger for the Outlook device-code flow — timestamped lines on stderr, visible
# in the terminal where ``python run_onboarding.py`` runs.
_outlog = logging.getLogger("journey_autopilot.outlook")
if not _outlog.handlers:
    _h = logging.StreamHandler(sys.stderr)
    _h.setFormatter(logging.Formatter("%(asctime)s [outlook] %(levelname)s %(message)s"))
    _outlog.addHandler(_h)
    _outlog.setLevel(logging.INFO)
_outlog.propagate = False

app = FastAPI(title="Journey Autopilot — Web App", version="0.1.0")

_STATIC = Path(__file__).resolve().parent / "static"

# In-memory sessions: token -> user_id. Deliberately without persistence for
# the single-user prototype; a restart simply means "log in again".
_SESSIONS: dict[str, str] = {}

# Pending SMS verifications: user_id -> (phone, code)
_PENDING_PHONE: dict[str, tuple[str, str]] = {}

# Pending Outlook device-code flows: user_id -> {thread, result, error,
# device_code}. The worker thread blocks on ``get_token()``; /status wraps
# the returned AccessToken in a StaticTokenCredential for the Graph preview
# call so MSAL's silent-auth path isn't re-entered (it's unreliable right
# after a device flow and would trigger a second interactive flow).
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


class BookTripRequest(BaseModel):
    journey: dict


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


# --- Booking (simple journey search via db_service, adds to monitored trips) ---


def _journey_to_trip(journey: dict) -> dict:
    """Convert a normalized db_ops journey option into the booked-trip shape.

    Times are truncated to naive local ISO (DB times are German local) so the
    booked trip renders like the imported demo trips. Coach/seat are mocked —
    there is no real booking, this exists to monitor live connections.
    """
    dep = (journey.get("planned_departure") or journey.get("departure") or "")[:19]
    arr = (journey.get("planned_arrival") or journey.get("arrival") or "")[:19]
    origin, destination = journey.get("origin"), journey.get("destination")
    train = journey.get("train") or journey.get("description")
    if not (dep and arr and origin and destination and train):
        raise HTTPException(status_code=422, detail="Journey is missing route or time data.")

    legs = journey.get("legs") or []
    platform = (legs[0].get("planned_platform") or legs[0].get("platform")) if legs else None
    # Deterministic id: booking the same connection twice updates instead of duplicating.
    key = f"{train}|{dep}|{origin}|{destination}"
    return {
        "trip_id": "BK-" + hashlib.md5(key.encode()).hexdigest()[:10].upper(),
        "order_number": secrets.token_hex(3).upper(),
        "origin": origin,
        "destination": destination,
        "train": train,
        "planned_departure": dep,
        "planned_arrival": arr,
        "platform": f"Platform {platform}" if platform else "Platform tba",
        "coach": "Coach 12",
        "seat": "Seat 42, window",
        "travel_class": 2,
        "price_eur": journey.get("price_eur"),
        "purpose": "Booked connection",
        # Real itinerary from the live search — the trip-detail screen renders
        # these instead of the simulated legs.
        "legs": [
            {
                "train": leg.get("train"),
                "direction": leg.get("direction"),
                "origin": leg.get("origin"),
                "destination": leg.get("destination"),
                "planned_departure": (leg.get("planned_departure") or leg.get("departure") or "")[:19],
                "planned_arrival": (leg.get("planned_arrival") or leg.get("arrival") or "")[:19],
                "platform": leg.get("planned_platform") or leg.get("platform"),
                "arrival_platform": leg.get("planned_arrival_platform") or leg.get("arrival_platform"),
            }
            for leg in legs
            if leg.get("train")
        ],
    }


@app.get("/api/journeys")
def search_journeys(
    from_id: str,
    to_id: str,
    departure: str | None = None,
    authorization: str | None = Header(default=None),
) -> dict:
    """Live journey search between two stations (EVA ids) via the db_service sidecar."""
    _user_id(authorization)
    try:
        payload = db_api.journeys(from_id, to_id, departure=departure, results=6)
    except db_api.DBServiceError as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Journey search needs the db_service sidecar (see db_service/README.md). {exc}",
        )
    return {"journeys": db_api.normalize_journeys(payload)}


@app.post("/api/trips")
def book_trip(body: BookTripRequest, authorization: str | None = Header(default=None)) -> dict:
    """Add a searched journey to the monitored trips (simulated booking)."""
    user_id = _user_id(authorization)
    trip = _journey_to_trip(body.journey)
    store.save_trips(user_id, [trip])
    return {"trip": trip, "trips": store.get_trips(user_id)}


def _live_leg_delays(trip: dict) -> dict[str, int]:
    """Best-effort live arrival delay per train from the db_service sidecar.

    Trips booked via the journey search (``BK-…`` ids) are not in the simulated
    ``LIVE_TRIP_STATUS``, so their live delay has to come from real DB data.
    Re-runs the connection search, matches the trip's train, and returns
    ``{train_name: delay_minutes}``. Returns ``{}`` on any sidecar miss so the
    caller falls back to the simulated status (or 0 = on time).
    """
    origin, destination = trip.get("origin"), trip.get("destination")
    if not origin or not destination:
        return {}
    try:
        from_eva = db_api_stations.resolve_eva(origin)
        to_eva = db_api_stations.resolve_eva(destination)
        if from_eva is None or to_eva is None:
            return {}
        payload = db_api.journeys(
            from_eva, to_eva, departure=trip.get("planned_departure"), results=5
        )
        options = db_api.normalize_journeys(payload)
        train = str(trip.get("train") or "")
        option = next(
            (o for o in options if train and train in o.get("trains", [])),
            options[0] if options else None,
        )
        if not option:
            return {}
        delays: dict[str, int] = {}
        for leg in option.get("legs") or []:
            name = leg.get("train")
            if not name:
                continue
            delay = leg.get("arrival_delay_minutes")
            if delay is None:
                delay = leg.get("departure_delay_minutes")
            delays[name] = round(delay or 0)
        return delays
    except db_api.DBServiceError:
        return {}
    except Exception:
        return {}


@app.get("/api/trips/{trip_id}/details")
def trip_details(trip_id: str, authorization: str | None = Header(default=None)) -> dict:
    """Journey details for one booked trip: legs, live delay, and risk forecast.

    The itinerary is simulated (ADR 0005), but the live delay is real: for trips
    booked via the journey search it is fetched from the db_service sidecar
    (``_live_leg_delays``); the demo trips fall back to the simulated
    ``LIVE_TRIP_STATUS``. The expected delay comes from ``journey_autopilot.risk``,
    scored from real historical DB punctuality data (see ``risk/delay_reference.py``).
    """
    user_id = _user_id(authorization)
    trip = next((t for t in store.get_trips(user_id) if t["trip_id"] == trip_id), None)
    if trip is None:
        raise HTTPException(status_code=404, detail="Trip not found.")

    legs = accounts.trip_journey(trip)
    live = mock_data.LIVE_TRIP_STATUS.get(trip_id)
    live_delays = _live_leg_delays(trip)
    for leg in legs:
        if live and live.get("train") == leg["train"]:
            leg["current_delay_minutes"] = live["current_delay_minutes"]
        else:
            leg["current_delay_minutes"] = live_delays.get(leg["train"], 0)
    for leg, forecast in zip(legs, risk.forecast_trip(trip, legs)):
        leg["forecast"] = forecast
    connection_warnings = risk.connection_risks(legs)

    return {
        "trip_id": trip_id,
        "legs": legs,
        "incidents": (live or {}).get("incidents", []),
        "connection_risk": " ".join(connection_warnings) or (live or {}).get("connection_risk"),
    }


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
    """

    d3 = date.today() + timedelta(days=12)
    return [date.today().isoformat(), d3.isoformat()]


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
    _OUTLOOK_AUTH.pop(user_id, None)

    try:
        from journey_autopilot.integrations.outlook import (
            create_device_credential,
            is_outlook_configured,
        )
    except ImportError:
        return {"mode": "simulated"}

    if not is_outlook_configured():
        return {"mode": "simulated"}

    auth_state: dict = {"thread": None, "result": None, "error": None, "device_code": None}
    _OUTLOOK_AUTH[user_id] = auth_state

    def prompt_callback(verification_uri: str, user_code: str, expires_on) -> None:
        _outlog.info("device code received — user_code=%s, verification_uri=%s", user_code, verification_uri)
        auth_state["device_code"] = {
            "user_code": user_code,
            "verification_uri": verification_uri,
            "expires_at": expires_on.isoformat() if hasattr(expires_on, "isoformat") else str(expires_on),
        }

    from journey_autopilot.integrations.outlook.auth import SCOPES

    def _run_device_flow() -> None:
        try:
            cred = create_device_credential(prompt_callback)
            auth_state["result"] = cred.get_token(*SCOPES)
            _outlog.info("device flow completed — token acquired")
        except Exception as exc:
            _outlog.error("device flow failed: %s", exc, exc_info=True)
            auth_state["error"] = str(exc)

    t = threading.Thread(target=_run_device_flow, daemon=True, name="outlook-device-flow")
    auth_state["thread"] = t
    t.start()

    # Wait briefly for prompt_callback to fire (before the blocking poll starts)
    t.join(timeout=5)

    dc = auth_state.get("device_code")
    if dc:
        return {"mode": "device_code", **dc}
    _outlog.warning("device code not received within 5s — returning pending")
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
    dc = auth_state.get("device_code") or {}

    if thread is not None and thread.is_alive():
        return {
            "status": "pending",
            "user_code": dc.get("user_code"),
            "verification_uri": dc.get("verification_uri"),
        }

    if auth_state.get("error"):
        _OUTLOOK_AUTH.pop(user_id, None)
        return {"status": "error", "error": auth_state["error"]}

    if auth_state.get("result"):
        # Wrap the just-acquired AccessToken in a StaticTokenCredential so the
        # Graph preview call doesn't re-enter MSAL — its silent-auth path is
        # unreliable right after a device flow and would trigger a second
        # interactive flow that hangs the request.
        access_token = auth_state["result"]
        _OUTLOOK_AUTH.pop(user_id, None)
        profile = store.update_profile(user_id, {"connections": {"outlook": True}})
        from journey_autopilot.integrations.outlook import StaticTokenCredential

        events = await _fetch_outlook_preview(
            user_id, credential=StaticTokenCredential(access_token)
        )
        _outlog.info("outlook connected — %d preview events", len(events) if events else 0)
        return {"status": "complete", "profile": profile, "events": events}

    _outlog.warning("device flow ended without a token — status=expired")
    _OUTLOOK_AUTH.pop(user_id, None)
    return {"status": "expired"}


async def _fetch_outlook_preview(
    user_id: str, credential: TokenCredential | None = None
) -> list[dict]:
    """Fetch real Outlook events for the pinned demo dates.

    Falls back to simulated events on any Graph error so the preview never
    breaks the onboarding flow.
    """
    try:
        from journey_autopilot.integrations.outlook import get_calendar_events_range

        dates = _outlook_preview_dates()
        return await get_calendar_events_range(dates[0], dates[-1], credential=credential)
    except Exception as exc:
        _outlog.warning("Graph preview failed (%s: %s) — using simulated events", type(exc).__name__, exc)
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
        from journey_autopilot.integrations.outlook import clear_token_cache

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


# --- Chat (runs the ReAct orchestrator, like scenarios/happy_path.py) --------------------------------


@app.post("/api/chat")
async def chat_endpoint(
    body: ChatRequest, authorization: str | None = Header(default=None)
) -> dict:
    """Drives the ReAct orchestrator from the chat UI.

    Clicking a trip opens a chat; each message is handed to ``root_agent``
    (the same orchestrator ``scenarios/happy_path.py`` uses). On the first message the
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
