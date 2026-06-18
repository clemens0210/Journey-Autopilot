"""Smoke test for the DB connection (db_service sidecar + db_api.py).

Requires the sidecar to be running (`make db-service`) and `requests` to be
installed (`pip install -r requirements.txt`).

Run from the project folder:

    python check_db.py
"""

from journey_autopilot.rerouting import stations
from journey_autopilot.rerouting import db_api


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
