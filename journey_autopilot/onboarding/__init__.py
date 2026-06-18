"""Onboarding & profile — web app (FastAPI) + SQLite store.

Standalone package alongside ``journey_autopilot``: the onboarding UI doesn't
need ADK, and the agents don't need FastAPI. The only shared touchpoint is
``onboarding.store`` (SQLite), from which ``journey_autopilot.tools`` reads
the user profile.
"""
