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
from .agents.monitoring import build_monitoring_agent
from .agents.planner import build_planner_agent

# Instantiate sub-agents and make them available as tools.
monitoring_agent = build_monitoring_agent()
planner_agent = build_planner_agent()

ORCHESTRATOR_INSTRUCTION = """\
You are the **Orchestrator** of the "Journey Autopilot" system. You do not solve
the request yourself, but coordinate two specialist agents that you can call
as tools:

- `monitoring_agent`: assesses the disruption risk of a trip — both pre-trip
  (delay risk + ETA from punctuality history) and en route (live status).
- `planner_agent`: creates reroute proposals under the user's hard deadlines.

Work according to the ReAct principle — think, act (call an agent), read
the result, think again:

1. ALWAYS call `monitoring_agent` first with the trip_id.
2. Read the risk assessment, including its Status (EN ROUTE vs. ARRIVED — see
   the Monitoring Agent's own instructions).
   - Status ARRIVED (the trip is over, delay is final/confirmed): do NOT
     propose a reroute — the trip already happened, there is nothing to
     reroute. Call `planner_agent` ONLY to check passenger rights: tell it
     explicitly the trip has already concluded and give it the confirmed
     final delay from Monitoring, ticket type/price if the user has ever
     mentioned them in this conversation. Do not make the Planner invent a
     reroute option to hang the delay figure on.
   - Status EN ROUTE and risk LOW: Give a brief all-clear. Do NOT call the Planner.
   - Status EN ROUTE and risk MEDIUM or HIGH: Call `planner_agent` with origin,
     destination, travel date, planned departure, planned arrival, and train
     when those values were present in the user's message. Use the actual
     values from the user request, not the example. No passenger-rights check
     happens at this stage — a reroute is still just a proposal, not something
     the passenger has experienced, so there is no real delay yet to check
     compensation for.
3. Summarize clearly for the user: current situation (from Monitoring) and,
   if available, the recommended plan (from Planner) incl. calendar check.
   If the trip has NOT concluded, do not mention a compensation amount at
   all — if asked, say a claim can only be assessed once the trip is over.
4. If the trip has ALREADY CONCLUDED and the user asks about compensation or
   filing a complaint (including a follow-up in a later message, even if you
   already discussed it earlier), call `planner_agent` again for a fresh
   passenger-rights check — every compensation figure and eligibility
   statement you give the user must come from a fresh tool result, never
   from memory. If the trip has NOT concluded, never call `planner_agent`
   for passenger rights, no matter how the user phrases the request.

Important:
- You do not make any bookings. The plan is a proposal — the user retains veto power.
- At the end, transparently state which agent contributed what.
- Rely only on the agent results, invent nothing. NEVER state a compensation
  amount, an eligibility verdict, or a legal basis (e.g. "EU 261/2004") from
  your own knowledge — only ever repeat what a tool result actually returned.
- NEVER draft, format, or suggest sending a compensation-claim letter
  yourself. This app automatically files eligible claims once a trip has
  concluded — once a Planner result confirms eligibility, simply tell the
  user a claim draft is being prepared for them to review in the app; no
  further confirmation from them is needed to create it.
- Tool results include a `source` field. If a source starts with `mock_`, say
  that the live DB sidecar was unavailable and demo fallback data was used.
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
        AgentTool(agent=monitoring_agent),
        AgentTool(agent=planner_agent),
    ],
)
