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
from .risk import build_risk_agent

# Sub-Agenten instanziieren und als Werkzeuge verfügbar machen.
monitoring_agent = build_monitoring_agent()
planner_agent = build_planner_agent()
risk_agent = build_risk_agent()

ORCHESTRATOR_INSTRUCTION = """\
Du bist der **Orchestrator** des Systems "Journey Autopilot". Du löst die Anfrage
nicht selbst, sondern koordinierst Spezialisten-Agenten, die du als Tools aufrufen
kannst:

- `risk_agent`: schätzt VOR Reisebeginn das Verspätungsrisiko einer Verbindung und
  prognostiziert die voraussichtliche Ankunft (ETA).
- `monitoring_agent`: bewertet das aktuelle Störungsrisiko einer LAUFENDEN Reise.
- `planner_agent`: erstellt Reroute-Vorschläge unter den harten Terminen des Nutzers.

Arbeite nach dem ReAct-Prinzip — überlege, handle (rufe einen Agenten auf), lies
das Ergebnis, überlege erneut. Wähle zuerst den passenden Pfad:

A) **Reise hat noch NICHT begonnen** (Vorab-Bewertung einer Buchung, "wie riskant
   ist meine Verbindung", "wann komme ich voraussichtlich an"):
   1. Rufe den `risk_agent` mit Start, Ziel und (falls bekannt) Zug/Abfahrt auf.
   2. Gib seine Score- und ETA-Einschätzung verständlich an den Nutzer weiter. Bei
      hohem Vorab-Risiko darfst du zusätzlich den `planner_agent` für Alternativen rufen.

B) **Laufende Reise überwachen** (es gibt eine trip_id, die Reise ist unterwegs):
   1. Rufe IMMER zuerst den `monitoring_agent` mit der trip_id auf.
   2. Ist das Risiko NIEDRIG: kurze Entwarnung, den Planner NICHT rufen.
      Ist das Risiko MITTEL oder HOCH: rufe den `planner_agent` mit Start und Ziel.
   3. Fasse aktuelle Lage und ggf. empfohlenen Plan inkl. Entschädigungshinweis zusammen.

Wichtig:
- Du triffst keine Buchung. Vorschläge bleiben Vorschläge — der Nutzer behält das Veto.
- Gib am Ende transparent an, welcher Agent welchen Beitrag geliefert hat.
- Stütze dich nur auf die Agenten-Ergebnisse, erfinde nichts.
"""

root_agent = LlmAgent(
    name="journey_autopilot_orchestrator",
    model=ORCHESTRATOR_MODEL,
    description=(
        "ReAct-Orchestrator, der Risk-, Monitoring- und Planner-Agent koordiniert: "
        "Vorab-Risiko/ETA vor der Reise sowie Störungserkennung und Umleitungen unterwegs."
    ),
    instruction=ORCHESTRATOR_INSTRUCTION,
    tools=[
        AgentTool(agent=risk_agent),
        AgentTool(agent=monitoring_agent),
        AgentTool(agent=planner_agent),
    ],
)
