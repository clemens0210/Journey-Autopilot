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

# Trace sink for the running chat turn. The Orchestrator's tool calls surface in
# the main ADK event stream, but each sub-agent runs inside its own AgentTool
# runner whose events never reach the web layer (see ui/chat.py). Sub-agents
# push their own tool calls here — inheriting this context the same way write
# tools inherit the user/session ids above — so the chat trace can show what the
# specialists actually did, nested under the Orchestrator's call.
current_trace_sink: ContextVar[list | None] = ContextVar("ja_trace_sink", default=None)


def bind(user_id: str, session_id: str) -> tuple[Token, Token]:
    return current_user_id.set(user_id), current_session_id.set(session_id)


def reset(tokens: tuple[Token, Token]) -> None:
    user_token, session_token = tokens
    current_session_id.reset(session_token)
    current_user_id.reset(user_token)


def set_trace_sink(sink: list) -> Token:
    return current_trace_sink.set(sink)


def reset_trace_sink(token: Token) -> None:
    current_trace_sink.reset(token)


def record_trace(entry: dict) -> None:
    """Append one trace entry to the active turn's sink, if any is bound."""
    sink = current_trace_sink.get()
    if sink is not None:
        sink.append(entry)
