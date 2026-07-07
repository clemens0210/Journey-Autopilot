"""Mocked delay forecast — placeholder until the real risk model lands here.

The forecast is deterministic (hash of trip id + leg index), so the UI shows
stable numbers across reloads and server restarts. The public contract is
``forecast_trip(trip, legs) -> list[forecast]``; the real predictor must keep
that signature so the ``/api/trips/{id}/details`` endpoint and the trip-detail
screen stay unchanged.
"""

from __future__ import annotations

import hashlib

# Plausible-sounding drivers for the mocked prediction, picked deterministically.
_FACTORS = [
    "High network load on this corridor",
    "Construction work along the route",
    "Historically delay-prone on this weekday",
    "Rolling stock arriving from a delayed inbound service",
    "Weather advisory along the route",
    "Signalling bottleneck near the destination",
]

# Extra minutes on top of the live delay (already-late trains tend to lose a
# bit more) vs. baseline risk minutes for currently on-time legs.
_ADDED_WHEN_LATE = (5, 12, 2, 8, 0, 3)
_BASELINE_WHEN_ON_TIME = (0, 2, 6, 11, 3, 17)


def _seed(trip_id: str, leg_index: int) -> int:
    digest = hashlib.md5(f"{trip_id}:{leg_index}".encode()).hexdigest()
    return int(digest[:8], 16)


def forecast_leg(trip: dict, leg: dict, leg_index: int) -> dict:
    """Mocked expected-delay forecast for a single journey leg."""
    seed = _seed(trip.get("trip_id", "?"), leg_index)
    current = int(leg.get("current_delay_minutes") or 0)
    added = (_ADDED_WHEN_LATE if current else _BASELINE_WHEN_ON_TIME)[seed % 6]
    expected = current + added
    level = "low" if expected < 5 else "medium" if expected < 15 else "high"
    return {
        "expected_delay_minutes": expected,
        "level": level,
        "confidence": round(0.55 + (seed % 35) / 100, 2),
        "factors": [] if expected == 0 else [_FACTORS[seed % len(_FACTORS)]],
        "source": "mock",
    }


def forecast_trip(trip: dict, legs: list[dict]) -> list[dict]:
    """Per-leg forecasts for a whole journey (same order as ``legs``)."""
    return [forecast_leg(trip, leg, i) for i, leg in enumerate(legs)]
