"""Function-Tools für die Agenten.

In ADK genügt eine getypte Python-Funktion mit Docstring — das Framework wrappt
sie automatisch in ein FunctionTool und leitet das Parameter-Schema aus den
Type-Hints + Docstring ab. Deshalb sind Docstrings und Typen hier nicht Deko,
sondern Teil der API, die das LLM sieht.

Alle Funktionen lesen aktuell aus `mock_data` — sie sind die Einstecksstellen
für echte DB-/Kalender-/RAG-Quellen.
"""

from __future__ import annotations

from . import mock_data
from .passenger_rights.rag_store import FahrgastrechteRAG
from .passenger_rights.rights_service import calculate_compensation


# --- Monitoring-Tools ---------------------------------------------------------


def get_live_trip_status(trip_id: str) -> dict:
    """Liefert den aktuellen Live-Zustand einer Bahnreise.

    Args:
        trip_id: Die ID der Reise, z. B. "DB-2026-0603-MUC-BLN".

    Returns:
        Ein Dict mit aktueller Verspätung, Trend, Position, bekannten Vorfällen
        und Anschlussrisiko. Enthält "error", wenn die Reise unbekannt ist.
    """
    status = mock_data.LIVE_TRIP_STATUS.get(trip_id)
    if status is None:
        return {"error": f"Keine Live-Daten für trip_id '{trip_id}' gefunden."}
    return status


def get_network_disruptions(region: str) -> dict:
    """Liefert die aktuelle netzweite Störungslage für eine Region.

    Args:
        region: Region in Kleinbuchstaben, z. B. "bayern" oder "berlin".

    Returns:
        Ein Dict mit der Liste aktiver Störungen für die Region.
    """
    disruptions = mock_data.NETWORK_DISRUPTIONS.get(region.lower(), [])
    return {"region": region, "disruptions": disruptions}


# --- Planner-Tools ------------------------------------------------------------


def find_reroute_options(origin: str, destination: str) -> dict:
    """Findet alternative Verbindungen (Reroute-Optionen) zwischen zwei Bahnhöfen.

    Args:
        origin: Start-Bahnhof, z. B. "München Hbf".
        destination: Ziel-Bahnhof, z. B. "Berlin Hbf".

    Returns:
        Ein Dict mit der Liste möglicher Umleitungen inklusive neuer Ankunftszeit,
        Anzahl Umstiege und Zusatzverspätung.
    """
    options = mock_data.REROUTE_OPTIONS.get((origin, destination), [])
    return {"origin": origin, "destination": destination, "options": options}


def get_user_calendar(date: str) -> dict:
    """Liest die Kalendertermine des Nutzers für ein Datum.

    Wird gebraucht, um harte Deadlines (z. B. ein Vor-Ort-Meeting) gegen
    Reroute-Optionen zu prüfen.

    Args:
        date: Datum im Format "YYYY-MM-DD", z. B. "2026-06-03".

    Returns:
        Ein Dict mit der Liste der Termine. `hard_constraint=True` markiert
        unverhandelbare Termine.
    """
    events = mock_data.USER_CALENDAR.get(date, [])
    return {"date": date, "events": events}


def get_passenger_rights(
    delay_minutes: int,
    ticket_type: str = "einzelticket",
    price_paid: float = 0.0,
    travel_class: int = 2,
    bahncard_type: str = "keine",
) -> dict:
    """Determines passenger rights and calculates the concrete compensation claim.

    Combines two sources:
      1. Deterministic rule logic (rights_service) → exact EUR amount
      2. RAG search in ChromaDB → legal context chunks from bahn.de

    Args:
        delay_minutes:  Expected arrival delay at destination in minutes.
        ticket_type:    Ticket type: "einzelticket" | "zeitkarte_fv" |
                        "zeitkarte_nv" | "bc100" | "deutschland_ticket".
        price_paid:     Ticket price paid in EUR (relevant for single tickets).
        travel_class:   Travel class, 1 or 2 (default: 2).
        bahncard_type:  User's BahnCard: "keine" | "bc25" | "bc50" | "bc100".

    Returns:
        Dict with calculated compensation claim and legal context.
    """
    # 1. Deterministic calculation — no LLM, no network
    compensation = calculate_compensation(
        delay_minutes=delay_minutes,
        ticket_type=ticket_type,
        price_paid=price_paid,
        travel_class=travel_class,
        bahncard_type=bahncard_type,
    )

    # 2. RAG context for the agent — semantically matching chunks
    try:
        rag = FahrgastrechteRAG()
        chunks = rag.retrieve_for_case(
            delay_minutes=delay_minutes,
            ticket_type=ticket_type,
            bahncard_type=bahncard_type,
        )
        legal_context = "\n\n--- Next Section ---\n".join(chunks)
    except Exception as e:
        legal_context = f"Knowledge base temporarily unavailable: {e}"

    return {
        **compensation,
        "legal_context": legal_context,
    }
