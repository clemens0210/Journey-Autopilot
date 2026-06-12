"""Startet die Onboarding-Web-App (FastAPI + DB-Navigator-Style-UI).

    python run_onboarding.py        # -> http://127.0.0.1:8000

Optional vorher den DB-Sidecar starten (``cd db_service && npm start``), dann
nutzt die Heimatbahnhof-Suche echte DB-Stationsdaten statt der Fallback-Liste.
"""

from __future__ import annotations

import os

import uvicorn

if __name__ == "__main__":
    host = os.getenv("ONBOARDING_HOST", "127.0.0.1")
    port = int(os.getenv("ONBOARDING_PORT", "8000"))
    print(f"Journey Autopilot Onboarding: http://{host}:{port}")
    uvicorn.run("onboarding.server:app", host=host, port=port, reload=False)
