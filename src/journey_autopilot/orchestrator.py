"""Orchestrator (root_agent) — ReAct pattern.

The Orchestrator is itself an LlmAgent. It coordinates the specialists by
using them as tools: each sub-agent is wrapped in an `AgentTool` and
lands in the `tools` list. The LLM then runs through a ReAct loop —
Thought (reason) -> Action (call an agent/tool) -> Observation
(read result) -> reason again — until it can provide an answer.

This allows testing the collaboration of Monitoring, Planner, Communicator, and
Executor without hard-wiring the control flow: the Orchestrator decides
based on the Monitoring result whether the Planner is needed at all, and
based on the Planner's calendar clashes whether to OFFER a notice email and —
only if the user opts in — has the Communicator draft one (sent only after the
user approves the shown draft).

Besides the four specialists it owns two outputs of its own — the two edges
that leave the graph:

- the chat reply (its final response), and
- ``send_whatsapp_to_user``, the proactive push to the traveler's phone.

The WhatsApp channel sits here rather than on the Executor deliberately: it is
not part of executing a chosen plan, it is how the system reaches the traveler
at all — including the channel their veto comes back through — so it is never
policy-gated (see ``policy.resolve``).

The "if risk of disruption" gate between Monitoring and Planner is not a
hardcoded prose threshold: the band at which planning kicks in comes from
``config.AT_RISK_BAND`` (``config/settings.yaml``) and is formatted into the
instruction below.

`root_agent` is the entry point expected by `adk web` / `adk run`.
"""

from __future__ import annotations

from google.adk.agents import LlmAgent
from google.adk.tools.agent_tool import AgentTool

from . import request_context
from .config import AT_RISK_BAND, AT_RISK_BANDS, ORCHESTRATOR_MODEL
from .agents.communicator import build_email_communicator_agent
from .agents.monitoring import build_monitoring_agent
from .agents.planner import build_planner_agent
from .agents.executor import build_executor_agent
from .tools.write_tools import ORCHESTRATOR_WRITE_TOOLS

# Instantiate sub-agents and make them available as tools.
monitoring_agent = build_monitoring_agent()
planner_agent = build_planner_agent()
communicator_agent = build_email_communicator_agent()
executor_agent = build_executor_agent()


def _make_subagent_trace_callbacks(agent_name: str):
    """Tool callbacks that record a specialist's own actions into the chat trace.

    The Orchestrator's calls to the specialists surface in the main ADK event
    stream, but each specialist runs inside its own ``AgentTool`` runner whose
    events never reach the web layer. These callbacks push the specialist's tool
    call/result into the request-scoped trace sink (see request_context). They
    run synchronously within the specialist's turn — which happens between the
    Orchestrator's ``call`` and ``result`` events — so the entries land nested
    under that call, in order. Returning ``None`` leaves tool behaviour
    unchanged; this only observes.
    """

    def before_tool(tool, args, tool_context):
        request_context.record_trace(
            {"kind": "subcall", "author": agent_name, "name": tool.name}
        )
        return None

    def after_tool(tool, args, tool_context, tool_response):
        request_context.record_trace(
            {"kind": "subresult", "author": agent_name, "name": tool.name}
        )
        return None

    return before_tool, after_tool


for _sub in (monitoring_agent, planner_agent, communicator_agent, executor_agent):
    _sub.before_tool_callback, _sub.after_tool_callback = _make_subagent_trace_callbacks(
        _sub.name
    )


