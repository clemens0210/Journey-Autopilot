"""Orchestrator (root_agent) — ReAct pattern.

The Orchestrator is itself an LlmAgent. It coordinates the specialists by
using them as tools: each sub-agent is wrapped in an `AgentTool` and
lands in the `tools` list. The LLM then runs through a ReAct loop —
Thought (reason) -> Action (call an agent/tool) -> Observation
(read result) -> reason again — until it can provide an answer.

This allows testing the collaboration of Monitoring, Planner, and
Communicator without hard-wiring the control flow: the Orchestrator decides
based on the Monitoring result whether the Planner is needed at all, and
based on the Planner's calendar clashes whether the Communicator should
draft a notice email (sent only after the user approves the shown draft).

`root_agent` is the entry point expected by `adk web` / `adk run`.
"""

from __future__ import annotations

from google.adk.agents import LlmAgent
from google.adk.tools.agent_tool import AgentTool

from .config import ORCHESTRATOR_MODEL
from .agents.communicator import build_email_communicator_agent
from .agents.monitoring import build_monitoring_agent
from .agents.planner import build_planner_agent

# Instantiate sub-agents and make them available as tools.
monitoring_agent = build_monitoring_agent()
planner_agent = build_planner_agent()
communicator_agent = build_email_communicator_agent()

ORCHESTRATOR_INSTRUCTION = """\
You are the **Orchestrator** of the "Journey Autopilot" system. You do not solve
the request yourself, but coordinate two specialist agents that you can call
as tools:

- `monitoring_agent`: assesses the disruption risk of a trip — both pre-trip
  (delay risk + ETA from punctuality history) and en route (live status).
- `planner_agent`: creates reroute proposals under the user's hard deadlines.
- `communicator_agent`: drafts a notice email to the contact of a calendar
  appointment the disruption endangers, and — only after the user approved
  the shown draft — sends it.

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
   compensation note. Whenever the summary contains a risk assessment, its
   FIRST line must be exactly `Risk: LOW`, `Risk: MEDIUM`, or `Risk: HIGH` —
   the app parses this line to trigger the proactive WhatsApp alert to the
   traveler.
4. NOTICE EMAIL (draft): if the Planner reports a clashing appointment that
   has a contact email, call `communicator_agent` in DRAFT mode: pass the
   appointment (title, date, time), the contact's name and email, the
   traveler's name if known, and the concrete circumstances (delay,
   expected arrival). Recipient choice: the organizer email — but if the
   appointment is self-organized (the organizer is the traveler), prefer an
   attendee email; with no attendees, the traveler's own address is the
   recipient (a self-notice). Present the returned draft VERBATIM in your
   answer (recipient, subject, body, approval_id) and ask the user whether
   it should be sent. NEVER claim it was sent.
5. NOTICE EMAIL (send): ONLY when the user's CURRENT message explicitly
   approves sending a previously shown draft (e.g. "yes, send it"), call
   `communicator_agent` in SEND mode with the approval_id from this
   conversation, and report the outcome (sent / simulated / error). If the
   user declines or edits, do not send; on edits, run DRAFT mode again with
   the changes.

Important:
- You do not make any bookings. The plan is a proposal — the user retains veto power.
- An email to a third party is only ever sent through the approval flow in
  steps 4-5 — a draft first, the user's explicit yes, then the send.
- At the end, transparently state which agent contributed what.
- Rely only on the agent results, invent nothing.
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
        AgentTool(agent=communicator_agent),
    ],
)
