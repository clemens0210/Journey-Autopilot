"""Python client for the db-vendo-client sidecar (see ``db_service/``).

This is the **single place** where the Python side talks to Deutsche Bahn.
The ADK tools call these functions; everything DB-specific (EVA numbers,
quirks of the vendo API, the Node sidecar) stays hidden behind this file.

The sidecar provides DB-Navigator-accurate live data: delays, platform
changes, routing including prices. If it isn't running, it raises
``DBServiceError`` — the tools can catch that and fall back to ``mock_data``.

Configuration via environment variables:
- ``DB_API_URL``      (default ``http://127.0.0.1:3000``) — address of the sidecar.
- ``DB_API_TIMEOUT``  (default ``20``) — request timeout in seconds.
"""

from __future__ import annotations

import os
from datetime import datetime
from typing import Any
from urllib.parse import quote

import requests

DB_API_URL = os.getenv("DB_API_URL", "http://127.0.0.1:3000").rstrip("/")
_TIMEOUT = float(os.getenv("DB_API_TIMEOUT", "20"))


class DBServiceError(RuntimeError):
    """Sidecar unreachable or the DB API returned an error."""


def _to_param(value: Any) -> Any:
    """``datetime`` -> ISO string, ``bool`` -> 'true'/'false', otherwise unchanged."""
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, bool):
        return "true" if value else "false"
    return value


def _get(path: str, params: dict | None = None) -> Any:
    """GET against the sidecar; ``None`` parameters are omitted."""
    clean = {k: _to_param(v) for k, v in (params or {}).items() if v is not None}
    try:
        resp = requests.get(f"{DB_API_URL}{path}", params=clean, timeout=_TIMEOUT)
    except requests.RequestException as exc:
        raise DBServiceError(f"db-service unreachable ({DB_API_URL}): {exc}") from exc
    if resp.status_code >= 400:
        raise DBServiceError(f"db-service error {resp.status_code}: {resp.text[:200]}")
    return resp.json()


def _minutes(delay_seconds: Any) -> float | None:
    """Convert db-vendo-client delay seconds to minutes."""
    if delay_seconds is None:
        return None
    try:
        return round(float(delay_seconds) / 60.0, 1)
    except (TypeError, ValueError):
        return None


def _place_name(value: Any) -> str | None:
    """Extract a station/location name from a db-vendo object."""
    if isinstance(value, dict):
        return value.get("name")
    return str(value) if value else None


def _line_name(leg: dict) -> str | None:
    line = leg.get("line") or {}
    return line.get("name") or leg.get("lineName")


def _platform(leg: dict, *keys: str) -> str | None:
    for key in keys:
        value = leg.get(key)
        if value:
            return str(value)
    return None


def _collect_remarks(*items: Any) -> list[str]:
    """Collect human-readable DB remarks from journeys/legs without duplicates."""
    out: list[str] = []
    seen: set[str] = set()
    for item in items:
        if not isinstance(item, dict):
            continue
        for remark in item.get("remarks") or []:
            if not isinstance(remark, dict):
                continue
            text = (remark.get("summary") or remark.get("text") or "").strip()
            if text and text not in seen:
                seen.add(text)
                out.append(text)
    return out


def normalize_locations(items: Any) -> list[dict]:
    """Stable station hits for UI/tools: ``id``, ``name``, type and coordinates."""
    hits: list[dict] = []
    for item in items or []:
        if not isinstance(item, dict):
            continue
        if item.get("type") not in ("stop", "station") or not item.get("id"):
            continue
        hit = {
            "id": str(item["id"]),
            "name": item.get("name"),
            "type": item.get("type"),
        }
        if item.get("location"):
            loc = item["location"]
            hit["latitude"] = loc.get("latitude")
            hit["longitude"] = loc.get("longitude")
        hits.append(hit)
    return hits


