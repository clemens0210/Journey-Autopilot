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
        "legal_context": rights.get("legal_context") or "",
        # Whether ``legal_context`` above is the English translation or the raw
        # German bahn.de quote. Set to True by create_draft_complaint after a
        # successful translation; left False here so the UI can label a
        # German-quote fallback honestly.
        "legal_context_translated": False,
        "legal_sources": list(rights.get("legal_sources") or []),
        "submitted_at": None,
    }


def maybe_create_from_rights(
    user_id: str, trip: dict | None, rights: dict | None
) -> dict | None:
    """Create a draft complaint from a turn's settled rights lookup, if eligible.

    ``rights`` comes from the caller (``ui.chat.chat_turn``) rather than being
    fetched here: ``get_passenger_rights`` runs inside the Planner sub-agent,
    which is only reachable via an ``AgentTool`` — ADK forwards just the
    Planner's final merged text to the parent, never its own tool-call results
    — so the chat layer picks it out of the turn workspace and hands it over.

    Only a ``trip_concluded=True`` lookup reaches that workspace at all, so a
    mid-trip entitlement check (Zugbindung and friends) can never seed a draft.
    ``is_trip_completed`` below still re-checks the live status independently —
    two guards, because a claim for a trip that has not happened is the one
    mistake here that reaches a real DB process.
    """
    if rights is None:
        return None
    if not rights.get("eligible") or float(rights.get("compensation_eur") or 0) <= 0:
        return None
    return create_draft_complaint(user_id, trip, rights, source="auto_chat")


def is_trip_completed(trip: dict | None) -> bool:
    """Whether the trip has actually arrived — conservative by default.

    A complaint may only be drafted once the trip is over, not while it's
    still en route (e.g. compensation figures the Planner cites for a
    candidate reroute mid-disruption are estimates, not a filed claim).
    ``arrived: true`` in the live status counts as completed — set explicitly
    by the fixture (``scripts/simulate_arrival.py``), derived from the live
    arrival time, or derived from the scripted delay's ETA having passed (see
    ``read_tools.get_live_trip_status``). Absent that flag the trip is
    treated as still ongoing.
    """
    trip_id = (trip or {}).get("trip_id")
    if not trip_id:
        return False
    from ..tools.read_tools import get_live_trip_status

    status = get_live_trip_status(trip_id)
    return bool(status.get("arrived"))


def create_draft_complaint(
    user_id: str,
    trip: dict | None,
    rights: dict,
    *,
    source: str = "auto_chat",
) -> dict | None:
    """Persist a draft; returns None if not eligible, the trip isn't over yet,
    or an open draft already exists for this trip/date."""
    payload = build_complaint_payload(trip, rights, source=source)
    if not payload["eligible"] or payload["compensation_eur"] <= 0:
        return None
    if not is_trip_completed(trip):
        return None

    trip_id = payload.get("trip_id")
    travel_date = payload.get("travel_date") or ""
    if trip_id and travel_date:
        existing = store.find_open_complaint(user_id, trip_id, travel_date)
        if existing:
            return None

    # Only now — for a draft we are actually about to persist — render the
    # German bahn.de legal context in English. Doing it here (not in
    # get_passenger_rights) keeps the LLM call off the agent's hot path: it
    # runs once per stored complaint, not on every rights check. Best-effort:
    # the German original is kept if translation is unavailable.
    if payload.get("legal_context"):
        from ..integrations.rights_rag.translate import translate_to_english

        english, translated = translate_to_english(payload["legal_context"])
        payload["legal_context"] = english
        payload["legal_context_translated"] = translated

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
