"""Shared state — the Context Record (data contract).

This is the typed shared state from the build spec (§6). In the LangGraph
reference design it would be the graph state object persisted via the
checkpointer; in this ADK build it is the *data contract* agents and tools
exchange — ADK transports the live run state via its ``SessionService`` (see
``persistence/checkpointer.py``).

Defined up front so the pieces have a single, typed vocabulary. Agents should
receive only the slice they need (context isolation) — never pass the whole
record into a prompt.

STATUS: scaffold. The sub-types start from spec §6 and are refined against the
scenarios; nothing wires the full record yet. See docs/journey-autopilot-build-spec.md.
"""

from __future__ import annotations

from typing import Literal, TypedDict


class Trip(TypedDict, total=False):
    trip_id: str
    passenger: str
    origin: str
    destination: str
    train: str
    planned_departure: str
    planned_arrival: str
    booking_refs: list[str]


class UserProfile(TypedDict, total=False):
    name: str
    home: dict
    bahnbonus_id: str
    channel_prefs: list[str]
    style_profile: dict


class Constraints(TypedDict, total=False):
    hard: list[dict]   # TimeWindow | HardRule (Kita/Sport/Schlaf, meeting arrival)
    soft: list[dict]   # Preference


class CalEvent(TypedDict, total=False):
    id: str
    title: str
    start: str
    end: str
    status: Literal["tentative", "confirmed"]
    participants: list[str]
    hard_constraint: bool


class RiskAssessment(TypedDict, total=False):
    at_risk: bool
    score: float
    confidence: float
    eta_impact_min: float
    rationale: str


class RerouteOption(TypedDict, total=False):
    option_id: str
    legs: list[dict]
    eta: str
    cost: float
    satisfies: list[str]
    violates: list[str]
    compensation_eligible: bool


class Decision(TypedDict, total=False):
    chosen_option_id: str
    user_veto: bool
    user_modifications: dict


class ExecutionResult(TypedDict, total=False):
    bookings: list[dict]
    reschedules: list[dict]
    compensation_claim: dict
    status: str


class NotificationLog(TypedDict, total=False):
    channel: str
    recipient: str
    body: str
    sent_at: str


class ToolFailure(TypedDict, total=False):
    tool: str
    attempt: int
    fallback_taken: str


class ContextRecord(TypedDict, total=False):
    session_id: str
    trip: Trip
    user_profile: UserProfile
    constraints: Constraints
    calendar: list[CalEvent]
    risk: RiskAssessment | None
    options: list[RerouteOption]
    decision: Decision | None
    execution: ExecutionResult | None
    notifications: list[NotificationLog]
    errors: list[ToolFailure]
    policy_mode: Literal["conservative", "balanced", "aggressive"]
