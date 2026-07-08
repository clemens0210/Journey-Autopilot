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

from typing import Any

APP_NAME = "journey_autopilot"
USER_ID = "ui-user"

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


def _seed_prompt(trip: dict | None, message: str) -> str:
    """First message of a chat: prepend the selected trip as context.

    The orchestrator expects a trip_id (and route/date) to call the monitoring
    agent — exactly what ``scenarios/happy_path.py`` passes in its hard-coded prompt. Here
    the values come from the trip the user clicked.
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
    session_id: str | None, message: str, trip: dict | None = None
) -> dict:
    """Run one chat turn through the orchestrator.

    Args:
        session_id: ADK session id from a previous turn, or ``None`` to start a
            new conversation.
        message: The user's chat message.
        trip: The selected trip (added as context on the first turn only).

    Returns:
        ``{"session_id", "reply", "trace"}`` — the (new or reused) session id,
        the orchestrator's final answer, and the agent/tool trace.
    """
    from google.genai import types

    runner = _get_runner()

    if not session_id:
        session = await runner.session_service.create_session(
            app_name=APP_NAME, user_id=USER_ID
        )
        session_id = session.id
        text = _seed_prompt(trip, message)
    else:
        # The session already carries the trip context from the first turn.
        text = message

    new_message = types.Content(role="user", parts=[types.Part(text=text)])
    trace: list[dict] = []
    reply = ""

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

    async for event in runner.run_async(
        user_id=USER_ID, session_id=session_id, new_message=new_message
    ):
        if event.is_final_response() and event.content and event.content.parts:
            reply = "".join(
                p.text for p in event.content.parts if getattr(p, "text", None)
            )
            continue
        trace.extend(_describe(event))

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
        "reply": reply or "(no response)",
        "trace": trace,
        "options": options,
        "options_source": options_source,
    }
