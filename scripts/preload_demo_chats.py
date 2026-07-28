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

Before warming, this script also connects Outlook (calendar + mail), reusing
the cached Microsoft login so the connect is silent — no device-code round
trip. That is what makes the warm-up run the *real* calendar check and
notice-email flow instead of the mock fallback (the agent only reads the live
calendar when ``profile.connections.outlook`` is set). This pre-connection is
invisible in the wizard: the Outlook step still starts from the "Sign in with
Microsoft" button and only shows "Connected" + the detected events once the
presenter runs the sign-in there (which reuses the cached login instantly).
Pass --no-connect-outlook to skip this and warm against the mock calendar.

By default the proactive WhatsApp notice is NOT sent, so the alert isn't spent
during setup — pass --notify if you want it fired now instead.
"""

from __future__ import annotations

import argparse
import sys
import time
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

from journey_autopilot.demo.accounts import DEMO_TRIP_ID  # noqa: E402

DEFAULT_BASE_URL = "http://127.0.0.1:8000"
DEFAULT_EMAIL = "lucas.wild@example.com"
DEFAULT_PASSWORD = "demo123"

# The two chats worth the wait: the canonical Munich→Berlin trip (full
# monitoring → reroute → calendar → email flow) and yesterday's heavily delayed
# Frankfurt→Munich trip (drives the passenger-rights/complaints demo).
DEFAULT_TRIP_IDS = [DEMO_TRIP_ID, "DB-FRA-MUC"]

# A warm-up turn runs the whole agent graph; the LLM backend makes this slow.
PRELOAD_TIMEOUT_S = 600.0

# The cached Microsoft login makes the connect instant, but poll a little in
# case the server is momentarily busy identifying the signed-in account.
OUTLOOK_CONNECT_TIMEOUT_S = 60.0


def connect_outlook(client: httpx.Client, token: str) -> None:
    """Connect Outlook (calendar + mail) on the server before warming chats.

    The agent only reads the *live* calendar (and drafts the notice email
    against the real organizer) when ``profile.connections.outlook`` is set —
    otherwise it falls back to the mock calendar. Connecting here therefore
    makes the warm-up exercise the same Graph calendar/Mail.Send path as the
    live demo. Relies on a cached Microsoft login (``scripts/check_outlook.py
    --login`` or an earlier onboarding run) so the connect completes silently.
    """
    headers = {"Authorization": f"Bearer {token}"}
    try:
        start = client.post("/api/connect/outlook/start", headers=headers)
    except httpx.HTTPError as exc:
        print(f"  ! Outlook connect skipped (request failed: {exc}).")
        return
    if start.status_code != 200:
        print(f"  ! Outlook connect skipped ({start.status_code}): {start.text}")
        return
    mode = start.json().get("mode")

    if mode == "simulated":
        # No Entra app configured — grant the simulated consent so the flag is
        # set and the warm-up still runs with Outlook "on" (mock calendar).
        resp = client.post(
            "/api/connect/outlook", headers=headers, json={"consent": True}
        )
        if resp.status_code == 200:
            print("  ✓ Outlook connected (simulated — no Entra app configured).")
        else:
            print(f"  ! Outlook connect failed ({resp.status_code}): {resp.text}")
        return

    if mode != "cached":
        # No cached login to reuse: /start began a real device-code flow that
        # needs a human at microsoft.com, which defeats unattended preloading.
        print("  ! No cached Microsoft login — Outlook needs an interactive "
              "sign-in. Run `python scripts/check_outlook.py --login` first, "
              "then re-run this script (or pass --no-connect-outlook).")
        return

    # Cached login reused — poll /status until the connection completes.
    deadline = time.monotonic() + OUTLOOK_CONNECT_TIMEOUT_S
    while time.monotonic() < deadline:
        status = client.get("/api/connect/outlook/status", headers=headers)
        if status.status_code != 200:
            print(f"  ! Outlook status failed ({status.status_code}): {status.text}")
            return
        data = status.json()
        state = data.get("status")
        if state == "complete":
            account = (data.get("account") or {}).get("email") or "connected account"
            events = len(data.get("events") or [])
            print(f"  ✓ Outlook connected as {account} — {events} calendar event(s) visible.")
            return
        if state in ("error", "expired", "none"):
            print(f"  ! Outlook connect did not complete (status={state}"
                  + (f": {data['error']}" if data.get("error") else "") + ").")
            return
        time.sleep(1.0)
    print("  ! Outlook connect timed out — warm-up will use the mock calendar.")


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
    parser.add_argument(
        "--no-connect-outlook",
        action="store_true",
        help="Skip pre-connecting Outlook. By default the script connects the "
             "calendar + mail (reusing a cached MS login) so the warm-up runs "
             "the real calendar/email flow instead of the mock fallback.",
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

        if not args.no_connect_outlook:
            print("Connecting Outlook (calendar + mail) so the warm-up uses the real flow…")
            connect_outlook(client, token)

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
