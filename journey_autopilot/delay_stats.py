"""Verspätungs-Statistik einer Verbindung aus echten DB-Daten.

Baut wie ``stations.py`` auf ``db_api`` auf und ist die Stelle, an der aus den
rohen DB-Boards belastbare Kennzahlen für die **Vorab-Risikobewertung** werden
(Modul ``risk.py``). Die Aggregation passiert hier deterministisch in Python —
der Agent soll bewerten, nicht rechnen.

Warum die Ankunftstafel als "Vergangenheitsdaten"? ``db-vendo-client`` bietet
KEINEN echten Verspätungs-Archiv-Endpunkt. Empirisch trägt die DB-API Ist-
Verspätungen aber in einem rollierenden Fenster von rund 5–6 Stunden um "jetzt" —
auch für die jüngste VERGANGENHEIT (Züge, die bereits angekommen sind). Ältere
Tage liefern nur den Soll-Fahrplan ohne Verspätung (getestet: ab ~7 h zurück und
für Vortage ist ``delay`` durchgängig ``None``).

Genau dieses Fenster nutzen wir: Wir lesen die Ankunftstafel des Zielbahnhofs für
die letzten Stunden aus (Fenster bewusst in die jüngste Vergangenheit gelegt).
Jeder dort gelistete, bereits angekommene Fernzug trägt so seine TATSÄCHLICH
eingetretene Verspätung — nicht nur eine Prognose. Ein echtes, db-gestütztes
Signal; ein Pünktlichkeits-Archiv würde später nur diese eine Funktion ersetzen,
die Schnittstelle bliebe gleich.
"""

from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timedelta
from functools import lru_cache
from pathlib import Path
from statistics import mean, median
from typing import Any

from . import db_api, stations

# Fernverkehr — für die Auswahl der passenden Kennzahlen-Gruppe im Archiv.
_LONG_DISTANCE = {"ICE", "IC", "EC", "ECE", "RJ", "RJX", "TGV", "NJ", "EN"}

# Historische Verspätungs-Referenz (vorab gebaut aus piebro/deutsche-bahn-data,
# siehe scripts/build_db_delay_reference.py). Liegt als kompakte JSON im Paket.
_REFERENCE_PATH = Path(__file__).resolve().parent / "data" / "db_delay_reference.json"

# Schwellen (in Minuten) für die abgeleiteten Quoten.
_PUNCTUAL_MAX_MINUTES = 5      # bis 5 Min gilt als pünktlich (DB-Konvention < 6 Min)
_HEAVY_DELAY_MINUTES = 15      # ab 15 Min als deutliche Verspätung gezählt


def _to_minutes(delay_seconds: Any) -> float | None:
    """``delay`` (Sekunden, kann ``None`` sein) -> Minuten, sonst ``None``."""
    if delay_seconds is None:
        return None
    try:
        return float(delay_seconds) / 60.0
    except (TypeError, ValueError):
        return None


def _is_long_distance(entry: dict) -> bool:
    """Fernverkehr (ICE/IC/EC)? Andere Produkte verfälschen die Korridor-Statistik."""
    line = entry.get("line") or {}
    if line.get("product") in ("nationalExpress", "national"):
        return True
    name = str(line.get("name") or "")
    return name.startswith(("ICE", "IC", "EC"))


def _percentile(values: list[float], pct: float) -> float:
    """Linear interpolierte Perzentile (stdlib-only, kein numpy)."""
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    rank = (pct / 100.0) * (len(ordered) - 1)
    low = int(rank)
    high = min(low + 1, len(ordered) - 1)
    frac = rank - low
    return ordered[low] + (ordered[high] - ordered[low]) * frac


def _sample_row(entry: dict, minutes: float | None, status: str) -> dict:
    """Eine berücksichtigte Ankunft als nachvollziehbare Zeile (für Transparenz)."""
    line = entry.get("line") or {}
    return {
        "train": line.get("name"),
        "from": entry.get("provenance") or entry.get("origin", {}).get("name"),
        "planned_arrival": entry.get("plannedWhen") or entry.get("when"),
        "delay_minutes": round(minutes, 1) if minutes is not None else None,
        "status": status,
    }


