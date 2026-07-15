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


def bind(user_id: str, session_id: str) -> tuple[Token, Token]:
    return current_user_id.set(user_id), current_session_id.set(session_id)


def reset(tokens: tuple[Token, Token]) -> None:
    user_token, session_token = tokens
    current_session_id.reset(session_token)
    current_user_id.reset(user_token)
