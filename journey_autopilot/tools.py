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


def get_passenger_rights(delay_minutes: int) -> dict:
    """Ermittelt die Fahrgastrechte-/Entschädigungsstufe für eine Verspätung.

    Args:
        delay_minutes: Erwartete Ankunftsverspätung in Minuten.

    Returns:
        Ein Dict mit der zutreffenden Entschädigung (oder einem Hinweis, dass
        unterhalb der Schwelle kein Anspruch besteht).
    """
    applicable = [
        rule
        for rule in mock_data.PASSENGER_RIGHTS
        if delay_minutes >= rule["min_delay_minutes"]
    ]
    if not applicable:
        return {
            "delay_minutes": delay_minutes,
            "compensation": "Unter 60 Minuten — kein Entschädigungsanspruch.",
        }
    best = max(applicable, key=lambda rule: rule["min_delay_minutes"])
    return {"delay_minutes": delay_minutes, "compensation": best["compensation"]}