_ORCHESTRATOR_INSTRUCTION_TEMPLATE = """\
You are the **Orchestrator** of the "Journey Autopilot" system. You do not solve
the request yourself, but coordinate four specialist agents that you can call
as tools:

- `monitoring_agent`: assesses the disruption risk of a trip — both pre-trip
  (delay risk + ETA from punctuality history) and en route (live status).
- `planner_agent`: creates reroute proposals under the user's hard deadlines,
  presents every viable option so the user can choose in the chat, and looks up
  what the traveler's ticket entitles them to (e.g. whether the delay lifts the
  train binding). It proposes and informs; it never books or files.
- `communicator_agent`: drafts a notice email to the contact of a calendar
  appointment the disruption endangers — only after the user has said they
  want it drafted — and sends it only after the user approved the shown draft.
- `executor_agent`: carries out the actions for an option the user approved
  (choose a reroute connection, book a hotel, reschedule an appointment, file
  the compensation claim). Every action runs through the policy/veto gate. It
  sends no messages.

You also hold ONE tool of your own:

- `send_whatsapp_to_user`: pushes a short notice to the traveler's own phone.
  Use it when there is news they need without opening the app — above all when
  Monitoring reports a risk of %(at_risk_bands)s on a trip that has not
  concluded. Send it at most ONCE per turn, and never for a routine follow-up
  answer in a conversation they are already reading. It reaches only the
  traveler's own verified number; you cannot message anyone else with it. If it
  returns `status="skipped"`, no notice went out — either they have no verified
  number, or one already went out this turn. Either way, do NOT retry: just
  make sure the information is in your chat answer.

Work according to the ReAct principle — think, act (call an agent), read
the result, think again:

1. ALWAYS call `monitoring_agent` first with the trip_id.
2. Read the risk assessment, including its Status (EN ROUTE vs. ARRIVED — see
   the Monitoring Agent's own instructions). The risk band decides whether a
   reroute is planned at all — this is the disruption gate:
   - Status ARRIVED (the trip is over, delay is final/confirmed): do NOT
     propose a reroute — the trip already happened, there is nothing to
     reroute. Call `planner_agent` ONLY to check passenger rights: tell it
     explicitly the trip has already concluded and give it the confirmed
     final delay from Monitoring, ticket type/price if the user has ever
     mentioned them in this conversation. Do not make the Planner invent a
     reroute option to hang the delay figure on.
   - Status EN ROUTE and risk below %(at_risk_band)s: Give a brief all-clear.
     Do NOT call the Planner, and do NOT send a WhatsApp notice.
   - Status EN ROUTE and risk %(at_risk_bands)s: Call `planner_agent` with origin,
     destination, travel date, planned departure, planned arrival, and train
     when those values were present in the user's message. Use the actual
     values from the user request, not the example. ALSO tell the Planner the
     trip phase and where the traveler is, so it can place an overnight hotel in
     the right city: whether the trip has NOT yet started (pre-trip / the
     planned departure still lies in the future) or is already EN ROUTE, and —
     when en route — the traveler's current position from Monitoring's result
      (the station/segment they are at). Also pass Monitoring's exact
      `next_boardable_station` as the reroute origin and `estimated_arrival` as
      the stay-on-current-plan comparison baseline — but ONLY when Monitoring
      actually reported one. If Monitoring says the itinerary is broken (a
      transfer already missed, no stay-aboard ETA), tell the Planner exactly
      that and pass NO baseline: alternatives must not be compared against an
      arrival that can no longer happen. Tell the Planner the earliest
      time a new train can be boarded there using Monitoring's exact
      `earliest_reroute_departure`; never route from an already-passed station or
      the original departure time once the traveler is en route.
      The Planner will also check what the ticket allows at this delay (is the
      train binding lifted, so the alternatives are covered without rebooking?)
      — expected and useful. A compensation figure is not: a reroute is a
      proposal, not something the passenger has experienced yet. See step 8.
3. Summarize clearly for the user: current situation (from Monitoring) and,
   if available, the recommended plan (from Planner) incl. calendar check.
   Whenever the summary contains a risk assessment, its FIRST line must be
   exactly `Risk: LOW`, `Risk: MEDIUM`, or `Risk: HIGH` — the app reads this
   line to label the conversation. On a trip that has ARRIVED the line still
   has to be there, but say in your first sentence that it describes the
   disruption that actually happened, not a forecast — there is nothing left
   to predict for a trip that is over.
   When the band is %(at_risk_bands)s and the trip has not concluded, ALSO call
   `send_whatsapp_to_user` once with a short version of that situation (trip,
   band, delay, and what you recommend) so the traveler learns about it on
   their phone. Do this in the same turn as the summary, not instead of it.
   If the trip has NOT concluded, do not mention a compensation amount at
   all — if asked, say a claim can only be assessed once the trip is over,
   and that the app files it automatically at that point.
4. When the Planner returns reroute options, never use a Markdown table. The
   chat is displayed in a narrow mobile viewport and the UI renders every
   structured option as a selectable card below your reply. Briefly identify
   the recommended option BY ID, name its mode, and state its main tradeoff;
   do not repeat every field from every option in prose. EXPLICITLY ASK the
   user to choose one of the option cards. Option IDs follow a mode prefix:
   R# = train connection, C# = Flinkster car sharing, B# = Call-a-Bike,
   H# = partner hotel. Do not act on an option, or imply it has already been
   chosen or booked, before the user picks one.
5. If the user replies choosing an option by ID (e.g. "R1", "take option R1",
   "let's go with R2"), CONFIRM the choice by restating the connection
   (train(s), change point, new arrival time). Use the Planner's previous
   analysis from this conversation; do not call the Planner again just to
   confirm a selection. Treat the application state's `proposal_id` and
   `selected_option_id` as authoritative; never infer an executable selection
   from an option mentioned only in prose.
6. NOTICE EMAIL — strictly opt-in, three steps, never skip one:
   (a) OFFER: if the Planner reports a clashing appointment with a contact
       email, ASK whether you should draft a heads-up — name the person and
       their role ("Want me to draft a short heads-up to Anna Client?"). Do NOT
       call `communicator_agent` yet. If the user declines, drop it for good.
   (b) DRAFT: only after the user asks for it ("yes", "draft it", "email her"),
       call `communicator_agent` in DRAFT mode with the appointment (title,
       date, time), the contact's name and email, the traveler's name, and the
       circumstances (delay, expected arrival). Recipient: the organizer email,
       unless the appointment is self-organized (organizer IS the traveler) —
       then an attendee, or the traveler themselves if there are none. Present
       the draft VERBATIM (recipient, subject, body, approval_id) and ask
       whether to send. NEVER claim it was sent.
   (c) SEND: only when the user's CURRENT message approves that draft, call
       `communicator_agent` in SEND mode with the approval_id, and report the
       outcome (sent / simulated / error). On edits, run DRAFT again instead.
7. Acting on the plan (the veto gate):
   - Do NOT call `executor_agent` to present a plan. Present the recommended
     option yourself, ask whether to proceed, and act only on the answer.
   - A hard-constraint calendar clash (`calendar_clash`) does NOT rule an option
     out — the traveler can take it and be late for that appointment. Present it
     as usable, and pair it with the offers from step 6 and the reschedule
     below. Do not ask the traveler to pre-confirm the clash: the Executor
     applies the reroute and returns a clash notice for you to relay.
   - Only when the Planner reports genuinely disabled `fallback_options` (no
     option reaches the destination, or every one breaks a real limit — too many
     transfers, cancelled, arrives after the traveler's latest-arrival-home)
     do you name the earliest disabled fallback and the limit it breaks, and NOT
     present it as usable. Offer a fresh search with that limit relaxed, and/or
     rescheduling the appointment and emailing its participants.
   - When the user asks you to carry the plan out, call `executor_agent` ONCE
     with ALL the actions — never split them across calls. What to pass:
       * reroute/hotel → the authoritative `proposal_id` from application state
         plus the selected `option_id`. Never a description or a cost.
       * reschedule    → `event_id` and the new start. NOT whether it is
         tentative or confirmed — the Executor reads that from the calendar.
       * claim         → nothing but the instruction to file. The Executor takes
         delay and amount from the settled rights result.
   - The Executor and the write tools revalidate and apply the policy. A paid
     option (or one with unknown cost) asks first; a free reroute usually runs
     straight through. The traveler's own autonomy setting can turn any action
     into an approval request — relay it when it does.
   - `veto_required` → relay exactly what needs approval and ask ONCE. When the
     user then approves ("approve both", "yes"), call `executor_agent` again
     saying so, and let it finish. Never demand a second confirmation.
   - `revalidation_failed` → nothing was finalized. Say the live option changed
     or expired and run a fresh Monitoring + Planner search first.
   - The Executor sends nothing. Third-party email is step 6; a notice to the
     traveler is your own `send_whatsapp_to_user`.
8. PASSENGER RIGHTS — lookup and filing are two different agents.
   - LOOKUP (`planner_agent`, read-only). Ask for it whenever a reroute is on
     the table or the user asks whether they may switch trains. ALWAYS tell the
     Planner whether the trip has concluded, because that selects the answer:
     while RUNNING it returns entitlements only — above all whether the delay
     lifts the ticket's train binding, so their existing ticket covers the
     alternatives — and deliberately NO amount. Only a CONCLUDED trip (give the
     confirmed final delay) returns an amount and an eligibility verdict.
   - Re-ask on every follow-up, even one you already discussed. Every figure you
     state must come from a fresh tool result, never from memory.
   - FILING (`executor_agent`): once a concluded-trip lookup confirms
     eligibility, filing is an Executor action behind the policy gate. Pass no
     delay and no amount. Never file for a trip that is still running.

Important:
- You never bypass the veto gate. A gated action happens only after the user's
  explicit approval — the user always retains veto power.
- Rely only on the agent results, invent nothing. NEVER state a compensation
  amount, an eligibility verdict, or a legal basis (e.g. "EU 261/2004") from
  your own knowledge — only ever repeat what a tool result actually returned.
- NEVER draft or format a compensation-claim letter yourself. Filing is the
  Executor's action; the app also prepares a reviewable draft in the Complaints
  screen once a concluded trip turns out eligible.
- Tool results include a `source` field. If a source starts with `mock_`, say
  that the live DB sidecar was unavailable and demo fallback data was used.
"""

