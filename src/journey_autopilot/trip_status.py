"""Where a trip sits in its lifecycle: PRE_TRIP -> EN_ROUTE -> ARRIVED.

One definition of the three phases, at two precisions:

- ``from_schedule`` — pure, derived from the booked times alone. Cheap enough
  to run over a whole trip list, so it is what ``store.get_trips`` attaches to
  every trip on the way out and what the browser falls back to.
- ``from_live_status`` — refines that with what the live feed actually
  reports (a ``get_live_trip_status`` result), the only thing that can tell an
  on-time arrival from a train still running late, or hold a trip at PRE_TRIP
  past a departure it missed.

Both agree on the rule the rest of the codebase already follows: a trip counts
as ARRIVED only once that is *confirmed*, never merely because its scheduled
arrival time passed. A train 90 minutes late is still EN_ROUTE at its planned
arrival — concluding early is the expensive mistake here, because the same
verdict decides whether a compensation claim may be assessed (see
``tools/read/monitoring.py`` and ``onboarding/complaints.py``). Hence the
schedule fallback waits out ``CONCLUDED_MARGIN``, and unusable dates resolve
towards the earlier phase: this module may under-claim, never over-claim.

Deliberately dependency-free beyond the shared ISO parser — the persistence
layer imports it, so it must not drag in the tool/agent stack.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from .tools.constraints import parse_datetime

# Wire values. Stored nowhere (always derived at read time) and shared verbatim
# with the browser (``ui/static/js/format.js``) and the agent prompts, which
# speak the same three words: PRE-TRIP, EN ROUTE, ARRIVED.
PRE_TRIP = "pre_trip"
EN_ROUTE = "en_route"
ARRIVED = "arrived"

# How long after the planned arrival a trip counts as certainly over when no
# live arrival time can confirm it. Generous on purpose: it has to outlast even
# a heavy delay. Shared with ``tools/read/monitoring`` (which reports the
# ``arrived`` flag off the same margin), so the label in the UI and the agent's
# ARRIVED verdict cannot drift apart.
CONCLUDED_MARGIN = timedelta(hours=3)


def _clock(anchor: datetime, now: datetime | None) -> datetime:
    """``now`` made comparable to ``anchor``.

    Stored trips carry naive German-local wall clock, the live sidecar returns
    offset-aware timestamps. On a mismatch both are compared as wall clocks —
    the same rule ``constraints.minutes_between`` applies.
    """
    if now is None:
        return datetime.now(anchor.tzinfo) if anchor.tzinfo else datetime.now()
    if (now.tzinfo is None) != (anchor.tzinfo is None):
        return now.replace(tzinfo=anchor.tzinfo)
    return now


def _has_passed(
    value: str | None, now: datetime | None = None, *, plus: timedelta | None = None
) -> bool:
    """True if ``value`` (+ optional offset) lies at or before now.

    A missing or unparseable timestamp answers False, which is what keeps a
    trip with broken dates in the earlier phase rather than claiming progress
    the data does not support.
    """
    moment = parse_datetime(value)
    if moment is None:
        return False
    if plus is not None:
        moment = moment + plus
    return moment <= _clock(moment, now)


def from_schedule(trip: dict, now: datetime | None = None) -> str:
    """The phase implied by the booked times alone — no live data, no I/O.

    Only ``planned_departure`` and ``planned_arrival`` are read, so a live
    status dict can be passed here just as well as a stored trip.
    """
    if not _has_passed(trip.get("planned_departure"), now):
        return PRE_TRIP
    # Without a usable arrival there is nothing that could conclude the trip,
    # so it stays EN_ROUTE rather than being aged out on a guess.
    if _has_passed(trip.get("planned_arrival"), now, plus=CONCLUDED_MARGIN):
        return ARRIVED
    return EN_ROUTE


def from_live_status(
    status: dict, *, departed: bool | None = None, now: datetime | None = None
) -> str:
    """Refine the schedule phase with what the live feed reports.

    ``status`` is a ``get_live_trip_status`` result. Its ``arrived`` flag is the
    only thing that confirms a trip is over ahead of ``CONCLUDED_MARGIN``, and
    ``departed`` — when the caller could work it out from the live legs — the
    only thing that can hold a trip at PRE_TRIP past its scheduled departure: a
    train leaving 20 minutes late has not started yet.
    """
    if status.get("arrived"):
        return ARRIVED
    if departed is not None:
        return EN_ROUTE if departed else PRE_TRIP
    return from_schedule(status, now)
