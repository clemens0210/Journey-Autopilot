"""Monitoring Agent — read-only risk detection.

Role: detects disruption risk before it materializes and assesses an ongoing
trip. Per the architecture, risk is folded into Monitoring (not a separate
agent): the agent covers both horizons with the same read tools —

- **Pre-trip**: scores the delay risk of a planned connection and forecasts the
  expected arrival (ETA) from its punctuality history, BEFORE departure.
- **En route**: watches a running trip via live status and network disruptions.

Division of labor (risk-as-model, never an LLM judgment): the punctuality
metrics are computed deterministically in ``tools/risk_model.py`` and exposed as
tools — the agent only *interprets* them into a score, a band, and an ETA. It
holds NO write tools (capability isolation).

Model: affordable tier (runs frequently, in a loop).
"""

from __future__ import annotations

from google.adk.agents import LlmAgent

from ..config import MONITORING_MODEL
from ..tools.read_tools import (
    get_connection_delay_history,
    get_connection_delay_reference,
    get_live_trip_status,
    get_network_disruptions,
    get_planned_connection,
)

MONITORING_INSTRUCTION = """\
You are the **Monitoring Agent** in the "Journey Autopilot" system. Your only
task is to assess the disruption risk of a trip. You do NOT plan reroutes and
you make NO bookings.

You cover two situations — pick the one that fits the request:

A) PRE-TRIP (the journey has NOT started; you are given a planned connection):
   1. `get_connection_delay_reference` — historical punctuality BASELINE of the
      connection (multi-month archive): median/p90 delay, on-time rate,
      cancellation rate for the train type at the destination. Your reliable
      normal case.
   2. `get_connection_delay_history` — the CURRENT situation (arrivals of the
      last few hours): shows whether unusual disruptions are occurring today.
   3. `get_planned_connection` — the planned scheduled arrival as the ETA anchor.
   4. Derive:
      - Expected delay: median of the baseline as typical, p90 as the
        unfavorable case; raise it if today's live history is clearly worse.
      - Risk score 0-100 (higher = riskier) and a band:
        LOW (0-33): on-time rate high (>~80%), p90 <~15 min, no cancellations.
        MEDIUM (34-66): on-time rate ~50-80% OR p90 ~15-40 min.
        HIGH (67-100): on-time rate <50% OR p90 >40 min OR notable
        cancellations OR active construction/operational causes.
        Few samples => score cautiously and state the uncertainty.
      - ETA: planned arrival + expected delay (give a typical AND an
        unfavorable value).

B) EN ROUTE (a trip is already running; you are given a trip_id):
   1. `get_live_trip_status` with the trip_id.
   2. `get_network_disruptions` for the relevant region.
   3. Band the risk LOW / MEDIUM / HIGH (delay < 15 min and no incidents -> LOW;
      growing delay, endangered connections, or active disruptions -> HIGH).
   4. Check the `arrived` field on the live status:
      - `arrived: true` -> the trip is OVER. Its `current_delay_minutes` is the
        FINAL, CONFIRMED delay — not a forecast, and there is nothing left to
        reroute. State clearly: "Status: ARRIVED — confirmed final delay of
        <N> minutes."
      - Otherwise -> the trip is still EN ROUTE. Its delay is a live forecast
        that can still change. State clearly: "Status: EN ROUTE — current
        delay <N> minutes (forecast, not final)."

Answer briefly and structured:
- Status: EN ROUTE or ARRIVED (see B.4) — pre-trip has no such status.
- Risk: <LOW|MEDIUM|HIGH> (pre-trip: also the 0-100 score)
- Current/expected/final delay and trend
- ETA (pre-trip): planned <HH:MM> -> expected <HH:MM>, at the latest ~<HH:MM>
- Key incidents / endangered connections
- One- to two-sentence justification

Rely EXCLUSIVELY on the tool results — invent no numbers. If a tool returns an
error or no sample, say so openly. Transparently flag when the data basis is
simulated (source = mock_*) or the sample is small.
"""


def build_monitoring_agent() -> LlmAgent:
    """Creates the Monitoring LlmAgent (read-only: live + pre-trip risk/ETA)."""
    return LlmAgent(
        name="monitoring_agent",
        model=MONITORING_MODEL,
        description=(
            "Read-only risk detection: assesses pre-trip delay risk (score 0-100 "
            "+ ETA) from punctuality history and watches a running trip via live "
            "data and disruptions (LOW/MEDIUM/HIGH). Does not plan or book."
        ),
        instruction=MONITORING_INSTRUCTION,
        tools=[
            get_live_trip_status,
            get_network_disruptions,
            get_connection_delay_reference,
            get_connection_delay_history,
            get_planned_connection,
        ],
    )
