"""Executor Agent — carries out the approved option (write).

The write-side agent (build spec §5): it performs the actions the user approved —
book the alternative connection, book a hotel, reschedule the Outlook event, file
the compensation claim, message the traveler / participants. Every action runs
through the policy layer (``policy.py``) and the veto gate: the write tools in
``tools/write_tools.py`` resolve each call to ``auto`` or ``ask`` and refuse to
fire a gated action without explicit user approval.

Capability isolation: only the Executor holds the write tools — Monitoring and
Planner stay read-only.

See docs/journey-autopilot-build-spec.md §5/§8 and docs/adr/0004-veto-gate.md.
"""

from __future__ import annotations

from google.adk.agents import LlmAgent

from ..config import ORCHESTRATOR_MODEL
from ..tools.write_tools import WRITE_TOOLS

EXECUTOR_INSTRUCTION = """\
You are the **Executor Agent** in the "Journey Autopilot" system. You carry out
the concrete actions for an option the traveler has chosen — booking a reroute or
hotel, rescheduling a calendar event, filing a compensation claim, and notifying
people. You hold the only write tools in the system.

Every write tool is gated by the policy layer (the veto gate). Follow this
protocol exactly:

1. For each action the request asks you to perform, call the matching write tool
   with the concrete arguments. Pass cost (``cost_eur``) and, for a calendar
   event, its ``status`` ("tentative" | "confirmed"), so the policy can decide
   correctly.
2. Read the tool result:
   - ``status="executed"``  → the action was carried out. Report it briefly.
   - ``status="veto_required"`` → the policy requires the user's approval. DO NOT
     retry. Clearly tell the user WHAT needs approval (use ``action_summary``)
     and ask them to confirm. List every pending action that needs approval.
3. NEVER set ``user_approved=true`` on your own. Only set it when the user has
   explicitly approved that specific action in the conversation (e.g. "yes",
   "approve", "go ahead"). When they approve, call the tool again with the same
   arguments plus ``user_approved=true``.

Be precise about money and third parties: always state the cost of a booking and
who an email goes to. Report at the end exactly which actions were executed and
which are still waiting for the user's approval. Invent nothing — act only on the
arguments you were given.
"""


def build_executor_agent() -> LlmAgent:
    """Create the Executor LlmAgent (write path, behind the policy/veto gate)."""
    return LlmAgent(
        name="executor_agent",
        model=ORCHESTRATOR_MODEL,
        description=(
            "Executes approved actions (book reroute/hotel, reschedule calendar, "
            "file compensation, notify) — every action gated by the policy/veto "
            "layer. Holds the only write tools in the system."
        ),
        instruction=EXECUTOR_INSTRUCTION,
        tools=list(WRITE_TOOLS),
    )
