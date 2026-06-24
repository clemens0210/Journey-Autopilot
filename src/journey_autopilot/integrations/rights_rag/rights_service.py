"""Passenger Rights Service — compensation calculation without LLM.

This module contains the pure calculation logic for compensation claims.
It is deliberately separated from the RAG layer:

  - ``calculate_compensation()`` → deterministic rule logic, testable without LLM
  - ``FahrgastrechteRAG``       → semantic search for context chunks

The Planner Agent combines both: it receives from ``get_passenger_rights()``
in tools/read_tools.py both the calculated amount and the legal context chunks.

Sources of rule logic (as of June 2026):
  - EU Regulation 2021/782 (passenger rights in rail transport)
  - bahn.de/fahrgastrechte
"""

from __future__ import annotations

from dataclasses import dataclass, field


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

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

    # --- Under 60 minutes: no claim ---
    if delay_minutes < 60:
        return CompensationResult(
            eligible=False,
            compensation_eur=0.0,
            reason=f"Delay {delay_minutes} min — claim only from 60 minutes onwards.",
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

    # --- Standard single ticket ---
    if ticket_type == "einzelticket":
        if price_paid <= 0:
            percentage = 0.50 if delay_minutes >= 120 else 0.25
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

        percentage = 0.50 if delay_minutes >= 120 else 0.25
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
        notes=["Valid types: einzelticket, zeitkarte_fv, zeitkarte_nv, bc100, deutschland_ticket"],
    )
