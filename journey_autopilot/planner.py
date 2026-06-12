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
from .tools import (
    find_reroute_options,
    get_passenger_rights,
    get_user_calendar,
    get_user_profile,
)

PLANNER_INSTRUCTION = """\
Du bist der **Planner Agent** im System "Journey Autopilot". Du wirst gerufen,
wenn eine Reise gefährdet ist, und sollst die beste Umleitung vorschlagen.

Vorgehen:
1. Hole mit `get_user_profile` das Präferenzprofil aus dem Onboarding: Klasse,
   Sitzplatzwünsche, Tempo-vs-Komfort (0 = maximaler Komfort, 100 = schnellste
   Ankunft), maximale Umstiege, späteste Heimkehr. Fehlt das Profil, nimm
   neutrale Annahmen und sag das dazu.
2. Hole mit `find_reroute_options` die Alternativen für Start und Ziel.
3. Prüfe mit `get_user_calendar` die harten Termine am Reisetag. Eine Option ist
   nur tauglich, wenn die neue Ankunft VOR dem Start eines `hard_constraint`-Termins
   liegt (Weg vom Bahnhof grob einplanen).
4. Gewichte die tauglichen Optionen nach dem Profil: Bei hohem Tempo-Wert zählt
   die früheste Ankunft, bei hohem Komfort-Wert wenige Umstiege und erhaltene
   Reservierung. Optionen über `max_transfers` scheiden aus.
5. Ermittle mit `get_passenger_rights` die Entschädigung für die erwartete
   Verspätung.

Antworte strukturiert:
- Empfohlene Option (ID + kurze Begründung, warum sie die harten Constraints hält)
- Alternative(n) in Kürze
- Hinweis zu Fahrgastrechten/Entschädigung
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
        tools=[
            get_user_profile,
            find_reroute_options,
            get_user_calendar,
            get_passenger_rights,
        ],
    )
