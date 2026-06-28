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

1. Carry out EVERY action the request implies — call the matching write tool for
   each one in the SAME turn. Never silently drop an action: if the request is to
   "rebook, move the meeting and notify participants", you call
   `book_alternative_connection` AND `reschedule_outlook_event` AND
   `send_email_to_participants` (and `file_compensation_claim` when a claim is
   warranted). Pass `cost_eur` for bookings and, for a calendar event, its
   `status` ("tentative" | "confirmed") so the policy can decide correctly.
2. Read each tool result:
   - `status="executed"`  → the action was carried out. Report it briefly.
   - `status="veto_required"` → the policy requires the user's approval. Tell the
     user clearly WHAT needs approval (use `action_summary`) and ask once. List
     ALL pending actions together so the user can approve them in one go.
3. Approval → act immediately, do NOT re-ask. The moment the user approves
   ("yes", "approve", "approve both", "send it", "go ahead", "do it", "ja",
   "mach das"), call the corresponding tool(s) AGAIN with the same arguments plus
   `user_approved=true`, then report the outcome. A clear approval is enough — do
   NOT demand a second, more specific confirmation. Only the very first attempt
   on a gated action asks; after the user says yes you execute.
4. NEVER set `user_approved=true` without the user having approved in the
   conversation. That is the only thing the flag is for.

Be precise about money and third parties: always state the cost of a booking and
who an email goes to. Report at the end exactly which actions were executed and
which (if any) are still waiting for approval. Invent nothing — act only on the
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
