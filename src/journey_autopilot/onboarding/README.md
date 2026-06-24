# Onboarding logic

The **logic** behind the onboarding web app — no UI, no FastAPI. The
user-facing app that drives this lives in
[`journey_autopilot/ui/`](../ui/) (see its README for the full flow, the
DB Navigator design, and the API).

| Module        | Responsibility                                                                 |
| ------------- | ------------------------------------------------------------------------------ |
| `accounts.py` | Simulated DB accounts, booked trips, and Outlook events. The **swap point** for a real DB/Microsoft integration — the interface (`authenticate`, `booked_trips`, `outlook_events`) stays stable. Also holds the station fallback list. |

The SQLite store itself lives one level up in
[`../persistence/store.py`](../persistence/store.py) (users, the profile as a
single JSON blob — see `DEFAULT_PROFILE` — and imported trips; standard-library
`sqlite3` only, no FastAPI dependency).

## Why this split?

- The **UI** doesn't need ADK; the **agents** don't need FastAPI. Keeping the
  logic here and the presentation in `ui/` makes that boundary explicit.
- The agents reach the profile through one shared touchpoint only:
  `journey_autopilot.tools` calls `persistence.store.any_profile()` (single-user:
  "the most recently maintained profile") to rank reroute options against the
  user's preferences.

## Demo data note

`accounts.booked_trips()` pins Lucas' first trip (Munich → Berlin) to the
canonical demo scenario in
[`journey_autopilot/mock_data.py`](../mock_data.py) — same `trip_id`, route, and
date (`DEMO_DATE`). That keeps the dashboard's trip chat in sync with the
monitoring/reroute/calendar fixtures, so it exercises the full disruption story.
The remaining trips are generated relative to today so the demo always shows
upcoming bookings.

> Why simulated at all? DB offers no official API for account login or ticket
> import, and Microsoft OAuth / SMS need registered apps. Background in
> [`CONTEXT_RECORD.md`](../../CONTEXT_RECORD.md) → *Onboarding & Profile*.
