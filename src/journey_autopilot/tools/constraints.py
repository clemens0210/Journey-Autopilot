"""Constraint rules shared by the Planner's discovery and the Executor's revalidation.

These are the rules that decide whether a reroute option is *usable at all* —
transfer count and buffer, cancellation, the traveler's mobility opt-outs, and
their latest-arrival-home limit. Two places apply them, at two different
moments:

- ``read_tools.finalize_reroute_options`` — discovery time, deciding which
  options become selectable cards.
- ``write_tools._profile_constraint_violations`` — execution time, reapplying
  them to the option the traveler actually picked, against refreshed live data.

They live in their own module so neither side owns the other's rules: before
this split the write path reached into ``read_tools`` for six private helpers,
which made "the two must not diverge" a matter of import discipline rather than
structure. Everything here is pure — no I/O, no live lookups, no request state
— so both callers can apply it whenever they need to.

Calendar verdicts and live-freshness checks (an option that has already
departed) are deliberately NOT here: they are caller-specific and time-bound.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any


def parse_datetime(value: str | None) -> datetime | None:
    """Parse ISO timestamps from DB/mock data, tolerating trailing ``Z``."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def minutes_between(start: str | None, end: str | None) -> int | None:
    start_dt = parse_datetime(start)
    end_dt = parse_datetime(end)
    if start_dt is None or end_dt is None:
        return None
    # Stored trips use naive German-local times while the live sidecar returns
    # offset-aware ones; on a mix, compare wall clocks (both are German local).
    if (start_dt.tzinfo is None) != (end_dt.tzinfo is None):
        start_dt = start_dt.replace(tzinfo=None)
        end_dt = end_dt.replace(tzinfo=None)
    return round((end_dt - start_dt).total_seconds() / 60)


def minimum_transfer_buffer(opt: dict) -> int | None:
    """Smallest live transfer buffer across an option's ride legs."""
    legs = opt.get("legs") or []
    buffers: list[int] = []
    for leg, following in zip(legs, legs[1:]):
        buffer_minutes = minutes_between(leg.get("arrival"), following.get("departure"))
        if buffer_minutes is not None:
            buffers.append(buffer_minutes)
    return min(buffers) if buffers else None


def station_key(value: Any) -> str:
    """Normalize a station name (or a station dict) for comparison."""
    if isinstance(value, dict):
        value = value.get("name")
    return " ".join(str(value or "").casefold().split())


def time_after_home_limit(
    arrival: datetime | None, departure: datetime | None, limit: str
) -> bool:
    """True if `arrival`'s wall-clock time is after "HH:MM" `limit`.

    Also true if `arrival` falls on a later calendar date than `departure`
    (an overnight arrival is "after" any same-day cutoff regardless of the
    clock reading). `limit` must already be validated as "HH:MM"; an
    unparseable `arrival` is treated as "not after" (nothing to compare).
    """
    if arrival is None:
        return False
    limit_hour, limit_minute = (int(part) for part in limit.split(":"))
    if departure is not None and arrival.replace(tzinfo=None).date() > departure.replace(
        tzinfo=None
    ).date():
        return True
    return arrival.hour * 60 + arrival.minute > limit_hour * 60 + limit_minute


def arrives_after_home_limit(option: dict, profile: dict) -> bool:
    """Apply latest-arrival-home only when the option actually ends at home."""
    home = profile.get("home") or {}
    home_station = station_key(home.get("home_station"))
    if not home_station or station_key(option.get("destination")) != home_station:
        return False
    limit = str(home.get("latest_arrival_home") or "").strip()
    if not re.fullmatch(r"\d{2}:\d{2}", limit):
        return False
    arrival = parse_datetime(option.get("new_arrival"))
    departure = parse_datetime(option.get("departure"))
    return time_after_home_limit(arrival, departure, limit)


def mode_eligibility_violations(
    option: dict,
    *,
    preferences: dict,
    mobility: dict,
    home: dict,
    recompute_transfer_buffer: bool,
) -> list[str]:
    """Shared per-mode transfer/cancellation/mobility/hotel eligibility rules.

    Returns the reasons the option is not eligible; an empty list means it
    passes. ``recompute_transfer_buffer`` forces a fresh buffer calculation
    from the (possibly refreshed) legs instead of trusting a cached value —
    the execution-time caller sets it, discovery does not.
    """
    reasons: list[str] = []
    mode = option.get("mode", "train")
    if mode == "train":
        if option.get("cancelled"):
            reasons.append("cancelled")
        try:
            max_transfers = max(0, int(preferences.get("max_transfers", 2)))
            min_transfer = max(0, int(preferences.get("min_transfer_minutes", 8)))
        except (TypeError, ValueError):
            max_transfers, min_transfer = 2, 8
        if (option.get("transfers") or 0) > max_transfers:
            reasons.append("too_many_transfers")
        if recompute_transfer_buffer or option.get("minimum_transfer_minutes") is None:
            option["minimum_transfer_minutes"] = minimum_transfer_buffer(option)
        buffer = option.get("minimum_transfer_minutes")
        if buffer is not None and buffer < min_transfer:
            reasons.append("transfer_too_short")
    elif mode == "car_sharing" and not mobility.get("car_sharing_ok", True):
        reasons.append("car_sharing_disabled")
    elif mode == "bike_sharing" and not mobility.get("bike_sharing_ok", True):
        reasons.append("bike_sharing_disabled")
    elif mode == "hotel" and not home.get("hotel_ok", True):
        reasons.append("hotel_disabled")
    return reasons
