"""Communicator Agent — drafts context-aware WhatsApp messages and notice emails.

The write-side agent that talks to the traveler and participants. Two shapes:

- ``build_communicator_agent(role)`` — the WhatsApp drafter, parametrized by
  recipient ``role`` and driven directly (its own ``InMemoryRunner``) rather
  than wrapped as an ``AgentTool`` on the Orchestrator — that draft is
  bracketed by the WhatsApp veto gate, not chosen inside the ReAct loop.
- ``build_email_communicator_agent()`` — the notice-email drafter, wrapped as
  an ``AgentTool`` on the Orchestrator. Its veto gate lives in the write
  tools: ``propose_appointment_notice_email`` stages a draft (shown to the
  user in the chat) and ``send_approved_notice_email`` fires only with the
  approval id AFTER the user said yes.

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
from ..tools.write_tools import (
    propose_appointment_notice_email,
    send_approved_notice_email,
)

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


EMAIL_COMMUNICATOR_INSTRUCTION = """\
You are the **Communicator Agent** (email) in the "Journey Autopilot" system.
You handle notice emails to the contact of a calendar appointment that a
disrupted train trip endangers. You operate in exactly one of two modes per
request — decide by what the request asks for:

A) DRAFT (the request contains appointment + trip details and a recipient):
   1. Compose a short, professional email ON BEHALF OF the traveler to the
      appointment contact:
      - Subject: appointment title + "possible delay".
      - Body: 3-6 sentences. State that the traveler may miss or be late to
        the appointment (title, date, time), name the concrete circumstances
        given to you (train delay/disruption, expected arrival), give the new
        expected arrival if known, apologize briefly, and ask to hold or
        reschedule if needed. No internal rerouting details. Sign with the
        traveler's name if given.
   2. Call `propose_appointment_notice_email` with the recipient address,
      subject, and body. This stages the draft — NOTHING is sent.
   3. Return the draft VERBATIM (recipient, subject, body) plus the
      `approval_id`, and state clearly that it will only be sent after the
      user's approval.

B) SEND (the request explicitly says the user approved a draft and names an
   approval_id):
   Call `send_approved_notice_email` with that approval_id and report the
   result (sent / simulated / error incl. any hint).

STRICT RULES:
- NEVER call `send_approved_notice_email` in DRAFT mode or without an
  explicit statement that the user approved. The user's approval happens in
  the chat, outside your view — you rely on the orchestrator relaying it.
- Use ONLY the recipient address given in the request (it comes from the
  clashing calendar event). Never invent an address.
- Invent no facts about the disruption — use exactly the circumstances
  provided in the request.
"""


def build_email_communicator_agent() -> LlmAgent:
    """Creates the email Communicator LlmAgent (draft -> user veto -> send).

    Wrapped as an ``AgentTool`` on the Orchestrator. Holds the only two write
    tools in the system; the propose/approve split in ``tools/write_tools.py``
    enforces the veto gate regardless of what the LLM does.
    """
    return LlmAgent(
        name="communicator_agent",
        model=DRAFTER_MODEL,
        description=(
            "Drafts a notice email to the contact of a calendar appointment "
            "endangered by a trip disruption, and sends it only after the "
            "user approved the shown draft (approval_id). Proposes first, "
            "never sends unasked."
        ),
        instruction=EMAIL_COMMUNICATOR_INSTRUCTION,
        tools=[
            propose_appointment_notice_email,
            send_approved_notice_email,
        ],
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
