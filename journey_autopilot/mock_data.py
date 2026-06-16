"""Mocked data for the prototype.

Deliberate decision (see project foundation / ADR): We have no real
DB API access. All live ops data is simulated here as fixtures. The
structure is aligned to a realistic scenario for the persona "Lucas Wild"
so that Monitoring and Planner Agents have something meaningful to do.

Later, a real API connection (or an MCP server) will replace exactly these
functions — the tool interface stays the same.
"""

from __future__ import annotations

# --- Demo trip: Lucas Wild, Munich → Berlin (happy/edge scenario) --------------

DEMO_TRIP = {
    "trip_id": "DB-2026-0619-MUC-BLN",
    "passenger": "Lucas Wild",
    "origin": "Munich Hbf",
    "destination": "Berlin Hbf",
    "train": "ICE 1006",
    "planned_departure": "2026-06-19T08:00:00",
    "planned_arrival": "2026-06-19T12:04:00",
}

# Mocked live status of the trip. A signal box malfunction near Nuremberg causes
# growing delay → Monitoring should detect elevated risk.
LIVE_TRIP_STATUS = {
    "DB-2026-0619-MUC-BLN": {
        "trip_id": "DB-2026-0619-MUC-BLN",
        "train": "ICE 1006",
        "current_delay_minutes": 28,
        "trend": "increasing",
        "current_position": "between Nuremberg and Erfurt",
        "incidents": [
            {
                "type": "Signal box malfunction",
                "location": "Nuremberg area",
                "impact": "Individual tracks blocked, cascading delays expected",
            }
        ],
        "connection_risk": "Connection in Berlin-Spandau at risk",
        "data_timestamp": "2026-06-19T09:42:00",
    }
}

# Mocked network-wide disruption status per region.
NETWORK_DISRUPTIONS = {
    "bavaria": [
        {
            "line": "ICE line Nuremberg-Erfurt",
            "type": "Signal box malfunction",
            "severity": "high",
            "expected_resolution": "2026-06-19T11:30:00",
        }
    ],
    "berlin": [],
}

# --- Planner knowledge: reroute alternatives, calendar, passenger rights ------

REROUTE_OPTIONS = {
    ("Munich Hbf", "Berlin Hbf"): [
        {
            "option_id": "R1",
            "description": "Transfer in Erfurt to ICE 1008 towards Berlin",
            "new_arrival": "2026-06-19T12:38:00",
            "transfers": 1,
            "added_delay_minutes": 34,
            "comfort": "Seat reservation transferable",
        },
        {
            "option_id": "R2",
            "description": "Via Leipzig with ICE 1612, then RE to Berlin",
            "new_arrival": "2026-06-19T13:15:00",
            "transfers": 2,
            "added_delay_minutes": 71,
            "comfort": "No reserved seat, more transfers",
        },
    ]
}

# Mocked calendar of the persona. The meeting in Berlin is the hard deadline.
USER_CALENDAR = {
    "2026-06-19": [
        {
            "title": "Client meeting Berlin (on-site)",
            "location": "Berlin Mitte",
            "start": "2026-06-19T14:00:00",
            "hard_constraint": True,
        }
    ]
}

# Simplified passenger rights knowledge base (placeholder for future RAG/ChromaDB).
PASSENGER_RIGHTS = [
    {"min_delay_minutes": 60, "compensation": "25% of ticket price"},
    {"min_delay_minutes": 120, "compensation": "50% of ticket price"},
]

# --- WhatsApp communicator: event fields derived from the existing mock scenario -
# Real phone numbers come from .env at runtime (run_demo.py).
# recipients is populated there from DEMO_TRAVELER_NUMBER / DEMO_CLIENT_NUMBER etc.
_r1 = REROUTE_OPTIONS[("Munich Hbf", "Berlin Hbf")][0]
_meeting = USER_CALENDAR["2026-06-19"][0]

# The WhatsApp communicator messages the traveler in English, so the event fields
# carry an English reroute summary (the orchestrator's REROUTE_OPTIONS above stay
# German for the German orchestrator demo).
DEMO_EVENT_FIELDS: dict = {
    "traveler_name": DEMO_TRIP["passenger"],
    "original_train": DEMO_TRIP["train"],
    "delay_minutes": LIVE_TRIP_STATUS[DEMO_TRIP["trip_id"]]["current_delay_minutes"],
    "reroute_summary": (
        f"Change at Erfurt to ICE 1008 toward Berlin "
        f"(arrival {_r1['new_arrival'][11:16]}, +{_r1['added_delay_minutes']} min)"
    ),
    "meeting_time_original": _meeting["start"][11:16],
    "meeting_time_new": _meeting["start"][11:16],  # Appointment holds — arrival 12:38 is before 14:00
}
