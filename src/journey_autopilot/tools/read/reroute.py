"""Planner tools: reroute discovery, ecosystem alternatives, and the shortlist.

Two clearly separated stages, and the separation is the point:

- **Discovery** (``find_reroute_options``, ``find_mobility_alternatives``,
  ``find_partner_hotels``) gathers raw candidates per mode family and stashes
  them in the turn workspace. Nothing here is selectable.
- **Finalization** (``finalize_reroute_options``) is the only place allowed to
  produce the UI-facing ``options``: it reapplies the profile's hard limits,
  ranks by the speed-vs-comfort preference, and keeps constraint-breaking
  routes separate and non-clickable.

Everything in between (pruning, dominance, diversity) is deterministic Python,
never an LLM judgment.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Any

from ... import mock_data
from ...config import REROUTE_MAX_ADDED_DELAY_MINUTES, REROUTE_MAX_OPTIONS
from ...errors import with_resilience
from ...integrations import db_ops as db_api
from ...integrations import hotels as hotels_api
from ...integrations import stations
from ..constraints import (
    arrives_after_home_limit,
    minimum_transfer_buffer,
    minutes_between,
    mode_eligibility_violations,
    parse_datetime,
    time_after_home_limit,
)
from .calendar import is_calendar_connected
from .profile import get_user_profile
from .workspace import _stash_options, turn_reroute_state

logger = logging.getLogger(__name__)


_REROUTE_PREFERENCE_DEFAULTS = {"max_transfers": 2, "min_transfer_minutes": 8, "speed_vs_comfort": 50}


def _sanitize_reroute_preferences(raw_prefs: dict | None) -> dict:
    """Clamp a raw ``profile.preferences`` blob to safe deterministic bounds.

    Pure function over an already-fetched preferences dict, so a caller that
    already has the profile in hand (e.g. ``finalize_reroute_options``) can
    sanitize it without a second store round-trip.
    """
    prefs = raw_prefs or {}
    try:
        return {
            "max_transfers": max(
                0, int(prefs.get("max_transfers", _REROUTE_PREFERENCE_DEFAULTS["max_transfers"]))
            ),
            "min_transfer_minutes": max(
                0,
                int(
                    prefs.get(
                        "min_transfer_minutes", _REROUTE_PREFERENCE_DEFAULTS["min_transfer_minutes"]
                    )
                ),
            ),
            "speed_vs_comfort": min(
                100,
                max(
                    0,
                    int(
                        prefs.get(
                            "speed_vs_comfort", _REROUTE_PREFERENCE_DEFAULTS["speed_vs_comfort"]
                        )
                    ),
                ),
            ),
        }
    except (TypeError, ValueError):
        return dict(_REROUTE_PREFERENCE_DEFAULTS)


def _profile_reroute_preferences() -> dict:
    """Read and sanitize the current request's deterministic reroute preferences."""
    try:
        from journey_autopilot.persistence import store
        from journey_autopilot.request_context import current_user_id

        user_id = current_user_id.get()
        profile = store.get_profile(user_id) if user_id else store.any_profile()
        return _sanitize_reroute_preferences((profile or {}).get("preferences"))
    except Exception:
        return dict(_REROUTE_PREFERENCE_DEFAULTS)


def _reroute_sort_key(opt: dict) -> tuple:
    """Order discovery candidates by earliest arrival, then transfers/cost.

    Live arrivals are uniformly tz-aware within one search; the tz is stripped so
    a stray naive value cannot raise on comparison (wall-clock ordering, like
    ``minutes_between``).
    """
    arr = parse_datetime(opt.get("new_arrival"))
    arr_key = arr.replace(tzinfo=None) if arr else datetime.max
    cost = _option_cost(opt)
    return (
        arr_key,
        opt.get("transfers") or 0,
        cost if cost is not None else float("inf"),
        opt.get("added_delay_minutes") or 0,
    )


def _option_cost(opt: dict) -> float | None:
    """Comparable cost when known; prefer actual added cost over quoted fare."""
    value = opt.get("added_cost_eur")
    if value is None:
        value = opt.get("price_eur")
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _apply_cost_contract(option: dict) -> None:
    """Make known, estimated, and unknown incremental cost unambiguous."""
    status = option.get("cost_status")
    if status in ("known", "estimate", "unknown"):
        return
    if option.get("added_cost_eur") is not None:
        option["cost_status"] = "known"
        return
    mode = option.get("mode", "train")
    if mode in ("car_sharing", "bike_sharing") and option.get("price_eur") is not None:
        option["added_cost_eur"] = option["price_eur"]
        option["cost_status"] = "estimate"
        return
    if mode == "hotel" and option.get("price_per_night_eur") is not None:
        try:
            option["added_cost_eur"] = round(
                float(option["price_per_night_eur"])
                * max(1, int(option.get("nights") or 1)),
                2,
            )
        except (TypeError, ValueError):
            pass
        else:
            option["cost_status"] = "estimate"
            return
    option["added_cost_eur"] = None
    option["cost_status"] = "unknown"


