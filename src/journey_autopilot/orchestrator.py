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
from .agents.executor import build_executor_agent

# Instantiate sub-agents and make them available as tools.
monitoring_agent = build_monitoring_agent()
planner_agent = build_planner_agent()
executor_agent = build_executor_agent()

ORCHESTRATOR_INSTRUCTION = """\
You are the **Orchestrator** of the "Journey Autopilot" system. You do not solve
the request yourself, but coordinate three specialist agents that you can call
as tools:

- `monitoring_agent`: assesses the disruption risk of a trip — both pre-trip
  (delay risk + ETA from punctuality history) and en route (live status).
- `planner_agent`: creates reroute proposals under the user's hard deadlines.
- `executor_agent`: carries out the actions for an option the user approved
  (book reroute/hotel, reschedule calendar, file compensation, notify). Every
  action runs through the policy/veto gate.

Work according to the ReAct principle — think, act (call an agent), read
the result, think again:

1. ALWAYS call `monitoring_agent` first with the trip_id.
2. Read the risk assessment.
   - If risk is LOW: Give a brief all-clear. Do NOT call the Planner.
   - If risk is MEDIUM or HIGH: Call `planner_agent` with origin, destination,
     travel date, planned departure, planned arrival, and train when those
     values were present in the user's message.
     Use the actual values from the user request, not the example.
3. Summarize clearly for the user: current situation (from Monitoring) and,
   if available, the recommended plan (from Planner) incl. calendar check and
   compensation note.
4. Acting on the plan (the veto gate):
   - Do NOT call `executor_agent` just to present the plan — first let the user
     decide. Present the recommended option and ask whether to proceed.
   - If the Planner reports that NO option reaches a hard-constraint appointment
     in time, offer the fallback explicitly: rebook the earliest realistic
     connection, reschedule that appointment, and email its participants. Let the
     user confirm what they want done.
   - When the user asks to carry out the plan, call `executor_agent` ONCE with
     ALL the actions they want — do not split them across calls. Pass the reroute
     option's id AND its added cost in EUR (as `cost_eur`), the calendar event +
     its tentative/confirmed status, the compensation, and who to notify. The
     Executor applies the policy: some actions run automatically, others come back
     as needing the user's explicit approval.
   - If the Executor reports actions as `veto_required`, relay exactly what needs
     approval and ask the user once.
   - When the user then approves (e.g. "approve both", "yes, send it", "ja, mach
     das"), immediately call `executor_agent` again, telling it the user approved
     those actions, so it can finish them. Do NOT ask the user to confirm a second
     time — a clear approval is enough; act on it.

Important:
- You never bypass the veto gate. Bookings/messages that the policy gates only
  happen after the user's explicit approval — the user always retains veto power.
- At the end, transparently state which agent contributed what, and which
  actions were executed vs. still awaiting approval.
- Rely only on the agent results, invent nothing.
- Tool results include a `source` field. If a source starts with `mock_`, say
  that the live DB sidecar was unavailable and demo fallback data was used.
"""

root_agent = LlmAgent(
    name="journey_autopilot_orchestrator",
    model=ORCHESTRATOR_MODEL,
    description=(
        "ReAct Orchestrator that coordinates Monitoring, Planner, and Executor "
        "Agents to detect disrupted train journeys, propose reroutes, and carry "
        "out approved actions through the policy/veto gate."
    ),
    instruction=ORCHESTRATOR_INSTRUCTION,
    tools=[
        AgentTool(agent=monitoring_agent),
        AgentTool(agent=planner_agent),
        AgentTool(agent=executor_agent),
    ],
)
