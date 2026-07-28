"""Web app server — FastAPI app assembly.

Start:
    python run_onboarding.py            # http://127.0.0.1:8000
    # or: uvicorn journey_autopilot.ui.server:app --reload

This module does four things and nothing else: configure logging, warm the
rights RAG, include the routers from ``ui/routes/``, and serve the static UI.
The endpoints themselves live one per theme in ``ui/routes/`` — see that
package's docstring for the map.

This is the presentation layer. The onboarding *logic* (simulated DB
accounts/trips and the SQLite profile store) lives in
``journey_autopilot.onboarding``. The chat endpoint (``/api/chat``) runs the
same ReAct orchestrator as ``scenarios/happy_path.py``.

What's simulated here (and why) is documented in the Context Record: DB
(Deutsche Bahn) offers no official API for account login / ticket import,
and Microsoft OAuth and SMS sending require registered apps or a gateway
contract. The flows are therefore built with real UX but simulated backends —
the API contracts match what a real integration would need to deliver.

Live data: the home station search (`/api/stations`) uses the db_service
sidecar (real DB station data) and falls back to a static list of major
stations without it.
"""

from __future__ import annotations

import logging
import sys
import threading
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .routes import ALL_ROUTERS

# Logger for the Outlook device-code flow — timestamped lines on stderr, visible
# in the terminal where ``python run_onboarding.py`` runs. The flow itself only
# calls getLogger("journey_autopilot.outlook"); attaching the handler is the
# app's job, so importing the integration configures nothing.
_outlog = logging.getLogger("journey_autopilot.outlook")
if not _outlog.handlers:
    _h = logging.StreamHandler(sys.stderr)
    _h.setFormatter(logging.Formatter("%(asctime)s [outlook] %(levelname)s %(message)s"))
    _outlog.addHandler(_h)
    _outlog.setLevel(logging.INFO)
_outlog.propagate = False

# Surface this package's INFO logs in the terminal — e.g. the HIGH-risk
# disruption alert emitted by integrations/whatsapp/ and ui/chat.py. uvicorn
# only configures its own loggers, so without this our INFO lines are swallowed.
# (Mirrors integrations/whatsapp/webhook.py, which runs under uvicorn too.)
logging.getLogger("journey_autopilot").setLevel(logging.INFO)
if not logging.getLogger().handlers:
    logging.basicConfig(level=logging.INFO)

app = FastAPI(title="Journey Autopilot — Web App", version="0.1.0")

for _router in ALL_ROUTERS:
    app.include_router(_router)


@app.on_event("startup")
def _warm_rights_rag() -> None:
    """Pre-load the passenger-rights embedding model in the background.

    The first ``get_passenger_rights`` tool call otherwise blocks the first
    chat answer while the ~1 GB sentence-transformers model loads. Warming it
    here — while the user is still in onboarding/dashboard — hides that
    latency; on failure the chat simply falls back to lazy loading.
    """

    def _load() -> None:
        try:
            from ..integrations.rights_rag.rag_store import FahrgastrechteRAG
            from ..tools import read_tools

            read_tools.get_passenger_rights._rag = FahrgastrechteRAG()
        except Exception:
            pass

    threading.Thread(target=_load, name="rights-rag-warmup", daemon=True).start()


# --- Static UI ----------------------------------------------------------------------------

_STATIC = Path(__file__).resolve().parent / "static"


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    return FileResponse(_STATIC / "index.html")


app.mount("/static", StaticFiles(directory=_STATIC), name="static")
