"""Monitoring tools: live trip status and network disruptions.

Live-first via the db_service sidecar, with two documented fallbacks — a
scripted fixture status (which wins outright, so the demo stays deterministic)
and, failing both, the historical forecast for the booked legs. Every path
reports its origin in ``source``, its lifecycle phase in ``status``
(``trip_status``: pre_trip / en_route / arrived) and, crucially, agrees on when
a trip counts as concluded: the ``arrived`` flag is what a compensation claim
is later assessed against.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from ... import risk, trip_status
from ...demo import mock_data
from ...integrations.db import ops as db_api
from ...integrations.db import stations
from ..constraints import parse_datetime


def _find_trip_context(trip_id: str) -> dict | None:
    """Find imported trip metadata for a tool call that only receives trip_id."""
    try:
        from ...persistence import store

        profile = store.any_profile()
        if profile is not None:
            for trip in store.get_trips(profile["user_id"]):
                if trip.get("trip_id") == trip_id:
                    return trip
    except Exception:
        pass

    if mock_data.DEMO_TRIP.get("trip_id") == trip_id:
        return mock_data.DEMO_TRIP
    if trip_id in mock_data.LIVE_TRIP_STATUS:
        return mock_data.DEMO_TRIP
    return None


def _journey_for_trip(trip: dict) -> dict | None:
    """Live search result for exactly the booked itinerary, or ``None``.

    Strict on purpose: matching only by origin/destination (or first train)
    would return a *different* connection — e.g. a later journey via another
    hub — and its delays/transfers would then be presented as the user's trip.
    """
    origin = trip.get("origin")
    destination = trip.get("destination")
    if not origin or not destination:
        return None
    from_eva = stations.resolve_eva(origin)
    to_eva = stations.resolve_eva(destination)
    if from_eva is None or to_eva is None:
        raise db_api.DBServiceError(
            f"Station not resolvable (origin={origin!r}, destination={destination!r})."
        )
    payload = db_api.journeys(
        from_eva,
        to_eva,
        departure=trip.get("planned_departure"),
        results=5,
        tickets=True,
    )
    return db_api.match_booked_journey(trip, db_api.normalize_journeys(payload))


def _trip_position(option: dict) -> str | None:
    for leg in option.get("legs") or []:
        delay = leg.get("arrival_delay_minutes")
        if delay and delay > 0:
            origin = leg.get("origin") or "unknown origin"
            destination = leg.get("destination") or "unknown destination"
            return f"between {origin} and {destination}"
    return None


def _locate_next_leg(option: dict) -> tuple[dict, str, datetime] | None:
    """Find the first not-yet-completed leg and how the traveler relates to it.

    Shared walk used by ``_next_boardable_station`` and
    ``_earliest_reroute_departure`` so the "where/when is the traveler" state
    machine lives in one place. Returns ``(leg, phase, now)`` where ``phase``
    is one of:

    - ``"cancelled"``: the leg never ran (regardless of its scheduled window
      being in the past or future) — the traveler is still at its origin.
    - ``"in_transit"``: the traveler is currently riding this leg.
    - ``"not_started"``: the leg hasn't departed yet — the traveler is still
      at its origin.

    Returns ``None`` if every leg is already completed or none carry a usable
    time. Live times win over planned times.
    """
    for leg in option.get("legs") or []:
        departure = parse_datetime(leg.get("departure") or leg.get("planned_departure"))
        arrival = parse_datetime(leg.get("arrival") or leg.get("planned_arrival"))
        anchor = arrival or departure
        if anchor is None:
            continue
        now = datetime.now(anchor.tzinfo) if anchor.tzinfo else datetime.now()
        # A cancelled leg was never boardable, no matter how far in the past its
        # scheduled arrival now is — the traveler could not have ridden it, so
        # it is always the next unfinished leg once reached in this order
        # (earlier, uncancelled legs are skipped as usual by falling through
        # below once their own arrival/departure are in the past).
        if leg.get("cancelled"):
            return leg, "cancelled", now
        if arrival is not None and arrival >= now:
            if departure is not None and departure <= now:
                return leg, "in_transit", now
            return leg, "not_started", now
        if departure is not None and departure >= now:
            return leg, "not_started", now
    return None


def _next_boardable_station(option: dict) -> str | None:
    """Best station from which an en-route traveler can start a new search.

    If the traveler is currently on a leg, its destination is the next place
    they can change trains. If the next leg has not started (or was
    cancelled), its origin is still boardable.
    """
    located = _locate_next_leg(option)
    if located is None:
        return None
    leg, phase, _now = located
    return leg.get("destination") if phase == "in_transit" else leg.get("origin")


def _has_departed(option: dict) -> bool | None:
    """Whether the traveler has actually started this journey, or ``None``.

    The one thing the timetable cannot answer: a first leg that leaves 20
    minutes late means the trip is still PRE_TRIP well past its scheduled
    departure. Only the FIRST leg decides — once any leg is behind them the
    journey is under way. A first leg whose live departure time has already
    passed while it still has not run (delayed beyond recognition, or
    cancelled) counts as departed: the traveler is standing in a disrupted
    trip, which is the Planner's problem, not a trip that never began.
    """
    legs = option.get("legs") or []
    if not legs:
        return None
    located = _locate_next_leg(option)
    if located is None:
        return True  # every leg is already completed
    leg, phase, now = located
    if leg is not legs[0] or phase == "in_transit":
        return True
    departure = parse_datetime(leg.get("departure") or leg.get("planned_departure"))
    return departure is not None and departure <= now


def _earliest_reroute_departure(option: dict) -> str | None:
    """Live time paired with ``_next_boardable_station`` for a new search."""
    located = _locate_next_leg(option)
    if located is None:
        return None
    leg, phase, now = located
    departure_raw = leg.get("departure") or leg.get("planned_departure")
    if phase == "cancelled":
        # Before its planned start, keep that intended start time; once that
        # time has passed, search from the current time.
        departure = parse_datetime(departure_raw)
        if departure is not None and departure > now:
            return departure_raw
        return now.replace(microsecond=0).isoformat()
    if phase == "in_transit":
        return leg.get("arrival") or leg.get("planned_arrival")
    return departure_raw


def _region_anchor(region: str) -> str | None:
    anchors = {
        "bavaria": "Munich Hbf",
        "bayern": "Munich Hbf",
        "berlin": "Berlin Hbf",
        "nrw": "Cologne Hbf",
        "north rhine-westphalia": "Cologne Hbf",
        "hessen": "Frankfurt (Main) Hbf",
        "hamburg": "Hamburg Hbf",
    }
    return anchors.get(region.lower())


def _board_warnings(board: Any) -> list[dict]:
    entries = []
    if isinstance(board, dict):
        entries = board.get("departures") or board.get("arrivals") or []
    elif isinstance(board, list):
        entries = board

    disruptions: list[dict] = []
    seen: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        line = (entry.get("line") or {}).get("name") or entry.get("direction") or "DB"
        for remark in entry.get("remarks") or []:
            if not isinstance(remark, dict):
                continue
            if remark.get("type") not in ("status", "warning"):
                continue
            text = (remark.get("summary") or remark.get("text") or "").strip()
            if not text or text in seen:
                continue
            seen.add(text)
            disruptions.append(
                {
                    "line": line,
                    "type": text,
                    "severity": "unknown",
                    "expected_resolution": None,
                }
            )
    return disruptions[:5]


def _arrival_in_past(arrival_iso: str | None) -> bool:
    """True if the (estimated) arrival lies in the past — i.e. the trip is over.

    The live DB feed has no explicit "arrived" flag (unlike the mock fixture,
    where scripts/simulate_arrival.py sets one), so it is derived from the
    arrival timestamp. Complaint drafting and the Monitoring agent's
    EN ROUTE/ARRIVED status both depend on this field being present.
    """
    arrival = parse_datetime(arrival_iso)
    if arrival is None:
        return False
    # Offset-aware timestamps (the live sidecar's format, including a trailing
    # "Z") compare as instants; genuinely naive ones are German-local wall clock
    # (see minutes_between) and compare against the local clock.
    now = datetime.now(arrival.tzinfo) if arrival.tzinfo else datetime.now()
    return arrival <= now


# How long after the planned arrival a trip counts as certainly over when no
# live arrival time can confirm it. Generous on purpose: it has to outlast even
# a heavy delay, because concluding too early is the expensive mistake (it lets
# a claim be assessed against a delay that was not yet final). Defined in
# ``trip_status`` because the UI's schedule-only fallback needs the very same
# cut-off — two constants would let the badge and this flag disagree.
_CONCLUDED_MARGIN = trip_status.CONCLUDED_MARGIN


def _concluded_without_arrival_time(planned_arrival: str | None) -> bool:
    """True when the planned arrival lies far enough in the past to be over.

    The fallback for the two cases that have no usable arrival instant: no live
    data at all, and a broken itinerary (whose booked arrival can no longer
    happen, so ``planned_arrival + current_delay`` would be fiction). Both
    report ARRIVED with an UNKNOWN final delay rather than inventing one.
    """
    arrival = parse_datetime(planned_arrival)
    return arrival is not None and _arrival_in_past(
        (arrival + _CONCLUDED_MARGIN).isoformat()
    )


# Why a concluded trip with a broken itinerary reports no final delay. Shared by
# the live and the scripted path so both explain it the same way.
_BROKEN_ITINERARY_CONCLUDED_NOTE = (
    "A transfer on this itinerary was missed, so the booked connection was "
    "never completed and its planned arrival says nothing about when the "
    "traveler got there. The trip is over, but the actually experienced final "
    "delay is UNKNOWN — it cannot be derived from the delay of the leg that "
    "caused the missed transfer, and a compensation claim cannot be assessed "
    "from this result."
)


def _overall_level(forecasts: list[dict]) -> str:
    """Worst per-leg forecast level as the trip's risk band (LOW/MEDIUM/HIGH).

    Ranked explicitly — max() on the raw strings would sort alphabetically
    ("low" > "high"), inverting the result.
    """
    rank = {"low": 0, "medium": 1, "high": 2}
    if not forecasts:
        return "LOW"
    worst = max(
        (f.get("level", "low") for f in forecasts),
        key=lambda lvl: rank.get(lvl, 0),
    )
    return worst.upper()


def _booked_risk_legs(trip: dict) -> list[dict]:
    """The booked trip's own legs in the risk module's shape (no live delay).

    Used when live data for the exact booked connection is unavailable, so the
    forecast still describes the user's actual itinerary instead of nothing.
    """
    booked = trip.get("legs") or []
    if not booked:
        if not (trip.get("origin") and trip.get("destination")):
            return []
        booked = [
            {
                "train": trip.get("train"),
                "origin": trip.get("origin"),
                "destination": trip.get("destination"),
                "planned_departure": trip.get("planned_departure"),
                "planned_arrival": trip.get("planned_arrival"),
            }
        ]
    return [
        {
            "train": leg.get("train"),
            "origin": {"name": leg.get("origin"), "planned": leg.get("planned_departure")},
            "destination": {"name": leg.get("destination"), "planned": leg.get("planned_arrival")},
            "current_delay_minutes": 0,
        }
        for leg in booked
    ]


def get_live_trip_status(trip_id: str) -> dict:
    """Returns the current live status of a train journey with risk forecasts.

    Args:
        trip_id: The trip ID, e.g. "DB-2026-0603-MUC-BLN".

    Returns:
        A dict with current delay, trend, position, known incidents,
        connection risk, risk forecasts, the trip's lifecycle ``status``
        (pre_trip / en_route / arrived) and ``source``. When ``source`` is
        ``db_history_forecast`` no live data was available for the exact booked
        connection — the numbers are the historical forecast for the booked
        legs and ``current_delay_minutes`` is null (see ``note``). Contains
        "error" only if the trip is entirely unknown.
    """
    trip = _find_trip_context(trip_id)

    # A scripted status in the fixture wins over the live search: the demo
    # trip's dates are rebased to "today", so a live lookup could otherwise
    # find the real train and silently replace the scripted disruption —
    # making the canonical demo non-deterministic. Trips without a scripted
    # status (e.g. self-booked BK-… connections) stay live-first.
    scripted = mock_data.LIVE_TRIP_STATUS.get(trip_id)

    if trip is not None and scripted is None:
        try:
            option = _journey_for_trip(trip)
            if option:
                delay = option.get("arrival_delay_minutes")
                if delay is None:
                    delay = option.get("departure_delay_minutes") or 0
                delay_int = round(delay)

                # Adapt the sidecar's flat legs (origin/destination are plain
                # strings, times in separate keys) into the shape the risk module
                # expects: origin/destination as dicts with ``name`` and
                # ``planned``, plus the leg's own live arrival delay. Passing the
                # flat legs directly would crash ``connection_risks`` (it reads
                # ``leg["destination"]["planned"]``).
                risk_legs: list[dict] = []
                for leg in option.get("legs") or []:
                    leg_delay = leg.get("arrival_delay_minutes")
                    if leg_delay is None:
                        leg_delay = leg.get("departure_delay_minutes") or 0
                    risk_legs.append(
                        {
                            "train": leg.get("train"),
                            "origin": {"name": leg.get("origin"), "planned": leg.get("planned_departure")},
                            "destination": {"name": leg.get("destination"), "planned": leg.get("planned_arrival")},
                            "current_delay_minutes": round(leg_delay or 0),
                        }
                    )

                # Historical forecast + transfer-miss warnings from the risk module.
                forecasts = risk.forecast_trip(trip, risk_legs)
                for leg, forecast in zip(risk_legs, forecasts):
                    leg["forecast"] = forecast
                connection_warnings = risk.connection_risks(risk_legs)
                # A definitively missed transfer breaks the itinerary: there is
                # no "stay aboard" arrival anymore, so the journey's reported
                # arrival (which assumes every transfer is still made) must not
                # be handed on as an ETA — downstream it would become the
                # baseline that rejects every real reroute as slower.
                missed_transfers = risk.missed_connections(risk_legs)
                itinerary_broken = bool(missed_transfers)
                risk_level = "HIGH" if itinerary_broken else _overall_level(forecasts)

                # Same reasoning as ``estimated_arrival`` just above, applied to
                # the arrival *check*: with the itinerary broken, the journey's
                # own arrival assumes the missed transfer was still made, so its
                # passing does not mean the traveler got there — and `delay_int`
                # is the leg delay that CAUSED the miss, not the arrival delay
                # they end up with. Concluding on that pair would publish a
                # "confirmed final delay" well below the real one, which is the
                # figure a compensation claim gets assessed against.
                if itinerary_broken:
                    arrived = _concluded_without_arrival_time(
                        option.get("planned_arrival") or trip.get("planned_arrival")
                    )
                    final_delay = None if arrived else delay_int
                    concluded_note = _BROKEN_ITINERARY_CONCLUDED_NOTE if arrived else None
                else:
                    # Only the *estimated* arrival confirms the trip is over; a
                    # scheduled planned_arrival passing while a train is delayed
                    # must NOT read as "arrived" (would draft a premature claim).
                    arrived = _arrival_in_past(option.get("arrival"))
                    final_delay = delay_int
                    concluded_note = None

                incidents = [
                    {"type": text, "location": trip.get("destination"), "impact": "DB live remark"}
                    for text in option.get("remarks") or []
                ]
                if option.get("platform_changes"):
                    incidents.extend(
                        {
                            "type": "Platform change",
                            "location": change.get("train"),
                            "impact": (
                                f"Platform {change.get('planned_platform')} -> "
                                f"{change.get('platform')}"
                            ),
                        }
                        for change in option["platform_changes"]
                    )

                planned_departure = option.get("planned_departure") or trip.get("planned_departure")
                planned_arrival = option.get("planned_arrival") or trip.get("planned_arrival")
                # The live legs are the only source precise enough to separate
                # "not boarded yet" from "under way" — the schedule cannot.
                phase = trip_status.from_live_status(
                    {
                        "arrived": arrived,
                        "planned_departure": planned_departure,
                        "planned_arrival": planned_arrival,
                    },
                    departed=_has_departed(option),
                )

                return {
                    "trip_id": trip_id,
                    "train": option.get("train") or trip.get("train"),
                    "current_delay_minutes": final_delay,
                    "trend": "unknown",
                    "current_position": _trip_position(option),
                    "next_boardable_station": _next_boardable_station(option),
                    "earliest_reroute_departure": _earliest_reroute_departure(option),
                    "incidents": incidents,
                    "connection_risk": " ".join(missed_transfers + connection_warnings) or (
                        "Arrival delay may affect onward plans."
                        if delay_int >= 15
                        else "No elevated connection risk visible from DB live data."
                    ),
                    "risk_level": risk_level,
                    "forecasts": forecasts,
                    "legs": risk_legs,
                    "planned_departure": planned_departure,
                    "planned_arrival": planned_arrival,
                    "status": phase,
                    "itinerary_broken": itinerary_broken,
                    "estimated_arrival": (
                        None if itinerary_broken
                        else option.get("arrival") or option.get("planned_arrival")
                    ),
                    "arrived": arrived,
                    "note": concluded_note,
                    "platform_changes": option.get("platform_changes", []),
                    "data_timestamp": datetime.now(timezone.utc).isoformat(),
                    "source": "db_service_live",
                }
        except db_api.DBServiceError:
            pass
        except Exception:
            pass

    if scripted is not None:
        result = {**scripted, "source": "mock_live_status"}
        # The fixture's en-route state carries neither the planned times nor a
        # risk band — fill both so the agent has a real ETA anchor (planned
        # arrival + delay) instead of guessing, and a deterministic band.
        if trip is not None:
            result.setdefault("planned_departure", trip.get("planned_departure"))
            result.setdefault("planned_arrival", trip.get("planned_arrival"))
        result.setdefault("next_boardable_station", None)
        result.setdefault("earliest_reroute_departure", None)
        result.setdefault("itinerary_broken", False)
        delay = result.get("current_delay_minutes") or 0
        result.setdefault(
            "risk_level",
            "HIGH" if result["itinerary_broken"] or delay >= 30
            else "MEDIUM" if delay >= 10 else "LOW",
        )
        if result["itinerary_broken"]:
            # A scripted missed transfer means the booked itinerary cannot be
            # completed — there is no stay-aboard ETA to synthesize, and a
            # fixture-supplied one would poison the reroute baseline.
            result["estimated_arrival"] = None
        else:
            planned_eta = parse_datetime(result.get("planned_arrival"))
            if planned_eta is not None:
                result.setdefault(
                    "estimated_arrival", (planned_eta + timedelta(minutes=delay)).isoformat()
                )
        if "arrived" not in result:
            # An explicit ``arrived`` in the fixture (scripts/simulate_arrival.py)
            # wins; otherwise derive it, because a scripted en-route status must
            # not keep a long-finished trip "running" forever.
            if not result["itinerary_broken"]:
                # Intact itinerary: the delayed ETA (planned arrival + current
                # delay, both German-local wall clock) is a real instant. Once
                # it has passed, the delay is the final one.
                eta = parse_datetime(result.get("planned_arrival"))
                if eta is not None:
                    result["arrived"] = _arrival_in_past(
                        (eta + timedelta(minutes=delay)).isoformat()
                    )
            elif _concluded_without_arrival_time(result.get("planned_arrival")):
                # Broken itinerary: `current_delay_minutes` is the delay of the
                # leg that CAUSED the missed transfer, not the arrival delay the
                # traveler ends up with — they still need a later connection.
                # Treating planned_arrival + that figure as the arrival instant
                # would report a "confirmed final delay" far below the real one,
                # which is precisely what a compensation claim gets assessed
                # against. So the trip concludes on the margin instead, and the
                # final delay goes back to UNKNOWN.
                result["arrived"] = True
                result["current_delay_minutes"] = None
                result["note"] = _BROKEN_ITINERARY_CONCLUDED_NOTE
        # Complaint drafting and the agent's EN ROUTE/ARRIVED status both read
        # this field, so it is never absent — only ever explicitly false.
        result.setdefault("arrived", False)
        # No live legs in a scripted status, so the phase falls back to the
        # fixture's own arrived flag over the (rebased) planned times.
        result["status"] = trip_status.from_live_status(result)
        return result

    # Trip is known but the exact booked connection had no live match (and no
    # simulated status exists): answer from the booked legs' historical
    # forecast instead of erroring — but clearly flagged, never as live data.
    if trip is not None:
        risk_legs = _booked_risk_legs(trip)
        if risk_legs:
            forecasts = risk.forecast_trip(trip, risk_legs)
            for leg, forecast in zip(risk_legs, forecasts):
                leg["forecast"] = forecast
            connection_warnings = risk.connection_risks(risk_legs)
            # No live data exists for this trip. If its planned arrival lies
            # comfortably in the past, the trip is over — report ARRIVED so
            # past trips are never monitored/rerouted as if they were still
            # running. The final delay stays unknown (None): a compensation
            # claim needs a confirmed delay, which this path cannot provide.
            long_past = _concluded_without_arrival_time(trip.get("planned_arrival"))
            note = (
                "Live status for the exact booked connection is currently "
                "unavailable; this assessment is the historical forecast for "
                "the booked legs only."
            )
            if long_past:
                note = (
                    "This trip's planned arrival lies well in the past — the "
                    "trip has concluded. No live data is available anymore, so "
                    "the actually experienced final delay is UNKNOWN (a "
                    "compensation claim cannot be assessed from this result)."
                )
            return {
                "trip_id": trip_id,
                "train": trip.get("train"),
                "current_delay_minutes": None,
                "trend": "unknown",
                "current_position": None,
                "next_boardable_station": None,
                "earliest_reroute_departure": None,
                "incidents": [],
                "connection_risk": " ".join(connection_warnings)
                or "No live data — no connection risk visible from the historical forecast.",
                "risk_level": "LOW" if long_past else _overall_level(forecasts),
                "forecasts": forecasts,
                "legs": risk_legs,
                "planned_departure": trip.get("planned_departure"),
                "planned_arrival": trip.get("planned_arrival"),
                "arrived": long_past,
                "status": trip_status.from_live_status({**trip, "arrived": long_past}),
                "note": note,
                "data_timestamp": datetime.now(timezone.utc).isoformat(),
                "source": "db_history_forecast",
            }

    return {"trip_id": trip_id, "source": "none", "error": f"No trip data found for trip_id '{trip_id}'."}


def get_network_disruptions(region: str) -> dict:
    """Returns the current network-wide disruption status for a region.

    Args:
        region: Region in lowercase, e.g. "bavaria" or "berlin".

    Returns:
        A dict with the list of active disruptions for the region and ``source``.
    """
    anchor = _region_anchor(region)
    if anchor:
        try:
            eva = stations.resolve_eva(anchor)
            if eva:
                disruptions = _board_warnings(db_api.departures(eva, duration=60, results=80))
                if disruptions:
                    return {
                        "region": region,
                        "anchor_station": anchor,
                        "disruptions": disruptions,
                        "source": "db_service_live",
                    }
        except db_api.DBServiceError:
            pass
        except Exception:
            pass

    disruptions = mock_data.NETWORK_DISRUPTIONS.get(region.lower(), [])
    return {"region": region, "disruptions": disruptions, "source": "mock_region"}
