"""Persistence — SQLite app store + run-state checkpointing.

- ``store``        — profile, constraints, channel prefs, and journey history
                     across sessions (the agents read the profile from here).
- ``checkpointer`` — run-state persistence; in the ADK build this is the
                     ``SessionService`` (stub documents the mapping).
"""
