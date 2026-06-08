"""Outlook Calendar Demo — fetches and inspects user calendar events.

Lists all events for a given date, checks which ones are hard constraints
(carry the Outlook category "Journey-Autopilot/Hard"), and logs the result
to the console.

Usage:
    python run_calendar_demo.py [YYYY-MM-DD]

Defaults to 2026-06-10 if no date is given.
"""

from __future__ import annotations

import asyncio
import os
import sys

try:
    from dotenv import load_dotenv

    load_dotenv()
    load_dotenv("journey_autopilot/.env")
except ImportError:
    pass

from journey_autopilot.calendar import get_calendar_events
from journey_autopilot import mock_data


def _format_event(ev: dict, idx: int) -> str:
    title = ev.get("title", "???")
    loc = ev.get("location", "???")
    start = ev.get("start", "???")
    hard = ev.get("hard_constraint", False)
    label = "HARD CONSTRAINT" if hard else "flexible"
    icon = "🔒" if hard else "  "
    return f"  {icon} [{idx}] {title}\n       {start} — {loc}  ({label})"


async def main() -> None:
    date = sys.argv[1] if len(sys.argv) > 1 else "2026-06-10"

    print("=" * 68)
    print(f"  Outlook Calendar Demo  —  {date}")
    print("=" * 68)

    configured = bool(os.getenv("MS_ENTRA_CLIENT_ID"))

    if not configured:
        print("\n[!] No MS_ENTRA_CLIENT_ID found in .env — using mock data.\n")
        events = mock_data.USER_CALENDAR.get(date, [])
        source = "mock"
    else:
        print(f"\n  MS_ENTRA_CLIENT_ID : {os.getenv('MS_ENTRA_CLIENT_ID')[:8]}...")
        print(f"  MS_ENTRA_TENANT_ID : {os.getenv('MS_ENTRA_TENANT_ID')}")
        print("\n  Fetching from Microsoft Graph ...\n")
        try:
            events = await get_calendar_events(date)
            source = "outlook"
        except Exception as exc:
            print(f"\n[!] Graph API error: {exc}")
            print("    Falling back to mock data.\n")
            events = mock_data.USER_CALENDAR.get(date, [])
            source = "mock (fallback)"

    print(f"  Source : {source}")
    print(f"  Events : {len(events)} found")

    if not events:
        print("\n  No events on this date.")
        return

    hard_count = sum(1 for e in events if e.get("hard_constraint"))
    print(f"  Hard constraints : {hard_count}")
    print()

    for i, event in enumerate(events, start=1):
        print(_format_event(event, i))

    print()
    if hard_count:
        print("  Planner behaviour: only reroutes arriving before the 🔒 event(s)")
        print("  will be suggested. Flexible events are ignored for routing.")
    else:
        print("  Planner behaviour: no hard constraints — all reroutes are valid.")
    print()


if __name__ == "__main__":
    asyncio.run(main())
