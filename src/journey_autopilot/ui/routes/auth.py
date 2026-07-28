"""DB account login and the session bootstrap the browser reads on reload."""

from __future__ import annotations

import os
import re

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel

from journey_autopilot.onboarding import accounts
from journey_autopilot.persistence import store

from .chat import chat_bootstrap
from .deps import create_session, current_user_id

router = APIRouter(tags=["auth"])


class LoginRequest(BaseModel):
    email: str
    password: str


@router.post("/api/auth/db-login")
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

    return {
        "token": create_session(account["user_id"]),
        "account": account,
        "trips": trips,
        "profile": profile,
        **chat_bootstrap(account["user_id"]),
    }


@router.get("/api/me")
def me(authorization: str | None = Header(default=None)) -> dict:
    user_id = current_user_id(authorization)
    return {
        "account": store.get_account(user_id),
        "profile": store.get_profile(user_id),
        "trips": store.get_trips(user_id),
        **chat_bootstrap(user_id),
    }
