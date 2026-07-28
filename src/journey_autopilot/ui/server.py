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
import os
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
from journey_autopilot.onboarding import accounts, complaints
from journey_autopilot.persistence import store
from journey_autopilot.integrations import db_ops as db_api
from journey_autopilot.integrations.stations import resolve_eva_or_id
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

# Surface this package's INFO logs in the terminal — e.g. the HIGH-risk
# disruption alert emitted by integrations/whatsapp.py and ui/chat.py. uvicorn
# only configures its own loggers, so without this our INFO lines are swallowed.
# (Mirrors integrations/whatsapp_webhook.py, which runs under uvicorn too.)
logging.getLogger("journey_autopilot").setLevel(logging.INFO)
if not logging.getLogger().handlers:
    logging.basicConfig(level=logging.INFO)

app = FastAPI(title="Journey Autopilot — Web App", version="0.1.0")


@app.on_event("startup")
def _warm_rights_rag() -> None:
    """Pre-load the passenger-rights embedding model in the background.

    The first ``get_passenger_rights`` tool call otherwise blocks the first
    chat answer while the ~1 GB sentence-transformers model loads. Warming it
    here — while the user is still in onboarding/dashboard — hides that
    latency; on failure the chat simply falls back to lazy loading.
    """

    def _load() -> None:
        try:
            from journey_autopilot.integrations.rights_rag.rag_store import (
                FahrgastrechteRAG,
            )
            from journey_autopilot.tools import read_tools

            read_tools.get_passenger_rights._rag = FahrgastrechteRAG()
        except Exception:
            pass

    threading.Thread(target=_load, name="rights-rag-warmup", daemon=True).start()


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

# Demo chats warmed ahead of a presentation: user_id -> [turn result + trip].
# Deliberately in memory, next to the ADK sessions they point at: the whole
# point is to survive a `reset_demo.py` DB wipe (so onboarding can be shown
# from scratch while the expensive first chat turn is already done) while
# dying with the server, since a restart kills the InMemoryRunner sessions
# these transcripts reference. Populated by POST /api/demo/preload.
_PRELOADED_CHATS: dict[str, list[dict]] = {}


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
    proposal_id: str | None = None
    selected_option_id: str | None = None


class PreloadRequest(BaseModel):
    trip_ids: list[str] | None = None   # None/empty => every imported trip
    message: str = ""                   # falls back to DEMO_MONITOR_PROMPT
    notify: bool = False                # send the proactive WhatsApp during preload


class ComplaintPatchRequest(BaseModel):
    status: str


class BookTripRequest(BaseModel):
    journey: dict
    purpose: str | None = None


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
    # Pre-fill (NOT verify) the traveler's phone number from the env — the
    # wizard's phone step then starts with the presenter's real number typed
    # in and only the code confirmation remains. Same variable the WhatsApp
    # demo uses (DEMO_TRAVELER_NUMBER); the .env.example placeholder
    # ("+49171xxxxxxx") fails the digit check and is ignored.
    demo_phone = re.sub(r"[^\d+]", "", os.getenv("DEMO_TRAVELER_NUMBER") or "")
    if re.fullmatch(r"\+?\d{8,15}", demo_phone):
        existing = store.get_profile(account["user_id"]) or {}
        if not (existing.get("notifications") or {}).get("phone"):
            store.update_profile(
                account["user_id"], {"notifications": {"phone": demo_phone}}
            )
    imported = accounts.booked_trips(account["user_id"])
    store.save_trips(account["user_id"], imported)
    # Re-importing owns the DB-account bookings ("DB-…" ids): drop imports from
    # earlier logins that are no longer in the account — their demo ids are
    # date-relative, so each day's login would otherwise pile up stale copies.
    # Locally booked monitors ("BK-…" ids) are never touched.
    fresh_ids = {t["trip_id"] for t in imported}
    stale = [
        t["trip_id"]
        for t in store.get_trips(account["user_id"])
        if t["trip_id"].startswith("DB-") and t["trip_id"] not in fresh_ids
    ]
    store.delete_trips(account["user_id"], stale)
    # Return the full stored list — imported demo trips AND locally booked
    # connections. Returning only the fresh import made booked trips vanish
    # from the UI after every re-login (until the next booking refreshed the
    # list from the store).
    trips = store.get_trips(account["user_id"])
    profile = store.update_profile(account["user_id"], {"connections": {"db_account": True}})

    token = secrets.token_urlsafe(24)
    _SESSIONS[token] = account["user_id"]
    return {
        "token": token,
        "account": account,
        "trips": trips,
        "profile": profile,
        **_chat_bootstrap(account["user_id"]),
    }