def _tool_visible_option(option: dict) -> dict:
    """Remove provider-only identifiers from agent/browser-facing payloads."""
    return {key: value for key, value in option.items() if not key.startswith("_provider_")}


def _before(value: str | None, threshold: str | None) -> bool:
    """Wall-clock comparison for German-local DB timestamps."""
    value_dt = parse_datetime(value)
    threshold_dt = parse_datetime(threshold)
    if value_dt is None or threshold_dt is None:
        return False
    return value_dt.replace(tzinfo=None) < threshold_dt.replace(tzinfo=None)


def _dominates(a: dict, b: dict) -> bool:
    """True if option ``a`` makes ``b`` pointless: ``a`` departs no earlier,
    arrives no later, has no more transfers, and is no more expensive. At least
    one dimension must be strictly better. Unknown-vs-known cost is deliberately
    incomparable so a potentially cheaper route is never discarded.
    """
    a_dep, b_dep = parse_datetime(a.get("departure")), parse_datetime(b.get("departure"))
    a_arr, b_arr = parse_datetime(a.get("new_arrival")), parse_datetime(b.get("new_arrival"))
    if a_dep is None or b_dep is None or a_arr is None or b_arr is None:
        return False
    # Wall-clock compare (strip tz; all live times are German-local).
    a_dep, b_dep = a_dep.replace(tzinfo=None), b_dep.replace(tzinfo=None)
    a_arr, b_arr = a_arr.replace(tzinfo=None), b_arr.replace(tzinfo=None)
    a_tr, b_tr = a.get("transfers") or 0, b.get("transfers") or 0
    a_cost, b_cost = _option_cost(a), _option_cost(b)
    if (a_cost is None) != (b_cost is None):
        return False
    cost_no_worse = True if a_cost is None else a_cost <= b_cost
    cost_better = False if a_cost is None else a_cost < b_cost
    no_worse = a_dep >= b_dep and a_arr <= b_arr and a_tr <= b_tr and cost_no_worse
    strictly_better = a_dep > b_dep or a_arr < b_arr or a_tr < b_tr or cost_better
    return no_worse and strictly_better


def _select_diverse_options(options: list[dict], max_options: int) -> list[dict]:
    """Cap a shortlist while preserving fastest, simplest, and cheapest choices."""
    if max_options <= 0 or not options:
        return []
    ranked = sorted(options, key=_reroute_sort_key)
    selected: list[dict] = []

    def add(option: dict | None) -> None:
        if option is not None and option not in selected and len(selected) < max_options:
            selected.append(option)

    add(ranked[0])
    add(min(ranked, key=lambda o: (o.get("transfers") or 0, _reroute_sort_key(o))))
    priced = [o for o in ranked if _option_cost(o) is not None]
    if priced:
        add(min(priced, key=lambda o: (_option_cost(o), _reroute_sort_key(o))))
    buffered = [o for o in ranked if o.get("minimum_transfer_minutes") is not None]
    if buffered:
        add(max(buffered, key=lambda o: o["minimum_transfer_minutes"]))
    for option in ranked:
        add(option)
    return sorted(selected, key=_reroute_sort_key)


