"""Gemockte Daten für den Prototyp.

Bewusste Entscheidung (siehe Projektgrundlage / ADR): Wir haben keinen echten
DB-API-Zugang. Alle Live-Ops-Daten werden hier als Fixtures simuliert. Die
Struktur ist an einem realistischen Szenario der Persona "Lucas Wild"
ausgerichtet, damit Monitoring- und Planner-Agent etwas Sinnvolles zu tun haben.

Später ersetzt eine echte API-Anbindung (oder ein MCP-Server) genau diese
Funktionen — die Tool-Schnittstelle bleibt gleich.
"""

from __future__ import annotations

# --- Demo-Reise: Lucas Wild, München -> Berlin (Happy/Edge-Szenario) ---------

DEMO_TRIP = {
    "trip_id": "DB-2026-0619-MUC-BLN",
    "passenger": "Lucas Wild",
    "origin": "München Hbf",
    "destination": "Berlin Hbf",
    "train": "ICE 1006",
    "planned_departure": "2026-06-19T08:00:00",
    "planned_arrival": "2026-06-19T12:04:00",
}

# Gemockter Live-Zustand der Reise. Ein Stellwerksproblem bei Nürnberg sorgt für
# wachsende Verspätung -> das Monitoring soll erhöhtes Risiko erkennen.
LIVE_TRIP_STATUS = {
    "DB-2026-0619-MUC-BLN": {
        "trip_id": "DB-2026-0619-MUC-BLN",
        "train": "ICE 1006",
        "current_delay_minutes": 28,
        "trend": "steigend",
        "current_position": "zwischen Nürnberg und Erfurt",
        "incidents": [
            {
                "type": "Stellwerksstörung",
                "location": "Raum Nürnberg",
                "impact": "Einzelne Gleise gesperrt, Folgeverspätungen erwartet",
            }
        ],
        "connection_risk": "Anschluss in Berlin-Spandau gefährdet",
        "data_timestamp": "2026-06-19T09:42:00",
    }
}

# Gemockte netzweite Störungslage je Region.
NETWORK_DISRUPTIONS = {
    "bayern": [
        {
            "line": "ICE-Strecke Nürnberg-Erfurt",
            "type": "Stellwerksstörung",
            "severity": "hoch",
            "expected_resolution": "2026-06-19T11:30:00",
        }
    ],
    "berlin": [],
}

# --- Planner-Wissen: Reroute-Alternativen, Kalender, Fahrgastrechte -----------

REROUTE_OPTIONS = {
    ("München Hbf", "Berlin Hbf"): [
        {
            "option_id": "R1",
            "description": "Umstieg in Erfurt auf ICE 1008 Richtung Berlin",
            "new_arrival": "2026-06-19T12:38:00",
            "transfers": 1,
            "added_delay_minutes": 34,
            "comfort": "Sitzplatzreservierung übertragbar",
        },
        {
            "option_id": "R2",
            "description": "Über Leipzig mit ICE 1612, dann RE nach Berlin",
            "new_arrival": "2026-06-19T13:15:00",
            "transfers": 2,
            "added_delay_minutes": 71,
            "comfort": "Kein reservierter Sitzplatz, mehr Umstiege",
        },
    ]
}

# Gemockter Kalender der Persona. Das Meeting in Berlin ist die harte Deadline.
USER_CALENDAR = {
    "2026-06-19": [
        {
            "title": "Kundentermin Berlin (vor Ort)",
            "location": "Berlin Mitte",
            "start": "2026-06-19T14:00:00",
            "hard_constraint": True,
        }
    ]
}

# Vereinfachte Fahrgastrechte-Wissensbasis (steht später für RAG/ChromaDB).
PASSENGER_RIGHTS = [
    {"min_delay_minutes": 60, "compensation": "25 % des Fahrpreises"},
    {"min_delay_minutes": 120, "compensation": "50 % des Fahrpreises"},
]