def _chat_bootstrap(user_id: str) -> dict:
    """Chat bookkeeping the browser needs before it can render conversations.

    ``preloaded_chats`` hands over any conversations warmed by
    scripts/preload_demo_chats.py. Returned by both /api/me and the login
    response, because a fresh demo tab logs in rather than booting with an
    existing token.
    """
    return {
        "complaints": store.get_complaints(user_id),
        "preloaded_chats": _PRELOADED_CHATS.get(user_id, []),
    }


@app.get("/api/me")
def me(authorization: str | None = Header(default=None)) -> dict:
    user_id = _user_id(authorization)
    return {
        "account": store.get_account(user_id),
        "profile": store.get_profile(user_id),
        "trips": store.get_trips(user_id),
        **_chat_bootstrap(user_id),
    }


@app.get("/api/trips")
def trips(authorization: str | None = Header(default=None)) -> dict:
    user_id = _user_id(authorization)
    return {"trips": store.get_trips(user_id)}


@app.get("/api/complaints")
def list_complaints(authorization: str | None = Header(default=None)) -> dict:
    user_id = _user_id(authorization)
    return {"complaints": store.get_complaints(user_id)}


@app.patch("/api/complaints/{complaint_id}")
def patch_complaint(
    complaint_id: str,
    body: ComplaintPatchRequest,
    authorization: str | None = Header(default=None),
) -> dict:
    user_id = _user_id(authorization)
    if body.status == "submitted":
        updated = complaints.submit_complaint(user_id, complaint_id)
    elif body.status == "rejected":
        updated = complaints.reject_complaint(user_id, complaint_id)
    else:
        raise HTTPException(status_code=422, detail="Unsupported status change.")
    if updated is None:
        raise HTTPException(status_code=404, detail="Complaint not found.")
    return {"complaint": updated}


# --- Booking (simple journey search via db_service, adds to monitored trips) ---


def _journey_to_trip(journey: dict, purpose: str | None = None) -> dict:
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
        "purpose": purpose or "Booked connection",
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
    trip = _journey_to_trip(body.journey, body.purpose)
    store.save_trips(user_id, [trip])
    return {"trip": trip, "trips": store.get_trips(user_id)}


@app.delete("/api/trips/{trip_id}")
def delete_trip(trip_id: str, authorization: str | None = Header(default=None)) -> dict:
    """Remove a single imported/added trip (does not touch the rest of the profile)."""
    user_id = _user_id(authorization)
    store.delete_trip(user_id, trip_id)
    return {"deleted": True, "trips": store.get_trips(user_id)}


