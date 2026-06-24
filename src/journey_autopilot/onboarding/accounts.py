"""Simulated DB accounts, bookings, and Outlook calendar.

Deliberate decision (see Context Record): there is NO official DB (Deutsche
Bahn) API for account login or importing booked tickets. Logging in against
bahn.de and the trip import are therefore simulated here — using the same
data structure a real integration would need to deliver later. The interface
(``authenticate``, ``booked_trips``, ``outlook_events``) stays stable.

The trip data is generated relative to "today" so the demo always shows
upcoming trips, regardless of when it's presented.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta

# Lucas' first booked trip is the canonical demo scenario (Munich → Berlin).
# Its fields are kept identical to ``journey_autopilot.mock_data.DEMO_TRIP``
# (same trip_id, route, date) so the dashboard chat drives the very same
# monitoring/reroute/calendar flow as ``scenarios/happy_path.py``: the orchestrator's live
# status, reroute options, and calendar mock are all pinned to this date.
DEMO_DATE = date(2026, 6, 19)
DEMO_TRIP_ID = "DB-2026-0619-MUC-BLN"

# --- Demo accounts ---------------------------------------------------------------
# Passwords in plain text because this is simulated: these are public demo
# credentials shown on the login screen. There is no real account.

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
    """Simulated bahn.de login. Returns the account or ``None``."""
    account = DEMO_ACCOUNTS.get(email.strip().lower())
    if account and account["password"] == password:
        return account["user"]
    return None


# --- Booked trips (trip import) ----------------------------------------------


def _iso(day: date, hhmm: str) -> str:
    h, m = hhmm.split(":")
    return datetime.combine(day, time(int(h), int(m))).isoformat()


def booked_trips(user_id: str, today: date | None = None) -> list[dict]:
    """Upcoming bookings for an account.

    Most trips are generated relative to today (so the demo always shows
    upcoming trips); Lucas' first trip is pinned to the canonical demo
    scenario (see ``DEMO_DATE``) so the dashboard chat exercises the full
    monitoring/reroute flow. Structure follows ``mock_data.DEMO_TRIP``,
    extended with the fields the DB Navigator shows per order (order number,
    coach/seat, price).
    """
    today = today or date.today()
    d2 = today + timedelta(days=5)
    d3 = today + timedelta(days=12)

    if user_id == "u-lucas-wild":
        return [
            {
                # Canonical demo trip — kept in sync with mock_data.DEMO_TRIP so
                # the dashboard chat triggers the full disruption/reroute flow.
                "trip_id": DEMO_TRIP_ID,
                "order_number": "QX7K2P",
                "origin": "Munich Hbf",
                "destination": "Berlin Hbf",
                "train": "ICE 1006",
                "planned_departure": _iso(DEMO_DATE, "08:00"),
                "planned_arrival": _iso(DEMO_DATE, "12:04"),
                "platform": "Platform 18",
                "coach": "Coach 9",
                "seat": "Seat 64, window",
                "travel_class": 2,
                "price_eur": 89.90,
                "purpose": "Client meeting Berlin",
            },
            {
                "trip_id": f"DB-{d2:%Y-%m%d}-BLN-MUC",
                "order_number": "QX7K2P",
                "origin": "Berlin Hbf",
                "destination": "Munich Hbf",
                "train": "ICE 1003",
                "planned_departure": _iso(d2, "16:28"),
                "planned_arrival": _iso(d2, "20:33"),
                "platform": "Platform 4",
                "coach": "Coach 23",
                "seat": "Seat 11, aisle",
                "travel_class": 2,
                "price_eur": 79.90,
                "purpose": "Return trip",
            },
            {
                "trip_id": f"DB-{d3:%Y-%m%d}-MUC-CGN",
                "order_number": "MR4T9A",
                "origin": "Munich Hbf",
                "destination": "Cologne Hbf",
                "train": "ICE 518",
                "planned_departure": _iso(d3, "07:28"),
                "planned_arrival": _iso(d3, "11:58"),
                "platform": "Platform 11",
                "coach": "Coach 31",
                "seat": "Seat 82, window",
                "travel_class": 2,
                "price_eur": 99.90,
                "purpose": "Workshop Cologne",
            },
            {
                "trip_id": f"DB-{today:%Y-%m%d}-CGN-MUC",
                "order_number": "MR4T9A",
                "origin": "Köln Hbf",
                "destination": "München Hbf",
                "train": "ICE 517",
                "planned_departure": _iso(today, "11:54"),
                "planned_arrival": _iso(today, "16:29"),
                "platform": "Platform 6",
                "coach": "Coach 12",
                "seat": "Seat 45, window",
                "travel_class": 2,
                "price_eur": 89.90,
                "purpose": "Return from Cologne",
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
                "platform": "Platform 7",
                "coach": "Coach 11",
                "seat": "Seat 23, window",
                "travel_class": 1,
                "price_eur": 142.50,
                "purpose": "Board meeting",
            },
        ]

    return []


# --- Outlook calendar (simulated Graph API call) ------------------------------


def outlook_events(user_id: str, today: date | None = None) -> list[dict]:
    """Simulated appointments, as a Microsoft Graph call would return them.

    Deliberately matched to the booked trips: the client meeting in Berlin is
    the hard constraint that the planner agent checks reroutes against.
    """
    today = today or date.today()
    d3 = today + timedelta(days=12)

    if user_id == "u-lucas-wild":
        # The Berlin meeting sits on the demo date and is the hard constraint the
        # planner checks the Munich → Berlin reroute against (see mock_data).
        return [
            {
                "title": "Client meeting Berlin (on-site)",
                "location": "Berlin Mitte, Friedrichstraße 100",
                "start": _iso(DEMO_DATE, "14:00"),
                "end": _iso(DEMO_DATE, "17:00"),
                "hard_constraint": True,
            },
            {
                "title": "Team sync (Teams call)",
                "location": "online",
                "start": _iso(DEMO_DATE, "10:30"),
                "end": _iso(DEMO_DATE, "11:00"),
                "hard_constraint": False,
            },
            {
                "title": "Workshop Agentic Systems",
                "location": "Cologne, MediaPark 5",
                "start": _iso(d3, "13:00"),
                "end": _iso(d3, "18:00"),
                "hard_constraint": True,
            },
        ]

    if user_id == "u-erika-muster":
        d2 = today + timedelta(days=5)
        return [
            {
                "title": "Board meeting",
                "location": "Hamburg, Ballindamm 25",
                "start": _iso(d2, "14:30"),
                "end": _iso(d2, "16:30"),
                "hard_constraint": True,
            },
        ]

    return []


# --- Fallback station list -------------------------------------------------------
# For home station autocomplete when the db_service sidecar isn't running.

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
