"""Fügt Mock-Complaints für Lucas Wild in die SQLite-Datenbank ein.

Aufruf (aus dem Journey-Autopilot Ordner):
    python scripts/seed_complaints.py
"""

from __future__ import annotations

import secrets
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from dotenv import load_dotenv
load_dotenv()

from journey_autopilot.persistence import store
from journey_autopilot.demo.accounts import booked_trips

# --- User anlegen (gleiche Daten wie accounts.py) ---
LUCAS = {
    "user_id": "u-lucas-wild",
    "email": "lucas.wild@example.com",
    "display_name": "Lucas Wild",
    "first_name": "Lucas",
    "bahncard": "BahnCard 50, 2. Klasse",
    "bahnbonus_status": "Gold",
    "bahnbonus_points": 14250,
}
store.upsert_user(LUCAS)

# Trips auch gleich importieren (wie beim echten Login)
trips = booked_trips("u-lucas-wild")
store.save_trips("u-lucas-wild", trips)
print(f"✓ User '{LUCAS['user_id']}' und {len(trips)} Trips angelegt")

# --- Alte Complaints löschen ---
from journey_autopilot.persistence.store import _connect
with _connect() as conn:
    conn.execute("DELETE FROM complaints WHERE user_id = ?", ("u-lucas-wild",))
print("✓ Alte Complaints gelöscht")

# --- Mock-Complaints ---
MOCK_COMPLAINTS = [
    {
        # Draft — wartet auf Nutzer-Entscheidung
        "status": "draft",
        "source": "auto_chat",
        "trip_id": "DB-2026-0619-MUC-BLN",
        "origin": "Munich Hbf",
        "destination": "Berlin Hbf",
        "train": "ICE 1006",
        "travel_date": "2026-06-19",
        "delay_minutes": 71,
        "compensation_eur": 22.48,
        "eligible": True,
        "reason": "Einzelticket, 71 Min. Verspätung → 25% von 89.90€ = 22.48€.",
        "claim_via": "DB Reisezentrum oder Servicecenter Fahrgastrechte",
        "notes": ["BahnCard 50 erkannt — Basis ist gezahlter Preis 89.90€"],
        "submitted_at": None,
    },
    {
        # Bereits eingereicht
        "status": "submitted",
        "source": "auto_chat",
        "trip_id": "DB-2026-0601-MUC-FFM",
        "origin": "Munich Hbf",
        "destination": "Frankfurt Hbf",
        "train": "ICE 524",
        "travel_date": "2026-06-01",
        "delay_minutes": 130,
        "compensation_eur": 31.25,
        "eligible": True,
        "reason": "Einzelticket, 130 Min. Verspätung → 50% von 62.50€ = 31.25€.",
        "claim_via": "DB Reisezentrum oder Servicecenter Fahrgastrechte",
        "notes": [],
        "submitted_at": "2026-06-01T18:30:00+00:00",
    },
    {
        # Abgelehnt
        "status": "rejected",
        "source": "auto_chat",
        "trip_id": "DB-2026-0610-MUC-BLN",
        "origin": "Munich Hbf",
        "destination": "Berlin Hbf",
        "train": "ICE 1008",
        "travel_date": "2026-06-10",
        "delay_minutes": 65,
        "compensation_eur": 12.50,
        "eligible": True,
        "reason": "Einzelticket, 65 Min. Verspätung → 25% von 50.00€ = 12.50€.",
        "claim_via": "DB Reisezentrum oder Servicecenter Fahrgastrechte",
        "notes": [],
        "submitted_at": None,
    },
    {
        # BC100 Pauschale — zweiter Draft
        "status": "draft",
        "source": "auto_chat",
        "trip_id": "DB-2026-0615-FFM-BLN",
        "origin": "Frankfurt Hbf",
        "destination": "Berlin Hbf",
        "train": "ICE 798",
        "travel_date": "2026-06-15",
        "delay_minutes": 90,
        "compensation_eur": 10.00,
        "eligible": True,
        "reason": "BahnCard 100 — Pauschale 10.0€ (Klasse 2).",
        "claim_via": "Servicecenter Fahrgastrechte (ausschließlich)",
        "notes": ["Zeitkarten werden nur über das Servicecenter bearbeitet."],
        "submitted_at": None,
    },
]

for payload in MOCK_COMPLAINTS:
    complaint_id = f"cmp-{secrets.token_urlsafe(9)}"
    store.create_complaint("u-lucas-wild", complaint_id, payload)
    print(f"✓ {payload['status']:10} | {payload['trip_id']} | {payload['compensation_eur']}€")

print(f"\n✔ {len(MOCK_COMPLAINTS)} Complaints eingefügt für user 'u-lucas-wild'")
print("  Jetzt einloggen mit: lucas.wild@example.com / demo123")
