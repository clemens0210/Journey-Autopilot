"""Booking flow: station lookup, live journey search, and adding a connection.

The three steps of one user action, which is why ``POST /api/trips`` lives here
rather than next to the other ``/api/trips`` routes in ``trips.py`` — it is the
end of the search, not a trip-management operation.

Station lookup falls back to a static list; the journey search deliberately
does not (see the note above ``journeys_search``).
"""

from __future__ import annotations

import hashlib
import secrets

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel

from ...demo import accounts
from ...integrations.db import ops as db_api
from ...integrations.db.stations import resolve_eva_or_id
from ...persistence import store
from .deps import current_user_id

router = APIRouter(tags=["booking"])


class BookTripRequest(BaseModel):
    journey: dict
    purpose: str | None = None


@router.get("/api/stations")
def stations(query: str = "") -> dict:
    """Station autocomplete — real DB data via the sidecar, static list without it."""
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


def _journey_to_trip(journey: dict, purpose: str | None = None) -> dict:
    """Convert a normalized db.ops journey option into the booked-trip shape.

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


@router.get("/api/journeys")
def search_journeys(
    from_id: str,
    to_id: str,
    departure: str | None = None,
    authorization: str | None = Header(default=None),
) -> dict:
    """Live journey search between two stations (EVA ids) via the db_service sidecar."""
    current_user_id(authorization)
    try:
        payload = db_api.journeys(from_id, to_id, departure=departure, results=6)
    except db_api.DBServiceError as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Journey search needs the db_service sidecar (see db_service/README.md). {exc}",
        )
    return {"journeys": db_api.normalize_journeys(payload)}


@router.post("/api/trips")
def book_trip(body: BookTripRequest, authorization: str | None = Header(default=None)) -> dict:
    """Add a searched journey to the monitored trips (simulated booking)."""
    user_id = current_user_id(authorization)
    trip = _journey_to_trip(body.journey, body.purpose)
    store.save_trips(user_id, [trip])
    return {"trip": trip, "trips": store.get_trips(user_id)}


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


@router.get("/api/journeys/search")
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
    current_user_id(authorization)

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
