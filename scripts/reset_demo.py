"""Reset the web-app state so the onboarding wizard runs from scratch.

Deletes every user from the SQLite store — profiles, imported trips,
complaints, and reroute proposals go with them (ON DELETE CASCADE). The
Outlook token cache and authentication record under ``.IdentityService`` are
deliberately NOT touched: a Microsoft login made beforehand (e.g. via
``python scripts/check_outlook.py --login`` or an earlier onboarding run)
stays valid, so the wizard's Outlook step connects instantly without a
device-code round trip.

Typical demo preparation:

    python scripts/check_outlook.py --login   # once: cache the MS login
    python scripts/reset_demo.py              # wipe profile/trips/complaints
    python run_onboarding.py                  # restart the web app, leave running
    python scripts/preload_demo_chats.py      # optional: warm the slow chats
    # then open http://127.0.0.1:8000 in a FRESH browser tab

The server restart clears the in-memory sessions; the fresh tab clears the
sessionStorage token and any persisted chat. Do NOT press "Disconnect" on the
Outlook connection during rehearsal — that clears the token cache and the next
connect needs the full device-code flow again.

Run this BEFORE preload_demo_chats.py, never after: the preloaded conversation
references a complaint draft that this script would delete.
"""

from __future__ import annotations

import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[1] / "src"
if _SRC.is_dir() and str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

try:
    from dotenv import load_dotenv

    load_dotenv()  # honor JA_DB_PATH from .env, like run_onboarding.py
except ImportError:
    pass

from journey_autopilot.persistence import store  # noqa: E402


def main() -> int:
    if not store.DB_PATH.exists():
        print(f"Nothing to reset — no database at {store.DB_PATH}")
        return 0

    with store._connect() as conn:
        users = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        trips = conn.execute("SELECT COUNT(*) FROM trips").fetchone()[0]
        complaints = conn.execute("SELECT COUNT(*) FROM complaints").fetchone()[0]
        # Explicit per-table deletes (not just the users cascade) so a DB file
        # created with an older schema without ON DELETE CASCADE ends up clean too.
        for table in ("reroute_proposals", "complaints", "trips", "profiles", "users"):
            conn.execute(f"DELETE FROM {table}")

    print(f"Reset {store.DB_PATH}")
    print(f"  removed {users} user(s), {trips} trip(s), {complaints} complaint(s)")
    print("Outlook token cache untouched — a cached Microsoft login stays valid.")
    print("Now restart run_onboarding.py and open the app in a fresh browser tab.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
