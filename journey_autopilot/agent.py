"""Orchestrator (root_agent) — ReAct-Muster.

Der Orchestrator ist selbst ein LlmAgent. Er koordiniert die Spezialisten, indem
er sie als Werkzeuge benutzt: jeder Sub-Agent wird in ein `AgentTool` gewrappt
und landet in der `tools`-Liste. Das LLM durchläuft dann einen ReAct-Loop —
Thought (überlegen) -> Action (einen Agenten/Tool aufrufen) -> Observation
(Ergebnis lesen) -> erneut überlegen — bis es eine Antwort geben kann.

So lässt sich die Zusammenarbeit von Monitoring und Planner testen, ohne den
Kontrollfluss fest zu verdrahten: Der Orchestrator entscheidet anhand des
Monitoring-Ergebnisses, ob der Planner überhaupt gebraucht wird.

`root_agent` ist der von `adk web` / `adk run` erwartete Einstiegspunkt.
"""

from __future__ import annotations

from google.adk.agents import LlmAgent
from google.adk.tools.agent_tool import AgentTool

from .config import ORCHESTRATOR_MODEL
from .monitoring import build_monitoring_agent
from .planner import build_planner_agent

# Sub-Agenten instanziieren und als Werkzeuge verfügbar machen.
monitoring_agent = build_monitoring_agent()
planner_agent = build_planner_agent()

ORCHESTRATOR_INSTRUCTION = """\
Du bist der **Orchestrator** des Systems "Journey Autopilot". Du löst die Anfrage
nicht selbst, sondern koordinierst zwei Spezialisten-Agenten, die du als Tools
aufrufen kannst:

- `monitoring_agent`: bewertet das aktuelle Störungsrisiko einer Reise.
- `planner_agent`: erstellt Reroute-Vorschläge unter den harten Terminen des Nutzers.

Arbeite nach dem ReAct-Prinzip — überlege, handle (rufe einen Agenten auf), lies
das Ergebnis, überlege erneut:

1. Rufe IMMER zuerst den `monitoring_agent` mit der trip_id auf.
2. Lies die Risiko-Einschätzung.
   - Ist das Risiko NIEDRIG: Gib eine kurze Entwarnung. Rufe den Planner NICHT auf.
   - Ist das Risiko MITTEL oder HOCH: Rufe den `planner_agent` mit Start, Ziel UND
     Reisedatum auf (z. B. "München Hbf nach Berlin Hbf am 2026-06-10").
3. Fasse für den Nutzer verständlich zusammen: aktuelle Lage (vom Monitoring) und,
   falls vorhanden, der empfohlene Plan (vom Planner) inkl. Kalender-Check und
   Entschädigungshinweis.

Wichtig:
- Du triffst keine Buchung. Der Plan ist ein Vorschlag — der Nutzer behält das Veto.
- Gib am Ende transparent an, welcher Agent welchen Beitrag geliefert hat.
- Stütze dich nur auf die Agenten-Ergebnisse, erfinde nichts.
"""

root_agent = LlmAgent(
    name="journey_autopilot_orchestrator",
    model=ORCHESTRATOR_MODEL,
    description=(
        "ReAct-Orchestrator, der Monitoring- und Planner-Agent koordiniert, um "
        "gestörte Bahnreisen zu erkennen und Umleitungen vorzuschlagen."
    ),
    instruction=ORCHESTRATOR_INSTRUCTION,
    tools=[
        AgentTool(agent=monitoring_agent),
        AgentTool(agent=planner_agent),
    ],
)
