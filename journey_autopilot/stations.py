"""Stationsnamen -> EVA-Nummer auflösen (mit Cache).

Die DB-API arbeitet mit EVA-Nummern (z. B. ``8000207`` für Köln Hbf), die Agenten
und Nutzer denken in Namen ("Köln Hbf"). Diese kleine Hilfe schließt die Lücke und
cached die Auflösung, damit nicht jeder Tool-Aufruf erneut sucht.
"""

from __future__ import annotations

from functools import lru_cache

from . import db_api


@lru_cache(maxsize=256)
def resolve_eva(name: str) -> str | None:
    """Liefert die EVA-Nummer zum Stationsnamen, oder ``None`` wenn nichts passt.

    Args:
        name: Stationsname, z. B. "Köln Hbf".

    Returns:
        Die EVA-Nummer als String (z. B. "8000207") oder ``None``.
        Wirft ``db_api.DBServiceError``, wenn der Sidecar nicht erreichbar ist.
    """
    for item in db_api.locations(name, results=5):
        if item.get("type") in ("stop", "station") and item.get("id"):
            return str(item["id"])
    return None
