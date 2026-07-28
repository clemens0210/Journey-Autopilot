"""Per-request identity available to nested agent tools via ``contextvars``.

The web layer binds the authenticated user and ADK session while a chat turn is
running. Nested AgentTool runners inherit this asynchronous context, allowing
write tools to validate persisted proposals without accepting identity from the
LLM as a tool argument.
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

# Outbound WhatsApp sends performed during this turn, in order. ADK's AgentTool
# hides a nested tool's result from the parent event stream, and the browser
# needs the delivery outcome to toast it — so the send tool records here and
# ui/chat.py reads the list once the turn is done.
current_whatsapp_sink: ContextVar[list | None] = ContextVar("ja_whatsapp_sink", default=None)

# Trace sink for the running chat turn. The Orchestrator's tool calls surface in
# the main ADK event stream, but each sub-agent runs inside its own AgentTool
# runner whose events never reach the web layer (see ui/chat.py). Sub-agents
# push their own tool calls here — inheriting this context the same way write
# tools inherit the user/session ids above — so the chat trace can show what the
# specialists actually did, nested under the Orchestrator's call.
current_trace_sink: ContextVar[list | None] = ContextVar("ja_trace_sink", default=None)


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


def set_whatsapp_sink(sink: list) -> Token:
    return current_whatsapp_sink.set(sink)


def reset_whatsapp_sink(token: Token) -> None:
    current_whatsapp_sink.reset(token)


def record_whatsapp_send(entry: dict) -> None:
    """Append one outbound WhatsApp result to the active turn's sink, if bound."""
    sink = current_whatsapp_sink.get()
    if sink is not None:
        sink.append(entry)


def set_trace_sink(sink: list) -> Token:
    return current_trace_sink.set(sink)


def reset_trace_sink(token: Token) -> None:
    current_trace_sink.reset(token)


def record_trace(entry: dict) -> None:
    """Append one trace entry to the active turn's sink, if any is bound."""
    sink = current_trace_sink.get()
    if sink is not None:
        sink.append(entry)
