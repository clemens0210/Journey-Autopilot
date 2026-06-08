"""Python-Client für den db-vendo-client-Sidecar (siehe ``db_service/``).

Das ist die **einzige Stelle**, an der die Python-Seite mit der Deutschen Bahn
spricht. Die ADK-Tools rufen diese Funktionen; alles DB-Spezifische (EVA-Nummern,
Eigenheiten der vendo-API, der Node-Sidecar) bleibt hinter dieser Datei verborgen.

Der Sidecar liefert DB-Navigator-genaue Live-Daten: Verspätungen, Gleiswechsel,
Routing inkl. Preise. Läuft er nicht, wirft ``DBServiceError`` — die Tools können
das fangen und auf ``mock_data`` zurückfallen.

Konfiguration über Umgebungsvariablen:
- ``DB_API_URL``      (Default ``http://127.0.0.1:3000``) — Adresse des Sidecars.
- ``DB_API_TIMEOUT``  (Default ``20``) — Request-Timeout in Sekunden.
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
    """Sidecar nicht erreichbar oder DB-API hat einen Fehler geliefert."""


def _to_param(value: Any) -> Any:
    """``datetime`` -> ISO-String, ``bool`` -> 'true'/'false', sonst unverändert."""
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, bool):
        return "true" if value else "false"
    return value


def _get(path: str, params: dict | None = None) -> Any:
    """GET gegen den Sidecar; ``None``-Parameter werden weggelassen."""
    clean = {k: _to_param(v) for k, v in (params or {}).items() if v is not None}
    try:
        resp = requests.get(f"{DB_API_URL}{path}", params=clean, timeout=_TIMEOUT)
    except requests.RequestException as exc:
        raise DBServiceError(f"db-service nicht erreichbar ({DB_API_URL}): {exc}") from exc
    if resp.status_code >= 400:
        raise DBServiceError(f"db-service Fehler {resp.status_code}: {resp.text[:200]}")
    return resp.json()


def health() -> dict:
    """True-ish Dict, wenn der Sidecar läuft. Wirft sonst ``DBServiceError``."""
    return _get("/health")


def locations(query: str, results: int = 5) -> list[dict]:
    """Stationssuche nach Name. Jeder Treffer trägt die EVA-Nummer als ``id``."""
    return _get("/locations", {"query": query, "results": results})


def departures(
    eva: str,
    when: datetime | str | None = None,
    duration: int = 30,
    results: int | None = None,
) -> dict:
    """Live-Abfahrtstafel einer Station (EVA). Enthält Verspätungen + Gleiswechsel."""
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
    """Live-Ankunftstafel einer Station (EVA)."""
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
    """Verbindungssuche zwischen zwei Stationen (EVA). ``tickets=True`` -> Preise.

    Weitere db-vendo-client-Optionen lassen sich via ``**opt`` durchreichen,
    z. B. ``transfers=0`` (nur Direktverbindungen) oder ``via="8000105"``.
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
    """Eine einzelne Fahrt verfolgen (alle Halte + Echtzeit)."""
    return _get(f"/trips/{quote(trip_id, safe='')}")


def nearby(latitude: float, longitude: float, results: int = 8) -> list[dict]:
    """Stationen in der Nähe einer Koordinate."""
    return _get(
        "/nearby",
        {"latitude": latitude, "longitude": longitude, "results": results},
    )