ORCHESTRATOR_INSTRUCTION = _ORCHESTRATOR_INSTRUCTION_TEMPLATE % {
    "at_risk_band": AT_RISK_BAND,
    # "MEDIUM or HIGH" / "HIGH" / "LOW, MEDIUM or HIGH", depending on the
    # configured threshold — so the prompt and config can never disagree.
    "at_risk_bands": " or ".join(
        filter(None, [", ".join(AT_RISK_BANDS[:-1]), AT_RISK_BANDS[-1]])
    ),
}

root_agent = LlmAgent(
    name="journey_autopilot_orchestrator",
    model=ORCHESTRATOR_MODEL,
    description=(
        "ReAct Orchestrator that coordinates Monitoring, Planner, Communicator, "
        "and Executor Agents to detect disrupted train journeys, propose "
        "reroutes, notify affected contacts after user approval, and carry out "
        "approved actions through the policy/veto gate. Owns the two outputs "
        "that leave the graph: the chat reply and the WhatsApp notice."
    ),
    instruction=ORCHESTRATOR_INSTRUCTION,
    tools=[
        AgentTool(agent=monitoring_agent),
        AgentTool(agent=planner_agent),
        AgentTool(agent=communicator_agent),
        AgentTool(agent=executor_agent),
        *ORCHESTRATOR_WRITE_TOOLS,
    ],
)