def connection_delay_history(
    origin: str,
    destination: str,
    train: str = "",
    lookback_minutes: int = 300,
    end: str | None = None,
    sample_limit: int = 200,
    details: bool = False,
) -> dict:
    """Verspätungs-Kennzahlen für eine Verbindung aus der DB-Ankunftstafel.

    Liest die Fernverkehrs-Ankünfte am Zielbahnhof über ein Zeitfenster in der
    jüngsten Vergangenheit und verdichtet die TATSÄCHLICH eingetretenen
    Verspätungen bereits angekommener Züge zu Kennzahlen.

    Args:
        origin: Start-Bahnhof (nur Kontext/Ausgabe, gefiltert wird am Ziel).
        destination: Ziel-Bahnhof — dessen Ankunftstafel wird ausgewertet.
        train: Optionaler Zugname (z. B. "ICE 1006"), nur als Kontext.
        lookback_minutes: Größe des Fensters — wie weit es von ``end`` in die
            Vergangenheit reicht. Default 300 (5 h), innerhalb des empirischen
            Echtzeit-Horizonts (~5–6 h); weiter zurück liefert die DB nur noch
            Soll-Zeiten ohne Verspätung.
        end: Fensterende als ISO-Zeit; ``None`` = jetzt. Ausgewertet wird
            ``[end - lookback_minutes, end]``.
        sample_limit: Obergrenze der angefragten Tafel-Einträge.
        details: Wenn ``True``, hängt eine ``samples``-Liste mit jeder einzelnen
            berücksichtigten Ankunft an (Zug, Herkunft, Verspätung, Status) —
            für transparente/verbose Ausgaben. Standardmäßig aus, damit der
            Agenten-Kontext schlank bleibt.

    Returns:
        Dict mit ``sample_count`` und den Kennzahlen. ``sample_count == 0``
        bedeutet: kein verwertbares Sample (Aufrufer fällt auf Mock zurück).

    Raises:
        db_api.DBServiceError: Sidecar nicht erreichbar / Ziel nicht auflösbar.
    """
    dest_eva = stations.resolve_eva(destination)
    if dest_eva is None:
        raise db_api.DBServiceError(f"Zielbahnhof '{destination}' nicht auflösbar.")

    # Fenster in die jüngste Vergangenheit legen: bereits angekommene Züge tragen
    # ihre real eingetretene Verspätung, nicht nur eine Prognose.
    #
    # Eine einzelne Ankunftstafel-Abfrage deckt aber nur ~1 h ab (der dbnav-
    # Profilcap greift unabhängig vom angefragten ``duration``). Für ein größeres
    # Fenster blättern wir es daher in ~60-Min-Schritten rückwärts durch und
    # dedupen die Züge über die ``tripId``.
    end_dt = datetime.fromisoformat(end).astimezone() if end else datetime.now().astimezone()
    step_minutes = 60
    n_chunks = max(1, (lookback_minutes + step_minutes - 1) // step_minutes)
    entries: list[dict] = []
    seen: set = set()
    for k in range(n_chunks):
        chunk_when = end_dt - timedelta(minutes=(k + 1) * step_minutes)
        board = db_api.arrivals(dest_eva, when=chunk_when, duration=step_minutes, results=sample_limit)
        chunk = board.get("arrivals", []) if isinstance(board, dict) else (board or [])
        for entry in chunk:
            key = entry.get("tripId") or id(entry)
            if key in seen:
                continue
            seen.add(key)
            entries.append(entry)

    delays: list[float] = []
    causes: Counter[str] = Counter()
    samples: list[dict] = []
    cancelled = 0
    for entry in entries:
        if not _is_long_distance(entry):
            continue
        if entry.get("cancelled"):
            cancelled += 1
            samples.append(_sample_row(entry, None, "Ausfall"))
            continue
        minutes = _to_minutes(entry.get("delay"))
        if minutes is None:
            samples.append(_sample_row(entry, None, "keine Echtzeitdaten"))
            continue
        delays.append(minutes)
        samples.append(_sample_row(entry, minutes, "gezählt"))
        for remark in entry.get("remarks") or []:
            if remark.get("type") in ("status", "warning"):
                text = (remark.get("summary") or remark.get("text") or "").strip()
                if text:
                    causes[text] += 1

    sample_count = len(delays)
    if sample_count == 0:
        result = {
            "origin": origin,
            "destination": destination,
            "train": train or None,
            "sample_count": 0,
            "cancellations": cancelled,
        }
        if details:
            result["samples"] = samples
        return result

    punctual = sum(1 for d in delays if d <= _PUNCTUAL_MAX_MINUTES)
    heavy = sum(1 for d in delays if d >= _HEAVY_DELAY_MINUTES)
    result = {
        "origin": origin,
        "destination": destination,
        "train": train or None,
        "window": f"letzte {lookback_minutes} Min bis {end_dt:%d.%m %H:%M} (Ankunftstafel {destination})",
        "sample_count": sample_count,
        "mean_delay_minutes": round(mean(delays), 1),
        "median_delay_minutes": round(median(delays), 1),
        "p90_delay_minutes": round(_percentile(delays, 90), 1),
        "max_delay_minutes": round(max(delays), 1),
        "on_time_rate_pct": round(100 * punctual / sample_count),
        "delayed_over_15_rate_pct": round(100 * heavy / sample_count),
        "cancellations": cancelled,
        "common_causes": [text for text, _ in causes.most_common(3)],
    }
    if details:
        result["samples"] = samples
    return result


def scheduled_connection(
    origin: str,
    destination: str,
    departure: str | None = None,
) -> dict | None:
    """Die geplante Verbindung (Soll-Zeiten) als Anker für die ETA-Prognose.

    Sucht die beste Verbindung und liest geplante Abfahrt/Ankunft, Umstiege und
    Zugname. Fußwege (Legs ohne ``line``) werden ignoriert.

    Args:
        origin: Start-Bahnhof.
        destination: Ziel-Bahnhof.
        departure: Optionaler Abfahrtszeitpunkt (ISO); ``None`` = nächste Verbindung.

    Returns:
        Dict mit Soll-Zeiten + ``realtime_arrival_delay_minutes`` (aktuelle
        Echtzeit-Prognose, falls vorhanden), oder ``None`` wenn nichts gefunden.

    Raises:
        db_api.DBServiceError: Sidecar nicht erreichbar / Stationen nicht auflösbar.
    """
    from_eva = stations.resolve_eva(origin)
    to_eva = stations.resolve_eva(destination)
    if from_eva is None or to_eva is None:
        raise db_api.DBServiceError(
            f"Bahnhof nicht auflösbar (origin={origin!r}, destination={destination!r})."
        )

    data = db_api.journeys(from_eva, to_eva, departure=departure, results=1, tickets=False)
    journeys = data.get("journeys") or []
    if not journeys:
        return None
    legs = [leg for leg in journeys[0].get("legs", []) if leg.get("line")]
    if not legs:
        return None

    first, last = legs[0], legs[-1]
    line = first.get("line") or {}
    return {
        "origin": origin,
        "destination": destination,
        "train": line.get("name"),
        "planned_departure": first.get("plannedDeparture") or first.get("departure"),
        "planned_arrival": last.get("plannedArrival") or last.get("arrival"),
        "transfers": max(len(legs) - 1, 0),
        "realtime_arrival_delay_minutes": _to_minutes(last.get("arrivalDelay")),
    }


# --- Historische Referenz (Mehr-Monats-Archiv, offline) -----------------------
#
# Anders als die Live-Tafel (~5–6 h Echtzeit) ist das ein echtes Pünktlichkeits-
# ARCHIV über Monate — die belastbare Baseline für den Risiko-Score. Quelle:
# piebro/deutsche-bahn-data (CC BY 4.0). Vorab verdichtet je (EVA, Zugtyp); zur
# Laufzeit nur ein kleiner JSON-Read, keine schweren Abhängigkeiten.


@lru_cache(maxsize=1)
def _load_reference() -> dict:
    """Lädt die committete Referenz-JSON einmalig (oder {} wenn nicht vorhanden)."""
    if not _REFERENCE_PATH.exists():
        return {}
    try:
        return json.loads(_REFERENCE_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _norm_name(name: str) -> str:
    """Stationsname robust normalisieren ('Berlin Hbf' ~ 'Berlin Hauptbahnhof')."""
    n = (name or "").lower().strip().replace("hauptbahnhof", "hbf")
    for ch in " .,()-/":
        n = n.replace(ch, "")
    return n


@lru_cache(maxsize=1)
def _name_index() -> dict:
    """{normalisierter Name -> EVA} aus der Referenz — Fallback ohne Sidecar."""
    index: dict[str, str] = {}
    for eva, entry in _load_reference().get("stations", {}).items():
        key = _norm_name(entry.get("station_name") or "")
        if key:
            index.setdefault(key, eva)
    return index


def _train_type(train: str) -> str | None:
    """'ICE 1006' -> 'ICE'."""
    tokens = (train or "").strip().split()
    return tokens[0].upper() if tokens else None


def historical_reference(destination: str, train: str = "") -> dict | None:
    """Historische Verspätungs-Kennzahlen am Zielbahnhof aus dem Archiv.

    Wählt die passende Kennzahlen-Gruppe: erst der konkrete Zugtyp (aus ``train``),
    sonst Fernverkehr-Sammelwert, sonst der Gesamtwert des Bahnhofs. Auflösung des
    Bahnhofs zuerst über die EVA (Sidecar), sonst über einen Namensindex — die
    Referenz funktioniert damit auch offline.

    Returns:
        Dict mit Kennzahlen + Metadaten (Monate, Quelle), oder ``None`` wenn der
        Bahnhof nicht im Archiv ist / keine Referenz vorliegt.
    """
    ref = _load_reference()
    stations_ = ref.get("stations") or {}
    if not stations_:
        return None

    eva = None
    try:
        resolved = stations.resolve_eva(destination)
        eva = str(int(resolved)) if resolved else None
    except Exception:
        eva = None  # Sidecar nicht erreichbar -> Namensindex versuchen

    entry = stations_.get(eva) if eva else None
    if entry is None:
        entry = stations_.get(_name_index().get(_norm_name(destination), ""))
    if entry is None:
        return None

    ttype = _train_type(train)
    by_type = entry.get("by_train_type") or {}
    if ttype and ttype in by_type:
        kpis, basis = by_type[ttype], ttype
    elif ttype in _LONG_DISTANCE and entry.get("long_distance"):
        kpis, basis = entry["long_distance"], "Fernverkehr"
    elif entry.get("long_distance"):
        kpis, basis = entry["long_distance"], "Fernverkehr"
    else:
        kpis, basis = entry["overall"], "alle Zugtypen"

    meta = ref.get("_meta") or {}
    return {
        "destination": destination,
        "station_name": entry.get("station_name"),
        "basis": basis,
        "months": meta.get("months"),
        "source": "db_history_archive",
        "source_url": meta.get("source_url"),
        "license": meta.get("license"),
        **kpis,
    }
