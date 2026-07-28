"""Passenger Rights Service — rule logic without LLM.

This module contains the pure calculation logic for passenger rights. It is
deliberately separated from the RAG layer, and split along the same line the
agent graph is split along:

  - ``evaluate_travel_rights()``  → what the traveler may DO right now
    (train binding lifted, higher train category allowed, taxi/hotel cover).
    Read-only entitlement info the **Planner** needs *during* a disruption to
    judge whether a reroute is even permitted on the existing ticket.
  - ``calculate_compensation()``  → the EUR amount owed AFTER the trip.
    Deterministic input for the **Executor**, which files the claim through
    the policy/veto gate.
  - ``FahrgastrechteRAG``         → semantic search for legal context chunks.

Sources of rule logic (as of June 2026):
  - EU Regulation 2021/782 (passenger rights in rail transport)
  - bahn.de/fahrgastrechte
"""

from __future__ import annotations

from dataclasses import dataclass, field


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

# --- Entitlement thresholds (DB Fahrgastrechte / EU 2021/782) ----------------
# Minutes of *expected* arrival delay at which each right kicks in. Expected, not
# final: these are exactly the rights that matter while the traveler is still en
# route and deciding whether to reroute — unlike compensation, which can only be
# settled once the trip is over.
TRAIN_BINDING_LIFTED_MINUTES = 20   # Zugbindung drops; any other train may be used
CONTINUE_OR_ABANDON_MINUTES = 60    # continue, or abandon + full fare refund
COMPENSATION_MINUTES = 60           # 25% of fare
COMPENSATION_HIGH_MINUTES = 120     # 50% of fare
TAXI_COVER_EUR = 120.0              # cover for the last leg in the night window

# Ticket types that carry a Zugbindung in the first place. A flexible fare
# (Flexpreis) or a season ticket is valid on any train anyway, so "the binding
# is lifted" would be a meaningless statement for them.
_TRAIN_BOUND_TICKETS = ("einzelticket", "sparpreis", "super_sparpreis")


@dataclass
class TravelRights:
    """What the traveler may do RIGHT NOW, given an expected delay.

    Deliberately carries no EUR figure: an expected delay is not an experienced
    one, and quoting compensation on a forecast is exactly the failure mode the
    agent instructions guard against.
    """

    delay_minutes: int
    train_binding_lifted: bool
    may_use_higher_category: bool
    may_abandon_for_full_refund: bool
    hotel_or_taxi_cover: bool
    entitlements: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "delay_minutes": self.delay_minutes,
            "train_binding_lifted": self.train_binding_lifted,
            "may_use_higher_category": self.may_use_higher_category,
            "may_abandon_for_full_refund": self.may_abandon_for_full_refund,
            "hotel_or_taxi_cover": self.hotel_or_taxi_cover,
            "entitlements": self.entitlements,
            "notes": self.notes,
        }


@dataclass
class CompensationResult:
    """Result of the compensation calculation for a specific case."""

    eligible: bool
    compensation_eur: float
    reason: str
    claim_via: str = ""
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "eligible": self.eligible,
            "compensation_eur": self.compensation_eur,
            "reason": self.reason,
            "claim_via": self.claim_via,
            "notes": self.notes,
        }


# ---------------------------------------------------------------------------
# Core logic
# ---------------------------------------------------------------------------

