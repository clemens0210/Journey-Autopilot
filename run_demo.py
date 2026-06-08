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
from journey_autopilot.mock_data import DEMO_TRIP

APP_NAME = "journey_autopilot"
USER_ID = "lucas"

# Anfrage, mit der die Reise an den Orchestrator übergeben wird.
PROMPT = (
    f"Bitte überwache meine Reise mit der trip_id {DEMO_TRIP['trip_id']} "
    f"von {DEMO_TRIP['origin']} nach {DEMO_TRIP['destination']} am 2026-06-10 "
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


if __name__ == "__main__":
    asyncio.run(main())