def _prune_reroute_options(
    options: list[dict],
    *,
    max_transfers: int,
    min_transfer_minutes: int,
    max_added_delay_minutes: int,
    max_options: int,
    earliest_departure: str = "",
    current_arrival: str = "",
) -> dict:
    """Split raw live journeys into eligible, fallback, and rejected buckets."""
    eligible: list[dict] = []
    invalid: list[dict] = []
    rejected: list[dict] = []
    summary: dict[str, int] = {}

    def reject(option: dict, reasons: list[str], *, fallback: bool) -> None:
        item = {**option, "eligible": False, "selectable": False, "constraint_violations": reasons}
        (invalid if fallback else rejected).append(item)
        for reason in reasons:
            summary[reason] = summary.get(reason, 0) + 1

    for raw in options:
        option = dict(raw)
        option["minimum_transfer_minutes"] = minimum_transfer_buffer(option)
        fatal_reasons: list[str] = []
        constraint_reasons: list[str] = []
        if option.get("cancelled"):
            fatal_reasons.append("cancelled")
        if earliest_departure and _before(option.get("departure"), earliest_departure):
            fatal_reasons.append("already_departed")
        if option.get("new_arrival") is None:
            fatal_reasons.append("missing_arrival")
        if (option.get("transfers") or 0) > max_transfers:
            constraint_reasons.append("too_many_transfers")
        transfer_buffer = option.get("minimum_transfer_minutes")
        if transfer_buffer is not None and transfer_buffer < min_transfer_minutes:
            constraint_reasons.append("transfer_too_short")

        # A current ETA is the decision baseline: a reroute that arrives later is
        # not useful. Callers omit current_arrival when the itinerary is broken
        # (missed transfer) — every option is then judged on its own merits.
        # Without it, fall back to the schedule-relative safety bound — and if
        # that too is unknown (no original_arrival to compare against), fall
        # back further to the option's own live-reported delay so an option
        # with no delay information at all is never silently treated as safe.
        saved = option.get("minutes_saved_vs_current_plan")
        if current_arrival and saved is not None:
            if saved < 0:
                constraint_reasons.append("slower_than_current_plan")
        else:
            effective_added_delay = option.get("added_delay_minutes")
            if effective_added_delay is None:
                effective_added_delay = option.get("live_delay_minutes")
            if effective_added_delay is not None and effective_added_delay > max_added_delay_minutes:
                constraint_reasons.append("excessive_added_delay")

        if fatal_reasons:
            reject(option, fatal_reasons + constraint_reasons, fallback=False)
        elif constraint_reasons:
            reject(option, constraint_reasons, fallback=True)
        else:
            option.update(eligible=True, selectable=True, constraint_violations=[])
            eligible.append(option)

    dominated: list[dict] = []
    survivors: list[dict] = []
    for option in eligible:
        if any(other is not option and _dominates(other, option) for other in eligible):
            dominated.append(
                {**option, "eligible": False, "selectable": False, "constraint_violations": ["dominated"]}
            )
            summary["dominated"] = summary.get("dominated", 0) + 1
        else:
            survivors.append(option)

    shortlist = _select_diverse_options(survivors, max_options)
    for option in survivors:
        if option not in shortlist:
            rejected.append(
                {**option, "eligible": False, "selectable": False, "constraint_violations": ["shortlist_cap"]}
            )
            summary["shortlist_cap"] = summary.get("shortlist_cap", 0) + 1
    rejected.extend(dominated)

    # Keep at most one least-bad, non-fatal route for explanation/constraint
    # relaxation. It is never placed in the selectable ``options`` list.
    fallback_options = sorted(invalid, key=_reroute_sort_key)[:1] if not shortlist else []
    return {
        "options": shortlist,
        "fallback_options": fallback_options,
        "rejected_options": rejected + ([o for o in invalid if o not in fallback_options]),
        "rejected_summary": summary,
        "raw_count": len(options),
    }


