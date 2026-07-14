"""Delay forecast for a trip's legs, from real historical DB data.

Deliberately simple: predicted delay = the leg's live delay plus the
historical average delay at its destination (see ``delay_reference``).
``level`` bands that predicted delay (low/medium/high); ``risk_score`` is
the historical on-time rate alone, inverted onto 0-100, for context on how
delay-prone the route normally is. Extend the model by adding more signals
to ``forecast_leg`` — the public contract, ``forecast_trip(trip, legs) ->
list[forecast]``, stays the same either way.
"""

from __future__ import annotations

from datetime import datetime

from . import delay_reference


def _level(expected_delay_minutes: int) -> str:
    return "low" if expected_delay_minutes < 5 else "medium" if expected_delay_minutes < 15 else "high"


def _train_type(train: str | None) -> str:
    parts = (train or "").split()
    return parts[0] if parts else ""


def _destination_name(leg: dict) -> str:
    destination = leg.get("destination")
    return destination["name"] if isinstance(destination, dict) else str(destination or "")


def forecast_leg(trip: dict, leg: dict, leg_index: int) -> dict:
    """Historical expected-delay forecast for a single journey leg."""
    destination = _destination_name(leg)
    train_type = _train_type(leg.get("train"))
    stats = delay_reference.lookup(destination, train_type)

    current = int(leg.get("current_delay_minutes") or 0)
    expected = max(0, round(current + stats["mean_delay"]))
    level = _level(expected)
    score = round(100 * (1 - stats["on_time_rate"]))

    factor = (
        f"{round(stats['on_time_rate'] * 100)}% on-time historically at {destination} "
        f"for {train_type or 'this'} trains (mean delay {stats['mean_delay']} min)."
    )
    return {
        "expected_delay_minutes": expected,
        "level": level,
        "risk_score": score,
        "confidence": round(min(0.95, 0.5 + stats["samples"] / 4000), 2),
        "factors": [] if level == "low" else [factor],
        "source": f"db_history_{stats['basis']}",
    }


def forecast_trip(trip: dict, legs: list[dict]) -> list[dict]:
    """Per-leg forecasts for a whole journey (same order as ``legs``)."""
    return [forecast_leg(trip, leg, i) for i, leg in enumerate(legs)]


def _planned(stop: dict | None) -> datetime | None:
    value = (stop or {}).get("planned")
    try:
        return datetime.fromisoformat(value) if value else None
    except ValueError:
        return None


def connection_risks(legs: list[dict]) -> list[str]:
    """Flags transfers a leg's forecast delay would blow, given the transfer buffer.

    Call after ``forecast_trip`` has set ``leg["forecast"]`` on each leg. Compares
    the buffer between a leg's planned arrival and the next leg's planned
    departure against the leg's ``expected_delay_minutes`` — if the delay meets
    or exceeds the buffer (including an already-negative buffer), the connection
    is at risk: the leg's forecast ``level`` is raised to "high" and a warning is
    returned (for the trip's ``connection_risk``).
    """
    warnings: list[str] = []
    for prev_leg, leg in zip(legs, legs[1:]):
        prev_arrival = _planned(prev_leg.get("destination"))
        next_departure = _planned(leg.get("origin"))
        if prev_arrival is None or next_departure is None:
            continue
        buffer_minutes = round((next_departure - prev_arrival).total_seconds() / 60)
        expected = prev_leg["forecast"]["expected_delay_minutes"]
        if expected >= buffer_minutes:
            prev_leg["forecast"]["level"] = "high"
            station = (prev_leg.get("destination") or {}).get("name", "the transfer station")
            warnings.append(
                f"A {expected} min delay on {prev_leg.get('train')} may cause you to miss the "
                f"{leg.get('train')} connection at {station} (only {buffer_minutes} min transfer)."
            )
    return warnings


def live_connection_risks(legs: list[dict]) -> list[str]:
    """Transfer warnings based ONLY on the current live delay per leg.

    Unlike ``connection_risks`` (which folds the historical mean into the
    check and therefore flags transfers on a perfectly punctual day), this
    variant warns only when a train is ACTUALLY running late enough to eat the
    transfer buffer — the right signal for the trip-detail screen, where a
    speculative "you may miss your connection" reads as a wrong warning.
    """
    warnings: list[str] = []
    for prev_leg, leg in zip(legs, legs[1:]):
        prev_arrival = _planned(prev_leg.get("destination"))
        next_departure = _planned(leg.get("origin"))
        if prev_arrival is None or next_departure is None:
            continue
        buffer_minutes = round((next_departure - prev_arrival).total_seconds() / 60)
        delay = int(prev_leg.get("current_delay_minutes") or 0)
        if delay > 0 and delay >= buffer_minutes:
            station = (prev_leg.get("destination") or {}).get("name", "the transfer station")
            warnings.append(
                f"{prev_leg.get('train')} is currently {delay} min late — the "
                f"{leg.get('train')} connection at {station} ({buffer_minutes} min "
                f"transfer) is at risk."
            )
    return warnings