def _live_leg_delays(trip: dict) -> dict[str, int]:
    """Best-effort live arrival delay per train from the db_service sidecar.

    Trips booked via the journey search (``BK-…`` ids) are not in the simulated
    ``LIVE_TRIP_STATUS``, so their live delay has to come from real DB data.
    Re-runs the connection search, matches the trip's exact itinerary (full
    train sequence + planned departure, see ``db_ops.match_booked_journey``),
    and returns ``{train_name: delay_minutes}``. Returns ``{}`` on any sidecar
    miss or when the exact booked connection is not found — never the delays
    of a different journey.
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
        option = db_api.match_booked_journey(trip, db_api.normalize_journeys(payload))
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
    # Warnings on this screen must reflect what is ACTUALLY happening: only
    # live delays can put a transfer at risk here. The historical variant
    # (risk.connection_risks) produced speculative "you may miss ..." warnings
    # on punctual days — those read as wrong warnings and are kept out of the
    # trip view (the per-leg "Expected" chip still shows the forecast).
    connection_warnings = risk.live_connection_risks(legs)

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
    # Deliver the code to the actual number via Twilio WhatsApp (degrades to a
    # demo no-op if Twilio isn't configured), AND still return it so the on-screen
    # demo display keeps working.
    from journey_autopilot.integrations import whatsapp

    delivery = whatsapp.send_verification_code(phone, code)
    return {"sent": True, "phone": phone, "demo_code": code, "delivery": delivery}


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
    """Date range for the post-connect preview.

    Spans the next two weeks of the user's REAL connected calendar (the
    range start/end are what ``_fetch_outlook_preview`` queries), so the
    preview reflects the actual signed-in account rather than pinned demo days.
    """
    today = date.today()
    return [today.isoformat(), (today + timedelta(days=14)).isoformat()]


@app.post("/api/connect/outlook/start")
def outlook_start(authorization: str | None = Header(default=None)) -> dict:
    """Begin the Outlook connection flow.

    If MS_ENTRA_CLIENT_ID is configured, first tries the cached login from an
    earlier session silently (``{"mode": "cached"}`` — the frontend then polls
    ``/status`` which completes immediately). Otherwise starts the real
    device-code flow in a background thread and returns the user code +
    verification URL for the browser to display; the frontend polls
    ``/api/connect/outlook/status``.

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

    from journey_autopilot.integrations.outlook.auth import (
        MAIL_SCOPES,
        SCOPES,
        acquire_credential,
        save_authentication_record,
    )

    # A cached Microsoft login from an earlier session (a previous onboarding
    # run or ``python scripts/check_outlook.py --login``) makes the connect
    # instant: acquire the token silently and hand it straight to the /status
    # completion path — no device-code round trip through microsoft.com. This
    # is what lets a live demo pre-authorize Outlook before the wizard starts.
    try:
        token = acquire_credential().get_token(*SCOPES)  # silent or raises
        _OUTLOOK_AUTH[user_id] = {
            "thread": None, "result": token, "error": None, "device_code": None,
        }
        _outlog.info("cached login reused — connected silently, no device flow")
        return {"mode": "cached"}
    except Exception as exc:
        _outlog.info("no usable cached login (%s: %s) — starting device flow", type(exc).__name__, exc)

    auth_state: dict = {"thread": None, "result": None, "error": None, "device_code": None}
    _OUTLOOK_AUTH[user_id] = auth_state

    def prompt_callback(verification_uri: str, user_code: str, expires_on) -> None:
        _outlog.info("device code received — user_code=%s, verification_uri=%s", user_code, verification_uri)
        auth_state["device_code"] = {
            "user_code": user_code,
            "verification_uri": verification_uri,
            "expires_at": expires_on.isoformat() if hasattr(expires_on, "isoformat") else str(expires_on),
        }

    # AADSTS codes that mean the Mail.Send scope could not be consented (app
    # registration lacks the permission / admin consent withheld). Calendar
    # connect must not break on those — fall back to the calendar-only scopes.
    _CONSENT_ERROR_CODES = ("AADSTS65001", "AADSTS70011", "AADSTS650053")

    def _run_device_flow() -> None:
        try:
            cred = create_device_credential(prompt_callback)
            # authenticate() runs the device flow AND returns the
            # AuthenticationRecord — the account metadata a fresh credential
            # needs to find the cached token later. Persisting it is what lets
            # the agent tools (acquire_credential) read the calendar silently
            # instead of falling back to mock with AuthenticationRequiredError.
            # MAIL_SCOPES (calendar + Mail.Send) so one consent covers both
            # reading the calendar and sending the approved notice email.
            try:
                record = cred.authenticate(scopes=MAIL_SCOPES)
            except Exception as exc:
                if not any(code in str(exc) for code in _CONSENT_ERROR_CODES):
                    raise
                _outlog.warning(
                    "Mail.Send consent unavailable (%s) — retrying with "
                    "calendar-only scopes; the notice-email send will stay "
                    "disabled until the app registration adds Mail.Send.",
                    exc,
                )
                record = cred.authenticate(scopes=SCOPES)
            save_authentication_record(record)
            # Same instance, record in memory -> silent; token for the preview.
            auth_state["result"] = cred.get_token(*SCOPES)
            _outlog.info("device flow completed — token acquired, auth record saved")
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
        from journey_autopilot.integrations.outlook import (
            StaticTokenCredential,
            get_signed_in_user,
        )

        credential = StaticTokenCredential(access_token)

        # Identify the ACTUAL signed-in Microsoft account (not the demo email)
        # and persist it, so the UI and later calendar reads use the real one.
        identity = {"email": None, "name": None}
        try:
            identity = await get_signed_in_user(credential=credential)
        except Exception as exc:
            _outlog.warning(
                "could not read signed-in user (%s: %s)", type(exc).__name__, exc
            )

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

        events = await _fetch_outlook_preview(user_id, credential=credential)
        _outlog.info(
            "outlook connected as %s — %d preview events",
            identity.get("email") or "<unknown>",
            len(events) if events else 0,
        )
        return {
            "status": "complete",
            "profile": profile,
            "events": events,
            "account": identity,
        }

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


