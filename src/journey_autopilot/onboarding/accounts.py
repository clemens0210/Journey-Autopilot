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
# monitoring/reroute/calendar flow as ``scenarios/happy_path.py``. The date
# comes from the rebased fixture (mock_data shifts the authored anchor day to
# "today"), so the demo trip, live status, reroutes, and calendar always agree.
from journey_autopilot.mock_data import DEMO_DAY as DEMO_DATE, DEMO_TIME_SHIFT

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
    """Compose a wall-clock time on ``day`` — shifted onto the demo clock.

    ``DEMO_TIME_SHIFT`` is the start-relative anchoring mock_data applies to
    every fixture datetime (demo trip departs ~90 min before app start).
    Adding the same delta here keeps the simulated bookings and calendar
    events on exactly the fixture's timeline.
    """
    h, m = hhmm.split(":")
    return (datetime.combine(day, time(int(h), int(m))) + DEMO_TIME_SHIFT).isoformat()


def booked_trips(user_id: str, today: date | None = None) -> list[dict]:
    """Bookings for an account — Lucas carries the three demo trips.

    1. ``DB-FRA-MUC`` (yesterday, direct, arrived +128 min): drives the
       passenger-rights/complaints demo — its final delay is scripted in the
       fixture's ``live_trip_status`` with ``arrived: true``.
    2. The canonical main trip (today, two transfers): pinned to
       ``mock_data.DEMO_TRIP`` (same trip_id/route/times/legs) so the dashboard
       chat exercises the full monitoring → reroute → calendar → email flow.
    3. ``DB-MUC-HAM`` (next week): filler so the dashboard shows a future trip.

    Trips are generated relative to today, so the set stays evergreen. Structure
    follows ``mock_data.DEMO_TRIP``, extended with the fields the DB Navigator
    shows per order (order number, coach/seat, price).
    """
    today = today or date.today()
    yesterday = today - timedelta(days=1)
    next_week = today + timedelta(days=8)

    if user_id == "u-lucas-wild":
        return [
            {
                # Yesterday's heavily delayed trip → complaints demo. trip_id is
                # route-stable (not date-encoded) so a re-login on a later day
                # upserts the SAME row and the relative date stays "yesterday".
                # Delay/arrival state lives in the fixture's live_trip_status
                # under this id (+128 min, arrived) — 128 min ≥ 120 means 50%
                # of the 79.90 € fare (39.95 €) per EU passenger rights.
                "trip_id": "DB-FRA-MUC",
                "order_number": "KL3M7Q",
                "origin": "Frankfurt (Main) Hbf",
                "destination": "Munich Hbf",
                "train": "ICE 521",
                "planned_departure": _iso(yesterday, "17:14"),
                "planned_arrival": _iso(yesterday, "20:32"),
                "platform": "Platform 7",
                "coach": "Coach 27",
                "seat": "Seat 31, window",
                "travel_class": 2,
                "price_eur": 79.90,
                "purpose": "Return from Frankfurt",
            },
            {
                # Canonical main demo trip — kept in sync with mock_data.DEMO_TRIP
                # so the dashboard chat triggers the full disruption/reroute flow.
                # Two transfers (Nuremberg, Erfurt); the scripted +55 min on the
                # first leg kills the 11:16 Nuremberg connection, and with the
                # line blocked until ~13:00 the earliest arrival (16:25) misses
                # the 16:00 Berlin meeting. The explicit legs render the real
                # itinerary on the trip card and the trip-detail screen
                # (trip_journey uses them).
                "trip_id": DEMO_TRIP_ID,
                "order_number": "QX7K2P",
                "origin": "Munich Hbf",
                "destination": "Berlin Hbf",
                "train": "ICE 528",
                "trains": ["ICE 528", "ICE 1537", "ICE 802"],
                "planned_departure": _iso(DEMO_DATE, "10:02"),
                "planned_arrival": _iso(DEMO_DATE, "14:10"),
                "platform": "Platform 18",
                "coach": "Coach 9",
                "seat": "Seat 64, window",
                "travel_class": 2,
                "price_eur": 89.90,
                "purpose": "Client meeting Berlin",
                "legs": [
                    {
                        "train": "ICE 528",
                        "origin": "Munich Hbf",
                        "destination": "Nuremberg Hbf",
                        "planned_departure": _iso(DEMO_DATE, "10:02"),
                        "planned_arrival": _iso(DEMO_DATE, "11:04"),
                        "platform": "18",
                        "arrival_platform": "8",
                    },
                    {
                        "train": "ICE 1537",
                        "origin": "Nuremberg Hbf",
                        "destination": "Erfurt Hbf",
                        "planned_departure": _iso(DEMO_DATE, "11:16"),
                        "planned_arrival": _iso(DEMO_DATE, "12:08"),
                        "platform": "6",
                        "arrival_platform": "2",
                    },
                    {
                        "train": "ICE 802",
                        "origin": "Erfurt Hbf",
                        "destination": "Berlin Hbf",
                        "planned_departure": _iso(DEMO_DATE, "12:20"),
                        "planned_arrival": _iso(DEMO_DATE, "14:10"),
                        "platform": "7",
                        "arrival_platform": "11",
                    },
                ],
            },
            {
                # Next week's trip — dashboard filler, not used in the demo.
                "trip_id": "DB-MUC-HAM",
                "order_number": "TP8W2C",
                "origin": "Munich Hbf",
                "destination": "Hamburg Hbf",
                "train": "ICE 786",
                "planned_departure": _iso(next_week, "09:17"),
                "planned_arrival": _iso(next_week, "15:01"),
                "platform": "Platform 22",
                "coach": "Coach 25",
                "seat": "Seat 84, aisle",
                "travel_class": 2,
                "price_eur": 109.90,
                "purpose": "Partner workshop Hamburg",
            },
        ]

    if user_id == "u-erika-muster":
        d2 = today + timedelta(days=5)
        return [
            {
                "trip_id": "DB-FRA-HAM",
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


# --- Journey details (simulated itinerary for the trip-detail screen) ---------

# Routes that involve a transfer in the simulated network; everything else is
# rendered as a direct connection. Mirrors what a DB journey-details API would
# return for the booked ticket.
_JOURNEY_VIA = {
    ("Munich Hbf", "Cologne Hbf"): {"station": "Frankfurt (Main) Hbf", "second_train": "ICE 924"},
    ("Frankfurt (Main) Hbf", "Hamburg Hbf"): {"station": "Hannover Hbf", "second_train": "ICE 787"},
}

_TRANSFER_MINUTES = 14


def _mock_platform(station: str) -> str:
    """Deterministic platform number so the itinerary is stable across reloads."""
    return str(sum(station.encode()) % 18 + 1)


def _leg(train: str, origin: tuple, destination: tuple) -> dict:
    (o_name, o_time, o_plat), (d_name, d_time, d_plat) = origin, destination
    return {
        "train": train,
        "direction": d_name,
        "origin": {"name": o_name, "planned": o_time.isoformat(timespec="minutes"), "platform": o_plat},
        "destination": {"name": d_name, "planned": d_time.isoformat(timespec="minutes"), "platform": d_plat},
    }


def trip_journey(trip: dict) -> list[dict]:
    """Expand a booked trip into its itinerary legs (simulated journey details).

    Returns one entry per train leg with per-stop planned times and platforms —
    the structure the trip-detail screen renders. Live delays and risk
    forecasts are layered on top by the API endpoint.

    Trips booked via the journey search carry their real itinerary in
    ``trip["legs"]`` — those are used as-is; only the demo trips get the
    simulated expansion below.
    """
    if trip.get("legs"):
        return [
            _leg(
                leg.get("train") or "?",
                (
                    leg["origin"],
                    datetime.fromisoformat(leg["planned_departure"]),
                    leg.get("platform") or _mock_platform(leg["origin"]),
                ),
                (
                    leg["destination"],
                    datetime.fromisoformat(leg["planned_arrival"]),
                    leg.get("arrival_platform") or _mock_platform(leg["destination"]),
                ),
            )
            for leg in trip["legs"]
        ]

    dep = datetime.fromisoformat(trip["planned_departure"])
    arr = datetime.fromisoformat(trip["planned_arrival"])
    first_platform = (
        (trip.get("platform") or "").removeprefix("Platform ").strip()
        or _mock_platform(trip["origin"])
    )

    via = _JOURNEY_VIA.get((trip["origin"], trip["destination"]))
    if via is None:
        return [_leg(
            trip["train"],
            (trip["origin"], dep, first_platform),
            (trip["destination"], arr, _mock_platform(trip["destination"])),
        )]

    # First leg covers ~55% of the riding time, then a fixed transfer window.
    ride_minutes = (arr - dep).total_seconds() / 60 - _TRANSFER_MINUTES
    via_arr = dep + timedelta(minutes=round(ride_minutes * 0.55))
    via_dep = via_arr + timedelta(minutes=_TRANSFER_MINUTES)
    return [
        _leg(
            trip["train"],
            (trip["origin"], dep, first_platform),
            (via["station"], via_arr, _mock_platform(via["station"])),
        ),
        _leg(
            via["second_train"],
            (via["station"], via_dep, _mock_platform(via["station"] + " dep")),
            (trip["destination"], arr, _mock_platform(trip["destination"])),
        ),
    ]


# --- Outlook calendar (simulated Graph API call) ------------------------------


def outlook_events(user_id: str, today: date | None = None) -> list[dict]:
    """Simulated appointments, as a Microsoft Graph call would return them.

    Deliberately matched to the booked trips: the client meeting in Berlin is
    the hard constraint that the planner agent checks reroutes against.
    """
    today = today or date.today()
    d2 = today + timedelta(days=7)
    d3 = today + timedelta(days=12)

    if user_id == "u-lucas-wild":
        # Chronological: one morning event and the Berlin client meeting on the
        # demo date (DEMO_DATE rebases to "today"), then two future events.
        # The Berlin meeting is the hard constraint the planner checks the
        # Munich → Berlin reroute against (see mock_data) — keep its date/time
        # in sync with fixtures/happy_path.json. Contact fields mirror the real
        # Graph mapper schema (integrations/outlook/mapper.py) so the
        # notice-email flow works in demo mode too.
        return [
            {
                # Authored "now" is ~11:32 (departure 10:02 + 90 min lead), so
                # this event always lands shortly after the app was started —
                # the calendar preview shows a meeting in the demo window.
                "title": "Team sync (Teams call)",
                "location": "online",
                "start": _iso(DEMO_DATE, "11:45"),
                "end": _iso(DEMO_DATE, "12:15"),
                "hard_constraint": False,
                "organizer_name": "Lucas Wild",
                "organizer_email": "lucas.wild@example.com",
                "attendee_emails": ["team@example.com"],
                "self_organized": True,
            },
            {
                "title": "Client meeting Berlin (on-site)",
                "location": "Berlin Mitte, Friedrichstraße 100",
                "start": _iso(DEMO_DATE, "16:00"),
                "end": _iso(DEMO_DATE, "18:00"),
                "hard_constraint": True,
                "organizer_name": "Anna Client",
                "organizer_email": "anna.client@example.com",
                "attendee_emails": ["lucas.wild@example.com"],
                "self_organized": False,
            },
            {
                "title": "Quarterly review (Teams call)",
                "location": "online",
                "start": _iso(d2, "09:30"),
                "end": _iso(d2, "11:00"),
                "hard_constraint": False,
                "organizer_name": "Lucas Wild",
                "organizer_email": "lucas.wild@example.com",
                "attendee_emails": ["team@example.com"],
                "self_organized": True,
            },
            {
                "title": "Workshop Agentic Systems",
                "location": "Cologne, MediaPark 5",
                "start": _iso(d3, "13:00"),
                "end": _iso(d3, "18:00"),
                "hard_constraint": True,
                "organizer_name": "Workshop Office",
                "organizer_email": "office@agentic-workshop.example.com",
                "attendee_emails": ["lucas.wild@example.com"],
                "self_organized": False,
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
                "organizer_name": "Vorstandsbüro",
                "organizer_email": "vorstand@example.com",
                "attendee_emails": ["erika.muster@example.com"],
                "self_organized": False,
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
