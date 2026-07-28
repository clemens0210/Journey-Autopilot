"""The stored profile: read, partial patch, onboarding completion, GDPR delete."""

from __future__ import annotations

from fastapi import APIRouter, Header

from journey_autopilot.persistence import store

from .connect import forget_pending_phone
from .deps import current_user_id, drop_sessions

router = APIRouter(tags=["profile"])


@router.get("/api/profile")
def get_profile(authorization: str | None = Header(default=None)) -> dict:
    user_id = current_user_id(authorization)
    return {"profile": store.get_profile(user_id)}


@router.put("/api/profile")
def put_profile(patch: dict, authorization: str | None = Header(default=None)) -> dict:
    """Partial patch: the UI sends only the changed fields per onboarding step."""
    user_id = current_user_id(authorization)
    # Only change connection/verification status via the dedicated endpoints.
    patch.pop("connections", None)
    if isinstance(patch.get("notifications"), dict):
        patch["notifications"].pop("phone_verified", None)
    return {"profile": store.update_profile(user_id, patch)}


@router.post("/api/onboarding/complete")
def complete_onboarding(authorization: str | None = Header(default=None)) -> dict:
    user_id = current_user_id(authorization)
    return {"profile": store.update_profile(user_id, {"onboarding_completed": True})}


@router.delete("/api/profile")
def delete_profile(authorization: str | None = Header(default=None)) -> dict:
    """GDPR deletion: completely remove account, profile, and imported trips."""
    user_id = current_user_id(authorization)
    store.delete_user(user_id)
    forget_pending_phone(user_id)
    drop_sessions(user_id)
    return {"deleted": True}
