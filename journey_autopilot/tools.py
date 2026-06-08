"""Function-Tools für die Agenten.

In ADK genügt eine getypte Python-Funktion mit Docstring — das Framework wrappt
sie automatisch in ein FunctionTool und leitet das Parameter-Schema aus den
Type-Hints + Docstring ab. Deshalb sind Docstrings und Typen hier nicht Deko,
sondern Teil der API, die das LLM sieht.

Alle Funktionen lesen aktuell aus `mock_data` — sie sind die Einstecksstellen
für echte DB-/Kalender-/RAG-Quellen.
"""

from __future__ import annotations

import os

from . import mock_data
from .calendar import get_calendar_events


def _calendar_configured() -> bool:
    """Return True if MS Entra credentials are present in the environment."""
    return bool(os.getenv("MS_ENTRA_CLIENT_ID"))


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


def get_user_calendar(date: str, user_email: str | None = None) -> dict:
    """Liest die Kalendertermine des Nutzers für ein Datum.

    Wird gebraucht, um harte Deadlines (z. B. ein Vor-Ort-Meeting) gegen
    Reroute-Optionen zu prüfen.

    Nutzt Outlook/Microsoft Graph, wenn die Entra-Credentials in der .env
    hinterlegt sind (MS_ENTRA_CLIENT_ID, MS_ENTRA_TENANT_ID). Ohne
    Konfiguration wird auf Mock-Daten zurückgegriffen.

    Args:
        date: Datum im Format "YYYY-MM-DD", z. B. "2026-06-03".
        user_email: Optionale E-Mail eines anderen Nutzers, dessen Kalender
            abgefragt werden soll. Ohne Angabe wird der eigene Kalender des
            authentifizierten Nutzers verwendet.

    Returns:
        Ein Dict mit der Liste der Termine. `hard_constraint=True` markiert
        unverhandelbare Termine. Enthält `source` ("outlook" oder "mock")
        und ggf. `error`.
    """
    if _calendar_configured():
        try:
            events = get_calendar_events(date, user_email)
            return {"date": date, "events": events, "source": "outlook"}
        except Exception as exc:
            return {"date": date, "events": [], "source": "outlook", "error": str(exc)}

    events = mock_data.USER_CALENDAR.get(date, [])
    return {"date": date, "events": events, "source": "mock"}


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
