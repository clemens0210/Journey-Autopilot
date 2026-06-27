"""Complaint drafts — passenger-rights claims prepared for the user to submit.

Auto-created when the orchestrator detects an eligible compensation case
(typically via ``get_passenger_rights`` in the chat trace). Status stays
``draft`` until the user submits manually from the Complaints screen.
"""

from __future__ import annotations

import secrets

from ..persistence import store


def bahncard_type(account: dict | None) -> str:
    """Map display BahnCard label to rights_service token."""
    label = (account or {}).get("bahncard", "").lower()
    if "100" in label:
        return "bc100"
    if "50" in label:
        return "bc50"
    if "25" in label:
        return "bc25"
    return "keine"


def _travel_date(trip: dict | None) -> str:
    if not trip:
        return ""
    dep = trip.get("planned_departure") or ""
    return dep[:10] if dep else ""


def build_complaint_payload(
    trip: dict | None,
    rights: dict,
    *,
    source: str = "auto_chat",
) -> dict:
    """Build the JSON blob stored in SQLite."""
    return {
        "status": "draft",
        "source": source,
        "trip_id": (trip or {}).get("trip_id"),
        "origin": (trip or {}).get("origin") or rights.get("origin") or "",
        "destination": (trip or {}).get("destination") or rights.get("destination") or "",
        "train": (trip or {}).get("train") or "",
        "travel_date": _travel_date(trip),
        "delay_minutes": int(rights.get("delay_minutes") or 0),
        "compensation_eur": float(rights.get("compensation_eur") or 0),
        "eligible": bool(rights.get("eligible")),
        "reason": rights.get("reason") or "",
        "claim_via": rights.get("claim_via") or "",
        "notes": list(rights.get("notes") or []),
        "submitted_at": None,
    }


def maybe_create_from_trace(
    user_id: str,
    trip: dict | None,
    trace: list[dict],
    account: dict | None,
) -> dict | None:
    """Create a draft complaint if the chat trace includes eligible passenger rights."""
    rights: dict | None = None
    for item in trace:
        if item.get("kind") != "result" or item.get("name") != "get_passenger_rights":
            continue
        data = item.get("data") or {}
        if data.get("eligible") and float(data.get("compensation_eur") or 0) > 0:
            rights = data
            break
    if rights is None:
        return None
    return create_draft_complaint(user_id, trip, rights, source="auto_chat")


def create_draft_complaint(
    user_id: str,
    trip: dict | None,
    rights: dict,
    *,
    source: str = "auto_chat",
) -> dict | None:
    """Persist a draft; returns None if an open draft already exists for this trip/date."""
    payload = build_complaint_payload(trip, rights, source=source)
    if not payload["eligible"] or payload["compensation_eur"] <= 0:
        return None

    trip_id = payload.get("trip_id")
    travel_date = payload.get("travel_date") or ""
    if trip_id and travel_date:
        existing = store.find_open_complaint(user_id, trip_id, travel_date)
        if existing:
            return None

    complaint_id = f"cmp-{secrets.token_urlsafe(9)}"
    return store.create_complaint(user_id, complaint_id, payload)


def submit_complaint(user_id: str, complaint_id: str) -> dict | None:
    """Mark a draft as submitted (simulated filing)."""
    current = store.get_complaint(user_id, complaint_id)
    if current is None:
        return None
    if current.get("status") != "draft":
        return current
    return store.update_complaint(user_id, complaint_id, {"status": "submitted"})


def reject_complaint(user_id: str, complaint_id: str) -> dict | None:
    """Discard a draft the user does not want to pursue."""
    current = store.get_complaint(user_id, complaint_id)
    if current is None:
        return None
    if current.get("status") != "draft":
        return current
    return store.update_complaint(user_id, complaint_id, {"status": "rejected"})
