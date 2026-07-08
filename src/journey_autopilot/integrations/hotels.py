"""Live hotel lookup near a station via OpenStreetMap (Overpass API).

Real-data source for the Planner's overnight fallback (``find_partner_hotels``):
the stranded/destination station is geocoded through the db_service sidecar
(``db_ops.locations`` — same live dependency the reroute search already uses),
then the keyless Overpass API returns real hotels within walking/taxi distance,
sorted by distance to the station.

OSM carries no room prices, so hotel options are shown without price
information. Everything else (name, distance, stars, contact) is real.

- ``OVERPASS_API_URL`` (default ``https://overpass-api.de/api/interpreter``)
- ``HOTEL_SEARCH_RADIUS_M`` (default 1500) — search radius around the station.
"""

from __future__ import annotations

import math
import os

import requests

from . import db_ops

OVERPASS_API_URL = os.getenv("OVERPASS_API_URL", "https://overpass-api.de/api/interpreter")
_RADIUS_M = int(os.getenv("HOTEL_SEARCH_RADIUS_M", "1500"))
_TIMEOUT = float(os.getenv("OVERPASS_TIMEOUT", "15"))


class HotelServiceError(RuntimeError):
    """Station not resolvable or the Overpass API failed."""


def _station_coords(location: str) -> tuple[float, float]:
    """Geocode a station/city name to (lat, lon) via the db_service sidecar."""
    try:
        hits = db_ops.normalize_locations(db_ops.locations(location, results=3))
    except db_ops.DBServiceError as exc:
        raise HotelServiceError(f"Station lookup failed for {location!r}: {exc}") from exc
    for hit in hits:
        if hit.get("latitude") is not None and hit.get("longitude") is not None:
            return float(hit["latitude"]), float(hit["longitude"])
    raise HotelServiceError(f"No coordinates found for {location!r}.")


def _distance_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Haversine distance in km."""
    rad = math.radians
    dlat, dlon = rad(lat2 - lat1), rad(lon2 - lon1)
    a = math.sin(dlat / 2) ** 2 + math.cos(rad(lat1)) * math.cos(rad(lat2)) * math.sin(dlon / 2) ** 2
    return 6371.0 * 2 * math.asin(math.sqrt(a))


def _element_coords(element: dict) -> tuple[float, float] | None:
    """Coordinates of an Overpass element (nodes carry lat/lon, ways a center)."""
    if element.get("lat") is not None and element.get("lon") is not None:
        return float(element["lat"]), float(element["lon"])
    center = element.get("center") or {}
    if center.get("lat") is not None and center.get("lon") is not None:
        return float(center["lat"]), float(center["lon"])
    return None


def find_hotels_near_station(location: str, max_results: int = 4) -> list[dict]:
    """Real hotels near a station, sorted by distance, in the planner shape.

    Returns the same fields as the ``mock_data.PARTNER_HOTELS`` entries
    (``mode``, ``option_id`` H#, ``name``, ``description``,
    ``distance_to_station_km``, ``price_per_night_eur``, ``nights``,
    ``remarks``, ``source``) so live and mock results are interchangeable.
    Raises ``HotelServiceError`` on any lookup failure — callers fall back.
    """
    lat, lon = _station_coords(location)
    query = (
        f"[out:json][timeout:{int(_TIMEOUT)}];"
        f'nwr["tourism"="hotel"]["name"](around:{_RADIUS_M},{lat},{lon});'
        f"out center {max_results * 5};"
    )
    try:
        resp = requests.post(
            OVERPASS_API_URL,
            data={"data": query},
            timeout=_TIMEOUT,
            # Overpass rejects the default python-requests agent with 406 —
            # its usage policy requires an identifying User-Agent.
            headers={"User-Agent": "journey-autopilot/0.1 (prototype)"},
        )
    except requests.RequestException as exc:
        raise HotelServiceError(f"Overpass API unreachable: {exc}") from exc
    if resp.status_code >= 400:
        raise HotelServiceError(f"Overpass API error {resp.status_code}: {resp.text[:200]}")

    candidates = []
    for element in resp.json().get("elements", []):
        tags = element.get("tags") or {}
        coords = _element_coords(element)
        if not tags.get("name") or coords is None:
            continue
        candidates.append((_distance_km(lat, lon, *coords), tags))
    candidates.sort(key=lambda c: c[0])

    hotels = []
    for i, (dist, tags) in enumerate(candidates[:max_results], start=1):
        stars = tags.get("stars")
        remarks = []
        if tags.get("website") or tags.get("contact:website"):
            remarks.append(tags.get("website") or tags["contact:website"])
        if tags.get("phone") or tags.get("contact:phone"):
            remarks.append(tags.get("phone") or tags["contact:phone"])
        hotels.append(
            {
                "mode": "hotel",
                "option_id": f"H{i}",
                "name": tags["name"],
                "description": (f"{stars}-star hotel" if stars else "Hotel")
                + f" {dist:.1f} km from {location}",
                "distance_to_station_km": round(dist, 1),
                "price_per_night_eur": None,
                "nights": 1,
                "remarks": remarks,
                "source": "osm_hotels_live",
            }
        )
    return hotels
