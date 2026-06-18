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
