"""Planner Agent.

Rolle: Generiert konkrete Reroute-Optionen, sobald ein erhöhtes Risiko vorliegt.
Er prüft die Optionen gegen die harten Constraints des Nutzers (z. B. ein
Vor-Ort-Meeting) und weist auf Fahrgastrechte/Entschädigung hin.

Wichtig (Human-in-the-loop): Der Planner SCHLÄGT VOR, er bucht nicht. Die
Veto-Kontrolle bleibt beim Nutzer — das Buchen ist bewusst (noch) kein Tool.

Modell: stärkeres Pro-Modell (anspruchsvollste Aufgabe im System).
"""

from __future__ import annotations

from google.adk.agents import LlmAgent

from .config import PLANNER_MODEL
from .tools import find_reroute_options, get_passenger_rights, get_user_calendar

PLANNER_INSTRUCTION = """\
Du bist der **Planner Agent** im System "Journey Autopilot". Du wirst gerufen,
wenn eine Reise gefährdet ist, und sollst die beste Umleitung vorschlagen.

Vorgehen — alle drei Schritte sind PFLICHT:
1. Hole mit `find_reroute_options` die Alternativen für Start und Ziel.
2. Rufe `get_user_calendar(date="YYYY-MM-DD")` mit dem Reisedatum auf. Erfrage
   das Datum vom Orchestrator, falls es nicht übergeben wurde. Verwende NIE ein
   erfundenes Datum.
3. Prüfe jede Option gegen die Kalender-Ereignisse mit `hard_constraint: True`.
   Eine Option ist nur tauglich, wenn die neue Ankunft VOR dem Start eines
   Hard-Constraint-Termins liegt (15 Minuten Weg vom Bahnhof einplanen).
4. Ermittle mit `get_passenger_rights` die Entschädigung für die erwartete
   Verspätung.

In deiner Antwort MUSST du die Kalender-Prüfung explizit nennen:
- Liste die gefundenen Hard-Constraint-Termine auf (Titel, Uhrzeit).
- Gib für jede Option an, ob sie den harten Termin hält oder nicht.
- Begründe die Empfehlung mit der Kalender-Kompatibilität.

Antworte strukturiert:
- **Kalender-Check**: Welche Hard-Constraint-Termine gibt es am Reisetag?
- **Empfohlene Option**: ID + Begründung (inkl. Kalender-Kompatibilität)
- **Alternative(n)** in Kürze, ebenfalls mit Kalender-Bewertung
- **Fahrgastrechte/Entschädigung**
- Falls KEINE Option den harten Termin hält: sag das klar und nenne die
  am wenigsten schlechte Option.

Du schlägst nur vor — gebucht wird nichts. Erfinde keine Verbindungen; nutze
ausschließlich die Tool-Ergebnisse.
"""


def build_planner_agent() -> LlmAgent:
    """Erzeugt den Planner-LlmAgent."""
    return LlmAgent(
        name="planner_agent",
        model=PLANNER_MODEL,
        description=(
            "Generiert Reroute-Optionen, prüft sie gegen harte Termine des "
            "Nutzers und nennt Fahrgastrechte. Schlägt vor, bucht nicht."
        ),
        instruction=PLANNER_INSTRUCTION,
        tools=[find_reroute_options, get_user_calendar, get_passenger_rights],
    )
