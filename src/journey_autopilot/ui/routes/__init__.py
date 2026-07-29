"""HTTP routers, one module per theme.

Split out of a single 1000-line ``ui/server.py`` so the file that assembles the
app no longer also implements it. Each module owns its own request models and
whatever pending state its flow needs; ``deps`` holds the only state that
genuinely spans them (the session table).

- ``auth``     — DB-account login and the /api/me bootstrap.
- ``trips``    — monitored trips, one trip's itinerary, and their complaints.
- ``booking``  — station lookup, live journey search, adding a connection.
- ``connect``  — phone verification and the Outlook connection.
- ``profile``  — profile read/patch, onboarding completion, GDPR delete.
- ``chat``     — the orchestrator chat turn and the demo preload.

``ALL_ROUTERS`` is what ``server.py`` includes; order is irrelevant because no
two routers claim the same method+path.
"""

from __future__ import annotations

from fastapi import APIRouter

from . import auth, booking, chat, connect, profile, trips

ALL_ROUTERS: list[APIRouter] = [
    auth.router,
    trips.router,
    booking.router,
    connect.router,
    profile.router,
    chat.router,
]

__all__ = ["ALL_ROUTERS"]
