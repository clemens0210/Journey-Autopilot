"""Today's delay situation on a route, aggregated from the live DB arrival board.

The second of this package's two sources. ``delay_reference`` answers "how
delay-prone is this route normally" from a multi-month archive; this module
answers "how is it going right now" from the arrival board. Both are pure
Python: risk is a model, never an LLM judgment — the Monitoring agent
*interprets* these numbers into a score and a band, it does not compute them.
The agent-facing tool wrappers live in ``tools/read/pretrip_risk.py``.

Why use the arrival board as "historical data"? ``db-vendo-client`` offers NO
real delay-archive endpoint. Empirically, though, the DB API carries actual
delays in a rolling window of roughly 5-6 hours around "now" — including for
the recent PAST (trains that have already arrived). Older days only deliver
the scheduled timetable without delay. This is exactly the window used here:
the arrival board of the destination station for the last few hours; every
long-distance train listed there that has already arrived carries its ACTUALLY
occurred delay — a real, DB-backed signal.

Unlike the rest of the package this reaches out to the db_service sidecar, so
it is deliberately NOT re-exported from ``risk/__init__.py`` — import it as
``from ...risk import live_stats`` where the live dependency is wanted.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta
from statistics import mean, median
from typing import Any

from ..integrations.db import ops as db_ops
from ..integrations.db import stations

# Long-distance traffic — other products would distort the corridor statistics.
_LONG_DISTANCE = {"ICE", "IC", "EC", "ECE", "RJ", "RJX", "TGV", "NJ", "EN"}

# Thresholds (in minutes) for the derived rates.
_PUNCTUAL_MAX_MINUTES = 5      # up to 5 min counts as punctual (DB convention < 6 min)
_HEAVY_DELAY_MINUTES = 15      # from 15 min counted as a significant delay


def _to_minutes(delay_seconds: Any) -> float | None:
    """``delay`` (seconds, can be ``None``) -> minutes, otherwise ``None``."""
    if delay_seconds is None:
        return None
    try:
        return float(delay_seconds) / 60.0
    except (TypeError, ValueError):
        return None


def _train_type(train: str) -> str | None:
    """'ICE 1006' -> 'ICE'."""
    tokens = (train or "").strip().split()
    return tokens[0].upper() if tokens else None


def _is_long_distance(entry: dict) -> bool:
    """Long-distance train? Uses the same product set as the archive baseline."""
    line = entry.get("line") or {}
    if line.get("product") in ("nationalExpress", "national"):
        return True
    return _train_type(line.get("name") or "") in _LONG_DISTANCE


def _percentile(values: list[float], pct: float) -> float:
    """Linearly interpolated percentile (stdlib-only, no numpy)."""
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    rank = (pct / 100.0) * (len(ordered) - 1)
    low = int(rank)
    high = min(low + 1, len(ordered) - 1)
    frac = rank - low
    return ordered[low] + (ordered[high] - ordered[low]) * frac


def _sample_row(entry: dict, minutes: float | None, status: str) -> dict:
    """A considered arrival as a traceable row (for transparency)."""
    line = entry.get("line") or {}
    return {
        "train": line.get("name"),
        "from": entry.get("provenance") or (entry.get("origin") or {}).get("name"),
        "planned_arrival": entry.get("plannedWhen") or entry.get("when"),
        "delay_minutes": round(minutes, 1) if minutes is not None else None,
        "status": status,
    }


def connection_delay_history(
    origin: str,
    destination: str,
    train: str = "",
    lookback_minutes: int = 300,
    end: str | None = None,
    sample_limit: int = 200,
    details: bool = False,
) -> dict:
    """Delay metrics for a connection from the DB arrival board.

    Reads the long-distance arrivals at the destination station over a time
    window in the recent past and condenses the ACTUALLY occurred delays of
    already-arrived trains into metrics.

    Args:
        origin: Departure station (context/output only, filtering happens at
            the destination).
        destination: Destination station — whose arrival board is evaluated.
        train: Optional train name (e.g. "ICE 1006"), context only.
        lookback_minutes: Size of the window — how far it reaches into the
            past from ``end``. Default 300 (5 h), within the empirical
            real-time horizon (~5-6 h).
        end: Window end as ISO time; ``None`` = now.
        sample_limit: Upper bound of requested board entries.
        details: If ``True``, appends a ``samples`` list with every single
            considered arrival — for verbose output. Off by default to keep
            the agent context lean.

    Returns:
        Dict with ``sample_count`` and the metrics. ``sample_count == 0``
        means: no usable sample (caller falls back to mock).

    Raises:
        db_ops.DBServiceError: Sidecar unreachable / destination not resolvable.
    """
    dest_eva = stations.resolve_eva(destination)
    if dest_eva is None:
        raise db_ops.DBServiceError(f"Destination station '{destination}' not resolvable.")

    # A single arrival-board query only covers ~1 h (the dbnav profile cap
    # applies regardless of the requested ``duration``), so page backwards in
    # ~60-min steps and dedupe trains via the ``tripId``.
    end_dt = datetime.fromisoformat(end).astimezone() if end else datetime.now().astimezone()
    step_minutes = 60
    n_chunks = max(1, (lookback_minutes + step_minutes - 1) // step_minutes)
    entries: list[dict] = []
    seen: set = set()
    for k in range(n_chunks):
        chunk_when = end_dt - timedelta(minutes=(k + 1) * step_minutes)
        board = db_ops.arrivals(dest_eva, when=chunk_when, duration=step_minutes, results=sample_limit)
        chunk = board.get("arrivals", []) if isinstance(board, dict) else (board or [])
        for entry in chunk:
            key = entry.get("tripId") or id(entry)
            if key in seen:
                continue
            seen.add(key)
            entries.append(entry)

    delays: list[float] = []
    causes: Counter[str] = Counter()
    samples: list[dict] = []
    cancelled = 0
    for entry in entries:
        if not _is_long_distance(entry):
            continue
        if entry.get("cancelled"):
            cancelled += 1
            samples.append(_sample_row(entry, None, "cancelled"))
            continue
        minutes = _to_minutes(entry.get("delay"))
        if minutes is None:
            samples.append(_sample_row(entry, None, "no real-time data"))
            continue
        delays.append(minutes)
        samples.append(_sample_row(entry, minutes, "counted"))
        for remark in entry.get("remarks") or []:
            if remark.get("type") in ("status", "warning"):
                text = (remark.get("summary") or remark.get("text") or "").strip()
                if text:
                    causes[text] += 1

    sample_count = len(delays)
    if sample_count == 0:
        result = {
            "origin": origin,
            "destination": destination,
            "train": train or None,
            "sample_count": 0,
            "cancellations": cancelled,
        }
        if details:
            result["samples"] = samples
        return result

    punctual = sum(1 for d in delays if d <= _PUNCTUAL_MAX_MINUTES)
    heavy = sum(1 for d in delays if d >= _HEAVY_DELAY_MINUTES)
    result = {
        "origin": origin,
        "destination": destination,
        "train": train or None,
        "window": f"last {lookback_minutes} min until {end_dt:%d.%m %H:%M} (arrival board {destination})",
        "sample_count": sample_count,
        "mean_delay_minutes": round(mean(delays), 1),
        "median_delay_minutes": round(median(delays), 1),
        "p90_delay_minutes": round(_percentile(delays, 90), 1),
        "max_delay_minutes": round(max(delays), 1),
        "on_time_rate_pct": round(100 * punctual / sample_count),
        "delayed_over_15_rate_pct": round(100 * heavy / sample_count),
        "cancellations": cancelled,
        "common_causes": [text for text, _ in causes.most_common(3)],
    }
    if details:
        result["samples"] = samples
    return result


def scheduled_connection(
    origin: str,
    destination: str,
    departure: str | None = None,
) -> dict | None:
    """The scheduled connection (planned times) as an anchor for the ETA forecast.

    Looks up the best connection and reads planned departure/arrival, transfers
    and train name. Walking legs (legs without ``line``) are ignored.

    Args:
        origin: Departure station.
        destination: Destination station.
        departure: Optional departure time (ISO); ``None`` = next connection.

    Returns:
        Dict with planned times + ``realtime_arrival_delay_minutes`` (current
        real-time forecast, if available), or ``None`` if nothing was found.

    Raises:
        db_ops.DBServiceError: Sidecar unreachable / stations not resolvable.
    """
    from_eva = stations.resolve_eva(origin)
    to_eva = stations.resolve_eva(destination)
    if from_eva is None or to_eva is None:
        raise db_ops.DBServiceError(
            f"Station not resolvable (origin={origin!r}, destination={destination!r})."
        )

    data = db_ops.journeys(from_eva, to_eva, departure=departure, results=1, tickets=False)
    journeys = data.get("journeys") or []
    if not journeys:
        return None
    legs = [leg for leg in journeys[0].get("legs", []) if leg.get("line")]
    if not legs:
        return None

    first, last = legs[0], legs[-1]
    line = first.get("line") or {}
    return {
        "origin": origin,
        "destination": destination,
        "train": line.get("name"),
        "planned_departure": first.get("plannedDeparture") or first.get("departure"),
        "planned_arrival": last.get("plannedArrival") or last.get("arrival"),
        "transfers": max(len(legs) - 1, 0),
        "realtime_arrival_delay_minutes": _to_minutes(last.get("arrivalDelay")),
    }
