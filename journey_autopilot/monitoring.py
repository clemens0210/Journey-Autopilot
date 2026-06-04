"""Monitoring Agent.

Rolle: Beobachtet eine laufende Bahnreise und bewertet das Störungsrisiko.
Er entscheidet NICHT über Umleitungen — er liefert nur eine belastbare
Risiko-Einschätzung, auf deren Basis der Orchestrator weiterroutet.

Modell: günstiges Flash-Modell (läuft potenziell häufig, im Loop).
"""

from __future__ import annotations

from google.adk.agents import LlmAgent

from .config import MONITORING_MODEL
from .tools import get_live_trip_status, get_network_disruptions

MONITORING_INSTRUCTION = """\
Du bist der **Monitoring Agent** im System "Journey Autopilot". Deine einzige
Aufgabe ist es, den aktuellen Zustand einer Bahnreise zu erfassen und das
Störungsrisiko einzuschätzen. Du planst KEINE Umleitungen.

Vorgehen:
1. Rufe `get_live_trip_status` mit der genannten trip_id auf.
2. Prüfe mit `get_network_disruptions` die Störungslage der relevanten Region.
3. Bewerte das Risiko auf einer Skala: NIEDRIG / MITTEL / HOCH.
   - Orientierung: Verspätung < 15 Min und keine Vorfälle -> NIEDRIG;
     wachsende Verspätung, gefährdete Anschlüsse oder aktive Störungen -> HOCH.

Antworte kurz und strukturiert:
- Risiko-Level: <NIEDRIG|MITTEL|HOCH>
- Aktuelle Verspätung und Trend
- Wesentliche Vorfälle / gefährdete Anschlüsse
- Eine Satzbegründung

Erfinde keine Zahlen — nutze ausschließlich die Tool-Ergebnisse. Fehlen Daten,
sage das explizit.
"""


def build_monitoring_agent() -> LlmAgent:
    """Erzeugt den Monitoring-LlmAgent."""
    return LlmAgent(
        name="monitoring_agent",
        model=MONITORING_MODEL,
        description=(
            "Überwacht eine laufende Bahnreise, liest Live-Daten und Störungen "
            "und liefert eine Risiko-Einschätzung (NIEDRIG/MITTEL/HOCH)."
        ),
        instruction=MONITORING_INSTRUCTION,
        tools=[get_live_trip_status, get_network_disruptions],
    )
