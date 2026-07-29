"""Orchestrator-backed chat for the dashboard.

Runs the same ReAct Orchestrator as ``scenarios/happy_path.py``, but driven by
chat messages from the UI instead of a single hard-coded prompt. Clicking a trip in
the dashboard opens a chat; each message is handed to ``root_agent``, and the
agent/tool trace plus the final answer are returned to the browser.

ADK and a configured Uni-GPT backend (.env) are required for this to work. The
heavy imports (ADK, the agent graph, LiteLLM) are deferred to first use, so
importing this module — and therefore starting the web app for the pure
onboarding flow — does not require the agent dependencies to be installed.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from ..onboarding import complaints

logger = logging.getLogger(__name__)

APP_NAME = "journey_autopilot"

_OPTION_CHOICE_RE = re.compile(
    r"^\s*(?:(?:take|choose|select|pick|book|use|go with|let'?s go with)\s+"
    r"(?:option\s+)?)?([RCBH]\d+)\s*[.!]?\s*$",
    re.IGNORECASE,
)

# The orchestrator is instructed to lead its summary with "Risk: <LOW|MEDIUM|HIGH>"
# (see orchestrator.py), but LLMs rephrase — observed variants include
# "**Risk level:** **HIGH**" and "the risk band is HIGH". The pattern therefore
# tolerates one connector word (level/band/score/rating) and one linking verb
# between "risk" and the band. Risk is a free-text signal here, so this is a
# deliberate heuristic — good enough to trigger the proactive alert.
_RISK_LABEL_RE = re.compile(
    r"\brisk(?:\s+(?:level|band|score|rating))?(?:\s+(?:is|remains|stays|currently))?"
    r"[\s:*_·—-]*\b(high|medium|low)\b",
    re.IGNORECASE,
)
_RISK_LOOSE_RE = re.compile(r"\b(high|medium|low)[\s-]+risk\b", re.IGNORECASE)


def detect_risk_band(text: str | None) -> str | None:
    """Pull the risk band (HIGH/MEDIUM/LOW) out of an agent message, or None."""
    if not text:
        return None
    match = _RISK_LABEL_RE.search(text) or _RISK_LOOSE_RE.search(text)
    return match.group(1).upper() if match else None


def _highest_risk_band(reply: str, trace: list[dict]) -> str | None:
    """Worst risk band mentioned in the final reply or any intermediate text.

    Scanning the trace too (not just the final answer) catches the case where the
    monitoring agent reported HIGH but the orchestrator softened the summary.
    Used only to LABEL the conversation for the browser — the decision to alert
    the traveler belongs to the Orchestrator's `send_whatsapp_to_user` tool, not
    to this parser.
    """
    rank = {"LOW": 0, "MEDIUM": 1, "HIGH": 2}
    texts = [reply] + [t.get("text", "") for t in trace if t.get("kind") == "text"]
    bands = [band for band in map(detect_risk_band, texts) if band]
    return max(bands, key=lambda b: rank[b]) if bands else None


# A single in-memory runner is created lazily and reused across requests; ADK
# keeps the per-chat conversation history in its session service. A server
# restart simply starts the conversations over (fine for the prototype).
_runner: Any = None


def _load_env() -> None:
    """Load the project's .env so UNI_GPT_* (and friends) reach the agent.

    The agent config (``journey_autopilot.config``) reads the LiteLLM
    credentials from the environment at import time, so .env must be loaded
    *before* the agent is imported. ``scenarios/happy_path.py`` does the same before pulling
    in ADK; doing it here — right before the lazy agent import — means the chat
    works regardless of how the server was started (``python run_onboarding.py``
    or ``uvicorn journey_autopilot.ui.server:app`` directly).
    """
    try:
        from dotenv import load_dotenv
    except ImportError:
        return  # python-dotenv missing -> rely on the real environment
    load_dotenv()
    load_dotenv("journey_autopilot/.env")


def _get_runner() -> Any:
    """Lazily build (and cache) the InMemoryRunner around ``root_agent``."""
    global _runner
    if _runner is None:
        _load_env()  # ensure credentials are present before config import

        from google.adk.runners import InMemoryRunner

        from ..agent import root_agent

        _runner = InMemoryRunner(agent=root_agent, app_name=APP_NAME)
    return _runner


def _seed_prompt(trip: dict | None, message: str, account: dict | None = None) -> str:
    """First message of a chat: prepend the selected trip as context.

    The orchestrator expects a trip_id (and route/date) to call the monitoring
    agent — exactly what ``scenarios/happy_path.py`` passes in its hard-coded prompt. Here
    the values come from the trip the user clicked. The account's BahnCard is
    included so a passenger-rights check uses the real discount instead of
    defaulting to "keine".
    """
    if not trip:
        return message
    when = (trip.get("planned_departure") or "")[:10]
    context = (
        f"Context — this is the trip I'm asking about: trip_id "
        f"{trip.get('trip_id')}, from {trip.get('origin')} to "
        f"{trip.get('destination')}"
    )
    if trip.get("train"):
        context += f", train {trip.get('train')}"
    if trip.get("planned_departure"):
        context += f", planned departure {trip.get('planned_departure')}"
    if trip.get("planned_arrival"):
        context += f", planned arrival {trip.get('planned_arrival')}"
    if when:
        context += f" on {when}"
    if trip.get("price_eur") is not None:
        # Give the agent the fare up front so a compensation claim doesn't stall
        # asking the user for the ticket price.
        context += f", ticket price {trip.get('price_eur')} EUR"
    if trip.get("travel_class"):
        context += f", {trip.get('travel_class')}. class"
    if account and account.get("bahncard"):
        context += (
            f". My BahnCard: {account['bahncard']}"
            f" (bahncard_type: {complaints.bahncard_type(account)})"
        )
    context += "."
    return f"{context}\n\n{message}"


def _proposal_context(proposal: dict) -> str:
    """Compact authoritative state appended by the application, not the user."""
    payload = proposal.get("proposal") or {}
    option_summaries = []
    for option in payload.get("options") or []:
        option_summaries.append(
            f"{option.get('option_id')} (mode={option.get('mode', 'train')}, "
            f"cost_status={option.get('cost_status', 'unknown')}, "
            f"added_cost_eur={option.get('added_cost_eur')})"
        )
    selected = proposal.get("selected_option_id") or "none"
    return (
        "Authoritative application state (not user-provided): active reroute "
        f"proposal_id={proposal.get('proposal_id')}, expires_at={proposal.get('expires_at')}, "
        f"selected_option_id={selected}, selectable_options=[{'; '.join(option_summaries)}]. "
        "Only these finalized options may be executed. The Executor must pass both "
        "proposal_id and option_id to a booking tool; descriptions and costs are read "
        "from the proposal, never reconstructed from conversation text."
    )


def _public_option(option: dict) -> dict:
    """Strip provider/execution internals before returning a card to the browser."""
    return {
        key: value
        for key, value in option.items()
        if not key.startswith("_provider_") and key != "ranking_score"
    }


def _describe(event: Any) -> list[dict]:
    """Turn one ADK event into compact trace entries for the chat UI.

    Mirrors ``happy_path._describe_event`` but returns structured data (which
    agent called which tool, tool results, intermediate texts) instead of
    printing it.
    """
    out: list[dict] = []
    author = getattr(event, "author", "?")
    content = getattr(event, "content", None)
    if content is None or not getattr(content, "parts", None):
        return out

    for part in content.parts:
        call = getattr(part, "function_call", None)
        response = getattr(part, "function_response", None)
        text = getattr(part, "text", None)

        if call is not None:
            out.append({"kind": "call", "author": author, "name": call.name})
        elif response is not None:
            out.append({"kind": "result", "author": author, "name": response.name})
        elif text and text.strip():
            out.append({"kind": "text", "author": author, "text": text.strip()})
    return out


async def chat_turn(
    session_id: str | None,
    message: str,
    trip: dict | None = None,
    account: dict | None = None,
    notify_phone: str | None = None,
    user_id: str = "ui-user",
    proposal_id: str | None = None,
    selected_option_id: str | None = None,
) -> dict:
    """Run one chat turn through the orchestrator.

    Args:
        session_id: ADK session id from a previous turn, or ``None`` to start a
            new conversation. An id the runner no longer knows (the usual cause
            is a server restart, which empties the InMemoryRunner) is treated
            as ``None``, so the turn still completes — see ``session_restarted``.
        message: The user's chat message.
        trip: The selected trip (added as context on the first turn only).
        account: The logged-in account — its BahnCard is added to the first-turn
            context for accurate passenger-rights checks.
        notify_phone: The traveler's verified number. Bound into the request
            context so the Orchestrator's ``send_whatsapp_to_user`` tool can
            reach them; it is never passed to the model, which decides only
            whether to send, not to whom. ``None`` makes that tool a no-op.
        user_id: Authenticated application user owning the ADK session/proposal.
        proposal_id: Authoritative proposal selected by a structured option card.
        selected_option_id: Eligible option selected from that proposal.

    Returns:
        ``{"session_id", "session_restarted", "reply", "trace", "risk_band",
        "alert"}`` — the (new or reused) session id, whether a dead session was
        silently replaced, the orchestrator's final answer, the agent/tool
        trace, the risk band parsed out of it as a label, and the delivery
        result of the WhatsApp notice the Orchestrator chose to send (or
        ``None`` when it sent none), plus the reroute option/proposal fields
        and any complaint draft the settled rights lookup produced.
    """
    from .. import request_context

    # One workspace for this turn, bound before any tool can run. The Planner's
    # reroute shortlist, the settled rights result, the specialists' trace and
    # the WhatsApp sends all land in it — everything nested AgentTool runs
    # produce that ADK does not surface to the parent event stream. Binding it
    # here rather than around the runner loop means it is still readable while
    # this function assembles its answer.
    workspace = request_context.new_turn_workspace()
    token = request_context.set_turn_workspace(workspace)
    try:
        return await _run_chat_turn(
            workspace,
            session_id,
            message,
            trip,
            account,
            notify_phone,
            user_id,
            proposal_id,
            selected_option_id,
        )
    finally:
        request_context.reset_turn_workspace(token)


async def _run_chat_turn(
    workspace: dict,
    session_id: str | None,
    message: str,
    trip: dict | None,
    account: dict | None,
    notify_phone: str | None,
    user_id: str,
    proposal_id: str | None,
    selected_option_id: str | None,
) -> dict:
    """The turn itself, with ``workspace`` already bound. See ``chat_turn``."""
    from google.genai import types

    runner = _get_runner()

    # The ADK session lives in the process-local InMemoryRunner, so a server
    # restart invalidates every session id the browser still holds. Detect that
    # here instead of letting run_async raise SessionNotFoundError: falling
    # through to the create path below gives the caller a fresh, trip-seeded
    # session and the conversation simply continues. The agent has genuinely
    # forgotten the earlier turns, which ``session_restarted`` lets the UI say
    # out loud rather than looking inexplicably forgetful.
    session_restarted = False
    if session_id:
        known = await runner.session_service.get_session(
            app_name=APP_NAME, user_id=user_id, session_id=session_id
        )
        if known is None:
            logger.info(
                "ADK session %s no longer exists (server restart?) — opening a fresh one",
                session_id,
            )
            session_id = None
            session_restarted = True

    if not session_id:
        session = await runner.session_service.create_session(
            app_name=APP_NAME, user_id=user_id
        )
        session_id = session.id
        text = _seed_prompt(trip, message, account)
    else:
        # The session already carries the trip context from the first turn.
        text = message

    # Structured card selections are validated against the persisted shortlist
    # before the model sees them. A conservative exact-text parser keeps manual
    # "Take option R1" input working without treating incidental mentions as a
    # selection.
    from ..persistence import store

    trip_id = str((trip or {}).get("trip_id") or "")
    active_proposal = store.get_active_reroute_proposal(
        user_id, session_id, trip_id if trip_id else None
    )
    chosen_id = selected_option_id
    chosen_proposal_id = proposal_id
    if not chosen_id and active_proposal:
        match = _OPTION_CHOICE_RE.fullmatch(message)
        if match:
            chosen_id = match.group(1).upper()
            chosen_proposal_id = active_proposal["proposal_id"]
    if chosen_id or chosen_proposal_id:
        if not chosen_id or not chosen_proposal_id:
            raise ValueError("Both proposal_id and selected_option_id are required for a selection.")
        selected = store.select_reroute_option(
            user_id, session_id, chosen_proposal_id, chosen_id.upper()
        )
        if selected.get("error"):
            raise ValueError(selected["error"])
        active_proposal = selected
    if active_proposal:
        text = f"{_proposal_context(active_proposal)}\n\n{text}"

    new_message = types.Content(role="user", parts=[types.Part(text=text)])
    reply = ""

    # Sub-agents append their own tool calls to the workspace trace via callbacks
    # (see orchestrator._make_subagent_trace_callbacks). Their AgentTool runner
    # runs synchronously between the Orchestrator's call and result events, so
    # those entries interleave in the right nested order.
    trace: list[dict] = workspace["trace"]
    # Every outbound WhatsApp outcome this turn, delivered or not. The send tool
    # reads the *delivered* subset back to refuse a second notice — which is why
    # the retry below must not clear it. The browser toasts the last entry, so
    # the undelivered ones (no verified number, Twilio error) are kept too.
    whatsapp_sends: list[dict] = workspace["whatsapp_sends"]

    async def _run_turn() -> str:
        """One pass through the orchestrator; rebuilds the trace from scratch."""
        from .. import request_context

        trace.clear()
        result = ""
        # The traveler's number is bound here, not passed to the model: the
        # Orchestrator decides *whether* to push a WhatsApp notice, never *to
        # whom*. send_whatsapp_to_user reads it from this context.
        context_tokens = request_context.bind(user_id, session_id, notify_phone)
        try:
            async for event in runner.run_async(
                user_id=user_id, session_id=session_id, new_message=new_message
            ):
                if event.is_final_response() and event.content and event.content.parts:
                    result = "".join(
                        p.text for p in event.content.parts if getattr(p, "text", None)
                    )
                    continue
                trace.extend(_describe(event))
        finally:
            request_context.reset(context_tokens)
        return result

    # The LLM backend occasionally emits malformed JSON in a tool call
    # (typically long multi-line arguments like an email body); ADK's parser
    # then raises JSONDecodeError. That's transient model output, not app
    # state — one retry usually clears it (error policy: recover inside the
    # loop, don't crash the turn). Write tools stay safe under the replay: the
    # email approval_id is single-use, a reroute proposal can only be claimed
    # once, and the WhatsApp notice refuses to fire again once one has actually
    # been delivered this turn (it checks the send record, which is deliberately
    # not reset on retry; a failed attempt stays retryable, since the traveler's
    # phone never buzzed).
    for attempt in (1, 2):
        try:
            reply = await _run_turn()
            break
        except json.JSONDecodeError as exc:
            if attempt == 1:
                logger.warning(
                    "malformed tool-call JSON from the model (%s) — retrying the turn once",
                    exc,
                )
            else:
                logger.error("malformed tool-call JSON twice in a row: %s", exc)
                reply = (
                    "The language model produced a malformed tool call twice in "
                    "a row, so this turn could not be completed. Please send "
                    "your message again."
                )

    reply = reply or "(no response)"

    # The proactive WhatsApp notice is the Orchestrator's own decision, taken
    # inside the ReAct loop via send_whatsapp_to_user (see orchestrator.py) —
    # not something this layer infers from the answer text afterwards. All that
    # happens here is reading back what it sent, so the browser can toast the
    # delivery result. The risk band is still parsed, but only as a LABEL for
    # the conversation; it no longer triggers anything.
    risk_band = _highest_risk_band(reply, trace)
    alert: dict | None = whatsapp_sends[-1] if whatsapp_sends else None

    # Log the outcome so a missing notice is diagnosable (band detected, phone
    # present, whether the agent chose to send) — not just silence.
    logger.info(
        "chat turn: session=%s risk_band=%s phone=%s whatsapp_attempts=%d "
        "whatsapp_delivered=%d alert=%s",
        session_id,
        risk_band,
        notify_phone or "(none)",
        len(whatsapp_sends),
        sum(1 for s in whatsapp_sends if s.get("sent") or s.get("demo")),
        alert,
    )
    # Pick up only the finalized structured shortlist. Discovery candidates are
    # deliberately invisible to the browser so calendar/profile rejections can
    # never become selectable cards.
    options: list[dict] | None = None
    fallback_options: list[dict] | None = None
    options_source: str | None = None
    recommended_option_id: str | None = None
    rejected_summary: dict | None = None
    response_proposal_id: str | None = (
        active_proposal.get("proposal_id") if active_proposal else None
    )
    proposal_expires_at: str | None = (
        active_proposal.get("expires_at") if active_proposal else None
    )
    stashed = workspace["reroute"]
    if stashed and stashed.get("finalized"):
        from ..config import REROUTE_PROPOSAL_TTL_SECONDS

        proposal = store.save_reroute_proposal(
            user_id,
            session_id,
            trip_id,
            {
                "trip_id": trip_id,
                "travel_date": ((trip or {}).get("planned_departure") or "")[:10],
                "origin": stashed.get("origin"),
                "destination": stashed.get("destination"),
                "source": stashed.get("source"),
                "options": stashed.get("options") or [],
                "fallback_options": stashed.get("fallback_options") or [],
                "recommended_option_id": stashed.get("recommended_option_id"),
                "calendar_checked": stashed.get("calendar_checked", False),
                "calendar_verdicts": stashed.get("calendar_verdicts") or {},
                "calendar_result": stashed.get("calendar_result") or {},
                "rejected_summary": stashed.get("rejected_summary") or {},
            },
            ttl_seconds=REROUTE_PROPOSAL_TTL_SECONDS,
        )
        response_proposal_id = proposal["proposal_id"]
        proposal_expires_at = proposal["expires_at"]
        options = [
            _public_option(option) for option in stashed.get("options") or []
        ] or None
        fallback_options = [
            _public_option(option)
            for option in stashed.get("fallback_options") or []
        ] or None
        options_source = stashed.get("source")
        recommended_option_id = stashed.get("recommended_option_id")
        rejected_summary = stashed.get("rejected_summary") or None

    # A settled rights lookup (concluded trip only — a mid-trip entitlement
    # check never reaches the workspace) seeds a reviewable complaint draft.
    # Done here rather than by the caller because this is where the workspace
    # is still bound; complaints.py takes the result as an argument.
    complaint_created = complaints.maybe_create_from_rights(
        user_id, trip, workspace["rights"]
    )

    return {
        "session_id": session_id,
        "session_restarted": session_restarted,
        "reply": reply,
        "trace": trace,
        "risk_band": risk_band,
        "alert": alert,
        "options": options,
        "fallback_options": fallback_options,
        "options_source": options_source,
        "recommended_option_id": recommended_option_id,
        "rejected_summary": rejected_summary,
        "proposal_id": response_proposal_id,
        "proposal_expires_at": proposal_expires_at,
        "complaint_created": complaint_created,
    }
