"""Resolve station names -> EVA number (with caching).

The DB API works with EVA numbers (e.g. ``8000207`` for Köln Hbf), while agents
and users think in names ("Köln Hbf"). This small helper bridges that gap and
caches the resolution so not every tool call has to look it up again.
"""

from __future__ import annotations

from functools import lru_cache

from . import db_api


@lru_cache(maxsize=256)
def resolve_eva(name: str) -> str | None:
    """Return the EVA number for a station name, or ``None`` if nothing matches.

    Args:
        name: Station name, e.g. "Köln Hbf".

    Returns:
        The EVA number as a string (e.g. "8000207") or ``None``.
        Raises ``db_api.DBServiceError`` if the sidecar is unreachable.
    """
    for item in db_api.locations(name, results=5):
        if item.get("type") in ("stop", "station") and item.get("id"):
            return str(item["id"])
    return None
