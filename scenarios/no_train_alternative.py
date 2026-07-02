"""Demo scenario: full track blockage — no viable train alternative.

Runs the Orchestrator against the ``no_train_alternative`` fixture, which has a
full overhead-wire failure near Erfurt. Every train reroute misses the 14:00
hard deadline, so the Planner widens its search to the DB ecosystem and proposes
Flinkster (car sharing), Call-a-Bike, and partner hotels.

Usage:
    JA_FIXTURES=no_train_alternative python scenarios/no_train_alternative.py

Prerequisite: a configured Uni-GPT backend in .env (UNI_GPT_*; see README).
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[1] / "src"
if _SRC.is_dir() and str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

# Must be set before mock_data is imported (mock_data reads it at module load).
os.environ.setdefault("JA_FIXTURES", "no_train_alternative")

try:
    from dotenv import load_dotenv

    load_dotenv()
    load_dotenv("journey_autopilot/.env")
except ImportError:
    pass

os.environ.setdefault("LITELLM_LOG", "CRITICAL")

# Windows cert-store patch — see scenarios/happy_path.py for explanation.
import ssl as _ssl

if sys.platform.startswith("win"):
    _orig_load_default_certs = _ssl.SSLContext.load_default_certs

    def _patched_load_default_certs(self, purpose=_ssl.Purpose.SERVER_AUTH):
        try:
            _orig_load_default_certs(self, purpose)
        except _ssl.SSLError as exc:
            if "NOT_ENOUGH_DATA" not in str(exc):
                raise

    _ssl.SSLContext.load_default_certs = _patched_load_default_certs

from google.adk.runners import InMemoryRunner
from google.genai import types

from journey_autopilot.agent import root_agent
from journey_autopilot.mock_data import DEMO_TRIP

APP_NAME = "journey_autopilot"
USER_ID = "lucas"

PROMPT = (
    f"Please monitor my trip with trip_id {DEMO_TRIP['trip_id']} "
    f"from {DEMO_TRIP['origin']} to {DEMO_TRIP['destination']} on 2026-06-19 "
    f"(train {DEMO_TRIP['train']}, planned departure {DEMO_TRIP['planned_departure']}, "
    f"planned arrival {DEMO_TRIP['planned_arrival']}) "
    "and tell me if I need to do anything."
)


def _describe_event(event) -> None:
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


async def main() -> None:
    runner = InMemoryRunner(agent=root_agent, app_name=APP_NAME)
    session = await runner.session_service.create_session(
        app_name=APP_NAME, user_id=USER_ID
    )

    print("=" * 72)
    print("Journey Autopilot — No-Train-Alternative Scenario")
    print("Fixture: no_train_alternative (overhead-wire failure near Erfurt)")
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
        print("\n[!] Run aborted.")
        print(f"    {type(exc).__name__}: {exc}")
        print("    Check UNI_GPT_BASE_URL, UNI_GPT_API_KEY, UNI_GPT_MODEL in .env")
        print("    and that google-adk[extensions] is installed (pip install -r requirements.txt).")
        return

    print("\n--- Response to User ------------------------------------------------")
    print(final_text or "(no text response)")
    print("\nExpected: Planner reports all train options miss the 14:00 deadline,")
    print("then calls find_mobility_alternatives + find_partner_hotels and lists")
    print("C# (Flinkster), B# (Call-a-Bike), and H# (hotel) options.")


if __name__ == "__main__":
    asyncio.run(main())
