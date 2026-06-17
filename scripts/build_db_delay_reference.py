"""Baut die historische Verspätungs-Referenz für die Risikobewertung.

Quelle: piebro/deutsche-bahn-data (Hugging Face) — echte DB-Halte mit
``delay_in_min`` über viele Monate, CC BY 4.0. Siehe
https://github.com/piebro/deutsche-bahn-data

Anders als die Live-Ankunftstafel (nur ~5–6 h Echtzeit-Horizont) ist das ein
echtes Pünktlichkeits-ARCHIV. Wir verdichten es einmalig zu kompakten
Kennzahlen je (Bahnhof-EVA, Zugtyp) und committen das Ergebnis als kleine
JSON-Datei. Zur Laufzeit liest die Risk-Pipeline nur diese Referenz — keine
schweren Abhängigkeiten, kein GB-Download.

Granularität: Ankunftsverspätung am Bahnhof (Zeilen mit gesetztem
``arrival_planned_time``), gruppiert je Zugtyp — dieselbe Logik wie die
Live-Tafel, nur über Monate statt Stunden.

Nutzung (dev-time, braucht pandas/pyarrow/huggingface_hub):
    python scripts/build_db_delay_reference.py 2025-08 2025-09 2025-10

Ausgabe: journey_autopilot/data/db_delay_reference.json
"""

from __future__ import annotations

import json
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

import pyarrow.parquet as pq
from huggingface_hub import HfFileSystem

REPO = "datasets/piebro/deutsche-bahn-data"
OUT_PATH = Path(__file__).resolve().parent.parent / "journey_autopilot" / "data" / "db_delay_reference.json"

# Nur diese Spalten ziehen (spaltenweises Parquet -> minimaler Transfer).
COLUMNS = ["eva", "station_name", "train_type", "delay_in_min", "is_canceled", "arrival_planned_time"]

# Fernverkehr — für eine zusätzliche zusammengefasste Kennzahl "FERN".
LONG_DISTANCE = {"ICE", "IC", "EC", "ECE", "RJ", "RJX", "TGV", "NJ", "EN"}

# Verspätung fürs Histogramm begrenzen (robuste Perzentile, beschränkter Speicher).
DELAY_MIN, DELAY_MAX = -30, 600
PUNCTUAL_MAX = 5     # bis 5 Min gilt als pünktlich (DB-Konvention < 6 Min)
HEAVY = 15           # ab 15 Min als deutliche Verspätung


class Acc:
    """Streaming-Akkumulator pro Gruppe (Counter statt aller Einzelwerte)."""

    __slots__ = ("n", "total", "maximum", "hist", "canceled")

    def __init__(self) -> None:
        self.n = 0
        self.total = 0
        self.maximum = DELAY_MIN
        self.hist: Counter[int] = Counter()
        self.canceled = 0

    def add_delay(self, delay: int) -> None:
        d = max(DELAY_MIN, min(DELAY_MAX, int(delay)))
        self.n += 1
        self.total += d
        self.maximum = max(self.maximum, d)
        self.hist[d] += 1


def _percentile_from_hist(hist: Counter[int], n: int, pct: float) -> float:
    """Perzentile direkt aus dem (Minuten-)Histogramm."""
    target = pct / 100.0 * n
    cum = 0
    for value in sorted(hist):
        cum += hist[value]
        if cum >= target:
            return float(value)
    return float(max(hist)) if hist else 0.0


def _finalize(acc: Acc) -> dict:
    n = acc.n
    punctual = sum(c for d, c in acc.hist.items() if d <= PUNCTUAL_MAX)
    heavy = sum(c for d, c in acc.hist.items() if d >= HEAVY)
    return {
        "sample_count": n,
        "mean_delay_minutes": round(acc.total / n, 1),
        "median_delay_minutes": round(_percentile_from_hist(acc.hist, n, 50), 1),
        "p90_delay_minutes": round(_percentile_from_hist(acc.hist, n, 90), 1),
        "max_delay_minutes": float(acc.maximum),
        "on_time_rate_pct": round(100 * punctual / n),
        "delayed_over_15_rate_pct": round(100 * heavy / n),
        "cancellation_rate_pct": round(100 * acc.canceled / (n + acc.canceled)) if (n + acc.canceled) else 0,
    }


