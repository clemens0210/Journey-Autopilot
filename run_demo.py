"""End-to-End-Demo des Orchestrators im Terminal.

Fährt eine einzelne Anfrage gegen den ReAct-Orchestrator und streamt die
Ereignisse mit, damit die Zusammenarbeit von Monitoring- und Planner-Agent
sichtbar wird (welcher Agent wird wann gerufen, welche Tools laufen).

Nutzung:
    python run_demo.py

Voraussetzung: ein konfiguriertes Uni-GPT-Backend in der .env (UNI_GPT_*; siehe
README). Alternativ läuft auch die ADK-Dev-UI:  adk web   bzw.
adk run journey_autopilot
"""

from __future__ import annotations

import asyncio

# Windows certificate store sometimes contains malformed certs that cause
# ssl.SSLError: [ASN1: NOT_ENOUGH_DATA] when aiohttp calls ssl.create_default_context()
# at module-load time. Patching load_default_certs to swallow that error lets the
# rest of the store (and certifi's bundle) still work fine.
import ssl as _ssl
_orig_load_default_certs = _ssl.SSLContext.load_default_certs
def _patched_load_default_certs(self, purpose=_ssl.Purpose.SERVER_AUTH):
    try:
        _orig_load_default_certs(self, purpose)
    except _ssl.SSLError:
        pass
_ssl.SSLContext.load_default_certs = _patched_load_default_certs

from google.adk.runners import InMemoryRunner
from google.genai import types

try:
    from dotenv import load_dotenv

    # .env aus dem Projekt-Root und aus dem Agenten-Paket laden.
    load_dotenv()
    load_dotenv("journey_autopilot/.env")
except ImportError:
    # python-dotenv ist eine ADK-Abhängigkeit; falls nicht da, .env manuell setzen.
    pass

from journey_autopilot.agent import root_agent
from journey_autopilot.mock_data import DEMO_TRIP, DEMO_EVENT_FIELDS
from journey_autopilot.whatsapp_communicator.models import DisruptionEvent, Recipient
from journey_autopilot.whatsapp_communicator import drafter, sender

APP_NAME = "journey_autopilot"
USER_ID = "lucas"

# Anfrage, mit der die Reise an den Orchestrator übergeben wird.
PROMPT = (
    f"Bitte überwache meine Reise mit der trip_id {DEMO_TRIP['trip_id']} "
    f"von {DEMO_TRIP['origin']} nach {DEMO_TRIP['destination']} am 2026-06-03 "
    "und sag mir, ob ich etwas tun muss."
)


def _describe_event(event) -> None:
    """Gibt Tool-Aufrufe, Tool-Ergebnisse und Texte je Event lesbar aus."""
    author = getattr(event, "author", "?")
    content = getattr(event, "content", None)
    if content is None or not getattr(content, "parts", None):
        return

    for part in content.parts:
        call = getattr(part, "function_call", None)
        response = getattr(part, "function_response", None)
        text = getattr(part, "text", None)

        if call is not None:
            print(f"  [{author}] -> ruft auf: {call.name}({dict(call.args or {})})")
        elif response is not None:
            print(f"  [{author}] <- Ergebnis von: {response.name}")
        elif text and text.strip():
            print(f"  [{author}] {text.strip()}")


