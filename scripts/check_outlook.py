"""Standalone check of the Microsoft connection and Outlook calendar reads.

Runs WITHOUT the web interface, the onboarding store, or the DB sidecar. It
exercises the exact same integration code the agents use (persistent token
cache + authentication record + Graph SDK with the CAE fix), so a green run
here means the Planner's calendar reads work too.

Why this works standalone: the device-code login (web onboarding or --login
here) stores two machine-level files under %LOCALAPPDATA%\\.IdentityService
(Windows) or ~/.IdentityService (Linux/macOS):

  - journey_autopilot.nocae           encrypted MSAL token cache
  - journey_autopilot.authrecord.json account metadata (no secrets)

Any Python process on the same machine, running as the same OS user with the
same MS_ENTRA_CLIENT_ID, can silently reuse that login. A login made in the
web interface is therefore fully usable from this script — and vice versa.

Usage:
    python scripts/check_outlook.py                     # today, silent auth
    python scripts/check_outlook.py 2026-06-19          # specific date
    python scripts/check_outlook.py 2026-06-19 --days 3 # small range
    python scripts/check_outlook.py --login             # device-code login in
                                                        # the terminal (no web
                                                        # interface needed)
    python scripts/check_outlook.py --send-test         # also send a test
                                                        # notice email to the
                                                        # connected account
                                                        # (needs Mail.Send)
    python scripts/check_outlook.py --reschedule-test ID # move an existing
                                                        # event 15 min and
                                                        # back (needs
                                                        # Calendars.ReadWrite)

Exit codes: 0 = all checks passed, 1 = configuration/Graph problem,
2 = no cached login (run --login or connect via the web onboarding).
"""

from __future__ import annotations

import argparse
import asyncio
import os
import ssl as _ssl
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

_SRC = Path(__file__).resolve().parents[1] / "src"
if _SRC.is_dir() and str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

try:
    from dotenv import load_dotenv

    load_dotenv()
    load_dotenv("journey_autopilot/.env")
except ImportError:
    pass

# Windows cert-store workaround for MSAL's requests-based token polling —
# same patch as run_onboarding.py / scenarios/happy_path.py.
if sys.platform.startswith("win"):
    _orig_load_default_certs = _ssl.SSLContext.load_default_certs

    def _patched_load_default_certs(self, purpose=_ssl.Purpose.SERVER_AUTH):
        try:
            _orig_load_default_certs(self, purpose)
        except _ssl.SSLError as exc:
            if "NOT_ENOUGH_DATA" not in str(exc):
                raise

    _ssl.SSLContext.load_default_certs = _patched_load_default_certs

from journey_autopilot.integrations.outlook import (  # noqa: E402
    create_device_credential,
    get_calendar_events,
    get_calendar_events_range,
    get_signed_in_user,
    is_outlook_configured,
    reschedule_calendar_event,
    send_notice_email,
)
from journey_autopilot.integrations.outlook.auth import (  # noqa: E402
    CALENDAR_WRITE_SCOPES,
    SCOPES,
    _auth_record_path,
    acquire_credential,
    load_authentication_record,
    save_authentication_record,
)


def _ok(label: str, detail: str = "") -> None:
    print(f"  [OK]   {label}" + (f" - {detail}" if detail else ""))


def _fail(label: str, detail: str = "") -> None:
    print(f"  [FAIL] {label}" + (f" - {detail}" if detail else ""))


def _step(title: str) -> None:
    print(f"\n{title}")
    print("-" * 68)


