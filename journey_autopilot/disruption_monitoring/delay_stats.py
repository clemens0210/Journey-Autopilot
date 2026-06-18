"""Delay statistics for a connection from real DB data.

Like ``stations.py``, this builds on ``db_api`` and is the place where raw DB
boards become reliable metrics for the **upfront risk assessment**
(module ``risk.py``). The aggregation happens here deterministically in
Python — the agent is meant to assess, not compute.

Why use the arrival board as "historical data"? ``db-vendo-client`` offers NO
real delay-archive endpoint. Empirically, though, the DB API carries actual
delays in a rolling window of roughly 5-6 hours around "now" — including for
the recent PAST (trains that have already arrived). Older days only deliver
the scheduled timetable without delay (tested: from ~7 h back and for
previous days, ``delay`` is consistently ``None``).

This is exactly the window we use: we read the arrival board of the
destination station for the last few hours (window deliberately placed in
the recent past). Every long-distance train listed there that has already
arrived thus carries its ACTUALLY occurred delay — not just a forecast. A
real, DB-backed signal; a punctuality archive would later only replace this
one function, the interface would stay the same.
"""

from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timedelta
from functools import lru_cache
from pathlib import Path
from statistics import mean, median
from typing import Any

from .rerouting import db_api

from ..rerouting import stations

# Long-distance traffic — for selecting the matching metrics group in the archive.
_LONG_DISTANCE = {"ICE", "IC", "EC", "ECE", "RJ", "RJX", "TGV", "NJ", "EN"}

# Historical delay reference (pre-built from piebro/deutsche-bahn-data,
# see scripts/build_db_delay_reference.py). Ships as compact JSON in the package.
_REFERENCE_PATH = Path(__file__).resolve().parent / "data" / "db_delay_reference.json"

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


def _is_long_distance(entry: dict) -> bool:
    """Long-distance (ICE/IC/EC)? Other products would distort the corridor statistics."""
    line = entry.get("line") or {}
    if line.get("product") in ("nationalExpress", "national"):
        return True
    name = str(line.get("name") or "")
    return name.startswith(("ICE", "IC", "EC"))


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
        "from": entry.get("provenance") or entry.get("origin", {}).get("name"),
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
            real-time horizon (~5-6 h); further back the DB only delivers
            scheduled times without delay.
        end: Window end as ISO time; ``None`` = now. Evaluated is
            ``[end - lookback_minutes, end]``.
        sample_limit: Upper bound of requested board entries.
        details: If ``True``, appends a ``samples`` list with every single
            considered arrival (train, origin, delay, status) — for
            transparent/verbose output. Off by default to keep the agent
            context lean.

    Returns:
        Dict with ``sample_count`` and the metrics. ``sample_count == 0``
        means: no usable sample (caller falls back to mock).

    Raises:
        db_api.DBServiceError: Sidecar unreachable / destination not resolvable.
    """
    dest_eva = stations.resolve_eva(destination)
    if dest_eva is None:
        raise db_api.DBServiceError(f"Destination station '{destination}' not resolvable.")

    # Place the window in the recent past: already-arrived trains carry their
    # actually occurred delay, not just a forecast.
    #
    # A single arrival-board query only covers ~1 h though (the dbnav profile
    # cap applies regardless of the requested ``duration``). For a larger
    # window we therefore page backwards in ~60-min steps and dedupe trains
    # via the ``tripId``.
    end_dt = datetime.fromisoformat(end).astimezone() if end else datetime.now().astimezone()
    step_minutes = 60
    n_chunks = max(1, (lookback_minutes + step_minutes - 1) // step_minutes)
    entries: list[dict] = []
    seen: set = set()
    for k in range(n_chunks):
        chunk_when = end_dt - timedelta(minutes=(k + 1) * step_minutes)
        board = db_api.arrivals(dest_eva, when=chunk_when, duration=step_minutes, results=sample_limit)
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
        db_api.DBServiceError: Sidecar unreachable / stations not resolvable.
    """
    from_eva = stations.resolve_eva(origin)
    to_eva = stations.resolve_eva(destination)
    if from_eva is None or to_eva is None:
        raise db_api.DBServiceError(
            f"Station not resolvable (origin={origin!r}, destination={destination!r})."
        )

    data = db_api.journeys(from_eva, to_eva, departure=departure, results=1, tickets=False)
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


# --- Historical reference (multi-month archive, offline) ---------------------
#
# Unlike the live board (~5-6 h real-time), this is a real punctuality ARCHIVE
# spanning months — the reliable baseline for the risk score. Source:
# piebro/deutsche-bahn-data (CC BY 4.0). Pre-aggregated per (EVA, train type);
# at runtime just a small JSON read, no heavy dependencies.


@lru_cache(maxsize=1)
def _load_reference() -> dict:
    """Loads the committed reference JSON once (or {} if not present)."""
    if not _REFERENCE_PATH.exists():
        return {}
    try:
        return json.loads(_REFERENCE_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _norm_name(name: str) -> str:
    """Robustly normalize a station name ('Berlin Hbf' ~ 'Berlin Hauptbahnhof')."""
    n = (name or "").lower().strip().replace("hauptbahnhof", "hbf")
    for ch in " .,()-/":
        n = n.replace(ch, "")
    return n


@lru_cache(maxsize=1)
def _name_index() -> dict:
    """{normalized name -> EVA} from the reference — fallback without sidecar."""
    index: dict[str, str] = {}
    for eva, entry in _load_reference().get("stations", {}).items():
        key = _norm_name(entry.get("station_name") or "")
        if key:
            index.setdefault(key, eva)
    return index


def _train_type(train: str) -> str | None:
    """'ICE 1006' -> 'ICE'."""
    tokens = (train or "").strip().split()
    return tokens[0].upper() if tokens else None


def historical_reference(destination: str, train: str = "") -> dict | None:
    """Historical delay metrics at the destination station from the archive.

    Selects the matching metrics group: first the specific train type (from
    ``train``), then the long-distance aggregate, then the station's overall
    value. Station resolution first via the EVA (sidecar), otherwise via a
    name index — the reference thus also works offline.

    Returns:
        Dict with metrics + metadata (months, source), or ``None`` if the
        station is not in the archive / no reference is available.
    """
    ref = _load_reference()
    stations_ = ref.get("stations") or {}
    if not stations_:
        return None

    eva = None
    try:
        resolved = stations.resolve_eva(destination)
        eva = str(int(resolved)) if resolved else None
    except Exception:
        eva = None  # Sidecar unreachable -> try name index

    entry = stations_.get(eva) if eva else None
    if entry is None:
        entry = stations_.get(_name_index().get(_norm_name(destination), ""))
    if entry is None:
        return None

    ttype = _train_type(train)
    by_type = entry.get("by_train_type") or {}
    if ttype and ttype in by_type:
        kpis, basis = by_type[ttype], ttype
    elif ttype in _LONG_DISTANCE and entry.get("long_distance"):
        kpis, basis = entry["long_distance"], "long-distance"
    elif entry.get("long_distance"):
        kpis, basis = entry["long_distance"], "long-distance"
    else:
        kpis, basis = entry["overall"], "all train types"

    meta = ref.get("_meta") or {}
    return {
        "destination": destination,
        "station_name": entry.get("station_name"),
        "basis": basis,
        "months": meta.get("months"),
        "source": "db_history_archive",
        "source_url": meta.get("source_url"),
        "license": meta.get("license"),
        **kpis,
    }
