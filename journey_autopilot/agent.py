"""Orchestrator (root_agent) — ReAct pattern.

The Orchestrator is itself an LlmAgent. It coordinates the specialists by
using them as tools: each sub-agent is wrapped in an `AgentTool` and
lands in the `tools` list. The LLM then runs through a ReAct loop —
Thought (reason) -> Action (call an agent/tool) -> Observation
(read result) -> reason again — until it can provide an answer.

This allows testing the collaboration of Monitoring and Planner without
hard-wiring the control flow: The Orchestrator decides based on the
Monitoring result whether the Planner is needed at all.

`root_agent` is the entry point expected by `adk web` / `adk run`.
"""

from __future__ import annotations

from google.adk.agents import LlmAgent
from google.adk.tools.agent_tool import AgentTool

from .config import ORCHESTRATOR_MODEL
from .monitoring import build_monitoring_agent
from .planner import build_planner_agent
from .risk import build_risk_agent

# Instantiate sub-agents and make them available as tools.
monitoring_agent = build_monitoring_agent()
planner_agent = build_planner_agent()
risk_agent = build_risk_agent()

ORCHESTRATOR_INSTRUCTION = """\
You are the **Orchestrator** of the "Journey Autopilot" system. You do not solve
the request yourself, but coordinate two specialist agents that you can call
as tools:

- `monitoring_agent`: assesses the current disruption risk of a trip.
- `planner_agent`: creates reroute proposals under the user's hard deadlines.

Work according to the ReAct principle — think, act (call an agent), read
the result, think again:

1. ALWAYS call `monitoring_agent` first with the trip_id.
2. Read the risk assessment.
   - If risk is LOW: Give a brief all-clear. Do NOT call the Planner.
   - If risk is MEDIUM or HIGH: Call `planner_agent` with origin, destination AND
     travel date (e.g. "Munich Hbf to Berlin Hbf on [DATE FROM USER REQUEST]").
     Use the actual date from the user request, not the example.
3. Summarize clearly for the user: current situation (from Monitoring) and,
   if available, the recommended plan (from Planner) incl. calendar check and
   compensation note.

Important:
- You do not make any bookings. The plan is a proposal — the user retains veto power.
- At the end, transparently state which agent contributed what.
- Rely only on the agent results, invent nothing.
"""

root_agent = LlmAgent(
    name="journey_autopilot_orchestrator",
    model=ORCHESTRATOR_MODEL,
    description=(
        "ReAct Orchestrator that coordinates Monitoring and Planner Agents to "
        "detect disrupted train journeys and propose reroutes."
    ),
    instruction=ORCHESTRATOR_INSTRUCTION,
    tools=[
        AgentTool(agent=risk_agent),
        AgentTool(agent=monitoring_agent),
        AgentTool(agent=planner_agent),
    ],
)
