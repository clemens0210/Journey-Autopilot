"""Historical delay reference, pre-aggregated from real DB data.

Built once by ``scripts/build_delay_stats.py`` from piebro/deutsche-bahn-data
(CC BY 4.0, https://github.com/piebro/deutsche-bahn-data) into
``data/delay_stats.json`` — mean delay, on-time rate and cancellation rate
per (station, train_type). This module only reads that JSON at runtime;
extending the model later just means enriching the aggregation in the build
script and re-running it, the lookup contract here stays the same.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

_PATH = Path(__file__).resolve().parent.parent / "data" / "delay_stats.json"

# The app shows different display names (English / historic) for some
# stations than the DB archive uses — map the ones seen in trip data.
_STATION_ALIASES = {
    "Munich Hbf": "München Hbf",
    "Cologne Hbf": "Köln Hbf",
    "Berlin Hbf": "Berlin Hauptbahnhof",
}


@lru_cache
def _reference() -> dict:
    return json.loads(_PATH.read_text())


def lookup(station: str, train_type: str) -> dict:
    """Delay stats for a station/train type, with graceful fallback.

    Falls back from (station, train_type) to station-wide, then to the
    network-wide average when the station or train type is unknown to the
    archive. Returns ``{"samples", "mean_delay", "on_time_rate",
    "cancel_rate", "basis"}`` — ``basis`` says which level matched.
    """
    ref = _reference()
    station = _STATION_ALIASES.get(station, station)
    by_type = ref["stations"].get(station)
    if by_type and train_type in by_type:
        return {**by_type[train_type], "basis": "station+type"}
    if by_type:
        return {**by_type["ALL"], "basis": "station"}
    return {**ref["global"], "basis": "network"}
