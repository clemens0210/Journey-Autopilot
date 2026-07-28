"""The simulated dataset the whole prototype runs on — two halves, one clock.

There is no real DB API for either live operations or account/ticket data (see
ADR 0005), so both are simulated. They used to sit in different layers
(``mock_data`` at the package root, ``accounts`` under ``onboarding/``), which
hid the fact that they are a single dataset that must agree with itself:

- ``mock_data`` — live ops: the demo trip, live status, network disruptions,
  reroute options, mobility/hotel offers, delay history, the user calendar.
  Loaded from ``data/fixtures/<JA_FIXTURES>.json`` and rebased onto today.
- ``accounts``  — the account side: bahn.de login, imported bookings, Outlook
  events. Composes its times in Python rather than from a fixture.

**The coupling:** ``mock_data`` rebases the authored fixture day onto today
(``DEMO_DAY``) and then shifts every wall-clock time so the demo trip departs
``JA_DEMO_TRIP_LEAD_MIN`` minutes before the process started
(``DEMO_TIME_SHIFT``). ``accounts`` composes its bookings and calendar entries
as "``DEMO_DAY`` at HH:MM plus ``DEMO_TIME_SHIFT``" — exactly the arithmetic the
fixture went through. Break that and the dashboard trip, the monitored live
status, the reroute arrivals, and the calendar clash silently drift apart.

Both are re-exported here so the shared anchor has one obvious home; import
``journey_autopilot.demo.DEMO_DAY``/``DEMO_TIME_SHIFT`` rather than reaching
into ``mock_data`` for them.
"""

from .mock_data import DEMO_DAY, DEMO_TIME_SHIFT

__all__ = ["DEMO_DAY", "DEMO_TIME_SHIFT"]
