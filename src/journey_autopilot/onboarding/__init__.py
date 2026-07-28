"""Onboarding logic — the functions behind the web app.

This package holds the *logic* of onboarding, not the UI:

- ``complaints`` — passenger-rights claim drafts prepared for the user to submit.

The simulated DB account and its booked trips/Outlook events used to live here
as ``accounts``; they are half of the demo dataset (and share its clock), so
they now live in ``journey_autopilot.demo.accounts``.

The SQLite store for users, the profile (JSON blob), and imported trips lives in
``journey_autopilot.persistence.store``.

The web front-end that drives this logic lives in ``journey_autopilot.ui``.
The only shared touchpoint with the agents is the persistence store, from which
``journey_autopilot.tools.read_tools`` reads the user profile — without any
FastAPI/UI dependency.
"""
