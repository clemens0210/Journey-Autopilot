"""Calendar reads and the conflict classification built on them.

Three concerns, deliberately together: whether a real calendar is available at
all (``is_calendar_connected``), reading a day's appointments
(``get_user_calendar``), and deciding which of them a trip window clashes with
(``classify_window_conflicts``). The write path reuses the last two at
execution time, so keeping them in one module is what stops discovery-time and
execution-time verdicts from drifting apart.

Only ``check_options_against_calendar`` is an ADK tool here; the other two are
plain Python called by the write path and the Planner's instruction provider.
"""

from __future__ import annotations

import os
import re
import time
from datetime import datetime, timedelta

from ...demo import mock_data
from ...errors import with_resilience_async
from ..constraints import parse_datetime
from .profile import _profile_connections
from .workspace import turn_reroute_state


def _calendar_configured() -> bool:
    """Return True if MS Entra credentials are present in the environment."""
    return bool(os.getenv("MS_ENTRA_CLIENT_ID"))


def _outlook_connected() -> bool:
    """Return True if the user connected Outlook during onboarding.

    Checks ``profile.connections.outlook`` in the onboarding store. This
    prevents the agent from triggering a blocking device-code flow when
    the user skipped Outlook — the token cache is only populated after a
    successful web-based device-code login.
    """
    return bool(_profile_connections().get("outlook"))


def is_calendar_connected() -> bool:
    """True when a real calendar is available (Entra creds + Outlook connected).

    The Planner's instruction provider reads this at call time: without a
    connected calendar the calendar steps are dropped from the prompt entirely,
    so no LLM round-trip (and no mock-calendar check) is spent on appointments
    the user never provided.
    """
    return _calendar_configured() and _outlook_connected()


# Internal alias Microsoft consumer accounts report as the organizer address
# of self-created events (e.g. outlook_45A79CDF4502E0CF@outlook.com). Graph's
# sendMail accepts it, but it is NOT a routable inbox — mail silently goes
# nowhere. Never use it as a recipient.
PSEUDO_OUTLOOK_ALIAS_RE = re.compile(r"^outlook_[0-9A-F]{8,}@outlook\.com$", re.IGNORECASE)


def _resolve_self_organized_contacts(events: list[dict]) -> None:
    """Replace the organizer contact of self-organized events in place.

    For events the connected user created themself, Graph reports the
    non-routable ``outlook_<hex>@outlook.com`` alias (see
    ``PSEUDO_OUTLOOK_ALIAS_RE``) as organizer address. The organizer IS the
    connected account, so substitute the real email/name stored during
    onboarding (``profile.connections.outlook_email``). Events organized by
    others are left untouched.
    """
    connections = _profile_connections()
    email = connections.get("outlook_email")
    if not email:
        return
    for event in events:
        if event.get("self_organized"):
            event["organizer_email"] = email
            event["organizer_name"] = connections.get("outlook_name") or event.get(
                "organizer_name"
            )


# Minutes planned for getting from the arrival station to an appointment —
# the same assumption the Planner uses when gating reroute options.
CALENDAR_TRAVEL_BUFFER_MINUTES = 30

# Live-calendar cache: (date, user_email) -> (monotonic timestamp, result).
# 60 seconds spans one planning run (overview + per-option conflict checks)
# without holding stale data across chat turns.
_CALENDAR_CACHE: dict[tuple[str | None, str, str | None], tuple[float, dict]] = {}
_CALENDAR_CACHE_TTL_S = 60

_TIME_ONLY_RE = re.compile(r"^\d{1,2}:\d{2}$")


