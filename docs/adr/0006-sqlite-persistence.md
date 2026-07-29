# ADR 0006 — SQLite for persistence

Status: Accepted

## Context
The system needs durable, cross-session data (profile, preferences, hard
constraints, channel prefs, imported trips, journey history) plus per-run state,
with a one-command setup (build spec §2/§7).

## Decision
Use **SQLite**, standard-library `sqlite3` only, in-process:

- `persistence/store.py` — users, profile (stored as a JSON blob to avoid
  migrations in a fast-moving prototype), and imported trips. Path configurable
  via `JA_DB_PATH`, defaulting to the package `data/` dir. The agent tools read
  the latest profile via `read_tools` without any FastAPI/UI dependency.
- Run state is owned by the ADK `SessionService` (see ADR 0001), configured at
  the runner in `ui/chat.py` — an `InMemoryRunner` today, so run state is not
  persisted at all. (A `persistence/checkpointer.py` stub once documented this
  mapping; it was removed as dead scaffolding, the mapping lives here instead.)

## Consequences
- No external database service to stand up; the one-command setup stays simple
  (a separate container for the DB is an OPEN option, not a requirement —
  build spec §12).
- JSON-blob profile trades schema rigor for prototype velocity; default fields
  are layered in on read so older rows don't miss newer fields.
- Single-user prototype: tools read "the latest" profile; multi-user needs a
  user/session context threaded through the agent stack (known limitation).