async def _demo_whatsapp() -> None:
    """WhatsApp-Communicator-Demo: Nachrichten entwerfen und (optional) per Twilio versenden."""
    import os

    traveler_number = os.getenv("DEMO_TRAVELER_NUMBER", "")
    client_number = os.getenv("DEMO_CLIENT_NUMBER", "")
    colleague_number = os.getenv("DEMO_COLLEAGUE_NUMBER", "")
    private_number = os.getenv("DEMO_PRIVATE_NUMBER", "")

    print("\n" + "=" * 72)
    print("Journey Autopilot — WhatsApp Communicator Demo")
    print("=" * 72)

    if not traveler_number:
        print(
            "[!] DEMO_TRAVELER_NUMBER nicht in .env gesetzt.\n"
            "    Setze DEMO_TRAVELER_NUMBER (und optional DEMO_CLIENT_NUMBER,\n"
            "    DEMO_COLLEAGUE_NUMBER, DEMO_PRIVATE_NUMBER) um die Demo zu aktivieren."
        )
        return

    recipients: list[Recipient] = [
        Recipient(name="Lucas Wild", role="traveler", whatsapp_number=traveler_number),
    ]
    if client_number:
        recipients.append(Recipient(name="Frau Dr. Bauer", role="client", whatsapp_number=client_number))
    if colleague_number:
        recipients.append(Recipient(name="Thomas Müller", role="colleague", whatsapp_number=colleague_number))
    if private_number:
        recipients.append(Recipient(name="Anna Wild", role="private", whatsapp_number=private_number))

    event = DisruptionEvent(**DEMO_EVENT_FIELDS, recipients=recipients)

    twilio_ready = bool(
        os.getenv("TWILIO_ACCOUNT_SID")
        and os.getenv("TWILIO_AUTH_TOKEN")
        and os.getenv("TWILIO_WHATSAPP_FROM")
    )

    print(f"\nSzenario : {event.traveler_name} | {event.original_train} | +{event.delay_minutes} min")
    print(f"Reroute  : {event.reroute_summary}")
    print(f"Termin   : {event.meeting_time_original} (unverändert — Ankunft 12:38 liegt vor 14:00)")

    non_traveler = [r for r in recipients if r.role != "traveler"]
    if not non_traveler:
        print("\nKeine weiteren Empfänger konfiguriert (DEMO_CLIENT_NUMBER etc. fehlen).")
    else:
        print(f"Entwürfe : {', '.join(r.name for r in non_traveler)}\n")

    for recipient in non_traveler:
        print(f"--- Entwurf für {recipient.name} ({recipient.role}) " + "-" * 30)
        try:
            draft = await drafter.draft_message_async(event, recipient)
            print(draft)
        except Exception as exc:
            print(f"[!] Entwurf fehlgeschlagen: {type(exc).__name__}: {exc}")
            continue

        if twilio_ready:
            print(f"\n  → Sende Freigabe-Anfrage an Lucas ({traveler_number}) ...")
            try:
                msg_id = sender.send_for_approval(event, draft, recipient)
                print(f"  → Gesendet. message_id={msg_id}")
                print("  → Lucas antwortet per WhatsApp mit YES / NO / EDIT <text>.")
            except Exception as exc:
                print(f"  [!] Twilio-Fehler: {type(exc).__name__}: {exc}")
        else:
            print(
                "\n  [Trockenlauf] TWILIO_* nicht konfiguriert — "
                "Nachricht würde als Freigabe-Anfrage an Lucas gesendet."
            )

    print("\n--- Webhook-Server ---")
    print("Empfangsserver für Lucas' Antworten starten:")
    print("  uvicorn journey_autopilot.whatsapp_communicator.webhook:app --port 8000")
    print("Twilio leitet YES / NO / EDIT-Nachrichten an POST /whatsapp/reply weiter.")


async def main() -> None:
    runner = InMemoryRunner(agent=root_agent, app_name=APP_NAME)
    session = await runner.session_service.create_session(
        app_name=APP_NAME, user_id=USER_ID
    )

    print("=" * 72)
    print("Journey Autopilot — Demo-Run (Orchestrator: ReAct)")
    print("=" * 72)
    print(f"User: {PROMPT}\n")
    print("--- Agenten-/Tool-Verlauf -------------------------------------------")

    message = types.Content(role="user", parts=[types.Part(text=PROMPT)])
    final_text = ""
    try:
        async for event in runner.run_async(
            user_id=USER_ID, session_id=session.id, new_message=message
        ):
            _describe_event(event)
            if event.is_final_response() and event.content and event.content.parts:
                final_text = "".join(
                    p.text for p in event.content.parts if getattr(p, "text", None)
                )
    except Exception as exc:
        # Fällt hierher u. a. bei LiteLLM-/Uni-GPT-Fehlern: falscher Endpunkt,
        # Key oder Modellname haben jeweils eigene Exception-Typen.
        print("\n[!] Lauf abgebrochen.")
        print(f"    {type(exc).__name__}: {exc}")
        print("    UNI_GPT_BASE_URL (inkl. /v1), UNI_GPT_API_KEY und UNI_GPT_MODEL")
        print("    in der .env prüfen — und ob google-adk[extensions] installiert")
        print("    ist (pip install -r requirements.txt).")
        return

    print("\n--- Antwort an den Nutzer -------------------------------------------")
    print(final_text or "(keine Textantwort)")

    await _demo_whatsapp()


if __name__ == "__main__":
    asyncio.run(main())
