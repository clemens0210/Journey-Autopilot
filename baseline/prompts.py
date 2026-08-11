"""Data-fed prompts for the naive baseline arm.

The baseline answers the question "what does one model call achieve, given the
same facts, without orchestration?" — so it has to *get* the same facts. The
bare prompt in ``single_shot.py`` describes the situation in a sentence and
supplies no data at all; beating that would prove only that tools beat no
tools. Here every fixture slice the agents' read tools would have returned is
serialized into the prompt as JSON, and the model is asked for the same
deliverable the Orchestrator produces.

What the baseline still cannot do is the point of the comparison: it cannot
gate a write behind the traveler's approval, cannot disclose which of these
figures came from a simulated source, and cannot look up whether the delay
lifted the ticket's Zugbindung — it can only assert. Those are structural, and
the evaluation reports them separately from answer quality.

**Read the shifted fixture, never the raw JSON.** ``demo.mock_data`` rebases
the authored day onto today and shifts every timestamp so the trip departed
``JA_DEMO_TRIP_LEAD_MIN`` minutes ago. Building the prompt from the file on
disk would hand the baseline a different clock than the agents run on, and the
deadline check — the single most important thing being scored — would be
comparing two different questions.
"""

from __future__ import annotations

import json
from typing import Any

# Imported lazily inside the builder: mock_data anchors its clock at import
# time, so it must not be pulled in before JA_FIXTURES is set for this run.

_INSTRUCTION = """You are helping a business traveller whose train journey is disrupted.

Below is everything known about the situation: the booked trip, its live status,
network disruptions, alternative connections, the traveller's calendar, car and
bike sharing offers, nearby partner hotels, the applicable passenger-rights
rules, and recent punctuality statistics for this route.

Assess the situation and tell the traveller what to do. Specifically:
1. Is the booked trip at risk, and will the traveller make their appointments?
2. If the trip is at risk, which concrete alternative do you recommend, and why?
3. What should be booked or changed, and what is the traveller entitled to?

Base every statement strictly on the data below. Do not invent trains, times,
prices, or connections that do not appear in it.

=== SITUATION DATA ===
{payload}
=== END SITUATION DATA ===

Give the traveller your assessment and recommendation."""


def _jsonable(node: Any) -> Any:
    """Make the fixture JSON-serializable.

    ``mock_data._by_route`` keys its lookups by ``(origin, destination)``
    tuples, which ``json.dumps`` rejects. Rendered as ``"Origin -> Dest"`` so
    the route stays readable to the model rather than becoming an opaque index.
    """
    if isinstance(node, dict):
        return {
            (" -> ".join(map(str, k)) if isinstance(k, tuple) else str(k)): _jsonable(v)
            for k, v in node.items()
        }
    if isinstance(node, (list, tuple)):
        return [_jsonable(item) for item in node]
    return node


def _payload() -> dict[str, Any]:
    """The fixture slices the read tools would have returned, post-shift."""
    from journey_autopilot.demo import mock_data as md

    payload: dict[str, Any] = {
        "booked_trip": md.DEMO_TRIP,
        "live_trip_status": md.LIVE_TRIP_STATUS,
        "network_disruptions": md.NETWORK_DISRUPTIONS,
        "alternative_connections": md.REROUTE_OPTIONS,
        "planned_connections": md.PLANNED_CONNECTIONS,
        "recent_delay_history": md.CONNECTION_DELAY_HISTORY,
        "traveller_calendar": md.USER_CALENDAR,
        "passenger_rights_rules": md.PASSENGER_RIGHTS,
        "car_sharing_offers": md.FLINKSTER_OPTIONS,
        "bike_sharing_offers": md.CALLABIKE_OPTIONS,
        "partner_hotels": md.PARTNER_HOTELS,
    }
    # The Planner ranks options against the onboarded profile, so the baseline
    # gets it too where one exists. Absent on a fresh database — the comparison
    # stays fair either way because neither arm then has it.
    try:
        from journey_autopilot.persistence import store

        if profile := store.any_profile():
            payload["traveller_profile"] = profile
    except Exception:
        pass
    return payload


def build_prompt() -> str:
    """The data-fed baseline prompt for whichever fixture ``JA_FIXTURES`` selects.

    One builder rather than three hand-written prompts: the scenarios differ in
    their *data*, not in what the traveller is asking for, so writing the
    question three times would only introduce wording differences that confound
    the comparison.
    """
    payload = json.dumps(_jsonable(_payload()), indent=2, ensure_ascii=False)
    return _INSTRUCTION.format(payload=payload)


if __name__ == "__main__":
    print(build_prompt())