# --- Journey search (live DB data via sidecar) --------------------------------
# NOTE: intentionally live-only — no mock fallback. Unlike the station lookup
# above (which falls back to a static list) and the agent tools (which fall
# back to mock_data and tag every result with `source`), there is no realistic
# mock for an arbitrary origin/destination journey search. When the sidecar is
# down we return an empty result set with source "unavailable" (HTTP 200) so
# the UI shows "Search unavailable" instead of crashing. This is a deliberate,
# documented exception to the AGENTS.md live-then-mock-fallback contract: the
# search endpoint is UI-only (not an LLM tool), so the Orchestrator's "disclose
# mock_* sources" rule doesn't apply.


@app.get("/api/journeys/search")
def journeys_search(
    origin: str = "",
    destination: str = "",
    date: str = "",
    time: str = "08:00",
    authorization: str | None = Header(default=None),
) -> dict:
    """Search live DB journeys between two stations.

    ``origin`` / ``destination`` accept either a station name or an EVA (all-digit
    id, passed straight through from the autocomplete). ``date`` is ``YYYY-MM-DD``;
    ``time`` defaults to 08:00 and is combined into the ISO datetime db_api wants.

    Returns ``{"results": [...], "source": "db-live"}`` on success, or
    ``{"results": [], "source": "unavailable", "message": ...}`` (HTTP 200)
    when a station can't be resolved, the sidecar is down, or DB temporarily
    rejects the upstream request.
    """
    _user_id(authorization)

    origin = (origin or "").strip()
    destination = (destination or "").strip()
    date = (date or "").strip()
    time = (time or "08:00").strip()
    if not origin or not destination or not date:
        return {
            "results": [],
            "source": "unavailable",
            "message": "Please fill in origin, destination and date.",
        }

    from_eva = resolve_eva_or_id(origin)
    to_eva = resolve_eva_or_id(destination)
    if from_eva is None or to_eva is None:
        return {
            "results": [],
            "source": "unavailable",
            "message": "Station lookup failed. Pick a station from the suggestions or try a major Hbf.",
        }

    try:
        payload = db_api.journeys(
            from_eva,
            to_eva,
            departure=f"{date}T{time}:00",
            results=6,
            tickets=True,
        )
        results = db_api.normalize_journeys(payload)
        return {"results": results, "source": "db-live"}
    except db_api.DBServiceError:
        return {"results": [], "source": "unavailable"}


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
    user_id = _user_id(authorization)  # chat is behind the login like the rest of the API
    # The traveler's saved number — used for the proactive HIGH-risk WhatsApp
    # alert. May be None if the phone step was skipped during onboarding.
    profile = store.get_profile(user_id) or {}
    notify_phone = (profile.get("notifications") or {}).get("phone")
    try:
        account = store.get_account(user_id)
        result = await chat.chat_turn(
            body.session_id,
            body.message,
            body.trip,
            account,
            notify_phone=notify_phone,
            user_id=user_id,
            proposal_id=body.proposal_id,
            selected_option_id=body.selected_option_id,
        )
        return result
    except Exception as exc:  # surfaced inline in the chat instead of a 500
        return {
            "session_id": body.session_id,
            "reply": None,
            "trace": [],
            "error": f"{type(exc).__name__}: {exc}",
        }


