"""Drafter Agent — entwirft kontextgerechte WhatsApp-Nachrichten.

Folgt demselben Muster wie planner.py und monitoring.py: LlmAgent aus dem
Google ADK, Modell aus config.DRAFTER_MODEL (Uni-GPT-Endpunkt via LiteLLM).
"""

from __future__ import annotations

import asyncio

from google.adk.agents import LlmAgent
from google.adk.runners import InMemoryRunner
from google.genai import types

from ..config import DRAFTER_MODEL
from .models import DisruptionEvent, Recipient

_APP_NAME = "whatsapp_drafter"
_USER_ID = "drafter"

_ROLE_INSTRUCTION: dict[str, str] = {
    "traveler": (
        "Du bist ein Reise-Assistent, der direkt an den Reisenden schreibt. "
        "Sei knapp und handlungsorientiert — zeige den neuen Reiseplan klar, "
        "damit er genau weiß, was zu tun ist."
    ),
    "colleague": (
        "Du schreibst im Namen des Reisenden an einen Arbeitskollegen. "
        "Halte es locker. Erwähne die Verspätung und ob der Kollege etwas tun muss."
    ),
    "client": (
        "Du schreibst im Namen des Reisenden an einen Geschäftskunden. "
        "Sei professionell und entschuldigend. Nenne nur die relevante Zeitänderung — "
        "interne Umleitungsdetails weglassen."
    ),
    "private": (
        "Du schreibst im Namen des Reisenden an eine Person aus dem Privatleben. "
        "Halte es herzlich, informell und kurz."
    ),
}


def _build_prompt(event: DisruptionEvent, recipient: Recipient) -> str:
    return (
        f"Zugstörungsdetails:\n"
        f"- Reisender: {event.traveler_name}\n"
        f"- Zug: {event.original_train}\n"
        f"- Verspätung: {event.delay_minutes} Minuten\n"
        f"- Umleitung: {event.reroute_summary}\n"
        f"- Meeting ursprünglich: {event.meeting_time_original} Uhr\n"
        f"- Meeting neu erwartet: {event.meeting_time_new} Uhr\n"
        f"- Empfänger: {recipient.name}\n\n"
        "Schreibe eine WhatsApp-Nachricht. Maximal 3 Sätze. "
        "Kein formelles 'Sehr geehrte/r' o. Ä. — direkt zur Sache."
    )


def _build_agent(role: str) -> LlmAgent:
    return LlmAgent(
        name="drafter_agent",
        model=DRAFTER_MODEL,
        instruction=_ROLE_INSTRUCTION[role],
    )


async def draft_message_async(event: DisruptionEvent, recipient: Recipient) -> str:
    """Entwirft eine WhatsApp-Nachricht über den LlmAgent."""
    agent = _build_agent(recipient.role)
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
    """Synchroner Wrapper — nur aus reinem Sync-Kontext aufrufen."""
    return asyncio.run(draft_message_async(event, recipient))
