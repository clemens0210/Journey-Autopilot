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

from datetime import datetime, timedelta

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


# A real gap at or below this many minutes is too tight to rely on, even
# though the train has not left yet.
_TIGHT_TRANSFER_MINUTES = 5


def _real_time(planned: datetime, leg: dict) -> datetime:
    """Planned time shifted by that leg's own live delay."""
    return planned + timedelta(minutes=int(leg.get("current_delay_minutes") or 0))


def _transfer_gaps(legs: list[dict]):
    """Scheduled and real minutes at each change station, both legs' delays applied."""
    for prev_leg, leg in zip(legs, legs[1:]):
        prev_arrival = _planned(prev_leg.get("destination"))
        next_departure = _planned(leg.get("origin"))
        if prev_arrival is None or next_departure is None:
            continue
        scheduled_minutes = round((next_departure - prev_arrival).total_seconds() / 60)
        real_minutes = round(
            (_real_time(next_departure, leg) - _real_time(prev_arrival, prev_leg)).total_seconds() / 60
        )
        station = (prev_leg.get("destination") or {}).get("name", "the transfer station")
        yield prev_leg, leg, station, scheduled_minutes, real_minutes


def _missed_warning(prev_leg: dict, leg: dict, station: str, real_minutes: int) -> str:
    return (
        f"{leg.get('train')} leaves {station} {-real_minutes} min before "
        f"{prev_leg.get('train')} gets in — that connection is missed."
    )


def missed_connections(legs: list[dict]) -> list[str]:
    """Transfers that are DEFINITIVELY gone: the connecting train leaves before
    the feeder gets in, with both ends shifted by their own live delay.

    A non-empty result means the booked itinerary can no longer be completed as
    planned — there is no "stay aboard" arrival time anymore, only the missed
    fact. At-risk-but-still-possible transfers are deliberately not included;
    those remain a forecast, not a fact.
    """
    return [
        _missed_warning(prev_leg, leg, station, real_minutes)
        for prev_leg, leg, station, _scheduled, real_minutes in _transfer_gaps(legs)
        if real_minutes < 0
    ]


def live_connection_risks(legs: list[dict]) -> list[str]:
    """Transfer warnings from the REAL gap at each change station.

    Both ends move: the feeder's arrival and the connecting train's departure
    are each shifted by their OWN live delay. Judging the transfer by the
    timetable gap and the feeder's delay alone cries wolf whenever a train runs
    late — a connection that is just as late is still perfectly safe. An 8 min
    timetable transfer with the feeder +16 and the connection +31 is in reality
    a comfortable 23 min, not a risk.

    Two cases are reported, because they are not the same thing: the gap has
    closed entirely (the train is gone — a fact, not a risk), or a delay has
    eaten it down to a buffer too thin to rely on. A short transfer the
    timetable always intended is not flagged: nothing went wrong there.
    """
    warnings: list[str] = []
    for prev_leg, leg, station, scheduled_minutes, real_minutes in _transfer_gaps(legs):
        if real_minutes < 0:
            warnings.append(_missed_warning(prev_leg, leg, station, real_minutes))
        elif real_minutes <= _TIGHT_TRANSFER_MINUTES and real_minutes < scheduled_minutes:
            warnings.append(
                f"Only {real_minutes} min left to change to {leg.get('train')} at "
                f"{station} (timetable: {scheduled_minutes} min) — this is tight."
            )
    return warnings
