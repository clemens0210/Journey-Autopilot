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

from ..onboarding.complaints import bahncard_type

logger = logging.getLogger(__name__)

APP_NAME = "journey_autopilot"
USER_ID = "ui-user"

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


def _is_high_risk(reply: str, trace: list[dict]) -> bool:
    """True if the final reply or any intermediate agent text flags HIGH risk.

    Scanning the trace too (not just the final answer) catches the case where the
    monitoring agent reported HIGH but the orchestrator softened the summary.
    """
    candidates = [reply]
    candidates += [t.get("text", "") for t in trace if t.get("kind") == "text"]
    return any(detect_risk_band(text) == "HIGH" for text in candidates)


def _alert_body(trip: dict | None, reply: str, risk_band: str | None = None) -> str:
    """Compose the WhatsApp notice (kept short for messaging).

    The header reflects the detected band: a HIGH-risk warning, a plain trip
    update with the band, or a generic update when no band was parsed.
    """
    if risk_band == "HIGH":
        header = "⚠️ Journey Autopilot — HIGH disruption risk"
    elif risk_band:
        header = f"🚆 Journey Autopilot — trip update (risk: {risk_band})"
    else:
        header = "🚆 Journey Autopilot — trip update"
    if trip:
        when = (trip.get("planned_departure") or "")[:10]
        route = f"{trip.get('origin')} → {trip.get('destination')}"
        line = " · ".join(p for p in (trip.get("train"), when) if p)
        header += f"\n{route}" + (f" ({line})" if line else "")
    summary = reply.strip()
    if len(summary) > 900:  # keep the WhatsApp body manageable
        summary = summary[:900].rstrip() + "…"
    return f"{header}\n\n{summary}"

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

        from journey_autopilot.agent import root_agent

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
            f" (bahncard_type: {bahncard_type(account)})"
        )
    context += "."
    return f"{context}\n\n{message}"


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
) -> dict:
    """Run one chat turn through the orchestrator.

    Args:
        session_id: ADK session id from a previous turn, or ``None`` to start a
            new conversation.
        message: The user's chat message.
        trip: The selected trip (added as context on the first turn only).
        account: The logged-in account — its BahnCard is added to the first-turn
            context for accurate passenger-rights checks.
        notify_phone: The traveler's saved number. When monitoring comes back
            HIGH risk, a proactive WhatsApp disruption alert is sent here.

    Returns:
        ``{"session_id", "reply", "trace", "risk_band", "alert"}`` — the (new or
        reused) session id, the orchestrator's final answer, the agent/tool
        trace, the detected risk band, and the disruption-alert result (or
        ``None`` when no alert was attempted).
    """
    from google.genai import types
    from journey_autopilot.tools.read_tools import clear_passenger_rights

    runner = _get_runner()
    clear_passenger_rights()  # a stale rights result from a prior turn must not leak in

    # Reset the in-process reroute slot so options from a previous turn are
    # never shown. The Planner's find_reroute_options tool repopulates it when
    # it runs; read after the loop. (Base AgentTool runs the sub-agent in its
    # own runner, so the tool payload doesn't surface in the event stream —
    # see tools/read_tools.py.)
    try:
        from ..tools import read_tools
        read_tools.clear_reroute_options()
    except Exception:
        read_tools = None  # type: ignore[assignment]

    if not session_id:
        session = await runner.session_service.create_session(
            app_name=APP_NAME, user_id=USER_ID
        )
        session_id = session.id
        text = _seed_prompt(trip, message, account)
    else:
        # The session already carries the trip context from the first turn.
        text = message

    new_message = types.Content(role="user", parts=[types.Part(text=text)])
    trace: list[dict] = []
    reply = ""

    async def _run_turn() -> str:
        """One pass through the orchestrator; rebuilds the trace from scratch."""
        trace.clear()
        result = ""
        async for event in runner.run_async(
            user_id=USER_ID, session_id=session_id, new_message=new_message
        ):
            if event.is_final_response() and event.content and event.content.parts:
                result = "".join(
                    p.text for p in event.content.parts if getattr(p, "text", None)
                )
                continue
            trace.extend(_describe(event))
        return result

    # The LLM backend occasionally emits malformed JSON in a tool call
    # (typically long multi-line arguments like an email body); ADK's parser
    # then raises JSONDecodeError. That's transient model output, not app
    # state — one retry usually clears it (error policy: recover inside the
    # loop, don't crash the turn). Write tools stay safe under the replay:
    # the email approval_id is single-use, so a send can never fire twice.
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

    # Proactive notice: push a one-way WhatsApp message to the traveler's saved
    # number (via Twilio) on every MONITORING turn — i.e. whenever the reply
    # carries a risk band, not only on HIGH. Turns without a band (greetings,
    # email approvals, follow-up questions) send nothing (alert=None), so the
    # traveler isn't spammed on every message. Twilio's client is blocking, so
    # it runs off the event loop.
    high = _is_high_risk(reply, trace)
    risk_band = "HIGH" if high else detect_risk_band(reply)
    alert: dict | None = None
    if risk_band is not None:
        import asyncio

        from journey_autopilot.integrations import whatsapp

        alert = await asyncio.to_thread(
            whatsapp.send_disruption_alert,
            notify_phone or "",
            _alert_body(trip, reply, risk_band),
        )

    # Always log the outcome so "no alert" is diagnosable (band detected, phone
    # present, send result) — not just silence.
    logger.info(
        "chat turn: session=%s risk_band=%s phone=%s alert=%s",
        session_id,
        risk_band,
        notify_phone or "(none)",
        alert,
    )
    # Pick up the structured option list the Planner's tool stashed, if any.
    options: list[dict] | None = None
    options_source: str | None = None
    if read_tools is not None:
        stashed = read_tools.last_reroute_options()
        if stashed and stashed.get("options"):
            options = stashed["options"]
            options_source = stashed.get("source")

    return {
        "session_id": session_id,
        "reply": reply,
        "trace": trace,
        "risk_band": risk_band,
        "alert": alert,
        "options": options,
        "options_source": options_source,
    }