async def get_user_calendar(date: str, user_email: str | None = None) -> dict:
    """Read the user's calendar appointments for a given date.

    NOT an agent tool — the Planner never calls this directly. Its two callers
    are ``check_options_against_calendar`` (discovery time, one fetch for the
    whole option batch) and ``write_tools._fresh_calendar_clash`` (execution
    time, revalidating the option the traveler picked). Both go through here so
    the 60s cache below can collapse their repeated reads into one Graph call.

    Uses Outlook/Microsoft Graph **only when both** Entra credentials are
    present in .env (MS_ENTRA_CLIENT_ID, MS_ENTRA_TENANT_ID) **and** the user
    has connected Outlook during onboarding (``profile.connections.outlook``).
    This prevents the agent from triggering a blocking device-code flow
    mid-chat when the user skipped Outlook. Without a connected Outlook,
    falls back to mock data.

    Args:
        date: Date in "YYYY-MM-DD" format, e.g. "2026-06-03".
        user_email: Optional email of another user whose calendar
            should be queried. If omitted, the authenticated user's
            own calendar is used.

    Returns:
        A dict with the list of appointments. ``hard_constraint=True`` marks
        non-negotiable appointments. Contains ``source`` ("outlook", "mock", or
        "mock (Graph-Fallback)") and optionally ``error`` on failed
        Graph access (mock data was used as fallback in that case).
    """
    mock_events = mock_data.USER_CALENDAR.get(date, [])

    if not (_calendar_configured() and _outlook_connected()):
        return {"date": date, "events": mock_events, "source": "mock"}

    # Short-lived cache: within one turn the same date is read by the batched
    # option check, and again by the Executor's fresh clash revalidation at
    # execution time. Serving those from a 60s cache turns repeated Graph
    # round-trips into one; failed fetches are never cached so a Graph hiccup
    # can recover on the next call.
    try:
        from ...request_context import current_user_id

        request_user_id = current_user_id.get()
    except Exception:
        request_user_id = None
    cache_key = (request_user_id, date, user_email)
    cached = _CALENDAR_CACHE.get(cache_key)
    if cached is not None and time.monotonic() - cached[0] < _CALENDAR_CACHE_TTL_S:
        return {**cached[1], "events": list(cached[1]["events"])}

    async def _primary() -> dict:
        from ...integrations.outlook import get_calendar_events

        events = await get_calendar_events(date, user_email)
        _resolve_self_organized_contacts(events)
        return {"date": date, "events": events, "source": "outlook"}

    def _fallback() -> dict:
        return {"date": date, "events": mock_events, "source": "mock (Graph-Fallback)"}

    outcome = await with_resilience_async(_primary, _fallback, tool="get_user_calendar")
    result = outcome.value
    if outcome.failure is not None:  # Graph access failed -> surface why
        result["error"] = outcome.failure["fallback_taken"]
    else:
        _CALENDAR_CACHE[cache_key] = (time.monotonic(), result)
    return result


def _parse_trip_time(date: str, value: str | None) -> datetime | None:
    """Parse "HH:MM" (combined with ``date``) or an ISO datetime to naive local time.

    Calendar events and DB times are all Europe/Berlin wall time in this app
    (the Graph client requests that timezone, the mapper strips the offset), so
    any offset is dropped rather than converted.
    """
    if not value:
        return None
    value = value.strip()
    if _TIME_ONLY_RE.match(value):
        value = f"{date}T{value}:00"
    dt = parse_datetime(value)
    return dt.replace(tzinfo=None) if dt is not None else None


def classify_window_conflicts(
    events: list[dict],
    date: str,
    planned_departure: str = "",
    expected_arrival: str = "",
    latest_arrival: str = "",
) -> dict | None:
    """Classify calendar events against one trip window (shared core).

    Returns ``None`` when no usable arrival estimate was given; otherwise a
    dict with ``conflicts`` (events tagged ``during_trip`` /
    ``at_risk_if_delayed``), ``hard_conflicts``, ``unparsed_events``, and
    ``departure_known``. Shared by ``check_options_against_calendar`` (one
    window per reroute option, at discovery time) and
    ``write_tools._fresh_calendar_clash`` (one window, at execution time), so
    the two can't diverge on what counts as a clash.
    """
    departure = _parse_trip_time(date, planned_departure)
    expected = _parse_trip_time(date, expected_arrival)
    latest = _parse_trip_time(date, latest_arrival)
    if expected is None and latest is None:
        return None
    expected = expected or latest
    latest = max(latest, expected) if latest else expected  # invariant: latest >= expected

    buffer = timedelta(minutes=CALENDAR_TRAVEL_BUFFER_MINUTES)
    conflicts: list[dict] = []
    unparsed = 0
    for event in events:
        start = _parse_trip_time(date, event.get("start"))
        if start is None:
            unparsed += 1
            continue
        if departure is not None and start < departure:
            continue  # before the trip — unaffected
        if start < expected + buffer:
            clash = "during_trip"
        elif start < latest + buffer:
            clash = "at_risk_if_delayed"
        else:
            continue  # reachable even in the unfavorable case
        conflicts.append({**event, "clash": clash})
    return {
        "conflicts": conflicts,
        "hard_conflicts": sum(1 for c in conflicts if c.get("hard_constraint")),
        "unparsed_events": unparsed,
        "departure_known": departure is not None,
    }