# --- Demo preload -------------------------------------------------------------------


# Kept in sync with MONITOR_PROMPT in ui/static/app.js, which sends the same
# text when a trip chat is opened live. A drift between the two only changes
# the wording of the preloaded first turn, never correctness.
DEMO_MONITOR_PROMPT = (
    "Monitor my trip: check the live status and current disruption risk, "
    "and tell me if I need to do anything."
)


@app.post("/api/demo/preload")
async def preload_demo_chats_endpoint(
    body: PreloadRequest, authorization: str | None = Header(default=None)
) -> dict:
    """Warm the expensive first chat turn for one or more trips, ahead of a demo.

    Runs exactly what opening a trip chat would run, then parks the result in
    ``_PRELOADED_CHATS`` so the browser can adopt the finished conversation
    instead of waiting on the orchestrator during a presentation. The ADK
    session created here stays live in this process, so the chat can be
    *continued* — the agent keeps its memory for the rest of the server's life.

    Driven by ``scripts/preload_demo_chats.py``; see that script for the demo
    preparation sequence.
    """
    user_id = _user_id(authorization)
    account = store.get_account(user_id)
    profile = store.get_profile(user_id) or {}
    # The proactive WhatsApp alert is opt-in here: preloading happens *before*
    # the presentation, so firing it by default would send the notice to the
    # presenter's phone during setup and spend the demo moment early.
    notify_phone = (profile.get("notifications") or {}).get("phone") if body.notify else None

    wanted = set(body.trip_ids or [])
    selected = [t for t in store.get_trips(user_id) if not wanted or t["trip_id"] in wanted]
    missing = sorted(wanted - {t["trip_id"] for t in selected})
    if not selected:
        raise HTTPException(
            status_code=400,
            detail=f"No matching trips to preload (unknown ids: {missing})." if missing
            else "No trips imported for this account yet.",
        )

    preloaded: list[dict] = []
    failures: list[dict] = []
    for trip in selected:
        try:
            result = await chat.chat_turn(
                None,  # fresh ADK session, so chat_turn seeds the trip context
                body.message or DEMO_MONITOR_PROMPT,
                trip,
                account,
                notify_phone=notify_phone,
                user_id=user_id,
            )
        except Exception as exc:
            failures.append({"trip_id": trip["trip_id"], "error": f"{type(exc).__name__}: {exc}"})
            continue
        result["trip"] = trip
        result["trip_id"] = trip["trip_id"]
        preloaded.append(result)

    # Replace only the trips just warmed, so preloading one trip doesn't discard
    # a conversation warmed by an earlier run.
    fresh_ids = {entry["trip_id"] for entry in preloaded}
    kept = [e for e in _PRELOADED_CHATS.get(user_id, []) if e["trip_id"] not in fresh_ids]
    _PRELOADED_CHATS[user_id] = kept + preloaded

    return {
        "preloaded": [
            {
                "trip_id": entry["trip_id"],
                "session_id": entry.get("session_id"),
                "risk_band": entry.get("risk_band"),
                "reply_chars": len(entry.get("reply") or ""),
                "options": len(entry.get("options") or []),
                "complaint_created": bool(entry.get("complaint_created")),
            }
            for entry in preloaded
        ],
        "failures": failures,
        "total_cached": len(_PRELOADED_CHATS[user_id]),
    }


# --- Static UI ----------------------------------------------------------------------------


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    return FileResponse(_STATIC / "index.html")


app.mount("/static", StaticFiles(directory=_STATIC), name="static")
