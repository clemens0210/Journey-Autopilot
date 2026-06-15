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
from journey_autopilot.whatsapp_communicator import drafter, tools

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


def _build_demo_context(os_module) -> tuple | None:
    """Shared setup logic for both WhatsApp demos.

    Returns (event, traveler, non_traveler, twilio_ready) or None if
    DEMO_TRAVELER_NUMBER is missing.
    """
    traveler_number = os_module.getenv("DEMO_TRAVELER_NUMBER", "")
    if not traveler_number:
        print(
            "[!] DEMO_TRAVELER_NUMBER not set in .env.\n"
            "    Set DEMO_TRAVELER_NUMBER (and optionally DEMO_CLIENT_NUMBER,\n"
            "    DEMO_COLLEAGUE_NUMBER, DEMO_PRIVATE_NUMBER) to enable the demo."
        )
        return None

    recipients: list[Recipient] = [
        Recipient(name="Lucas Wild", role="traveler", whatsapp_number=traveler_number),
    ]
    if client_number := os_module.getenv("DEMO_CLIENT_NUMBER", ""):
        recipients.append(Recipient(name="Frau Dr. Bauer", role="client", whatsapp_number=client_number))
    if colleague_number := os_module.getenv("DEMO_COLLEAGUE_NUMBER", ""):
        recipients.append(Recipient(name="Thomas Müller", role="colleague", whatsapp_number=colleague_number))
    if private_number := os_module.getenv("DEMO_PRIVATE_NUMBER", ""):
        recipients.append(Recipient(name="Anna Wild", role="private", whatsapp_number=private_number))

    event = DisruptionEvent(**DEMO_EVENT_FIELDS, recipients=recipients)
    traveler = next(r for r in recipients if r.role == "traveler")
    non_traveler = [r for r in recipients if r.role != "traveler"]
    twilio_ready = bool(
        os_module.getenv("TWILIO_ACCOUNT_SID")
        and os_module.getenv("TWILIO_AUTH_TOKEN")
        and os_module.getenv("TWILIO_WHATSAPP_FROM")
    )
    return event, traveler, non_traveler, twilio_ready


async def _demo_direct_notify() -> None:
    """Demo 1 — Direct message: disruption notice sent straight to the traveler, no drafter."""
    import os

    print("\n" + "=" * 72)
    print("WhatsApp Demo 1 — Direct message to traveler (no drafter)")
    print("=" * 72)

    ctx = _build_demo_context(os)
    if ctx is None:
        return
    event, traveler, _non_traveler, twilio_ready = ctx

    print(f"\nScenario : {event.traveler_name} | {event.original_train} | +{event.delay_minutes} min")

    notice = (
        f"*Journey Autopilot — Disruption Notice*\n\n"
        f"Train: {event.original_train}\n"
        f"Current delay: {event.delay_minutes} min\n"
        f"Proposed reroute: {event.reroute_summary}\n"
        f"Your appointment at {event.meeting_time_original} is still achievable."
    )

    print(f"\nNachricht an {traveler.name} ({traveler.whatsapp_number}):\n")
    print(notice)

    if twilio_ready:
        print(f"\n  → Sending directly to {traveler.name} ({traveler.whatsapp_number}) ...")
        try:
            tools.dispatch_message(notice, traveler)
            print("  → Sent.")
        except Exception as exc:
            print(f"  [!] Twilio error: {type(exc).__name__}: {exc}")
    else:
        print("\n  [Dry run] TWILIO_* not configured — message would be sent directly.")


async def _demo_approval_flow() -> None:
    """Demo 2 — Approval workflow: drafter drafts, traveler approves via WhatsApp."""
    import os

    print("\n" + "=" * 72)
    print("WhatsApp Demo 2 — Drafter + approval workflow")
    print("=" * 72)

    ctx = _build_demo_context(os)
    if ctx is None:
        return
    event, traveler, non_traveler, twilio_ready = ctx

    print(f"\nScenario : {event.traveler_name} | {event.original_train} | +{event.delay_minutes} min")

    # Demo only drafts and asks approval for a single message — the client.
    recipient = next((r for r in non_traveler if r.role == "client"), None)
    if recipient is None:
        print("\nNo client recipient configured (DEMO_CLIENT_NUMBER missing).")
        return

    print(f"--- Draft for {recipient.name} ({recipient.role}) " + "-" * 30)
    try:
        draft = await drafter.draft_message_async(event, recipient)
        print(draft)
    except Exception as exc:
        print(f"[!] Draft failed: {type(exc).__name__}: {exc}")
        return

    if twilio_ready:
        print(f"\n  → Sending approval request to {traveler.name} ({traveler.whatsapp_number}) ...")
        try:
            msg_id = tools.send_for_approval(event, draft, recipient)
            print(f"  → Sent. message_id={msg_id}")
            print("  → Reply via WhatsApp: YES / NO / EDIT <text>")
        except Exception as exc:
            print(f"  [!] Twilio error: {type(exc).__name__}: {exc}")
    else:
        print(
            "\n  [Dry run] TWILIO_* not configured — "
            "approval request would be sent to the traveler."
        )

    print("\n--- Webhook server ---")
    print("Start the receiver for YES/NO/EDIT replies:")
    print("  uvicorn journey_autopilot.whatsapp_communicator.webhook:app --port 8000")
    print("Twilio forwards inbound messages to POST /whatsapp/reply.")


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

    await _demo_direct_notify()
    await _demo_approval_flow()


if __name__ == "__main__":
    asyncio.run(main())
