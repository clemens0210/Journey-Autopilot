"""Verbose Standalone-Demo des Risk Agent — Vorab-Risiko & ETA vor Reisebeginn.

Macht jeden Schritt der Vorab-Risikobewertung transparent — BEVOR die Reise
begonnen hat:

  1) Datenbasis  — jede einzelne Verbindung (Fernverkehrs-Ankunft), die in die
     Analyse eingeflossen ist, mit ihrer real eingetretenen Verspätung.
  2) Kennzahlen  — wie aus diesen Fahrten deterministisch die Statistik und die
     ETA-Bausteine berechnet werden (Median, p90, Pünktlichkeitsquote ...).
  3) Agenten-Verlauf — der vollständige ReAct-Trace des `risk_agent`: jeder
     Gedanke, jeder Tool-Aufruf (mit Argumenten) und jedes Tool-Ergebnis (roh).
  4) Antwort     — die finale Einschätzung an den Nutzer (Score + ETA).

Nutzung:
    python run_risk_demo.py

Voraussetzung: ein konfiguriertes Uni-GPT-Backend in der .env (UNI_GPT_*; siehe
README). Läuft der db_service-Sidecar, kommen die Verspätungsdaten live aus der
DB-Ankunftstafel; sonst greift die simulierte Historie (Feld `source`).
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta

from google.adk.runners import InMemoryRunner
from google.genai import types

try:
    from dotenv import load_dotenv

    load_dotenv()
    load_dotenv("journey_autopilot/.env")
except ImportError:
    pass

from journey_autopilot import db_api, delay_stats, tools
from journey_autopilot.mock_data import DEMO_TRIP
from journey_autopilot.risk import build_risk_agent

APP_NAME = "journey_autopilot_risk"
USER_ID = "lucas"

# ORIGIN = DEMO_TRIP["origin"]
# DESTINATION = DEMO_TRIP["destination"]
# TRAIN = DEMO_TRIP["train"]
# DEPARTURE = DEMO_TRIP["planned_departure"]

ORIGIN = 'Köln Hbf'
DESTINATION = 'Bonn Hbf'
TRAIN = 'IC 2007'
DEPARTURE = '10:32'

# Vorab-Anfrage: die Reise hat noch nicht begonnen.
PROMPT = (
    f"Ich habe eine Verbindung von {ORIGIN} nach {DESTINATION} mit dem {TRAIN} "
    f"gebucht (Abfahrt {DEPARTURE}). Wie hoch ist das Verspätungsrisiko und "
    "wann komme ich voraussichtlich an?"
)


def _hhmm(iso: str | None) -> str:
    """ISO-Zeit -> 'HH:MM', tolerant gegenüber fehlenden/kaputten Werten."""
    if not iso:
        return "??:??"
    try:
        return datetime.fromisoformat(iso).strftime("%H:%M")
    except ValueError:
        return str(iso)


def _eta(iso: str | None, add_minutes: float | None) -> str:
    """Geplante Ankunft + erwartete Verspätung -> 'HH:MM'."""
    if not iso or add_minutes is None:
        return "??:??"
    try:
        return (datetime.fromisoformat(iso) + timedelta(minutes=add_minutes)).strftime("%H:%M")
    except ValueError:
        return "??:??"


def _delay_label(minutes: float | None, status: str) -> str:
    """Verspätung lesbar machen (+X Min / pünktlich / Statushinweis)."""
    if status != "gezählt":
        return status
    if minutes is None:
        return status
    if minutes <= 0:
        return "pünktlich" if minutes == 0 else f"{minutes:+.0f} Min"
    return f"+{minutes:.0f} Min"


def _fetch_history_verbose() -> dict:
    """Wie das Tool, aber mit Einzelfahrten (details=True) für die Ausgabe.

    Spiegelt den Live-/Mock-Fallback des Tools, damit die Datenbasis exakt der
    entspricht, die der Agent gleich nutzen wird.
    """
    try:
        stats = delay_stats.connection_delay_history(
            ORIGIN, DESTINATION, train=TRAIN, details=True
        )
        if stats.get("sample_count", 0) > 0:
            stats["source"] = "db_service_live"
            return stats
    except db_api.DBServiceError:
        pass
    except Exception:
        pass
    # Fallback: aggregierte Mock-Historie (ohne Einzelfahrten).
    return tools.get_connection_delay_history(ORIGIN, DESTINATION, TRAIN)


def print_data_basis() -> tuple[dict, dict]:
    """Abschnitte 1–3: berücksichtigte Verbindungen, Kennzahlen, ETA-Anker."""
    history = _fetch_history_verbose()
    planned = tools.get_planned_connection(ORIGIN, DESTINATION, DEPARTURE)

    print("--- 1) Datenbasis: berücksichtigte Verbindungen ---------------------")
    print(f"Quelle: {history.get('source')}  |  Fenster: {history.get('window', '—')}")
    samples = history.get("samples")
    if samples:
        print(f"Fernverkehrs-Ankünfte in {DESTINATION} (N={len(samples)}):")
        for s in samples:
            train = (s.get("train") or "?").ljust(10)
            origin = (s.get("from") or "?").ljust(22)[:22]
            arr = _hhmm(s.get("planned_arrival"))
            print(f"  {train} aus {origin} plan {arr}   {_delay_label(s.get('delay_minutes'), s.get('status'))}")
    elif "error" in history:
        print(f"  (keine Daten: {history['error']})")
    else:
        print("  (simulierte Aggregat-Historie — keine Einzelfahrten verfügbar)")

    print("\n--- 2) Kennzahlen (deterministisch in delay_stats.py berechnet) -----")
    if history.get("sample_count"):
        median = history.get("median_delay_minutes")
        p90 = history.get("p90_delay_minutes")
        print(f"  Stichprobe (Fahrten)      : {history.get('sample_count')}")
        print(f"  Pünktlich (≤5 Min)        : {history.get('on_time_rate_pct')} %   (Anteil Fahrten mit Verspätung ≤ 5 Min)")
        print(f"  Deutlich verspätet (≥15)  : {history.get('delayed_over_15_rate_pct')} %")
        print(f"  Median-Verspätung         : {median} Min   -> erwartete Verspätung (typisch)")
        print(f"  90.-Perzentil (p90)       : {p90} Min   -> ungünstiger Fall (Worst-Case-Puffer)")
        print(f"  Mittelwert / Max          : {history.get('mean_delay_minutes')} / {history.get('max_delay_minutes')} Min")
        print(f"  Ausfälle im Fenster       : {history.get('cancellations')}")
        print(f"  Häufigste Ursachen        : {', '.join(history.get('common_causes') or []) or '—'}")

        planned_arrival = planned.get("planned_arrival")
        print("\n  ETA-Berechnung (geplante Ankunft + erwartete Verspätung):")
        print(f"    geplante Ankunft        : {_hhmm(planned_arrival)}")
        print(f"    ETA typisch (+Median)   : {_hhmm(planned_arrival)} + {median} Min = {_eta(planned_arrival, median)}")
        print(f"    ETA ungünstig (+p90)    : {_hhmm(planned_arrival)} + {p90} Min = {_eta(planned_arrival, p90)}")
        print("  (Den finalen Score und die ETA bildet der Agent aus genau diesen Zahlen.)")
    else:
        print("  (keine belastbaren Kennzahlen — Agent muss das offenlegen)")

    print("\n--- 3) Geplante Verbindung (ETA-Anker) ------------------------------")
    if "error" in planned:
        print(f"  (keine geplante Verbindung: {planned['error']})")
    else:
        print(
            f"  Zug {planned.get('train')} | Abfahrt {_hhmm(planned.get('planned_departure'))} "
            f"| geplante Ankunft {_hhmm(planned.get('planned_arrival'))} "
            f"| Umstiege {planned.get('transfers')} | Quelle {planned.get('source')}"
        )

    return history, planned


def _describe_event(event) -> None:
    """Verbose: Tool-Aufrufe (mit Args), Tool-Ergebnisse (roh) und Agent-Texte."""
    author = getattr(event, "author", "?")
    content = getattr(event, "content", None)
    if content is None or not getattr(content, "parts", None):
        return

    for part in content.parts:
        call = getattr(part, "function_call", None)
        response = getattr(part, "function_response", None)
        text = getattr(part, "text", None)

        if call is not None:
            args = json.dumps(dict(call.args or {}), ensure_ascii=False)
            print(f"  [{author}] -> ruft auf: {call.name}({args})")
        elif response is not None:
            payload = getattr(response, "response", None)
            print(f"  [{author}] <- Ergebnis {response.name}:")
            dump = json.dumps(payload, ensure_ascii=False, indent=2, default=str)
            for line in dump.splitlines():
                print(f"        {line}")
        elif text and text.strip():
            print(f"  [{author}] denkt/antwortet: {text.strip()}")


async def main() -> None:
    print("=" * 72)
    print("Journey Autopilot — Demo-Run (Risk Agent: Vorab-Risiko & ETA) [VERBOSE]")
    print("=" * 72)
    print(f"User: {PROMPT}\n")

    print_data_basis()

    risk_agent = build_risk_agent()
    runner = InMemoryRunner(agent=risk_agent, app_name=APP_NAME)
    session = await runner.session_service.create_session(
        app_name=APP_NAME, user_id=USER_ID
    )

    print("\n--- 4) Agenten-Verlauf (ReAct: Denken -> Tool -> Beobachten) --------")
    message = types.Content(role="user", parts=[types.Part(text=PROMPT)])
    final_text = ""
    try:
        async for event in runner.run_async(
            user_id=USER_ID, session_id=session.id, new_message=message
        ):
            _describe_event(event)
            if event.is_final_response() and event.content and event.content.parts:
                final_text = "".join(
                    p.text for p in event.content.parts if getattr(p, "text", None)
                )
    except Exception as exc:
        print("\n[!] Lauf abgebrochen.")
        print(f"    {type(exc).__name__}: {exc}")
        print("    UNI_GPT_BASE_URL (inkl. /v1), UNI_GPT_API_KEY und UNI_GPT_MODEL")
        print("    in der .env prüfen — und ob google-adk[extensions] installiert")
        print("    ist (pip install -r requirements.txt).")
        return

    print("\n--- 5) Antwort an den Nutzer ----------------------------------------")
    print(final_text or "(keine Textantwort)")


if __name__ == "__main__":
    asyncio.run(main())