def _login():
    """Interactive device-code login in the terminal; saves the auth record.

    Returns the authenticated credential so the subsequent Graph calls reuse
    it directly (same instance = guaranteed silent).
    """

    def prompt(verification_uri: str, user_code: str, expires_on: datetime) -> None:
        print(f"\n  To sign in, open   {verification_uri}")
        print(f"  and enter the code {user_code}\n")

    cred = create_device_credential(prompt)
    # CALENDAR_WRITE_SCOPES so the consent also covers the notice-email send
    # path and rescheduling an appointment (Calendars.ReadWrite).
    record = cred.authenticate(scopes=CALENDAR_WRITE_SCOPES)
    save_authentication_record(record)
    _ok("device-code login", f"signed in as {record.username}, auth record saved")
    return cred


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("date", nargs="?", default=date.today().isoformat(),
                        help="date to read (YYYY-MM-DD, default: today)")
    parser.add_argument("--days", type=int, default=0,
                        help="additionally read a range of N days from DATE")
    parser.add_argument("--login", action="store_true",
                        help="run a device-code login in the terminal first")
    parser.add_argument("--send-test", nargs="?", const="self", default=None,
                        metavar="ADDRESS",
                        help="send a test notice email (requires Mail.Send "
                             "consent; default recipient: the connected "
                             "account itself)")
    parser.add_argument("--reschedule-test", metavar="EVENT_ID", default=None,
                         help="move an existing event by 15 minutes and back "
                              "(requires Calendars.ReadWrite consent; get an "
                              "EVENT_ID from the calendar read above)")
    args = parser.parse_args()

    print("=" * 68)
    print("  Outlook connection check")
    print("=" * 68)

    # 1. Configuration ------------------------------------------------------
    _step("1) Configuration (.env)")
    if not is_outlook_configured():
        _fail("MS_ENTRA_CLIENT_ID missing",
              "copy .env.example to .env and fill in the Entra app values")
        return 1
    _ok("MS_ENTRA_CLIENT_ID", os.getenv("MS_ENTRA_CLIENT_ID", "")[:8] + "...")
    _ok("MS_ENTRA_TENANT_ID", os.getenv("MS_ENTRA_TENANT_ID", "(default: consumers)"))

    # 2. Cached login -------------------------------------------------------
    _step("2) Cached login (token cache + authentication record)")
    credential = None
    if args.login:
        credential = _login()
    else:
        record = load_authentication_record()
        if record is None:
            _fail("authentication record missing", str(_auth_record_path()))
        else:
            _ok("authentication record", f"account {record.username}")

        try:
            credential = acquire_credential()
            credential.get_token(*SCOPES)  # silent or raises — never prompts
            _ok("silent token acquisition")
        except Exception as exc:
            _fail("silent token acquisition", f"{type(exc).__name__}: {exc}")
            print("\n  No usable cached login. Either:")
            print("    - run:  python scripts/check_outlook.py --login")
            print("    - or connect Outlook via the web onboarding (run_onboarding.py)")
            return 2

    # 3. Graph identity -----------------------------------------------------
    _step("3) Microsoft Graph identity (/me)")
    try:
        identity = await get_signed_in_user(credential=credential)
        _ok("connected account", f"{identity.get('email')} ({identity.get('name')})")
    except Exception as exc:
        _fail("/me request", f"{type(exc).__name__}: {exc}")
        return 1

    # 4. Calendar reads -----------------------------------------------------
    _step(f"4) Calendar read - {args.date}"
          + (f" .. +{args.days} days" if args.days else ""))
    try:
        if args.days:
            end = (date.fromisoformat(args.date) + timedelta(days=args.days)).isoformat()
            events = await get_calendar_events_range(args.date, end, credential=credential)
        else:
            events = await get_calendar_events(args.date, credential=credential)
    except Exception as exc:
        _fail("calendarView request", f"{type(exc).__name__}: {exc}")
        return 1

    hard = sum(1 for e in events if e.get("hard_constraint"))
    _ok("calendarView request", f"{len(events)} event(s), {hard} hard constraint(s)")
    for i, ev in enumerate(events, start=1):
        lock = "HARD" if ev.get("hard_constraint") else "soft"
        print(f"     [{i}] {ev.get('start')}  {ev.get('title')!r}"
              f"  @ {ev.get('location')}  ({lock})")
    if not events:
        print("     (no events on this date — the connected account's DEFAULT")
        print("      calendar is read; events in other calendars/accounts are")
        print("      not visible. Hard constraints need the Outlook category")
        print("      'Journey-Autopilot/Hard'.)")

    # 5. Optional: test email send (Mail.Send scope) ------------------------
    if args.send_test is not None:
        to = identity.get("email") if args.send_test == "self" else args.send_test
        _step(f"5) Test email send - to {to}")
        if not to:
            _fail("no recipient", "could not resolve the connected account's email")
            return 1
        try:
            await send_notice_email(
                to,
                "Journey Autopilot - test notice",
                "This is a test of the appointment-notice email path.\n"
                "If you can read this, Mail.Send consent and Graph sendMail "
                "work.\n\n(sent by scripts/check_outlook.py --send-test)",
                credential=credential,
            )
            _ok("sendMail request", f"check the inbox/Sent folder of {to}")
        except Exception as exc:
            _fail("sendMail request", f"{type(exc).__name__}: {exc}")
            print("\n  Most common cause: the cached login has no Mail.Send")
            print("  consent yet. Fix: add the Mail.Send delegated permission")
            print("  to the Entra app registration, then reconnect once:")
            print("    python scripts/check_outlook.py --login")
            return 1

    # 6. Optional: test reschedule (Calendars.ReadWrite scope) --------------
    if args.reschedule_test is not None:
        _step(f"6) Test reschedule - event {args.reschedule_test}")
        target = next((e for e in events if e.get("id") == args.reschedule_test), None)
        if target is None:
            _fail("event not found in the events read above",
                  "pass an EVENT_ID printed by step 4 (same DATE/--days window)")
            return 1
        start = datetime.fromisoformat(target["start"])
        end = datetime.fromisoformat(target["end"]) if target.get("end") else None
        bumped_start = (start + timedelta(minutes=15)).isoformat(timespec="seconds")
        bumped_end = (
            (end + timedelta(minutes=15)).isoformat(timespec="seconds") if end else None
        )
        try:
            await reschedule_calendar_event(
                args.reschedule_test, start=bumped_start, end=bumped_end,
                credential=credential,
            )
            _ok("PATCH request", f"moved to {bumped_start}")
            # Move it back so the test is non-destructive.
            await reschedule_calendar_event(
                args.reschedule_test,
                start=start.isoformat(timespec="seconds"),
                end=end.isoformat(timespec="seconds") if end else None,
                credential=credential,
            )
            _ok("PATCH request", f"moved back to {target['start']}")
        except Exception as exc:
            _fail("PATCH request", f"{type(exc).__name__}: {exc}")
            print("\n  Most common cause: the cached login has no")
            print("  Calendars.ReadWrite consent yet. Fix: add the")
            print("  Calendars.ReadWrite delegated permission to the Entra")
            print("  app registration, then reconnect once:")
            print("    python scripts/check_outlook.py --login")
            return 1

    print("\n" + "=" * 68)
    print("  ALL CHECKS PASSED - the agents can read this calendar.")
    print("=" * 68)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
