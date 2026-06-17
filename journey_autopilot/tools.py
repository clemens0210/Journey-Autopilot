"""Function-Tools für die Agenten.

In ADK genügt eine getypte Python-Funktion mit Docstring — das Framework wrappt
sie automatisch in ein FunctionTool und leitet das Parameter-Schema aus den
Type-Hints + Docstring ab. Deshalb sind Docstrings und Typen hier nicht Deko,
sondern Teil der API, die das LLM sieht.

Alle Funktionen lesen aktuell aus `mock_data` — sie sind die Einstecksstellen
für echte DB-/Kalender-/RAG-Quellen.
"""

from __future__ import annotations

from . import db_api, delay_stats, mock_data


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


def get_user_profile() -> dict:
    """Liest das persönliche Präferenzprofil des Nutzers aus dem Onboarding.

    Enthält Klasse, Sitzplatzwünsche, die Tempo-vs-Komfort-Abwägung (0 = maximaler
    Komfort, 100 = schnellste Ankunft), maximale Umstiege, Heimatbahnhof, späteste
    Heimkehr sowie das Autonomie-Level. Reroute-Optionen sollen gegen dieses
    Profil bewertet werden.

    Returns:
        Ein Dict mit dem Profil, oder mit "error", wenn noch kein Onboarding
        durchlaufen wurde.
    """
    try:
        # Lazy Import: hält das ADK-Paket unabhängig vom Onboarding-Paket,
        # solange das Tool nicht aufgerufen wird.
        from onboarding import store

        profile = store.any_profile()
    except Exception as exc:  # Onboarding-Paket/DB nicht verfügbar
        return {"error": f"Profil nicht lesbar: {exc}"}
    if profile is None:
        return {"error": "Kein Nutzerprofil vorhanden — Onboarding noch nicht durchlaufen."}
    return profile


def get_upcoming_trips() -> dict:
    """Liefert die im Onboarding importierten, bevorstehenden Reisen des Nutzers.

    Returns:
        Ein Dict mit der Liste der überwachten Reisen (trip_id, Start, Ziel, Zug,
        Soll-Zeiten). Fällt auf die Demo-Reise zurück, wenn kein Onboarding
        durchlaufen wurde.
    """
    try:
        from onboarding import store

        profile = store.any_profile()
        if profile is not None:
            return {"trips": store.get_trips(profile["user_id"])}
    except Exception:
        pass
    return {"trips": [mock_data.DEMO_TRIP], "note": "Fallback: Demo-Reise (kein Onboarding-Profil)."}


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


# --- Risk-Tools (Vorab-Risiko, vor Reisebeginn) -------------------------------


def get_connection_delay_reference(origin: str, destination: str, train: str = "") -> dict:
    """Liefert die historische Pünktlichkeits-Referenz der Verbindung (Monats-Archiv).

    Die belastbare Baseline für die Risikobewertung: Wie pünktlich kommen Züge
    dieses Typs am Zielbahnhof über MEHRERE MONATE an? Quelle ist ein echtes
    Verspätungs-Archiv (piebro/deutsche-bahn-data, DB-Daten, CC BY 4.0), vorab zu
    Kennzahlen je Bahnhof und Zugtyp verdichtet. Ergänzt `get_connection_delay_history`
    (nur letzte Stunden, aktuelle Lage): das Archiv liefert den langfristigen
    Normalfall, die Live-Historie die heutige Situation.

    Args:
        origin: Start-Bahnhof (nur Kontext; gewertet wird die Ankunft am Ziel).
        destination: Ziel-Bahnhof, z. B. "Berlin Hbf".
        train: Optionaler Zugname (z. B. "ICE 1006") — bestimmt den Zugtyp.

    Returns:
        Ein Dict mit `sample_count`, mittlerer/median/p90-Verspätung,
        Pünktlichkeitsquote, Ausfallquote, der verwendeten `basis` (Zugtyp),
        den abgedeckten `months` und `source="db_history_archive"`. Enthält
        "error", wenn der Bahnhof nicht im Archiv liegt.
    """
    ref = delay_stats.historical_reference(destination, train=train)
    if ref is None:
        return {
            "origin": origin,
            "destination": destination,
            "error": "Keine historische Referenz für diesen Zielbahnhof verfügbar.",
        }
    ref["origin"] = origin
    return ref