def evaluate_travel_rights(
    delay_minutes: int,
    ticket_type: str = "einzelticket",
    *,
    last_train_of_day: bool = False,
) -> dict:
    """What the traveler is entitled to do with an EXPECTED delay of this size.

    This is the reroute-relevant half of passenger rights: whether the ticket's
    Zugbindung (train binding) still applies, whether a higher train category
    may be used, whether the journey may be abandoned for a full refund, and
    whether onward transport/accommodation is covered. It answers "may I take a
    different train?", not "what am I owed?" — so it is valid while the trip is
    still running, where a compensation figure would not be.

    Args:
        delay_minutes: Expected arrival delay at the destination in minutes.
        ticket_type: ``einzelticket`` | ``sparpreis`` | ``super_sparpreis`` |
            ``zeitkarte_fv`` | ``zeitkarte_nv`` | ``bc100`` |
            ``deutschland_ticket``. Only train-bound fares can have a binding
            lifted; the rest are valid on any train to begin with.
        last_train_of_day: True when the delayed connection is the last
            scheduled one of the day — widens the taxi/accommodation cover.

    Returns:
        Dict with ``train_binding_lifted``, ``may_use_higher_category``,
        ``may_abandon_for_full_refund``, ``hotel_or_taxi_cover``, a
        plain-language ``entitlements`` list, and ``notes``.
    """
    train_bound = ticket_type in _TRAIN_BOUND_TICKETS
    lifted = train_bound and delay_minutes >= TRAIN_BINDING_LIFTED_MINUTES
    abandon = delay_minutes >= CONTINUE_OR_ABANDON_MINUTES
    cover = abandon and last_train_of_day

    entitlements: list[str] = []
    notes: list[str] = []

    if not train_bound:
        notes.append(
            f"Ticket type '{ticket_type}' is not bound to a specific train — "
            "any connection to the destination may be used regardless of delay."
        )
    elif lifted:
        entitlements.append(
            f"Train binding (Zugbindung) is lifted from "
            f"{TRAIN_BINDING_LIFTED_MINUTES} min expected delay — the existing "
            "ticket is valid on another connection to the same destination "
            "without rebooking."
        )
        entitlements.append(
            "A higher train category may be used; the surcharge is refundable."
        )
    else:
        notes.append(
            f"Expected delay {delay_minutes} min is below the "
            f"{TRAIN_BINDING_LIFTED_MINUTES} min threshold — the train binding "
            "still applies, so switching connections would need a rebooking."
        )

    if abandon:
        entitlements.append(
            f"From {CONTINUE_OR_ABANDON_MINUTES} min expected delay the journey "
            "may be abandoned instead of continued, against a full refund of "
            "the unused fare (including a return to the origin)."
        )
    if cover:
        entitlements.append(
            f"As the last scheduled connection of the day, onward transport is "
            f"covered up to {TAXI_COVER_EUR:.0f} EUR, and reasonable "
            "accommodation is covered if the destination cannot be reached today."
        )

    notes.append(
        "Entitlements only — no compensation amount is assessable from an "
        "expected delay; that follows once the trip has actually concluded."
    )

    return TravelRights(
        delay_minutes=delay_minutes,
        train_binding_lifted=lifted,
        may_use_higher_category=lifted,
        may_abandon_for_full_refund=abandon,
        hotel_or_taxi_cover=cover,
        entitlements=entitlements,
        notes=notes,
    ).to_dict()


def calculate_compensation(
    delay_minutes: int,
    ticket_type: str,
    price_paid: float = 0.0,
    travel_class: int = 2,
    bahncard_type: str = "keine",
) -> dict:
    """Calculates the compensation claim based on official DB passenger rights.

    Args:
        delay_minutes:  Actual arrival delay at destination in minutes.
        ticket_type:    ``einzelticket`` | ``zeitkarte_fv`` | ``zeitkarte_nv`` |
                        ``bc100`` | ``deutschland_ticket``
        price_paid:     Ticket price paid in EUR (only relevant for single tickets).
        travel_class:   1 or 2.
        bahncard_type:  ``keine`` | ``bc25`` | ``bc50`` | ``bc100``

    Returns:
        Dict with eligible, compensation_eur, reason, claim_via, notes.
    """
    result = _calculate(delay_minutes, ticket_type, price_paid, travel_class, bahncard_type)
    return result.to_dict()


