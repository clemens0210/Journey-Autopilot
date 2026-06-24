"""Run-state checkpointing.

In the LangGraph reference design (build spec §2/§7) this is the SQLite
checkpointer that persists the graph state. In this ADK build, run state is owned
by the ADK ``SessionService``: the demos/UI use ``InMemoryRunner`` (volatile,
per run), and a ``DatabaseSessionService`` can persist sessions to SQLite when
durable run state is wanted.

The cross-session *app* data (profile, constraints, channel prefs, journey
history) lives separately in ``persistence/store.py`` — that is the durable part
today.

STATUS: scaffold. Documents the mapping; wiring a DatabaseSessionService is an
OPEN option (build spec §12), not a starting requirement.
"""

from __future__ import annotations

# TODO: expose a configured ADK SessionService (InMemory by default,
# DatabaseSessionService -> data/journey_autopilot.db when JA_PERSIST_SESSIONS).