def find_reroute_options(
    origin: str,
    destination: str,
    departure: str = "",
    original_arrival: str = "",
    current_arrival: str = "",
    max_results: int = 8,
) -> dict:
    """Finds alternative connections (reroute options) between two stations.

    Args:
        origin: Departure station, e.g. "Munich Hbf".
        destination: Destination station, e.g. "Berlin Hbf".
        departure: Earliest boardable departure time (ISO timestamp). En route,
            pair this with the next boardable station as ``origin``.
        original_arrival: Original scheduled arrival, used only for the
            schedule-relative arrival delta.
        current_arrival: Current estimated arrival if the traveler stays on the
            disrupted itinerary. Options arriving later become non-selectable
            fallbacks; options arriving earlier expose minutes saved. Leave
            EMPTY when the itinerary is broken (a transfer already missed,
            ``itinerary_broken`` on the live status): staying aboard has no
            arrival time then, and a phantom baseline would demote every real
            alternative as "slower than doing nothing".
        max_results: Candidate-pool size before deterministic pruning (1..12).

    Returns:
        A dict with the list of possible reroutes including new arrival time,
        number of transfers, added delay, price if available, per-leg ``legs``
        (train, change stations, departure/arrival times — cite the change
        stations and transfer times when presenting an option), and ``source``.
    """
    # Scripted reroutes win over the live sidecar — same rationale as
    # get_live_trip_status: a demo route carries curated fixture options (the
    # happy-path R1 free / R2 via Leipzig) that tell one deterministic story.
    # A live search from the same station would return real, ever-changing
    # trains and silently break the canonical demo. Routes the fixture does NOT
    # curate return nothing here and fall through to the live-first path below,
    # so self-booked / non-demo trips stay live exactly as before.
    curated = mock_data.lookup_route(mock_data.REROUTE_OPTIONS, origin, destination)
    if curated:
        return _mock_reroute_result(origin, destination, curated)

    try:
        preferences = _profile_reroute_preferences()
        max_results = max(1, min(int(max_results), 12))
        from_eva = stations.resolve_eva(origin)
        to_eva = stations.resolve_eva(destination)
        if from_eva is None or to_eva is None:
            raise db_api.DBServiceError(
                f"Station not resolvable (origin={origin!r}, destination={destination!r})."
            )
        query_preferences: dict[str, Any] = {
            "transferTime": preferences["min_transfer_minutes"],
        }
        # Keep the DB candidate pool broader than the transfer preference. The
        # deterministic split below needs to distinguish "no compliant train"
        # from "no train at all" and retain one disabled least-bad fallback.
        payload = db_api.journeys(
            from_eva,
            to_eva,
            departure=departure or None,
            results=max_results,
            tickets=True,
            **query_preferences,
        )
        live_options = []
        for option in db_api.normalize_journeys(payload)[:max_results]:
            arrival = option.get("arrival") or option.get("planned_arrival")
            added_delay = minutes_between(original_arrival, arrival)
            minutes_saved = minutes_between(arrival, current_arrival)
            comfort_parts = []
            if option.get("price_eur") is not None:
                comfort_parts.append(f"Price: {option['price_eur']} EUR")
            if option.get("platform_changes"):
                comfort_parts.append("Platform change reported")
            # Compact per-leg itinerary so the UI (and the Planner's answer)
            # can show the change stations and the transfer time at each stop.
            legs = [
                {
                    "train": leg.get("train"),
                    "origin": leg.get("origin"),
                    "destination": leg.get("destination"),
                    "departure": leg.get("departure") or leg.get("planned_departure"),
                    "arrival": leg.get("arrival") or leg.get("planned_arrival"),
                    "planned_departure": leg.get("planned_departure"),
                    "planned_arrival": leg.get("planned_arrival"),
                    "departure_delay_minutes": leg.get("departure_delay_minutes"),
                    "arrival_delay_minutes": leg.get("arrival_delay_minutes"),
                    "cancelled": bool(leg.get("cancelled")),
                }
                for leg in option.get("legs") or []
                if leg.get("train")
            ]
            live_options.append(
                {
                    "option_id": option.get("option_id"),
                    "_provider_refresh_token": option.get("refresh_token"),
                    "mode": "train",
                    "description": option.get("description"),
                    "origin": origin,
                    "destination": destination,
                    "departure": option.get("departure") or option.get("planned_departure"),
                    "new_arrival": arrival,
                    "transfers": option.get("transfers", 0),
                    "added_delay_minutes": round(added_delay) if added_delay is not None else None,
                    "live_delay_minutes": option.get("arrival_delay_minutes"),
                    "minutes_saved_vs_current_plan": (
                        round(minutes_saved) if minutes_saved is not None else None
                    ),
                    "current_plan_arrival": current_arrival or None,
                    "comfort": "; ".join(comfort_parts) or "Live DB connection",
                    "price_eur": option.get("price_eur"),
                    "added_cost_eur": None,
                    "cost_status": "unknown",
                    "trains": option.get("trains", []),
                    "legs": legs,
                    "cancelled": bool(option.get("cancelled")),
                    "remarks": option.get("remarks", []),
                    "source": "db_service_live",
                }
            )
        if live_options:
            # Deterministic pre-filter: the sidecar returns the next N departures
            # verbatim, so without this the UI floods with the user's own delayed
            # train, later runs of the same line, and slow many-transfer routings.
            pruned = _prune_reroute_options(
                live_options,
                max_transfers=preferences["max_transfers"],
                min_transfer_minutes=preferences["min_transfer_minutes"],
                max_added_delay_minutes=REROUTE_MAX_ADDED_DELAY_MINUTES,
                max_options=REROUTE_MAX_OPTIONS,
                earliest_departure=departure,
                current_arrival=current_arrival,
            )
            logger.info(
                "reroute discovery: route=%s -> %s raw=%d eligible=%d fallback=%d rejected=%s",
                origin,
                destination,
                pruned["raw_count"],
                len(pruned["options"]),
                len(pruned["fallback_options"]),
                pruned["rejected_summary"],
            )
            live_options = pruned["options"]
            fallback_options = pruned["fallback_options"]
            # Renumber after pruning. R1 is the earliest discovery candidate;
            # final recommendation/order is assigned after profile/calendar checks.
            for i, opt in enumerate(live_options, start=1):
                opt["option_id"] = f"R{i}"
            for i, opt in enumerate(fallback_options, start=1):
                opt["option_id"] = f"R{i}"
            _stash_options(
                live_options,
                family="train",
                origin=origin,
                destination=destination,
                source="db_service_live",
                fallback_options=fallback_options,
                rejected_options=pruned["rejected_options"],
                rejected_summary=pruned["rejected_summary"],
            )
            return {
                "origin": origin,
                "destination": destination,
                "options": [_tool_visible_option(option) for option in live_options],
                "fallback_options": [
                    _tool_visible_option(option) for option in fallback_options
                ],
                "rejected_summary": pruned["rejected_summary"],
                "raw_count": pruned["raw_count"],
                "source": "db_service_live",
            }
    except db_api.DBServiceError:
        pass  # sidecar down/blocked — expected, fall back to mock quietly
    except Exception:
        # A bug in the live path (not a sidecar outage) — fall back too, but
        # leave a trace: this once hid a TypeError as "sidecar unavailable".
        logger.warning("find_reroute_options live path failed", exc_info=True)

    options = mock_data.lookup_route(mock_data.REROUTE_OPTIONS, origin, destination)
    if options:
        return _mock_reroute_result(origin, destination, options)
    _stash_options([], family="train", origin=origin, destination=destination, source="none")
    return {
        "origin": origin,
        "destination": destination,
        "options": [],
        "fallback_options": [],
        "source": "none",
        "error": "No reroute options available for this route.",
    }


