"""Starts the web app (FastAPI + DB Navigator-style UI).

    python run_onboarding.py        # -> http://127.0.0.1:8000

Serves the onboarding wizard, the dashboard, and the per-trip chat that runs
the ReAct orchestrator (see ``journey_autopilot/ui``).

Optionally start the DB sidecar first (``cd db_service && npm start``), then
the home station search uses real DB station data instead of the fallback list.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import uvicorn

# Make the src/ layout importable when run directly without an editable install.
_SRC = Path(__file__).resolve().parent / "src"
if _SRC.is_dir() and str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

try:
    # Load .env so ONBOARDING_HOST/PORT, DB_API_URL and the agent's UNI_GPT_*
    # credentials are available (same as scenarios/happy_path.py).
    from dotenv import load_dotenv

    load_dotenv()
    load_dotenv("journey_autopilot/.env")
except ImportError:
    pass

if __name__ == "__main__":
    host = os.getenv("ONBOARDING_HOST", "127.0.0.1")
    port = int(os.getenv("ONBOARDING_PORT", "8000"))
    print(f"Journey Autopilot Web App: http://{host}:{port}")
    uvicorn.run("journey_autopilot.ui.server:app", host=host, port=port, reload=False)
