"""End-to-end demo of the Orchestrator in the terminal.

Runs a single request against the ReAct Orchestrator and streams the
events along, so the collaboration of Monitoring and Planner Agents
becomes visible (which agent is called when, which tools run).

Usage:
    python run_demo.py

Prerequisite: a configured Uni-GPT backend in .env (UNI_GPT_*; see
README). Alternatively, the ADK Dev UI also works:  adk web   or
adk run journey_autopilot
"""

from __future__ import annotations

import asyncio

from google.adk.runners import InMemoryRunner
from google.genai import types

try:
    from dotenv import load_dotenv

    # Load .env from the project root and from the agent package.
    load_dotenv()
    load_dotenv("journey_autopilot/.env")
except ImportError:
    # python-dotenv is an ADK dependency; if absent, set .env manually.
    pass

from journey_autopilot.agent import root_agent
from journey_autopilot.mock_data import DEMO_TRIP

APP_NAME = "journey_autopilot"
USER_ID = "lucas"

# Request that passes the trip to the Orchestrator.
PROMPT = (
    f"Please monitor my trip with trip_id {DEMO_TRIP['trip_id']} "
    f"from {DEMO_TRIP['origin']} to {DEMO_TRIP['destination']} on 2026-06-19 "
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
            _describe_event(event)
            if event.is_final_response() and event.content and event.content.parts:
                final_text = "".join(
                    p.text for p in event.content.parts if getattr(p, "text", None)
                )
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


if __name__ == "__main__":
    asyncio.run(main())