def _mock_reroute_result(origin: str, destination: str, options: list[dict]) -> dict:
    """Build the reroute payload from curated fixture options (scripted / offline).

    Used both when a route has scripted options (scripted-wins, sidecar up) and
    as the fallback when the live sidecar is unreachable. The curated options
    already carry authored arrival/delay/cost fields, so no pruning or
    schedule-relative recomputation is applied — the fixture is the source of
    truth for these routes.
    """
    mock_options = [
        {
            **option,
            "mode": option.get("mode", "train"),
            "origin": origin,
            "destination": destination,
            "eligible": True,
            "selectable": True,
            "source": "mock_reroute_options",
        }
        for option in options
    ]
    for option in mock_options:
        _apply_cost_contract(option)
    _stash_options(
        mock_options,
        family="train",
        origin=origin,
        destination=destination,
        source="mock_reroute_options",
    )
    return {
        "origin": origin,
        "destination": destination,
        "options": mock_options,
        "source": "mock_reroute_options",
    }


def find_mobility_alternatives(
    location: str,
    destination: str = "",
    leg: str = "last_mile",
    max_results: int = 4,
) -> dict:
    """Finds Flinkster (car sharing) and Call-a-Bike (bike sharing) options near a station.

    Call this ONLY when no train option from ``find_reroute_options`` is viable —
    i.e. all train alternatives miss the hard-constraint deadline, exceed
    ``preferences.max_transfers``, or no train options were returned at all.
    Only call when ``profile.mobility.car_sharing_ok`` or
    ``profile.mobility.bike_sharing_ok`` is True (or the ``mobility`` section is
    absent from the profile, in which case both are assumed True by default).

    Swap point for a future real integration: replace the mock lookups below with
    DB Connect / Flinkster API calls keyed on the station's coordinates.

    Args:
        location: Station or city where the traveler is stranded, e.g. "Munich Hbf".
        destination: Target destination — used for context and drive-time estimates.
        leg: "last_mile" for short local connections, "bridging" for longer legs.
        max_results: Maximum number of alternatives to return in total.

    Returns:
        A dict with ``flinkster`` (car-sharing options, option_id C#) and
        ``callabike`` (bike-sharing options, option_id B#) lists. Each option
        carries: ``mode`` ("car_sharing" / "bike_sharing"), ``option_id``,
        ``description``, ``pickup`` location, ``distance_km``,
        ``est_duration_minutes``, ``new_arrival`` (ISO string or null),
        ``price_eur``, ``remarks``, and ``source`` ("mock_flinkster" /
        "mock_callabike"). Also includes ``source`` on the top-level dict.
    """
    max_results = max(1, min(int(max_results), 8))
    flinkster = [
        {
            **o,
            "origin": location,
            "destination": destination,
            "eligible": True,
            "selectable": True,
            "source": "mock_flinkster",
        }
        for o in mock_data.lookup_location(mock_data.FLINKSTER_OPTIONS, location)[:max_results]
    ]
    callabike = [
        {
            **o,
            "origin": location,
            "destination": destination,
            "eligible": True,
            "selectable": True,
            "source": "mock_callabike",
        }
        for o in mock_data.lookup_location(mock_data.CALLABIKE_OPTIONS, location)[:max_results]
    ]
    all_options: list[dict] = []
    for index in range(max(len(flinkster), len(callabike))):
        for group in (flinkster, callabike):
            if index < len(group) and len(all_options) < max_results:
                all_options.append(group[index])
    kept_ids = {o.get("option_id") for o in all_options}
    flinkster = [o for o in flinkster if o.get("option_id") in kept_ids]
    callabike = [o for o in callabike if o.get("option_id") in kept_ids]
    _stash_options(
        all_options,
        family="mobility",
        origin=location,
        destination=destination,
        source="mock_mobility" if all_options else "none",
    )
    return {
        "location": location,
        "destination": destination,
        "leg": leg,
        "flinkster": flinkster,
        "callabike": callabike,
        "source": "mock_mobility" if all_options else "none",
    }


