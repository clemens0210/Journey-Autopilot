"""Resolve station names -> EVA number (with caching).

The DB API works with EVA numbers (e.g. ``8000207`` for Köln Hbf), while agents
and users think in names ("Köln Hbf"). This small helper bridges that gap.

Two layers keep us off the sidecar's ``/locations`` endpoint — which DB rate-
limits aggressively (``OPS_BLOCKED``) and which a burst of identical lookups in
one orchestrator run would otherwise hammer:

1. A **static name -> EVA table** for the handful of cities the demo/fixtures
   use. EVA numbers are stable DB identifiers, so these resolve locally with
   zero network calls.
2. For names not in the table, a **live lookup** via the sidecar whose result is
   cached: positives forever (within the process), and *misses/blocks* for a
   short TTL — so a DB block during a burst is not retried over and over (the old
   ``lru_cache`` did not cache exceptions, so every retry re-hit DB).
"""

from __future__ import annotations

import os
import time

from . import ops as db_ops

# Static table for the stations the demo/fixtures touch, plus common German
# Hbf. Keys are normalized via ``_norm`` (lowercase, collapsed whitespace);
# English aliases (Munich/Cologne/Nuremberg) map to the same station.
_STATIC_EVA: dict[str, str] = {
    "berlin hbf": "8011160",
    "munich hbf": "8000261",
    "münchen hbf": "8000261",
    "köln hbf": "8000207",
    "cologne hbf": "8000207",
    "frankfurt hbf": "8000105",
    "frankfurt(main)hbf": "8000105",
    "frankfurt (main) hbf": "8000105",
    "hamburg hbf": "8002549",
    "stuttgart hbf": "8000096",
    "bonn hbf": "8000036",
    "nuremberg hbf": "8000284",
    "nürnberg hbf": "8000284",
    "hannover hbf": "8000152",
    "düsseldorf hbf": "8000085",
    "leipzig hbf": "8010205",
    "dresden hbf": "8010085",
    "erfurt hbf": "8010101",
}

# How long (seconds) a failed/empty live lookup is remembered before retrying.
# 30 s is enough to debounce burst lookups within a single orchestrator run
# (~10–60 s) without blocking recovery after a momentary sidecar blip.
_NEG_TTL = float(os.getenv("EVA_NEG_TTL", "30"))

_pos_cache: dict[str, str] = {}      # normalized name -> EVA (kept for the process)
_neg_cache: dict[str, float] = {}    # normalized name -> expiry (monotonic seconds)


def _norm(name: str) -> str:
    """Normalize a station name for table/cache lookup."""
    return " ".join(name.strip().lower().split())


def resolve_eva(name: str) -> str | None:
    """Return the EVA number for a station name, or ``None`` if unresolved.

    Resolution order: static table -> positive cache -> negative cache (within
    its TTL) -> live sidecar lookup. The live lookup never raises: if the sidecar
    is unreachable or DB blocks the request, the miss is cached for ``_NEG_TTL``
    seconds and ``None`` is returned, so a burst of identical lookups in one run
    does not hammer the (already failing) endpoint.

    Args:
        name: Station name, e.g. "Köln Hbf".

    Returns:
        The EVA number as a string (e.g. "8000207") or ``None``.
    """
    key = _norm(name)

    static = _STATIC_EVA.get(key)
    if static is not None:
        return static

    cached = _pos_cache.get(key)
    if cached is not None:
        return cached

    expiry = _neg_cache.get(key)
    if expiry is not None:
        if expiry > time.monotonic():
            return None
        del _neg_cache[key]  # TTL lapsed — evict so the dict doesn't grow forever

    try:
        for item in db_ops.locations(name, results=5):
            if item.get("type") in ("stop", "station") and item.get("id"):
                eva = str(item["id"])
                _pos_cache[key] = eva
                return eva
    except db_ops.DBServiceError:
        # Sidecar unreachable or DB blocked the request — remember the miss so
        # the next tool in the same run doesn't re-hit it immediately.
        _neg_cache[key] = time.monotonic() + _NEG_TTL
        return None

    # A valid-but-empty result (no station matched) — cache briefly as well.
    _neg_cache[key] = time.monotonic() + _NEG_TTL
    return None


def resolve_eva_or_id(value: str) -> str | None:
    """Resolve a station name OR an already-numeric EVA to an EVA number.

    The journey search autocomplete returns the EVA (an all-digit id) directly
    when a station is selected; a manually typed input is a name. All-digit
    values are passed straight through (zero network calls), everything else
    goes through ``resolve_eva``. No EVA-format validation: a malformed digit
    string simply yields no journeys downstream (clean failure).
    """
    v = (value or "").strip()
    if not v:
        return None
    if v.isdigit():
        return v
    return resolve_eva(v)
