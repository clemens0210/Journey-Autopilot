"""Passenger rights — the READ half: what the traveler is entitled to.

Filing a claim is a separate, policy-gated Executor action
(``write_tools.file_compensation_claim``); nothing here writes anything.

Two sources feed the answer, deliberately kept apart: the entitlements and the
compensation amount are computed deterministically in Python
(``integrations.rights_rag.rights_service``), while the RAG store only supplies
the bahn.de context chunks that explain them. The LLM never derives the figure.
"""

from __future__ import annotations

import threading

from ...integrations.rights_rag.rights_service import (
    calculate_compensation,
    evaluate_travel_rights,
)
from ...request_context import turn_workspace

# get_passenger_rights is a Planner tool, and the Planner is only reachable
# through an AgentTool (the orchestrator calls it as a nested agent). ADK's
# AgentTool runs the wrapped agent in its own internal Runner and forwards
# only the agent's final merged *text* to the parent — the Planner's own
# tool-call results (get_passenger_rights included) never reach the
# top-level event stream that ui.chat iterates. Scanning that trace for a
# "get_passenger_rights" entry therefore never matches.
#
# Workaround (the same turn workspace the reroute shortlist uses): the tool
# stashes its result there while it runs, and the caller reads it after the run
# instead of scanning the trace. The workspace is bound per chat turn, so a
# result can neither survive into the next turn nor leak into a concurrent one.


def turn_rights_result() -> dict | None:
    """Returns this turn's settled rights result, or None.

    Only a ``trip_concluded=True`` lookup lands here. A mid-trip entitlement
    check (Zugbindung and friends) deliberately does not, so neither the
    automatic complaint draft nor the Executor's claim can ever be built from a
    delay the traveler has not actually experienced.
    """
    return turn_workspace().get("rights")


def _legal_context(delay_minutes: int, ticket_type: str, bahncard_type: str) -> tuple[str, list[str]]:
    """RAG context chunks plus the bahn.de pages they came from."""
    try:
        rag = _get_or_build_rag()
        chunks = rag.retrieve_for_case(
            delay_minutes=delay_minutes,
            ticket_type=ticket_type,
            bahncard_type=bahncard_type,
        )
        return (
            "\n\n--- Next Section ---\n".join(c["text"] for c in chunks),
            sorted({c["source"] for c in chunks if c.get("source")}),
        )
    except Exception:
        return "Knowledge base temporarily unavailable.", []


def get_passenger_rights(
    delay_minutes: int,
    ticket_type: str = "einzelticket",
    price_paid: float = 0.0,
    travel_class: int = 2,
    bahncard_type: str = "keine",
    trip_concluded: bool = False,
    last_train_of_day: bool = False,
) -> dict:
    """Looks up the traveler's passenger rights for a delay. Files nothing.

    This is the READ half of passenger rights — it tells the traveler what they
    are entitled to. Actually filing a compensation claim is a separate,
    policy-gated Executor action (``file_compensation_claim``); this tool never
    files anything and never needs approval.

    What it returns depends on where the trip is:

    - **Trip still running** (``trip_concluded=False``, the default): the
      entitlements that govern the reroute decision — above all whether the
      ticket's Zugbindung (train binding) has been lifted, so the existing
      ticket is valid on another connection. NO compensation amount: the delay
      is still a forecast, and a forecast cannot be settled.
    - **Trip concluded** (``trip_concluded=True``, a confirmed final delay):
      additionally the exact compensation the traveler can claim, computed
      deterministically from the fare. Only this branch produces the figure the
      Executor later files a claim with.

    Args:
        delay_minutes:  Arrival delay at the destination in minutes — expected
                        while running, confirmed/final once concluded.
        ticket_type:    "einzelticket" | "sparpreis" | "super_sparpreis" |
                        "zeitkarte_fv" | "zeitkarte_nv" | "bc100" |
                        "deutschland_ticket".
        price_paid:     Ticket price paid in EUR (relevant for single tickets).
        travel_class:   Travel class, 1 or 2 (default: 2).
        bahncard_type:  User's BahnCard: "keine" | "bc25" | "bc50" | "bc100".
        trip_concluded: True ONLY when the trip is over and the delay above is
                        the confirmed final one. Leave False while en route.
        last_train_of_day: True when the delayed connection is the last
                        scheduled one of the day (widens taxi/hotel cover).

    Returns:
        Dict with ``entitlements`` (train binding, refund, taxi/hotel cover),
        ``legal_context`` chunks, the bahn.de ``legal_sources`` they came from,
        and — only when ``trip_concluded`` — the ``compensation`` block.
    """
    entitlements = evaluate_travel_rights(
        delay_minutes=delay_minutes,
        ticket_type=ticket_type,
        last_train_of_day=last_train_of_day,
    )
    legal_context, legal_sources = _legal_context(delay_minutes, ticket_type, bahncard_type)

    result: dict = {
        "delay_minutes": delay_minutes,
        "trip_concluded": trip_concluded,
        "entitlements": entitlements,
        "legal_context": legal_context,
        "legal_sources": legal_sources,
    }

    if not trip_concluded:
        result["compensation"] = {
            "assessable": False,
            "note": (
                "The trip is still running, so this delay is a forecast — a "
                "compensation claim can only be assessed once the trip has "
                "actually concluded. Do not quote an amount."
            ),
        }
        return result

    # Deterministic calculation — no LLM, no network. Flattened into the result
    # as well as nested, because the complaint builder reads the flat keys.
    compensation = calculate_compensation(
        delay_minutes=delay_minutes,
        ticket_type=ticket_type,
        price_paid=price_paid,
        travel_class=travel_class,
        bahncard_type=bahncard_type,
    )
    result["compensation"] = {"assessable": True, **compensation}
    result.update(compensation)

    # Only a concluded trip may seed the automatic complaint draft; stashing a
    # mid-trip forecast here would let the app file a claim for a delay the
    # traveler has not experienced yet (see onboarding/complaints.py).
    turn_workspace()["rights"] = result
    return result


# Constructing FahrgastrechteRAG imports sentence_transformers + torch, which
# takes ~20-40s the first time in a fresh process — almost entirely Python
# import overhead (proven by timing it directly), not model download or
# inference. Left to happen on the first real get_passenger_rights() call,
# that cost lands squarely in the middle of a chat turn. Instead, kick it off
# in the background as soon as this module loads (i.e. as soon as a chat turn
# starts building the agent graph) so it runs concurrently with the ReAct
# loop's own LLM latency — by the time the Planner actually calls
# get_passenger_rights, the model is very likely already warm.
_RAG_LOCK = threading.Lock()


def _get_or_build_rag():
    """Returns the cached FahrgastrechteRAG singleton, building it if needed."""
    rag = getattr(get_passenger_rights, "_rag", None)
    if rag is not None:
        return rag
    with _RAG_LOCK:
        rag = getattr(get_passenger_rights, "_rag", None)
        if rag is None:
            from ...integrations.rights_rag.rag_store import FahrgastrechteRAG

            rag = FahrgastrechteRAG()
            setattr(get_passenger_rights, "_rag", rag)
        return rag


def _warm_rag_in_background() -> None:
    try:
        _get_or_build_rag()
    except Exception:
        pass  # get_passenger_rights() retries synchronously and surfaces the real error


threading.Thread(target=_warm_rag_in_background, daemon=True).start()