def main(months: list[str]) -> None:
    fs = HfFileSystem()
    # per_eva[eva_norm][train_type] = Acc ; '_ALL_' und '_FERN_' sind Sammelgruppen
    per_eva: dict[str, dict[str, Acc]] = defaultdict(lambda: defaultdict(Acc))
    names: dict[str, Counter[str]] = defaultdict(Counter)

    for month in months:
        path = f"{REPO}/monthly_processed_data/data-{month}.parquet"
        print(f"[{month}] öffne {path} ...", flush=True)
        pf = pq.ParquetFile(fs.open(path))
        rows = 0
        for batch in pf.iter_batches(batch_size=200_000, columns=COLUMNS):
            d = batch.to_pydict()
            evas = d["eva"]; types = d["train_type"]; delays = d["delay_in_min"]
            cancels = d["is_canceled"]; arr = d["arrival_planned_time"]; snames = d["station_name"]
            for i in range(len(evas)):
                if arr[i] is None:        # keine Ankunft an diesem Halt -> ignorieren
                    continue
                eva = str(int(evas[i]))   # '08000207' -> '8000207' (wie db-vendo-client)
                ttype = types[i] or "?"
                names[eva][snames[i] or ""] += 1
                groups = per_eva[eva]
                if cancels[i]:
                    for g in (groups[ttype], groups["_ALL_"]):
                        g.canceled += 1
                    if ttype in LONG_DISTANCE:
                        groups["_FERN_"].canceled += 1
                    continue
                delay = delays[i]
                if delay is None:
                    continue
                groups[ttype].add_delay(delay)
                groups["_ALL_"].add_delay(delay)
                if ttype in LONG_DISTANCE:
                    groups["_FERN_"].add_delay(delay)
            rows += len(evas)
            print(f"  ... {rows:,} Zeilen", flush=True)

    # Verdichten
    stations: dict[str, dict] = {}
    for eva, groups in per_eva.items():
        all_acc = groups.get("_ALL_")
        if not all_acc or all_acc.n < 50:   # zu dünn -> weglassen
            continue
        by_type = {
            ttype: _finalize(acc)
            for ttype, acc in groups.items()
            if not ttype.startswith("_") and acc.n >= 30
        }
        station_name = names[eva].most_common(1)[0][0] if names[eva] else None
        entry = {"station_name": station_name, "overall": _finalize(all_acc), "by_train_type": by_type}
        if "_FERN_" in groups and groups["_FERN_"].n >= 30:
            entry["long_distance"] = _finalize(groups["_FERN_"])
        stations[eva] = entry

    out = {
        "_meta": {
            "source": "piebro/deutsche-bahn-data",
            "source_url": "https://github.com/piebro/deutsche-bahn-data",
            "dataset_url": "https://huggingface.co/datasets/piebro/deutsche-bahn-data",
            "license": "CC BY 4.0 (Daten: Deutsche Bahn)",
            "metric": "Ankunftsverspätung in Minuten (delay_in_min an Halten mit arrival_planned_time)",
            "months": months,
            "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "punctual_max_minutes": PUNCTUAL_MAX,
            "stations": len(stations),
        },
        "stations": dict(sorted(stations.items())),
    }
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nGeschrieben: {OUT_PATH}  ({len(stations)} Bahnhöfe, {OUT_PATH.stat().st_size/1024:.0f} kB)")


if __name__ == "__main__":
    args = sys.argv[1:] or ["2025-08", "2025-09", "2025-10"]
    main(args)
