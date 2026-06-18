"""Starts the onboarding web app (FastAPI + DB Navigator-style UI).

    python run_onboarding.py        # -> http://127.0.0.1:8000

Optionally start the DB sidecar first (``cd db_service && npm start``), then
the home station search uses real DB station data instead of the fallback list.
"""

from __future__ import annotations

import os

import uvicorn

if __name__ == "__main__":
    host = os.getenv("ONBOARDING_HOST", "127.0.0.1")
    port = int(os.getenv("ONBOARDING_PORT", "8000"))
    print(f"Journey Autopilot Onboarding: http://{host}:{port}")
    uvicorn.run("onboarding.server:app", host=host, port=port, reload=False)
