"""Monitored trips: the list, one trip's itinerary, and the complaints on them.

Complaints live here rather than in their own router because they are always
*about* a trip — a compensation claim is the tail end of a journey that went
wrong, and the dashboard renders both from the same screen.
"""

from __future__ import annotations

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel

from ... import risk
from ...demo import accounts, mock_data
from ...integrations.db import ops as db_api
from ...integrations.db import stations as db_api_stations
from ...onboarding import complaints
from ...persistence import store
from .deps import current_user_id

router = APIRouter(tags=["trips"])


class ComplaintPatchRequest(BaseModel):
    status: str


@router.get("/api/trips")
def trips(authorization: str | None = Header(default=None)) -> dict:
    user_id = current_user_id(authorization)
    return {"trips": store.get_trips(user_id)}


@router.delete("/api/trips/{trip_id}")
def delete_trip(trip_id: str, authorization: str | None = Header(default=None)) -> dict:
    """Remove a single imported/added trip (does not touch the rest of the profile)."""
    user_id = current_user_id(authorization)
    store.delete_trip(user_id, trip_id)
    return {"deleted": True, "trips": store.get_trips(user_id)}


def _live_leg_delays(trip: dict) -> dict[str, int]:
    """Best-effort live arrival delay per train from the db_service sidecar.

    Trips booked via the journey search (``BK-…`` ids) are not in the simulated
    ``LIVE_TRIP_STATUS``, so their live delay has to come from real DB data.
    Re-runs the connection search, matches the trip's exact itinerary (full
    train sequence + planned departure, see ``db.ops.match_booked_journey``),
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


@router.get("/api/trips/{trip_id}/details")
def trip_details(trip_id: str, authorization: str | None = Header(default=None)) -> dict:
    """Journey details for one booked trip: legs, live delay, and risk forecast.

    The itinerary is simulated (ADR 0005), but the live delay is real: for trips
    booked via the journey search it is fetched from the db_service sidecar
    (``_live_leg_delays``); the demo trips fall back to the simulated
    ``LIVE_TRIP_STATUS``. The expected delay comes from ``journey_autopilot.risk``,
    scored from real historical DB punctuality data (see ``risk/delay_reference.py``).
    """
    user_id = current_user_id(authorization)
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


@router.get("/api/complaints")
def list_complaints(authorization: str | None = Header(default=None)) -> dict:
    user_id = current_user_id(authorization)
    return {"complaints": store.get_complaints(user_id)}


@router.patch("/api/complaints/{complaint_id}")
def patch_complaint(
    complaint_id: str,
    body: ComplaintPatchRequest,
    authorization: str | None = Header(default=None),
) -> dict:
    user_id = current_user_id(authorization)
    if body.status == "submitted":
        updated = complaints.submit_complaint(user_id, complaint_id)
    elif body.status == "rejected":
        updated = complaints.reject_complaint(user_id, complaint_id)
    else:
        raise HTTPException(status_code=422, detail="Unsupported status change.")
    if updated is None:
        raise HTTPException(status_code=404, detail="Complaint not found.")
    return {"complaint": updated}