def normalize_journey(journey: dict, option_id: str | None = None) -> dict:
    """Convert a raw db-vendo journey into the stable shape used by tools."""
    legs = [leg for leg in journey.get("legs") or [] if isinstance(leg, dict)]
    ride_legs = [leg for leg in legs if leg.get("line")]
    first = ride_legs[0] if ride_legs else (legs[0] if legs else {})
    last = ride_legs[-1] if ride_legs else (legs[-1] if legs else {})

    normalized_legs: list[dict] = []
    platform_changes: list[dict] = []
    for idx, leg in enumerate(ride_legs or legs, start=1):
        planned_departure_platform = _platform(
            leg, "plannedDeparturePlatform", "plannedPlatform"
        )
        departure_platform = _platform(leg, "departurePlatform", "platform")
        planned_arrival_platform = _platform(leg, "plannedArrivalPlatform")
        arrival_platform = _platform(leg, "arrivalPlatform")
        platform_change = (
            planned_departure_platform
            and departure_platform
            and planned_departure_platform != departure_platform
        )
        if platform_change:
            platform_changes.append(
                {
                    "leg": idx,
                    "train": _line_name(leg),
                    "planned_platform": planned_departure_platform,
                    "platform": departure_platform,
                }
            )
        normalized_legs.append(
            {
                "train": _line_name(leg),
                "origin": _place_name(leg.get("origin")),
                "destination": _place_name(leg.get("destination")),
                "departure": leg.get("departure"),
                "planned_departure": leg.get("plannedDeparture"),
                "arrival": leg.get("arrival"),
                "planned_arrival": leg.get("plannedArrival"),
                "departure_delay_minutes": _minutes(leg.get("departureDelay")),
                "arrival_delay_minutes": _minutes(leg.get("arrivalDelay")),
                "platform": departure_platform,
                "planned_platform": planned_departure_platform,
                "arrival_platform": arrival_platform,
                "planned_arrival_platform": planned_arrival_platform,
                "trip_id": leg.get("tripId"),
                "direction": leg.get("direction"),
                "remarks": _collect_remarks(leg),
            }
        )

    trains = []
    for leg in normalized_legs:
        train = leg.get("train")
        if train and train not in trains:
            trains.append(train)

    price = journey.get("price") or {}
    price_amount = price.get("amount") if isinstance(price, dict) else None
    remarks = _collect_remarks(journey, *legs)
    description = " -> ".join(trains) if trains else "Connection"

    return {
        "option_id": option_id,
        "description": description,
        "train": trains[0] if trains else None,
        "trains": trains,
        "trip_id": first.get("tripId"),
        "origin": _place_name(first.get("origin")),
        "destination": _place_name(last.get("destination")),
        "departure": first.get("departure"),
        "planned_departure": first.get("plannedDeparture"),
        "arrival": last.get("arrival"),
        "planned_arrival": last.get("plannedArrival"),
        "departure_delay_minutes": _minutes(first.get("departureDelay")),
        "arrival_delay_minutes": _minutes(last.get("arrivalDelay")),
        "transfers": max(len(ride_legs) - 1, 0),
        "price_eur": price_amount,
        "legs": normalized_legs,
        "remarks": remarks,
        "platform_changes": platform_changes,
    }


def normalize_journeys(payload: Any) -> list[dict]:
    """Normalize the ``journeys`` response into option dictionaries."""
    if isinstance(payload, dict):
        raw = payload.get("journeys") or []
    else:
        raw = payload or []
    return [
        normalize_journey(journey, option_id=f"R{idx}")
        for idx, journey in enumerate(raw, start=1)
        if isinstance(journey, dict)
    ]


def health() -> dict:
    """Truthy dict if the sidecar is running. Raises ``DBServiceError`` otherwise."""
    return _get("/health")


def locations(query: str, results: int = 5) -> list[dict]:
    """Search stations by name. Each match carries the EVA number as ``id``."""
    return _get("/locations", {"query": query, "results": results})


def departures(
    eva: str,
    when: datetime | str | None = None,
    duration: int = 30,
    results: int | None = None,
) -> dict:
    """Live departure board for a station (EVA). Includes delays + platform changes."""
    return _get(
        f"/departures/{eva}",
        {"when": when, "duration": duration, "results": results},
    )


def arrivals(
    eva: str,
    when: datetime | str | None = None,
    duration: int = 30,
    results: int | None = None,
) -> dict:
    """Live arrival board for a station (EVA)."""
    return _get(
        f"/arrivals/{eva}",
        {"when": when, "duration": duration, "results": results},
    )


def journeys(
    from_eva: str,
    to_eva: str,
    departure: datetime | str | None = None,
    results: int = 5,
    tickets: bool = True,
    **opt: Any,
) -> dict:
    """Search journeys between two stations (EVA). ``tickets=True`` -> prices.

    Additional db-vendo-client options can be passed through via ``**opt``,
    e.g. ``transfers=0`` (direct connections only) or ``via="8000105"``.
    """
    params = {
        "from": from_eva,
        "to": to_eva,
        "departure": departure,
        "results": results,
        "tickets": tickets,
        **opt,
    }
    return _get("/journeys", params)


def trip(trip_id: str) -> dict:
    """Track a single trip (all stops + real-time data)."""
    return _get(f"/trips/{quote(trip_id, safe='')}")


def nearby(latitude: float, longitude: float, results: int = 8) -> list[dict]:
    """Stations near a coordinate."""
    return _get(
        "/nearby",
        {"latitude": latitude, "longitude": longitude, "results": results},
    )
