"""Simulierte DB-Konten, Buchungen und Outlook-Kalender.

Bewusste Entscheidung (siehe Context Record): Es gibt KEINE offizielle DB-API
für Konto-Login oder den Import gebuchter Tickets. Der Login gegen bahn.de und
der Trip-Import werden deshalb hier simuliert — mit derselben Datenstruktur, die
eine echte Anbindung später liefern müsste. Die Schnittstelle (``authenticate``,
``booked_trips``, ``outlook_events``) bleibt dabei stabil.

Die Reisedaten werden relativ zu "heute" erzeugt, damit die Demo immer
bevorstehende Reisen zeigt — egal wann sie vorgeführt wird.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta

# --- Demo-Konten ---------------------------------------------------------------
# Passwörter im Klartext, weil simuliert: Es sind öffentliche Demo-Zugänge, die
# auf dem Login-Screen angezeigt werden. Ein echtes Konto gibt es nicht.

DEMO_ACCOUNTS = {
    "lucas.wild@example.com": {
        "password": "demo123",
        "user": {
            "user_id": "u-lucas-wild",
            "email": "lucas.wild@example.com",
            "display_name": "Lucas Wild",
            "first_name": "Lucas",
            "bahncard": "BahnCard 50, 2. Klasse",
            "bahnbonus_status": "Gold",
            "bahnbonus_points": 14250,
        },
    },
    "erika.muster@example.com": {
        "password": "demo123",
        "user": {
            "user_id": "u-erika-muster",
            "email": "erika.muster@example.com",
            "display_name": "Erika Musterfrau",
            "first_name": "Erika",
            "bahncard": "BahnCard 25, 1. Klasse",
            "bahnbonus_status": "Silber",
            "bahnbonus_points": 4810,
        },
    },
}


def authenticate(email: str, password: str) -> dict | None:
    """Simulierter bahn.de-Login. Liefert das Konto oder ``None``."""
    account = DEMO_ACCOUNTS.get(email.strip().lower())
    if account and account["password"] == password:
        return account["user"]
    return None


# --- Gebuchte Reisen (Trip-Import) ----------------------------------------------


def _iso(day: date, hhmm: str) -> str:
    h, m = hhmm.split(":")
    return datetime.combine(day, time(int(h), int(m))).isoformat()


def booked_trips(user_id: str, today: date | None = None) -> list[dict]:
    """Bevorstehende Buchungen eines Kontos — dynamisch relativ zu heute.

    Struktur orientiert sich an ``mock_data.DEMO_TRIP``, ergänzt um die Felder,
    die der DB Navigator pro Auftrag anzeigt (Auftragsnummer, Wagen/Sitz, Preis).
    """
    today = today or date.today()
    d1 = today + timedelta(days=3)
    d2 = today + timedelta(days=5)
    d3 = today + timedelta(days=12)

    if user_id == "u-lucas-wild":
        return [
            {
                "trip_id": f"DB-{d1:%Y-%m%d}-MUC-BLN",
                "order_number": "QX7K2P",
                "origin": "München Hbf",
                "destination": "Berlin Hbf",
                "train": "ICE 1006",
                "planned_departure": _iso(d1, "08:00"),
                "planned_arrival": _iso(d1, "12:04"),
                "platform": "Gleis 18",
                "coach": "Wagen 9",
                "seat": "Platz 64, Fenster",
                "travel_class": 2,
                "price_eur": 89.90,
                "purpose": "Kundentermin Berlin",
            },
            {
                "trip_id": f"DB-{d2:%Y-%m%d}-BLN-MUC",
                "order_number": "QX7K2P",
                "origin": "Berlin Hbf",
                "destination": "München Hbf",
                "train": "ICE 1003",
                "planned_departure": _iso(d2, "16:28"),
                "planned_arrival": _iso(d2, "20:33"),
                "platform": "Gleis 4",
                "coach": "Wagen 23",
                "seat": "Platz 11, Gang",
                "travel_class": 2,
                "price_eur": 79.90,
                "purpose": "Rückreise",
            },
            {
                "trip_id": f"DB-{d3:%Y-%m%d}-MUC-CGN",
                "order_number": "MR4T9A",
                "origin": "München Hbf",
                "destination": "Köln Hbf",
                "train": "ICE 518",
                "planned_departure": _iso(d3, "07:28"),
                "planned_arrival": _iso(d3, "11:58"),
                "platform": "Gleis 11",
                "coach": "Wagen 31",
                "seat": "Platz 82, Fenster",
                "travel_class": 2,
                "price_eur": 99.90,
                "purpose": "Workshop Köln",
            },
        ]

    if user_id == "u-erika-muster":
        return [
            {
                "trip_id": f"DB-{d2:%Y-%m%d}-FRA-HAM",
                "order_number": "ZK1N8B",
                "origin": "Frankfurt (Main) Hbf",
                "destination": "Hamburg Hbf",
                "train": "ICE 774",
                "planned_departure": _iso(d2, "09:13"),
                "planned_arrival": _iso(d2, "12:53"),
                "platform": "Gleis 7",
                "coach": "Wagen 11",
                "seat": "Platz 23, Fenster",
                "travel_class": 1,
                "price_eur": 142.50,
                "purpose": "Vorstandstermin",
            },
        ]

    return []


# --- Outlook-Kalender (simulierter Graph-API-Abruf) ------------------------------


def outlook_events(user_id: str, today: date | None = None) -> list[dict]:
    """Simulierte Termine, wie sie ein Microsoft-Graph-Abruf liefern würde.

    Bewusst passend zu den gebuchten Reisen: Der Kundentermin in Berlin ist der
    harte Constraint, gegen den der Planner-Agent Reroutes prüft.
    """
    today = today or date.today()
    d1 = today + timedelta(days=3)
    d3 = today + timedelta(days=12)

    if user_id == "u-lucas-wild":
        return [
            {
                "title": "Kundentermin Berlin (vor Ort)",
                "location": "Berlin Mitte, Friedrichstraße 100",
                "start": _iso(d1, "14:00"),
                "end": _iso(d1, "17:00"),
                "hard_constraint": True,
            },
            {
                "title": "Team-Sync (Teams-Call)",
                "location": "online",
                "start": _iso(d1, "10:30"),
                "end": _iso(d1, "11:00"),
                "hard_constraint": False,
            },
            {
                "title": "Workshop Agentic Systems",
                "location": "Köln, MediaPark 5",
                "start": _iso(d3, "13:00"),
                "end": _iso(d3, "18:00"),
                "hard_constraint": True,
            },
        ]

    if user_id == "u-erika-muster":
        d2 = today + timedelta(days=5)
        return [
            {
                "title": "Vorstandssitzung",
                "location": "Hamburg, Ballindamm 25",
                "start": _iso(d2, "14:30"),
                "end": _iso(d2, "16:30"),
                "hard_constraint": True,
            },
        ]

    return []


# --- Fallback-Stationsliste -------------------------------------------------------
# Für die Heimatbahnhof-Autocomplete, wenn der db_service-Sidecar nicht läuft.

FALLBACK_STATIONS = [
    {"id": "8000261", "name": "München Hbf"},
    {"id": "8011160", "name": "Berlin Hbf"},
    {"id": "8000207", "name": "Köln Hbf"},
    {"id": "8000105", "name": "Frankfurt (Main) Hbf"},
    {"id": "8002549", "name": "Hamburg Hbf"},
    {"id": "8000096", "name": "Stuttgart Hbf"},
    {"id": "8000085", "name": "Düsseldorf Hbf"},
    {"id": "8000152", "name": "Hannover Hbf"},
    {"id": "8010205", "name": "Leipzig Hbf"},
    {"id": "8000284", "name": "Nürnberg Hbf"},
    {"id": "8000080", "name": "Dortmund Hbf"},
    {"id": "8000098", "name": "Essen Hbf"},
    {"id": "8000244", "name": "Mannheim Hbf"},
    {"id": "8000191", "name": "Karlsruhe Hbf"},
    {"id": "8000013", "name": "Augsburg Hbf"},
    {"id": "8010085", "name": "Dresden Hbf"},
    {"id": "8000036", "name": "Bonn Hbf"},
    {"id": "8000050", "name": "Bremen Hbf"},
    {"id": "8000183", "name": "Kiel Hbf"},
    {"id": "8010224", "name": "Magdeburg Hbf"},
]