def _calculate(
    delay_minutes: int,
    ticket_type: str,
    price_paid: float,
    travel_class: int,
    bahncard_type: str,
) -> CompensationResult:

    # --- Below the compensation threshold: no claim ---
    if delay_minutes < COMPENSATION_MINUTES:
        return CompensationResult(
            eligible=False,
            compensation_eur=0.0,
            reason=(
                f"Delay {delay_minutes} min — claim only from "
                f"{COMPENSATION_MINUTES} minutes onwards."
            ),
        )

    # --- BahnCard 100 / Mobility BahnCard 100 ---
    if bahncard_type == "bc100" or ticket_type == "bc100":
        amount = 15.0 if travel_class == 1 else 10.0
        return CompensationResult(
            eligible=True,
            compensation_eur=amount,
            reason=f"BahnCard 100 — flat rate {amount}€ (class {travel_class}).",
            claim_via="Customer Service Centre for Passenger Rights (exclusively — not at travel centres)",
            notes=[
                "Season tickets are only processed via the Customer Service Centre.",
                "Maximum 25% of total card value as annual total compensation.",
                "Include BC100 number in the application.",
            ],
        )

    # --- Long-distance season ticket ---
    if ticket_type == "zeitkarte_fv":
        amount = 7.50 if travel_class == 1 else 5.0
        return CompensationResult(
            eligible=True,
            compensation_eur=amount,
            reason=f"Long-distance season ticket — flat rate {amount}€ per delay case.",
            claim_via="Customer Service Centre for Passenger Rights (exclusively)",
            notes=[
                "Maximum 25% of season ticket value as total compensation.",
                "Multiple cases can be submitted together.",
            ],
        )

    # --- Local transport season ticket ---
    if ticket_type == "zeitkarte_nv":
        amount = 2.25 if travel_class == 1 else 1.50
        notes = [
            "Minimum amount 4 EUR — collect cases and submit together.",
            "Deutschland-Ticket, Quer-durchs-Land and regional tickets fall under this category.",
        ]
        if amount < 4.0:
            notes.insert(0, "Individual case below 4€ minimum threshold — please collect multiple cases.")
        return CompensationResult(
            eligible=True,
            compensation_eur=amount,
            reason=f"Local transport season ticket — flat rate {amount}€ per delay case.",
            claim_via="Customer Service Centre for Passenger Rights",
            notes=notes,
        )

    # --- Deutschland-Ticket ---
    if ticket_type == "deutschland_ticket":
        return CompensationResult(
            eligible=True,
            compensation_eur=1.50,
            reason="Deutschland-Ticket — flat rate 1.50€ per local transport delay case.",
            claim_via="Customer Service Centre for Passenger Rights",
            notes=[
                "Minimum amount 4€ — collect cases and submit together.",
                "Maximum 25% of monthly ticket value.",
                "No delay certificate required — recorded electronically.",
            ],
        )

    # --- Standard single ticket (Sparpreis fares settle the same way) ---
    if ticket_type in _TRAIN_BOUND_TICKETS:
        if price_paid <= 0:
            percentage = 0.50 if delay_minutes >= COMPENSATION_HIGH_MINUTES else 0.25
            return CompensationResult(
                eligible=True,
                compensation_eur=0.0,
                reason=(
                    f"Single ticket, {delay_minutes} min delay — compensation is {int(percentage*100)}% "
                    "of the fare paid (minimum 4€). Provide price_paid to calculate the exact amount."
                ),
                notes=[
                    f"Rule of thumb: {int(percentage*100)}% of fare paid (min 4€).",
                ],
            )

        percentage = 0.50 if delay_minutes >= COMPENSATION_HIGH_MINUTES else 0.25
        amount = round(price_paid * percentage, 2)
        # Minimum threshold
        if amount < 4.0:
            return CompensationResult(
                eligible=False,
                compensation_eur=0.0,
                reason=(
                    f"Calculated amount {amount}€ is below the 4€ minimum threshold "
                    f"(ticket price {price_paid}€ × {int(percentage*100)}%)."
                ),
                notes=["For cheap tickets, filing a claim is often not worthwhile."],
            )

        notes = [f"Basis: {price_paid}€ × {int(percentage*100)}% = {amount}€"]

        if bahncard_type in ("bc25", "bc50"):
            notes.append(
                f"BahnCard {bahncard_type.upper()} detected — compensation is based on "
                f"the actual price paid ({price_paid}€), not the full price."
            )

        return CompensationResult(
            eligible=True,
            compensation_eur=amount,
            reason=(
                f"Single ticket, {delay_minutes} min delay → "
                f"{int(percentage*100)}% of {price_paid}€ = {amount}€."
            ),
            claim_via="DB Travel Centre or Customer Service Centre for Passenger Rights (online/postal)",
            notes=notes,
        )

    # --- Unknown ticket type ---
    return CompensationResult(
        eligible=False,
        compensation_eur=0.0,
        reason=f"Unknown ticket type '{ticket_type}' — please check manually.",
        notes=[
            "Valid types: einzelticket, sparpreis, super_sparpreis, "
            "zeitkarte_fv, zeitkarte_nv, bc100, deutschland_ticket"
        ],
    )
