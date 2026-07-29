"""Session state shared by every router.

In-memory sessions: token -> user_id. Deliberately without persistence for the
single-user prototype; a restart simply means "log in again".

Kept in its own module because it is the one piece of state that genuinely
spans routers — ``auth`` mints tokens, every other router resolves them, and
``profile`` purges them on GDPR deletion.
"""

from __future__ import annotations

import secrets

from fastapi import HTTPException

_SESSIONS: dict[str, str] = {}


def create_session(user_id: str) -> str:
    """Mint a bearer token for a freshly authenticated user."""
    token = secrets.token_urlsafe(24)
    _SESSIONS[token] = user_id
    return token


def current_user_id(authorization: str | None) -> str:
    """Resolves the bearer token to a user_id, otherwise 401."""
    if authorization and authorization.startswith("Bearer "):
        token = authorization.removeprefix("Bearer ")
        user_id = _SESSIONS.get(token)
        if user_id:
            return user_id
    raise HTTPException(status_code=401, detail="You're not signed in.")


def drop_sessions(user_id: str) -> None:
    """Invalidate every token for this user (account deletion)."""
    for token, uid in list(_SESSIONS.items()):
        if uid == user_id:
            del _SESSIONS[token]
