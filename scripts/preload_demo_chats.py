"""Warm the expensive first chat turn(s) before a demo.

Opening a trip chat runs a full orchestrator pass — live status, risk model,
reroute search, calendar check — which is the slowest thing in the demo. This
script runs those turns ahead of time against a *running* server, so during the
presentation you click a trip and the finished conversation is already there,
and can be continued: the ADK session created here stays live in the server
process, so the agent keeps its memory for the rest of that process's life.

Demo preparation:

    python scripts/check_outlook.py --login   # once: cache the MS login
    python scripts/reset_demo.py              # wipe profile/trips/complaints
    python run_onboarding.py                  # start the app, LEAVE IT RUNNING
    python scripts/preload_demo_chats.py      # warm the chats (this script)
    # then open http://127.0.0.1:8000 in a FRESH browser tab and demo

The order matters. Logging in here recreates the user and re-imports the trips,
but it does NOT complete onboarding — ``onboarding_completed`` stays False — so
the wizard still runs from the start during the demo. Do not run reset_demo.py
after this script: it would delete the complaint draft the preloaded chat
refers to. Restarting the server also discards the warm-up (the ADK sessions
die with the process); just run this script again.

By default the proactive WhatsApp notice is NOT sent, so the alert isn't spent
during setup — pass --notify if you want it fired now instead.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[1] / "src"
if _SRC.is_dir() and str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

import httpx  # noqa: E402

from journey_autopilot.onboarding.accounts import DEMO_TRIP_ID  # noqa: E402

DEFAULT_BASE_URL = "http://127.0.0.1:8000"
DEFAULT_EMAIL = "lucas.wild@example.com"
DEFAULT_PASSWORD = "demo123"

# The two chats worth the wait: the canonical Munich→Berlin trip (full
# monitoring → reroute → calendar → email flow) and yesterday's heavily delayed
# Frankfurt→Munich trip (drives the passenger-rights/complaints demo).
DEFAULT_TRIP_IDS = [DEMO_TRIP_ID, "DB-FRA-MUC"]

# A warm-up turn runs the whole agent graph; the LLM backend makes this slow.
PRELOAD_TIMEOUT_S = 600.0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--email", default=DEFAULT_EMAIL)
    parser.add_argument("--password", default=DEFAULT_PASSWORD)
    parser.add_argument(
        "--trip",
        action="append",
        dest="trips",
        metavar="TRIP_ID",
        help=f"Trip to warm; repeatable. Default: {', '.join(DEFAULT_TRIP_IDS)}",
    )
    parser.add_argument(
        "--all-trips",
        action="store_true",
        help="Warm every imported trip instead of the default two.",
    )
    parser.add_argument(
        "--notify",
        action="store_true",
        help="Send the proactive WhatsApp notice now (off by default, so the "
             "alert isn't spent before the demo starts).",
    )
    args = parser.parse_args()

    base = args.base_url.rstrip("/")
    trip_ids = [] if args.all_trips else (args.trips or DEFAULT_TRIP_IDS)

    with httpx.Client(base_url=base, timeout=PRELOAD_TIMEOUT_S) as client:
        try:
            login = client.post(
                "/api/auth/db-login",
                json={"email": args.email, "password": args.password},
            )
        except httpx.ConnectError:
            print(f"No server at {base} — start it with `python run_onboarding.py` first.")
            return 1
        if login.status_code != 200:
            print(f"Login failed ({login.status_code}): {login.text}")
            return 1
        data = login.json()
        token = data["token"]
        print(f"Logged in as {data['account']['display_name']} — {len(data['trips'])} trips imported.")

        targets = trip_ids or [t["trip_id"] for t in data["trips"]]
        print(f"Warming {len(targets)} chat(s): {', '.join(targets)}")
        print("This runs the full orchestrator per trip and takes a while…")

        response = client.post(
            "/api/demo/preload",
            headers={"Authorization": f"Bearer {token}"},
            json={"trip_ids": trip_ids or None, "notify": args.notify},
        )
        if response.status_code != 200:
            print(f"Preload failed ({response.status_code}): {response.text}")
            return 1
        result = response.json()

    for entry in result["preloaded"]:
        bits = [f"risk={entry['risk_band'] or 'n/a'}", f"{entry['reply_chars']} chars"]
        if entry["options"]:
            bits.append(f"{entry['options']} reroute option(s)")
        if entry["complaint_created"]:
            bits.append("complaint drafted")
        print(f"  ✓ {entry['trip_id']}: {', '.join(bits)}")
    for failure in result["failures"]:
        print(f"  ✗ {failure['trip_id']}: {failure['error']}")

    if not result["preloaded"]:
        print("Nothing was preloaded — the demo will run the first turn live.")
        return 1

    print(f"\n{result['total_cached']} chat(s) cached in the running server.")
    print("Open the app in a FRESH browser tab; onboarding still runs from the start.")
    print("Leave the server running — restarting it discards the warm-up.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
