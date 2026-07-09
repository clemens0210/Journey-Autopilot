"""Schaltet den Demo-Trip in der Fixture auf "angekommen" — oder zurück.

Complaints werden erst nach Journey-Ende erstellt (``arrived: true`` im
Live-Status, siehe ``onboarding/complaints.py::is_trip_completed``). Dieses
Script simuliert die Ankunft, damit der Flow ohne echten Sidecar demobar ist.

Aufruf (aus dem Journey-Autopilot Ordner):
    python scripts/simulate_arrival.py               # Ankunft mit 71 Min. Verspätung
    python scripts/simulate_arrival.py --delay 130   # Ankunft mit eigener Verspätung
    python scripts/simulate_arrival.py --reset       # zurück zum En-Route-Zustand
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta
from pathlib import Path

FIXTURE = Path("src/journey_autopilot/data/fixtures/happy_path.json")
TRIP_ID = "DB-2026-0619-MUC-BLN"
# current_delay_minutes des eingecheckten En-Route-Zustands der Fixture —
# --reset stellt genau diesen Wert wieder her.
EN_ROUTE_DELAY = 28


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--delay", type=int, default=71,
                        help="finale Verspätung in Minuten (Default: 71)")
    parser.add_argument("--reset", action="store_true",
                        help="Ankunfts-Felder entfernen, En-Route-Zustand wiederherstellen")
    args = parser.parse_args()

    fx = json.loads(FIXTURE.read_text(encoding="utf-8"))
    status = fx["live_trip_status"][TRIP_ID]
    planned = fx["demo_trip"]["planned_arrival"]

    if args.reset:
        for key in ("arrived", "actual_arrival", "planned_arrival"):
            status.pop(key, None)
        status["current_delay_minutes"] = EN_ROUTE_DELAY
        message = f"OK: Zurückgesetzt — Zug wieder unterwegs mit {EN_ROUTE_DELAY} Min. Verspätung"
    else:
        planned_dt = datetime.fromisoformat(planned)
        actual_dt = planned_dt + timedelta(minutes=args.delay)
        status.update({
            "arrived": True,
            "actual_arrival": actual_dt.isoformat(),
            "planned_arrival": planned,
            "current_delay_minutes": args.delay,
        })
        message = (
            f"OK: Zug angekommen mit {args.delay} Min. Verspätung "
            f"({actual_dt.strftime('%H:%M')} statt {planned_dt.strftime('%H:%M')})"
        )

    # Format der eingecheckten Fixture erhalten (2er-Einrückung, LF,
    # Newline am Dateiende), damit jeder Lauf einen minimalen Diff erzeugt.
    with FIXTURE.open("w", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(fx, indent=2, ensure_ascii=False) + "\n")
    print(message)


if __name__ == "__main__":
    main()
