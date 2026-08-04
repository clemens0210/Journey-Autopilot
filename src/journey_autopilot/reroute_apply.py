"""Turning a chosen reroute option back into the traveler's booked trip.

Taking a reroute does not create a second journey — it changes the one the
traveler is already on. So the chosen option is *spliced* into the stored trip
rather than replacing it: the legs already behind the traveler stay, everything
from the boarding station onwards becomes the option's itinerary. The trip keeps
its ``trip_id``, which is what keeps the chat conversation, the reroute
proposals and the complaint de-duplication attached to it.

The splice point is found by station name, not by time. A leg whose scheduled
arrival lies before the option's departure has *not* necessarily been travelled:
in the canonical disruption the Nuremberg transfer is missed, so the booked
Nuremberg->Erfurt leg never ran even though its times are in the past. Only "the
leg that arrives where the new option departs" identifies the real cut.

Deliberately pure and dependency-free — no I/O, no store, no clock beyond the
one stamp: ``tools.write_tools`` calls it inside the Executor's booking action
and persists the result itself.
"""

from __future__ import annotations

from datetime import datetime


def _norm(name: str | None) -> str:
    """Case/whitespace-insensitive station key, or "" for a missing name."""
    return " ".join((name or "").strip().lower().split())


def _same_station(a: str | None, b: str | None) -> bool:
    """Whether two leg endpoints name the same station.

    A missing name never matches: an itinerary splice must not pair up two legs
    just because both lack a station. Both sides of every real comparison come
    from the same source (hand-authored fixture legs against fixture options,
    sidecar legs against sidecar options), so plain normalization is enough —
    the German/English alias table in ``demo.mock_data`` is only needed where an
    LLM-supplied station name reaches a lookup, which is not the case here.
    """
    if not a or not b:
        return False
    return _norm(a) == _norm(b)


def _option_legs(option: dict) -> list[dict]:
    """The option's itinerary in the stored-trip leg shape.

    Reroute options carry live times in ``departure``/``arrival`` and scheduled
    ones in ``planned_*``; stored trips only ever speak ``planned_*``. An option
    without usable legs (a curated single-hop, a mobility option) still yields
    one leg synthesized from the option itself, so the trip is never left
    without an itinerary.
    """
    legs: list[dict] = []
    for leg in option.get("legs") or []:
        train = leg.get("train")
        if not train:
            continue
        legs.append(
            {
                "train": train,
                "origin": leg.get("origin"),
                "destination": leg.get("destination"),
                # Prefer the scheduled time: it is what a booked trip records,
                # and what trip_status/the UI compare against.
                "planned_departure": leg.get("planned_departure") or leg.get("departure"),
                "planned_arrival": leg.get("planned_arrival") or leg.get("arrival"),
                "platform": leg.get("platform"),
                "arrival_platform": leg.get("arrival_platform"),
            }
        )
    if legs:
        return legs

    trains = option.get("trains") or []
    return [
        {
            "train": trains[0] if trains else (option.get("description") or "Connection"),
            "origin": option.get("origin"),
            "destination": option.get("destination"),
            "planned_departure": option.get("departure"),
            "planned_arrival": option.get("new_arrival"),
            "platform": None,
            "arrival_platform": None,
        }
    ]


def _legs_before_boarding(trip_legs: list[dict], boarding: str | None) -> list[dict]:
    """The already-travelled prefix: up to and including the leg that arrives at
    ``boarding``. Empty when the traveler boards the reroute at the trip's own
    origin, or when no leg arrives there (nothing is known to be behind them).
    """
    if not boarding:
        return []
    for index, leg in enumerate(trip_legs):
        if _same_station(leg.get("destination"), boarding):
            return [dict(leg) for leg in trip_legs[: index + 1]]
    return []


def apply_reroute(
    trip: dict,
    option: dict,
    *,
    proposal_id: str | None = None,
    now: datetime | None = None,
) -> dict:
    """Return ``trip`` updated so ``option`` is the itinerary it is monitored on.

    Intended for train reroutes (``mode == "train"``); the caller decides which
    modes may rewrite an itinerary at all — a car-sharing last mile is not a
    replacement train journey and must not overwrite one.

    What is preserved: ``trip_id``, ``order_number``, ``travel_class``,
    ``price_eur`` and ``purpose`` — the booking identity and the fare the
    passenger-rights calculation runs on, none of which a reroute changes.

    What is dropped: ``coach``/``seat``. The reservation was for the original
    train and does not carry over; showing it against the new one would state a
    seat the traveler does not have.

    Returns a new dict — the input trip is not mutated.
    """
    trip_legs = [leg for leg in (trip.get("legs") or []) if isinstance(leg, dict)]
    new_legs = _option_legs(option)
    boarding = new_legs[0].get("origin") or option.get("origin")
    kept = _legs_before_boarding(trip_legs, boarding)
    legs = kept + new_legs

    first, last = legs[0], legs[-1]
    trains = [leg["train"] for leg in legs if leg.get("train")]
    stamp = (now or datetime.now()).replace(microsecond=0).isoformat()

    updated = {
        **trip,
        # With a kept prefix these stay the trip's own origin/departure, so the
        # journey remains EN_ROUTE rather than resetting to PRE_TRIP at the
        # reroute's departure time. Without one, the trip legitimately starts at
        # the boarding station.
        "origin": first.get("origin") or trip.get("origin"),
        "destination": last.get("destination") or option.get("destination") or trip.get("destination"),
        "planned_departure": first.get("planned_departure") or trip.get("planned_departure"),
        "planned_arrival": option.get("new_arrival") or last.get("planned_arrival"),
        "train": first.get("train") or trip.get("train"),
        "trains": trains,
        "legs": legs,
        # The platform of the leg the traveler actually boards next. Unknown for
        # a curated/mobility option, and then better absent than stale.
        "platform": trip.get("platform") if kept else _platform_label(new_legs[0]),
        "coach": None,
        "seat": None,
        "rerouted_at": stamp,
        "rerouted_from": {
            "option_id": option.get("option_id"),
            "proposal_id": proposal_id,
            "boarding_station": boarding,
            "previous_train": trip.get("train"),
            "previous_trains": list(trip.get("trains") or ([trip["train"]] if trip.get("train") else [])),
            "previous_planned_arrival": trip.get("planned_arrival"),
            "kept_legs": len(kept),
        },
    }
    return updated


def _platform_label(leg: dict) -> str | None:
    """Render a leg's platform the way stored trips spell it ("Platform 7")."""
    platform = leg.get("platform")
    if platform in (None, ""):
        return None
    text = str(platform)
    return text if text.lower().startswith("platform") else f"Platform {text}"