def get_connection_delay_history(origin: str, destination: str, train: str = "") -> dict:
    """Liefert Verspätungs-Kennzahlen einer Verbindung aus Vergangenheitsdaten.

    Die Datenbasis für die Vorab-Risikobewertung: Wie pünktlich sind die Züge
    dieser Verbindung in der Vergangenheit angekommen? Versucht zuerst echte
    DB-Daten über den db_service-Sidecar (Ankunftstafel am Ziel); ist der
    Sidecar nicht erreichbar oder liefert kein Sample, greift eine simulierte
    Historie. Das Feld `source` macht transparent, woher die Zahlen stammen.

    Args:
        origin: Start-Bahnhof, z. B. "München Hbf".
        destination: Ziel-Bahnhof, z. B. "Berlin Hbf".
        train: Optionaler Zugname (z. B. "ICE 1006"), nur als Kontext.

    Returns:
        Ein Dict mit `sample_count`, mittlerer/median/p90-Verspätung,
        Pünktlichkeitsquote, Ausfällen, häufigsten Ursachen und `source`
        ("db_service_live" | "mock_history"). Enthält "error", wenn für die
        Verbindung weder Live- noch Mock-Daten vorliegen.
    """
    try:
        stats = delay_stats.connection_delay_history(origin, destination, train=train)
        if stats.get("sample_count", 0) > 0:
            stats["source"] = "db_service_live"
            return stats
    except db_api.DBServiceError:
        pass  # Sidecar nicht erreichbar -> simulierte Historie
    except Exception:
        pass  # unerwartetes Parsing-Problem -> simulierte Historie

    mock = mock_data.CONNECTION_DELAY_HISTORY.get((origin, destination))
    if mock is None:
        return {
            "origin": origin,
            "destination": destination,
            "error": "Keine Verspätungs-Historie für diese Verbindung verfügbar.",
        }
    result = dict(mock)
    result.update(
        {"origin": origin, "destination": destination, "train": train or None, "source": "mock_history"}
    )
    return result


def get_planned_connection(origin: str, destination: str, departure: str = "") -> dict:
    """Liefert die geplante Verbindung (Soll-Zeiten) als Anker für die ETA.

    Das Risk-Modul braucht die geplante Ankunftszeit, um daraus die
    voraussichtliche Ankunft (ETA = Soll-Ankunft + erwartete Verspätung) zu
    bilden. Versucht echte DB-Daten über den db_service-Sidecar; fällt sonst auf
    simulierte Soll-Zeiten zurück.

    Args:
        origin: Start-Bahnhof, z. B. "München Hbf".
        destination: Ziel-Bahnhof, z. B. "Berlin Hbf".
        departure: Optionale Abfahrtszeit (ISO "YYYY-MM-DDTHH:MM:SS"); leer =
            nächste Verbindung.

    Returns:
        Ein Dict mit `train`, `planned_departure`, `planned_arrival`,
        `transfers`, einer etwaigen Echtzeit-Ankunftsverspätung und `source`.
        Enthält "error", wenn keine Verbindung gefunden wurde.
    """
    try:
        conn = delay_stats.scheduled_connection(origin, destination, departure or None)
        if conn:
            conn["source"] = "db_service_live"
            return conn
    except db_api.DBServiceError:
        pass  # Sidecar nicht erreichbar -> simulierte Soll-Zeiten
    except Exception:
        pass

    mock = mock_data.PLANNED_CONNECTIONS.get((origin, destination))
    if mock is None:
        return {
            "origin": origin,
            "destination": destination,
            "error": "Keine geplante Verbindung für diese Strecke gefunden.",
        }
    result = dict(mock)
    result.update({"origin": origin, "destination": destination, "source": "mock_planned"})
    return result