async def check_options_against_calendar(
    date: str,
    planned_departure: str = "",
    user_email: str | None = None,
) -> dict:
    """Checks ALL reroute options found this turn against the calendar at once.

    Call this ONCE after ``find_reroute_options`` (and any ecosystem tools),
    not once per option. It reads the options gathered in this planning turn,
    fetches the calendar a single time, and returns a per-option verdict.

    Args:
        date: Travel date in "YYYY-MM-DD" format, e.g. "2026-06-19".
        planned_departure: The trip's planned departure (ISO datetime or
            "HH:MM") — used for options that carry no departure of their own,
            so appointments before the trip are not misclassified.
        user_email: Optional email of another user whose calendar to query.

    Returns:
        A dict with ``option_verdicts`` — one entry per option:
        ``option_id``, ``mode``, ``viable`` (True = no hard-constraint clash),
        ``hard_conflicts``, and the clashing appointments (incl. their
        ``hard_constraint`` flag and organizer/attendee contacts). Options
        without a same-day arrival (e.g. hotels) return ``viable: null`` with
        a note. Also contains ``hard_constraint_appointments`` (all
        non-negotiable appointments that day), ``events_checked``,
        ``buffer_minutes``, and the calendar ``source``.
    """
    if not is_calendar_connected():
        stashed = turn_reroute_state()
        if stashed is not None:
            stashed["calendar_checked"] = True
            stashed["calendar_verdicts"] = {}
        return {
            "calendar_connected": False,
            "checked": False,
            "note": (
                "No calendar is connected — appointment deadlines cannot be "
                "checked. Treat options as free of calendar conflicts."
            ),
        }

    stashed = turn_reroute_state()
    if stashed is None:
        return {
            "date": date,
            "error": (
                "No reroute options were gathered this turn — call "
                "find_reroute_options first, then this check."
            ),
        }
    options = stashed.get("candidate_options") or []
    if not options:
        stashed["calendar_checked"] = True
        stashed["calendar_verdicts"] = {}
        return {
            "calendar_connected": True,
            "date": date,
            "checked": True,
            "option_verdicts": [],
            "note": "No eligible reroute candidates to check; widen the search or finalize fallbacks.",
        }

    calendar = await get_user_calendar(date, user_email)
    events = calendar.get("events", [])

    verdicts: list[dict] = []
    for option in options:
        entry: dict = {
            "option_id": option.get("option_id"),
            "mode": option.get("mode", "train"),
        }
        arrival = option.get("new_arrival")
        if not arrival:
            entry.update(
                viable=None,
                note=(
                    "No same-day arrival time (e.g. overnight hotel) — not "
                    "checkable against the travel day's appointments."
                ),
            )
            verdicts.append(entry)
            continue
        classified = classify_window_conflicts(
            events,
            date,
            planned_departure=option.get("departure") or planned_departure,
            expected_arrival=arrival,
        )
        # classified can't be None here: arrival is a non-empty expected_arrival.
        entry.update(
            new_arrival=arrival,
            viable=classified["hard_conflicts"] == 0,
            hard_conflicts=classified["hard_conflicts"],
            conflicts=classified["conflicts"],
        )
        verdicts.append(entry)

    result = {
        "calendar_connected": True,
        "date": date,
        "buffer_minutes": CALENDAR_TRAVEL_BUFFER_MINUTES,
        "events_checked": len(events),
        "hard_constraint_appointments": [
            e for e in events if e.get("hard_constraint")
        ],
        "option_verdicts": verdicts,
        "source": calendar.get("source"),
    }
    if calendar.get("error"):
        result["calendar_error"] = calendar["error"]
    stashed["calendar_checked"] = True
    stashed["calendar_verdicts"] = {
        entry.get("option_id"): entry for entry in verdicts if entry.get("option_id")
    }
    stashed["calendar_result"] = result
    return result
