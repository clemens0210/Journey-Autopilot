"""Onboarding-Server — FastAPI-App mit JSON-API und DB-Navigator-Style-UI.

Starten:
    python run_onboarding.py            # http://127.0.0.1:8000
    # oder: uvicorn onboarding.server:app --reload

Was hier simuliert ist (und warum) steht im Context Record: DB bietet keine
offizielle API für Konto-Login / Ticket-Import, Microsoft-OAuth und SMS-Versand
brauchen registrierte Apps bzw. einen Gateway-Vertrag. Die Flows sind deshalb
mit echter UX, aber simulierten Backends gebaut — die API-Verträge entsprechen
dem, was eine echte Anbindung liefern müsste.

Live-Daten: Die Heimatbahnhof-Suche (`/api/stations`) nutzt den db_service-
Sidecar (echte DB-Stationsdaten) und fällt ohne ihn auf eine statische Liste
großer Bahnhöfe zurück.
"""

from __future__ import annotations

import os
import re
import secrets
import random

import requests
from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pathlib import Path
from pydantic import BaseModel

from . import accounts, store

app = FastAPI(title="Journey Autopilot — Onboarding", version="0.1.0")

_STATIC = Path(__file__).resolve().parent / "static"

DB_API_URL = os.getenv("DB_API_URL", "http://127.0.0.1:3000").rstrip("/")

# Sessions in-memory: Token -> user_id. Für den Single-User-Prototyp bewusst
# ohne Persistenz; ein Neustart heißt einfach "neu einloggen".
_SESSIONS: dict[str, str] = {}

# Offene SMS-Verifizierungen: user_id -> (phone, code)
_PENDING_PHONE: dict[str, tuple[str, str]] = {}


# --- Request-Modelle ---------------------------------------------------------------


class LoginRequest(BaseModel):
    email: str
    password: str


class PhoneStartRequest(BaseModel):
    phone: str


class PhoneConfirmRequest(BaseModel):
    code: str


class OutlookConnectRequest(BaseModel):
    consent: bool = False


# --- Auth-Helfer ---------------------------------------------------------------------


def _user_id(authorization: str | None) -> str:
    """Löst den Bearer-Token zur user_id auf, sonst 401."""
    if authorization and authorization.startswith("Bearer "):
        token = authorization.removeprefix("Bearer ")
        user_id = _SESSIONS.get(token)
        if user_id:
            return user_id
    raise HTTPException(status_code=401, detail="Nicht angemeldet.")


# --- DB-Konto: Login & Trip-Import -----------------------------------------------------


@app.post("/api/auth/db-login")
def db_login(body: LoginRequest) -> dict:
    """Simulierter bahn.de-Login. Importiert direkt die gebuchten Reisen."""
    account = accounts.authenticate(body.email, body.password)
    if account is None:
        raise HTTPException(
            status_code=401,
            detail="E-Mail oder Passwort falsch. Demo-Zugang: lucas.wild@example.com / demo123",
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


# --- Mobilnummer: SMS-Verifizierung (simuliert) ------------------------------------------


@app.post("/api/verify/phone/start")
def phone_start(body: PhoneStartRequest, authorization: str | None = Header(default=None)) -> dict:
    user_id = _user_id(authorization)
    phone = re.sub(r"[^\d+]", "", body.phone)
    if not re.fullmatch(r"\+?\d{8,15}", phone):
        raise HTTPException(status_code=422, detail="Bitte eine gültige Mobilnummer angeben (z. B. +49 151 12345678).")

    code = f"{random.randint(0, 9999):04d}"
    _PENDING_PHONE[user_id] = (phone, code)
    # Simulierter Versand: Im echten System ginge der Code per SMS-Gateway raus.
    # Im Demo-Modus liefern wir ihn zurück, damit der Flow vorführbar bleibt.
    return {"sent": True, "phone": phone, "demo_code": code}


@app.post("/api/verify/phone/confirm")
def phone_confirm(body: PhoneConfirmRequest, authorization: str | None = Header(default=None)) -> dict:
    user_id = _user_id(authorization)
    pending = _PENDING_PHONE.get(user_id)
    if pending is None:
        raise HTTPException(status_code=409, detail="Kein Code angefordert.")
    phone, code = pending
    if body.code.strip() != code:
        raise HTTPException(status_code=422, detail="Der Code stimmt nicht. Bitte erneut versuchen.")

    del _PENDING_PHONE[user_id]
    profile = store.update_profile(
        user_id, {"notifications": {"phone": phone, "phone_verified": True}}
    )
    return {"verified": True, "profile": profile}


# --- Outlook-Kalender (simulierter OAuth-Consent) ------------------------------------------


@app.post("/api/connect/outlook")
def connect_outlook(body: OutlookConnectRequest, authorization: str | None = Header(default=None)) -> dict:
    user_id = _user_id(authorization)
    if not body.consent:
        raise HTTPException(status_code=422, detail="Zustimmung erforderlich.")
    profile = store.update_profile(user_id, {"connections": {"outlook": True}})
    return {"connected": True, "events": accounts.outlook_events(user_id), "profile": profile}


@app.delete("/api/connect/outlook")
def disconnect_outlook(authorization: str | None = Header(default=None)) -> dict:
    user_id = _user_id(authorization)
    profile = store.update_profile(user_id, {"connections": {"outlook": False}})
    return {"connected": False, "profile": profile}


# --- Profil ------------------------------------------------------------------------------


@app.get("/api/profile")
def get_profile(authorization: str | None = Header(default=None)) -> dict:
    user_id = _user_id(authorization)
    return {"profile": store.get_profile(user_id)}


@app.put("/api/profile")
def put_profile(patch: dict, authorization: str | None = Header(default=None)) -> dict:
    """Teil-Patch: Die UI schickt pro Onboarding-Schritt nur die geänderten Felder."""
    user_id = _user_id(authorization)
    # Verbindungs-/Verifizierungs-Status nur über die dedizierten Endpunkte ändern.
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
    """DSGVO-Löschung: Konto, Profil und importierte Reisen restlos entfernen."""
    user_id = _user_id(authorization)
    store.delete_user(user_id)
    _PENDING_PHONE.pop(user_id, None)
    for token, uid in list(_SESSIONS.items()):
        if uid == user_id:
            del _SESSIONS[token]
    return {"deleted": True}


# --- Stationssuche (echte DB-Daten via Sidecar, mit Fallback) --------------------------------


@app.get("/api/stations")
def stations(query: str = "") -> dict:
    query = query.strip()
    if len(query) < 2:
        return {"stations": [], "source": "none"}
    try:
        resp = requests.get(
            f"{DB_API_URL}/locations", params={"query": query, "results": 6}, timeout=4
        )
        resp.raise_for_status()
        hits = [
            {"id": str(item["id"]), "name": item["name"]}
            for item in resp.json()
            if item.get("type") in ("stop", "station") and item.get("id")
        ]
        return {"stations": hits, "source": "db-live"}
    except requests.RequestException:
        needle = query.lower()
        hits = [s for s in accounts.FALLBACK_STATIONS if needle in s["name"].lower()]
        return {"stations": hits[:6], "source": "fallback"}


# --- Statische UI ----------------------------------------------------------------------------


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    return FileResponse(_STATIC / "index.html")


app.mount("/static", StaticFiles(directory=_STATIC), name="static")
