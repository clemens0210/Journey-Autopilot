"""Per-request identity and scratch space, available to nested agent tools.

Two things travel with a chat turn through ``contextvars``, so that nested
``AgentTool`` runners inherit them automatically:

1. **Identity** — the authenticated user and ADK session. Write tools validate
   persisted proposals against these instead of accepting an identity from the
   LLM as a tool argument.
2. **The turn workspace** — one dict per chat turn, collecting the structured
   results the web layer needs but cannot see. ADK's ``AgentTool`` runs each
   sub-agent in its own runner and forwards only its final *text* to the
   parent, so a nested tool's result never reaches the event stream
   ``ui.chat`` iterates. Everything that has to cross that gap goes in here:

   - ``trace`` — the specialists' own tool calls, for the chat trace.
   - ``whatsapp_sends`` — every outbound WhatsApp *outcome* this turn, delivered
     or not (see ``whatsapp_delivered`` for the subset that actually reached
     the traveler).
   - ``reroute`` — the Planner's discovery/finalize workspace.
   - ``rights`` — the settled passenger-rights result (concluded trips only).
   - ``rerouted_trip`` — the stored trip as the Executor rewrote it after a
     booked reroute, so the browser can show the new itinerary without a reload.

   One workspace per turn is also what keeps concurrent turns apart: the
   binding lives in the request's asyncio task, so two travelers can never see
   each other's reroute options or rights result.
"""

from __future__ import annotations

from contextvars import ContextVar, Token

current_user_id: ContextVar[str | None] = ContextVar("ja_user_id", default=None)
current_session_id: ContextVar[str | None] = ContextVar("ja_session_id", default=None)

# The traveler's verified WhatsApp number for this turn, taken from their
# profile by the web layer. The Orchestrator decides *whether* to message the
# traveler; it never gets to decide *who* — an LLM-supplied number would be an
# unverified recipient for a real outbound send.
current_notify_phone: ContextVar[str | None] = ContextVar("ja_notify_phone", default=None)

current_turn_workspace: ContextVar[dict | None] = ContextVar(
    "ja_turn_workspace", default=None
)


def new_turn_workspace() -> dict:
    """An empty workspace for one chat turn."""
    return {
        "trace": [],
        "whatsapp_sends": [],
        "reroute": None,
        "rights": None,
        "rerouted_trip": None,
    }


# Direct scenario/demo calls (scenarios/*.py, scripts/*.py) drive the agent
# through their own runner with no chat turn around it, so nothing binds a
# workspace. They share this one instead — correct for a single-shot script,
# and the reason ``turn_workspace()`` never returns None. A real chat turn
# cannot land here: ui.chat.chat_turn binds a fresh workspace unconditionally.
_UNBOUND_WORKSPACE: dict = new_turn_workspace()


def bind(
    user_id: str, session_id: str, notify_phone: str | None = None
) -> tuple[Token, Token, Token]:
    return (
        current_user_id.set(user_id),
        current_session_id.set(session_id),
        current_notify_phone.set(notify_phone or None),
    )


def reset(tokens: tuple[Token, Token, Token]) -> None:
    user_token, session_token, phone_token = tokens
    current_notify_phone.reset(phone_token)
    current_session_id.reset(session_token)
    current_user_id.reset(user_token)


def set_turn_workspace(workspace: dict) -> Token:
    return current_turn_workspace.set(workspace)


def reset_turn_workspace(token: Token) -> None:
    current_turn_workspace.reset(token)


def turn_workspace() -> dict:
    """The running turn's workspace, or the shared unbound-call fallback."""
    workspace = current_turn_workspace.get()
    return _UNBOUND_WORKSPACE if workspace is None else workspace


def record_trace(entry: dict) -> None:
    """Append one trace entry (a specialist's tool call) to this turn."""
    turn_workspace()["trace"].append(entry)


def record_whatsapp_send(entry: dict) -> None:
    """Append one outbound WhatsApp *outcome* to this turn.

    Every attempt is recorded, including the ones that delivered nothing (no
    verified number, Twilio error) — the web layer reads the last entry back to
    tell the traveler why no notice arrived. Only ``whatsapp_delivered()``
    entries count as "the traveler has been informed".
    """
    turn_workspace()["whatsapp_sends"].append(entry)


def whatsapp_sends() -> list[dict]:
    """Every outbound WhatsApp outcome this turn, delivered or not."""
    return turn_workspace()["whatsapp_sends"]


def whatsapp_delivered() -> list[dict]:
    """The notices that actually left this turn — the one-notice-per-turn guard.

    Deliberately narrower than ``whatsapp_sends()``: a failed send buzzed
    nobody's phone, so it must not arm the guard and block the retry.
    ``demo`` counts as delivered — Twilio is simply not configured, and the
    message was logged in its place, so resending would be just as pointless.
    """
    return [
        entry
        for entry in whatsapp_sends()
        if entry.get("sent") or entry.get("demo")
    ]
