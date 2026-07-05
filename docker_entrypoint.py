"""Container entrypoint — prepare runtime data, then start the web app.

Runs on every container start (see Dockerfile CMD):

1. If the passenger-rights Chroma index is missing or empty — the first start
   on a fresh /data volume — crawl bahn.de and build it (scripts/run_crawler).
   The embedding model is baked into the image, so this only needs network
   access to bahn.de. If the crawl fails, the app starts anyway:
   ``get_passenger_rights`` then reports the knowledge base as unavailable
   while the deterministic compensation logic keeps working.
2. Replace this process with run_onboarding.py (uvicorn web app).

Not used for local development — there you run ``python run_onboarding.py``
and ``python scripts/run_crawler.py`` directly.
"""

from __future__ import annotations

import os
import subprocess
import sys


def _rights_index_count() -> int:
    """Number of chunks in the 'fahrgastrechte' collection (0 = needs a build)."""
    try:
        import chromadb

        from journey_autopilot.integrations.rights_rag.rag_store import CHROMA_PATH

        client = chromadb.PersistentClient(path=str(CHROMA_PATH))
        return client.get_collection("fahrgastrechte").count()
    except Exception:
        return 0


def main() -> None:
    if _rights_index_count() == 0:
        print(
            "[entrypoint] Passenger-rights index empty — building it from bahn.de ...",
            flush=True,
        )
        result = subprocess.run([sys.executable, "scripts/run_crawler.py"])
        if result.returncode != 0:
            print(
                "[entrypoint] Crawl failed — starting without the rights index.",
                flush=True,
            )

    os.execv(sys.executable, [sys.executable, "run_onboarding.py"])


if __name__ == "__main__":
    main()
