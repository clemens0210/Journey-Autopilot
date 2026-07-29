"""End-to-end demo of the Orchestrator in the terminal.

Runs a single request against the ReAct Orchestrator and streams the
events along, so the collaboration of Monitoring and Planner Agents
becomes visible (which agent is called when, which tools run).

Usage:
    python scenarios/happy_path.py

Prerequisite: a configured Uni-GPT backend in .env (UNI_GPT_*; see
README). Alternatively, the ADK Dev UI also works:  adk web   or
adk run src/journey_autopilot
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

# Make the src/ layout importable when run directly (python scenarios/happy_path.py)
# without an editable install. With `pip install -e .` this is a no-op.
_SRC = Path(__file__).resolve().parents[1] / "src"
if _SRC.is_dir() and str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

try:
    from dotenv import load_dotenv

    # Load .env before importing ADK/LiteLLM so logging settings and credentials
    # are visible during model initialization.
    load_dotenv()
    load_dotenv("journey_autopilot/.env")
except ImportError:
    # python-dotenv is an ADK dependency; if absent, set .env manually.
    pass

# Keep LiteLLM's background logging worker from printing best-effort telemetry
# timeouts into the terminal demo. Set LITELLM_LOG explicitly to see them again.
os.environ.setdefault("LITELLM_LOG", "CRITICAL")

# Windows certificate store sometimes contains malformed certs that cause
# ssl.SSLError: [ASN1: NOT_ENOUGH_DATA] when aiohttp calls ssl.create_default_context()
# at module-load time. Patching load_default_certs to swallow that error lets the
# rest of the store (and certifi's bundle) still work fine.
import ssl as _ssl

if sys.platform.startswith("win"):
    _orig_load_default_certs = _ssl.SSLContext.load_default_certs

    def _patched_load_default_certs(self, purpose=_ssl.Purpose.SERVER_AUTH):
        try:
            _orig_load_default_certs(self, purpose)
        except _ssl.SSLError as exc:
            # Only swallow the known Windows cert-store parsing issue.
            if "NOT_ENOUGH_DATA" not in str(exc):
                raise

    _ssl.SSLContext.load_default_certs = _patched_load_default_certs

from google.adk.runners import InMemoryRunner
from google.genai import types

from journey_autopilot.agent import root_agent
from journey_autopilot.demo.mock_data import DEMO_TRIP, DEMO_EVENT_FIELDS
from journey_autopilot.integrations.whatsapp import DisruptionEvent, Recipient
from journey_autopilot.agents import communicator
from journey_autopilot.integrations import whatsapp

APP_NAME = "journey_autopilot"
USER_ID = "lucas"

# Request that passes the trip to the Orchestrator.
PROMPT = (
    f"Please monitor my trip with trip_id {DEMO_TRIP['trip_id']} "
    f"from {DEMO_TRIP['origin']} to {DEMO_TRIP['destination']} on {DEMO_TRIP['planned_departure'][:10]} "
    f"(train {DEMO_TRIP['train']}, planned departure {DEMO_TRIP['planned_departure']}, "
    f"planned arrival {DEMO_TRIP['planned_arrival']}) "
    "and tell me if I need to do anything."
)


def _describe_event(event) -> None:
    """Prints tool calls, tool results, and texts per event in a readable way."""
    author = getattr(event, "author", "?")
    content = getattr(event, "content", None)
    if content is None or not getattr(content, "parts", None):
        return

    for part in content.parts:
        call = getattr(part, "function_call", None)
        response = getattr(part, "function_response", None)
        text = getattr(part, "text", None)

        if call is not None:
            print(f"  [{author}] -> calls: {call.name}({dict(call.args or {})})")
        elif response is not None:
            print(f"  [{author}] <- result of: {response.name}")
        elif text and text.strip():
            print(f"  [{author}] {text.strip()}")


def _build_demo_context() -> tuple | None:
    """Shared setup logic for both WhatsApp demos.

    Returns (event, traveler, non_traveler, twilio_ready) or None if
    DEMO_TRAVELER_NUMBER is missing.
    """
    traveler_number = os.getenv("DEMO_TRAVELER_NUMBER", "")
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
    if client_number := os.getenv("DEMO_CLIENT_NUMBER", ""):
        recipients.append(Recipient(name="Frau Dr. Bauer", role="client", whatsapp_number=client_number))
    if colleague_number := os.getenv("DEMO_COLLEAGUE_NUMBER", ""):
        recipients.append(Recipient(name="Thomas Müller", role="colleague", whatsapp_number=colleague_number))
    if private_number := os.getenv("DEMO_PRIVATE_NUMBER", ""):
        recipients.append(Recipient(name="Anna Wild", role="private", whatsapp_number=private_number))

    event = DisruptionEvent(**DEMO_EVENT_FIELDS, recipients=recipients)
    traveler = next(r for r in recipients if r.role == "traveler")
    non_traveler = [r for r in recipients if r.role != "traveler"]
    twilio_ready = bool(
        os.getenv("TWILIO_ACCOUNT_SID")
        and os.getenv("TWILIO_AUTH_TOKEN")
        and os.getenv("TWILIO_WHATSAPP_FROM")
    )
    return event, traveler, non_traveler, twilio_ready


async def _demo_direct_notify() -> None:
    """Demo 1 — Direct message: disruption notice sent straight to the traveler, no communicator."""
    print("\n" + "=" * 72)
    print("WhatsApp Demo 1 — Direct message to traveler (no drafter)")
    print("=" * 72)

    ctx = _build_demo_context()
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

    print(f"\nMessage to {traveler.name} ({traveler.whatsapp_number}):\n")
    print(notice)

    if twilio_ready:
        print(f"\n  → Sending directly to {traveler.name} ({traveler.whatsapp_number}) ...")
        try:
            whatsapp.dispatch_message(notice, traveler)
            print("  → Sent.")
        except Exception as exc:
            print(f"  [!] Twilio error: {type(exc).__name__}: {exc}")
    else:
        print("\n  [Dry run] TWILIO_* not configured — message would be sent directly.")


async def _demo_approval_flow() -> None:
    """Demo 2 — Approval workflow: drafter drafts, traveler approves via WhatsApp."""
    print("\n" + "=" * 72)
    print("WhatsApp Demo 2 — Drafter + approval workflow")
    print("=" * 72)

    ctx = _build_demo_context()
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
        draft = await communicator.draft_message_async(event, recipient)
        print(draft)
    except Exception as exc:
        print(f"[!] Draft failed: {type(exc).__name__}: {exc}")
        return

    if twilio_ready:
        print(f"\n  → Sending approval request to {traveler.name} ({traveler.whatsapp_number}) ...")
        try:
            msg_id = whatsapp.send_for_approval(event, draft, recipient)
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
    print("  uvicorn journey_autopilot.integrations.whatsapp.webhook:app --port 8000")
    print("Twilio forwards inbound messages to POST /whatsapp/reply.")


async def main() -> None:
    runner = InMemoryRunner(agent=root_agent, app_name=APP_NAME)
    session = await runner.session_service.create_session(
        app_name=APP_NAME, user_id=USER_ID
    )

    print("=" * 72)
    print("Journey Autopilot — Demo Run (Orchestrator: ReAct)")
    print("=" * 72)
    print(f"User: {PROMPT}\n")
    print("--- Agent/Tool Trace ------------------------------------------------")

    message = types.Content(role="user", parts=[types.Part(text=PROMPT)])
    final_text = ""
    try:
        async for event in runner.run_async(
            user_id=USER_ID, session_id=session.id, new_message=message
        ):
            if event.is_final_response() and event.content and event.content.parts:
                final_text = "".join(
                    p.text for p in event.content.parts if getattr(p, "text", None)
                )
                continue
            _describe_event(event)
    except Exception as exc:
        # Falls through here e.g. for LiteLLM/Uni-GPT errors: wrong endpoint,
        # key or model name each have their own exception types.
        print("\n[!] Run aborted.")
        print(f"    {type(exc).__name__}: {exc}")
        print("    Check UNI_GPT_BASE_URL (incl. /v1), UNI_GPT_API_KEY and UNI_GPT_MODEL")
        print("    in .env — and whether google-adk[extensions] is installed")
        print("    (pip install -r requirements.txt).")
        return

    print("\n--- Response to User ------------------------------------------------")
    print(final_text or "(no text response)")

    await _demo_direct_notify()
    await _demo_approval_flow()


if __name__ == "__main__":
    asyncio.run(main())
