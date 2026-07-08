"""Builds the historical delay reference from local DB monthly data.

Source: piebro/deutsche-bahn-data (CC BY 4.0), downloaded as monthly parquet
files into ``src/journey_autopilot/data/monthly_data/`` (gitignored — too
large to commit). This script aggregates real arrival delays per
(station, train_type) into one small JSON file that ships with the package —
the risk predictor only ever reads that JSON, no pyarrow needed at runtime.

Usage (needs pyarrow: ``pip install journey-autopilot[reference-build]``):
    python scripts/build_delay_stats.py
"""

from __future__ import annotations

import json
from pathlib import Path

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.dataset as ds

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "src/journey_autopilot/data/monthly_data"
OUT_PATH = ROOT / "src/journey_autopilot/data/delay_stats.json"

PUNCTUAL_MAX_MINUTES = 5  # DB convention: delay <= 5 min still counts as on-time
MIN_SAMPLES = 20  # drop (station, train_type) groups too small to be meaningful


def _stats(row: dict) -> dict:
    return {
        "samples": row["delay_in_min_count"],
        "mean_delay": round(row["delay_in_min_mean"], 1),
        "on_time_rate": round(row["on_time_mean"], 2),
        "cancel_rate": round(row["canceled_mean"], 2),
    }


def main() -> None:
    dataset = ds.dataset(sorted(DATA_DIR.glob("*.parquet")), format="parquet")
    table = dataset.to_table(columns=["station_name", "train_type", "delay_in_min", "is_canceled"])
    valid = pc.and_(
        pc.and_(pc.is_valid(table["station_name"]), pc.is_valid(table["train_type"])),
        pc.is_valid(table["delay_in_min"]),
    )
    table = table.filter(valid)
    table = table.append_column("on_time", pc.less_equal(table["delay_in_min"], PUNCTUAL_MAX_MINUTES))
    table = table.append_column("canceled", table["is_canceled"].cast(pa.float64()))

    def aggregate(keys: list[str]) -> list[dict]:
        return table.group_by(keys).aggregate(
            [
                ("delay_in_min", "mean"),
                ("delay_in_min", "count"),
                ("on_time", "mean"),
                ("canceled", "mean"),
            ]
        ).to_pylist()

    stations: dict[str, dict] = {}
    for row in aggregate(["station_name"]):
        stations.setdefault(row["station_name"], {})["ALL"] = _stats(row)
    for row in aggregate(["station_name", "train_type"]):
        if row["delay_in_min_count"] < MIN_SAMPLES:
            continue
        stations.setdefault(row["station_name"], {})[row["train_type"]] = _stats(row)

    result = {"global": _stats(aggregate([])[0]), "stations": stations}
    OUT_PATH.write_text(json.dumps(result, sort_keys=True))
    print(f"wrote {OUT_PATH.name}: {len(stations)} stations, {OUT_PATH.stat().st_size / 1024:.0f} KB")


if __name__ == "__main__":
    main()
