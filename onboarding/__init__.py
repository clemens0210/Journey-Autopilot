"""Onboarding & Profil — Web-App (FastAPI) + SQLite-Store.

Eigenständiges Paket neben ``journey_autopilot``: Die Onboarding-UI braucht kein
ADK, und die Agenten brauchen kein FastAPI. Gemeinsamer Berührungspunkt ist
ausschließlich ``onboarding.store`` (SQLite), aus dem ``journey_autopilot.tools``
das Nutzerprofil liest.
"""