def find_partner_hotels(
    location: str,
    check_in_date: str,
    max_results: int = 4,
) -> dict:
    """Finds hotel options near a stranded station for an overnight stay.

    Call this ONLY when no same-day option (train or mobility) can get the
    traveler to their destination, AND ``profile.home.hotel_ok`` is True.
    Covers the overnight case — traveler cannot reach the destination today.

    Live-first: real hotels near the station via OpenStreetMap
    (``integrations.hotels``), sorted by distance. Hotel prices are not shown
    because the live source cannot check rates. Falls back to the mock
    partner-hotel list when the live lookup fails or finds nothing.

    Args:
        location: Station or city near which to search — pass where the traveler
            ACTUALLY IS: the origin/start city before departure, the current
            stranded station en route, or the destination only when they can
            still arrive today (just too late), e.g. "Munich Hbf".
        check_in_date: Planned check-in date in "YYYY-MM-DD" format.
        max_results: Maximum number of hotels to return.

    Returns:
        A dict with a ``hotels`` list (option_id H#). Each hotel carries:
        ``mode`` ("hotel"), ``option_id``, ``name``, ``description``,
        ``distance_to_station_km``, ``price_per_night_eur``, ``check_in_date``,
        ``nights``, ``remarks``, and ``source`` ("osm_hotels_live" or
        "mock_hotels").
    """
    outcome = with_resilience(
        lambda: hotels_api.find_hotels_near_station(location, max_results=max_results),
        lambda: [
            {**h, "source": "mock_hotels"}
            for h in mock_data.lookup_location(mock_data.PARTNER_HOTELS, location)[:max_results]
        ],
        tool="find_partner_hotels",
        accept=lambda hs: bool(hs),
    )
    hotels = [
        {
            **h,
            "origin": location,
            "destination": location,
            "check_in_date": check_in_date,
            "eligible": True,
            "selectable": True,
        }
        for h in outcome.value
    ]
    source = hotels[0]["source"] if hotels else "none"
    _stash_options(
        hotels,
        family="hotel",
        origin=location,
        destination=location,
        source=source,
    )
    return {
        "location": location,
        "check_in_date": check_in_date,
        "hotels": hotels,
        "source": source,
    }


def _profile_rank_score(option: dict, *, earliest_arrival: datetime | None, preferences: dict) -> float:
    """Deterministic profile score; lower is better."""
    speed = preferences.get("speed_vs_comfort", 50) / 100.0
    arrival = parse_datetime(option.get("new_arrival"))
    arrival_penalty = 24 * 60.0
    if arrival is not None and earliest_arrival is not None:
        arrival_penalty = max(
            0.0,
            (arrival.replace(tzinfo=None) - earliest_arrival.replace(tzinfo=None)).total_seconds()
            / 60.0,
        )
    elif option.get("mode") == "hotel":
        arrival_penalty = 48 * 60.0

    transfers = float(option.get("transfers") or 0)
    time_weight = 0.5 + speed
    transfer_weight = 45.0 - 35.0 * speed
    cost = _option_cost(option)
    cost_penalty = (cost or 0.0) * 0.05
    buffer = option.get("minimum_transfer_minutes")
    buffer_penalty = 0.0
    if buffer is not None:
        safe_buffer = preferences.get("min_transfer_minutes", 8) + 5
        buffer_penalty = max(0, safe_buffer - buffer) * (2.0 - speed)
    return round(
        arrival_penalty * time_weight
        + transfers * transfer_weight
        + cost_penalty
        + buffer_penalty,
        2,
    )


