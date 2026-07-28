"""Persistence — the SQLite app store.

- ``store`` — profile, constraints, channel prefs, trips, reroute proposals,
  and complaints across sessions (the agents read the profile from here).

Run state is deliberately NOT persisted here: ADK owns it via its
``SessionService``, and the app runs an ``InMemoryRunner`` (see ``ui/chat.py``),
so a server restart simply starts the conversations over. Swapping in a
``DatabaseSessionService`` would make them durable; that is a one-line change at
the runner, not a layer of its own.
"""
