"""Smoke test for the DB connection (db_service sidecar + integrations/db/).

Requires the sidecar to be running (`cd db_service && npm start`, port 3000)
and `requests` to be installed (`pip install -r requirements.txt`).

Run from the project folder:

    python scripts/check_db.py
"""

import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[1] / "src"
if _SRC.is_dir() and str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from journey_autopilot.integrations.db import stations
from journey_autopilot.integrations.db import ops as db_api


def main() -> None:
    print("1) Health:", db_api.health())

    koeln = stations.resolve_eva("Köln Hbf")
    bonn = stations.resolve_eva("Bonn Hbf")
    print(f"2) EVA numbers: Köln Hbf={koeln}, Bonn Hbf={bonn}")

    result = db_api.journeys(koeln, bonn, results=20, departure='2026-01-01T09:56:00+02:00')
    journey = result["journeys"][0]
    first = journey["legs"][0]
    last = journey["legs"][-1]
    price = journey.get("price", {}).get("amount")
    print(f"3) Connection: {first['departure']} -> {last['arrival']}")
    print(f"  Price: {price} EUR" if price else f"3) Connection: {first['departure']} -> {last['arrival']}")
    print("\nOK — DB connection working.")


if __name__ == "__main__":
    main()