def _select_final_diverse(options: list[dict], max_options: int) -> list[dict]:
    """Recommended option first, while preserving distinct useful trade-offs."""
    if not options:
        return []
    max_options = max(1, max_options)
    ranked = sorted(options, key=lambda o: (o.get("ranking_score", float("inf")), _reroute_sort_key(o)))
    selected: list[dict] = []

    def add(option: dict | None) -> None:
        if option is not None and option not in selected and len(selected) < max_options:
            selected.append(option)

    add(ranked[0])
    timed = [o for o in ranked if parse_datetime(o.get("new_arrival")) is not None]
    if timed:
        add(min(timed, key=_reroute_sort_key))
    trains = [o for o in ranked if o.get("mode", "train") == "train"]
    if trains:
        add(min(trains, key=lambda o: (o.get("transfers") or 0, _reroute_sort_key(o))))
    priced = [o for o in ranked if _option_cost(o) is not None]
    if priced:
        add(min(priced, key=lambda o: (_option_cost(o), o.get("ranking_score", float("inf")))))
    for option in ranked:
        add(option)
    return selected


def finalize_reroute_options(max_options: int = REROUTE_MAX_OPTIONS) -> dict:
    """Finalize the selectable reroute cards after all discovery/calendar calls.

    This is the only tool allowed to populate the UI-facing ``options`` list.
    It reapplies profile hard limits, ranks by the speed-vs-comfort preference,
    preserves fastest/simple/cheap alternatives, and keeps constraint-breaking
    fallbacks (cancelled, too many transfers, mode disabled, arrives after the
    traveler's own latest-arrival-home time) separate and non-selectable.

    A hard-constraint CALENDAR clash is treated differently: it does not
    disqualify an option (the traveler can still take a train that arrives
    late for one appointment) — merged verdicts instead annotate the option
    with ``calendar_clash`` so the Planner can recommend rescheduling that
    appointment and notifying its contact, while still presenting the option
    as bookable. Hotels are gated purely on whether any reachable option's
    predicted arrival still beats ``home.latest_arrival_home`` — never on a
    calendar clash alone.

    Args:
        max_options: Maximum number of selectable cards across all modes.

    Returns:
        ``options`` (eligible/selectable), ``fallback_options`` (disabled), the
        recommended option id, and a rejection summary. Call this after the
        calendar check and after any mobility/hotel widening.
    """
    stashed = turn_reroute_state()
    if stashed is None:
        return {"error": "No reroute search has run in this turn."}
    candidates = [dict(option) for option in stashed.get("candidate_options") or []]
    if is_calendar_connected() and candidates and not stashed.get("calendar_checked"):
        return {
            "error": (
                "Calendar is connected but the current candidate batch has not been checked. "
                "Call check_options_against_calendar after the last discovery tool, then finalize again."
            )
        }

    profile = get_user_profile()
    if profile.get("error"):
        profile = {
            "preferences": dict(_REROUTE_PREFERENCE_DEFAULTS),
            "home": {},
            "mobility": {"car_sharing_ok": True, "bike_sharing_ok": True},
        }
    # Sanitize/clamp the preferences already fetched above (malformed or
    # out-of-range values) instead of re-reading the profile from the store a
    # second time — do not overwrite `profile` with the raw sanitized blob.
    preferences = _sanitize_reroute_preferences(profile.get("preferences"))
    mobility = profile.get("mobility") or {}
    home = profile.get("home") or {}
    verdicts = stashed.get("calendar_verdicts") or {}
    rejected_summary: dict[str, int] = {}
    fallback_pool: list[dict] = []
    for family_data in stashed.get("families", {}).values():
        fallback_pool.extend(dict(option) for option in family_data.get("fallback_options") or [])
        for reason, count in (family_data.get("rejected_summary") or {}).items():
            rejected_summary[reason] = rejected_summary.get(reason, 0) + int(count)
    eligible: list[dict] = []

    for option in candidates:
        _apply_cost_contract(option)
        reasons = mode_eligibility_violations(
            option,
            preferences=preferences,
            mobility=mobility,
            home=home,
            recompute_transfer_buffer=False,
        )

        # A hard-constraint calendar clash does NOT make the option unbookable —
        # the traveler can still take it, just late for that appointment. Surface
        # the clash (so the Planner can recommend rescheduling + notifying
        # participants) instead of hiding a perfectly reachable option.
        verdict = verdicts.get(option.get("option_id"))
        if verdict and verdict.get("viable") is False:
            option["calendar_clash"] = {
                "hard_conflicts": verdict.get("hard_conflicts"),
                "conflicts": verdict.get("conflicts"),
            }
        if arrives_after_home_limit(option, profile):
            reasons.append("after_latest_arrival_home")

        if reasons:
            fallback_pool.append(
                {**option, "eligible": False, "selectable": False, "constraint_violations": reasons}
            )
            for reason in reasons:
                rejected_summary[reason] = rejected_summary.get(reason, 0) + 1
        else:
            option.update(eligible=True, selectable=True, constraint_violations=[])
            eligible.append(option)

    # Hotels are a genuine last resort: only worth suggesting when nothing that
    # actually reaches the destination gets the traveler home before the
    # profile's latest-arrival-home cutoff — never merely because a reachable
    # option misses a meeting (see calendar_clash above) or exceeds the
    # transfer preference (that's already excluded via constraint_violations).
    limit = str(home.get("latest_arrival_home") or "").strip()
    non_hotel_options = [option for option in eligible if option.get("mode") != "hotel"]
    if re.fullmatch(r"\d{2}:\d{2}", limit):
        known_arrivals = [
            (parse_datetime(option.get("new_arrival")), parse_datetime(option.get("departure")))
            for option in non_hotel_options
            if option.get("new_arrival")
        ]
        hotel_needed = all(
            time_after_home_limit(arrival, departure, limit) for arrival, departure in known_arrivals
        )
    else:
        # No configured cutoff — fall back to the previous rule: any reachable
        # non-hotel option makes hotels redundant.
        hotel_needed = not non_hotel_options

    if not hotel_needed:
        omitted_hotels = sum(option.get("mode") == "hotel" for option in eligible)
        eligible = [option for option in eligible if option.get("mode") != "hotel"]
        if omitted_hotels:
            rejected_summary["hotel_not_needed"] = (
                rejected_summary.get("hotel_not_needed", 0) + omitted_hotels
            )

    arrivals = [parse_datetime(o.get("new_arrival")) for o in eligible]
    earliest_arrival = min(
        (arrival for arrival in arrivals if arrival is not None),
        key=lambda dt: dt.replace(tzinfo=None),
        default=None,
    )
    for option in eligible:
        option["ranking_score"] = _profile_rank_score(
            option, earliest_arrival=earliest_arrival, preferences=preferences
        )

    selected = _select_final_diverse(eligible, min(max(1, int(max_options)), REROUTE_MAX_OPTIONS))
    omitted = len(eligible) - len(selected)
    if omitted:
        rejected_summary["final_shortlist_cap"] = rejected_summary.get("final_shortlist_cap", 0) + omitted
    for rank, option in enumerate(selected, start=1):
        option["rank"] = rank
        option["recommended"] = rank == 1
    recommended_option_id = selected[0].get("option_id") if selected else None

    # Fallbacks are useful only when there is no selectable plan. Never expose
    # more than one, and never let it pass the UI's click gate.
    fallback_options: list[dict] = []
    if not selected and fallback_pool:
        fallback = sorted(fallback_pool, key=_reroute_sort_key)[0]
        fallback_options = [{**fallback, "eligible": False, "selectable": False}]

    stashed["options"] = selected
    stashed["fallback_options"] = fallback_options
    stashed["recommended_option_id"] = recommended_option_id
    stashed["rejected_summary"] = rejected_summary
    stashed["finalized"] = True
    logger.info(
        "reroute finalized: candidates=%d selectable=%d fallback=%d recommended=%s rejected=%s",
        len(candidates),
        len(selected),
        len(fallback_options),
        recommended_option_id,
        rejected_summary,
    )
    return {
        "options": [_tool_visible_option(option) for option in selected],
        "fallback_options": [
            _tool_visible_option(option) for option in fallback_options
        ],
        "recommended_option_id": recommended_option_id,
        "candidate_count": len(candidates),
        "rejected_summary": rejected_summary,
        "source": stashed.get("source"),
    }
