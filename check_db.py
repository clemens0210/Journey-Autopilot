"""Smoke-Test für die DB-Anbindung (db_service-Sidecar + db_api.py).

Voraussetzung: der Sidecar läuft (`make db-service`) und `requests` ist
installiert (`pip install -r requirements.txt`).

Ausführen aus dem Projektordner:

    python check_db.py
"""

from journey_autopilot import db_api, stations


def main() -> None:
    print("1) Health:", db_api.health())

    koeln = stations.resolve_eva("Köln Hbf")
    bonn = stations.resolve_eva("Bonn Hbf")
    print(f"2) EVA-Nummern: Köln Hbf={koeln}, Bonn Hbf={bonn}")

    result = db_api.journeys(koeln, bonn, results=20, departure='2026-01-01T09:56:00+02:00')
    journey = result["journeys"][0]
    first = journey["legs"][0]
    last = journey["legs"][-1]
    price = journey.get("price", {}).get("amount")
    print(f"3) Verbindung: {first['departure']} -> {last['arrival']}"
          f"  Preis: {price} EUR" if price else f"3) Verbindung: {first['departure']} -> {last['arrival']}")

    print(result)
    print("\nOK — DB-Anbindung funktioniert.")


if __name__ == "__main__":
    main()
