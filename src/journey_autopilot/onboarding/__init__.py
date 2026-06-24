"""Onboarding logic — the functions behind the web app.

This package holds the *logic* of onboarding, not the UI:

- ``accounts`` — simulated DB accounts, booked trips, and Outlook events
  (the swap point for a real DB/Microsoft integration).

The SQLite store for users, the profile (JSON blob), and imported trips lives in
``journey_autopilot.persistence.store``.

The web front-end that drives this logic lives in ``journey_autopilot.ui``.
The only shared touchpoint with the agents is the persistence store, from which
``journey_autopilot.tools.read_tools`` reads the user profile — without any
FastAPI/UI dependency.
"""
