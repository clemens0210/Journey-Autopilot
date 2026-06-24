"""Communicator Agent — drafts context-aware WhatsApp messages.

The write-side agent that talks to the traveler and participants. Like its
siblings it exposes a ``build_communicator_agent()`` factory returning an
ADK ``LlmAgent`` (model from ``config.DRAFTER_MODEL``, the Uni-GPT endpoint via
LiteLLM). It differs from monitoring/planner in one respect: it is parametrized
by recipient ``role`` and is driven directly (its own ``InMemoryRunner``) rather
than wrapped as an ``AgentTool`` on the Orchestrator — the draft is bracketed by
the veto gate, not chosen inside the ReAct loop.

The remaining functionality (Twilio sending, approval/veto queue) lives in
``integrations/whatsapp.py``; inbound YES/NO/EDIT traffic is handled by
``integrations/whatsapp_webhook.py``.
"""

from __future__ import annotations

import asyncio

from google.adk.agents import LlmAgent
from google.adk.runners import InMemoryRunner
from google.genai import types

from ..config import DRAFTER_MODEL
from ..integrations.whatsapp_models import DisruptionEvent, Recipient

_APP_NAME = "whatsapp_drafter"
_USER_ID = "drafter"

_ROLE_INSTRUCTION: dict[str, str] = {
    "traveler": (
        "You are a travel assistant writing directly to the traveler. "
        "Be concise and action-oriented — show the new travel plan clearly, "
        "so they know exactly what to do."
    ),
    "colleague": (
        "You are writing on behalf of the traveler to a work colleague. "
        "Keep it casual. Mention the delay and whether the colleague needs to do anything."
    ),
    "client": (
        "You are writing on behalf of the traveler to a business client. "
        "Be professional and apologetic. Mention only the relevant time change — "
        "leave out internal rerouting details."
    ),
    "private": (
        "You are writing on behalf of the traveler to someone from their private life. "
        "Keep it warm, informal and short."
    ),
}


def _build_prompt(event: DisruptionEvent, recipient: Recipient) -> str:
    return (
        f"Train disruption details:\n"
        f"- Traveler: {event.traveler_name}\n"
        f"- Train: {event.original_train}\n"
        f"- Delay: {event.delay_minutes} minutes\n"
        f"- Reroute: {event.reroute_summary}\n"
        f"- Meeting originally: {event.meeting_time_original}\n"
        f"- Meeting now expected: {event.meeting_time_new}\n"
        f"- Recipient: {recipient.name}\n\n"
        "Write a WhatsApp message in English. At most 3 sentences. "
        "No formal 'Dear Sir/Madam' or similar — get straight to the point."
    )


def build_communicator_agent(role: str) -> LlmAgent:
    """Creates the Communicator LlmAgent for a given recipient role.

    Unlike ``build_monitoring_agent`` / ``build_planner_agent`` this takes a
    ``role`` (traveler / colleague / client / private) — the Communicator's
    instruction is parametrized by who is being written to.
    """
    return LlmAgent(
        name="communicator_agent",
        model=DRAFTER_MODEL,
        instruction=_ROLE_INSTRUCTION[role] + " Always write in English.",
    )


async def draft_message_async(event: DisruptionEvent, recipient: Recipient) -> str:
    """Drafts a WhatsApp message via the LlmAgent."""
    agent = build_communicator_agent(recipient.role)
    runner = InMemoryRunner(agent=agent, app_name=_APP_NAME)
    session = await runner.session_service.create_session(
        app_name=_APP_NAME, user_id=_USER_ID
    )

    message = types.Content(
        role="user", parts=[types.Part(text=_build_prompt(event, recipient))]
    )
    draft = ""
    async for ev in runner.run_async(
        user_id=_USER_ID, session_id=session.id, new_message=message
    ):
        if ev.is_final_response() and ev.content and ev.content.parts:
            draft = "".join(
                p.text for p in ev.content.parts if getattr(p, "text", None)
            )
    return draft.strip()


def draft_message(event: DisruptionEvent, recipient: Recipient) -> str:
    """Synchronous wrapper — only call from a pure sync context."""
    return asyncio.run(draft_message_async(event, recipient))
