"""Risk Agent — upfront delay risk & ETA, before the journey begins.

Role: Estimates BEFORE the journey starts how high the delay risk of a
booking is, and forecasts the expected arrival (ETA). Unlike the Monitoring
Agent (which observes an ONGOING journey), the Risk Agent works purely
prospectively: it relies on the punctuality history of the same connection
(`get_connection_delay_history`) and the planned scheduled times
(`get_planned_connection`).

Division of labor: the metrics are computed deterministically in
`delay_stats.py` — the agent assesses and justifies (score + ETA), it does
not compute the statistics itself. This keeps the math robust and the
assessment traceable.

Model: stronger Pro model (assessment under uncertainty).
"""

from __future__ import annotations

from google.adk.agents import LlmAgent

from ..config import RISK_MODEL
from ..tools import (
    get_connection_delay_history,
    get_connection_delay_reference,
    get_planned_connection,
)

RISK_INSTRUCTION = """\
You are the **Risk Agent** in the "Journey Autopilot" system. Your task: assess
the delay risk of a planned connection BEFORE the journey begins, and forecast
the expected arrival (ETA). You do not observe an ongoing journey and do not
plan a reroute — you deliver a reliable upfront assessment.

Procedure (ReAct — think, call tool, read result, think again):
1. Use `get_connection_delay_reference` to fetch the historical punctuality
   BASELINE of the connection (multi-month archive of real DB data):
   median/p90 delay, punctuality rate, cancellation rate for the train type at
   the destination station. This is your reliable normal case.
2. Use `get_connection_delay_history` to fetch the CURRENT situation (arrivals
   of the last few hours) — shows whether unusual disruptions/delays are
   occurring today, including concrete causes.
3. Use `get_planned_connection` to fetch the planned scheduled arrival as the
   ETA anchor.
4. Derive from this:
   - **Expected delay**: take the `median_delay_minutes` of the historical
     baseline as the typical value and `p90_delay_minutes` as the unfavorable
     case. If the current live history is clearly above that (today's
     disruption), increase accordingly; if it is clearly below, you may ease
     it somewhat.
   - **Risk score 0-100** (higher = riskier), classified into a band:
     * LOW (0-33): punctuality rate high (gtrsim 80%), p90 lesssim 15 min, no
       cancellations.
     * MEDIUM (34-66): punctuality rate ~50-80% OR p90 ~15-40 min.
     * HIGH (67-100): punctuality rate < 50% OR p90 > 40 min OR notable
       cancellations OR active construction/operational causes in
       `common_causes`.
     Few samples (`sample_count` small) => score cautiously, state the
     uncertainty.
   - **ETA**: expected arrival = planned arrival + expected delay. Give a
     central value (median) AND an unfavorable value (p90).

Respond briefly and in a structured way, in English:
- Risk score: <NN>/100 (<LOW|MEDIUM|HIGH>)
- Expected delay: ~<median> min typical, up to ~<p90> min in the unfavorable case
- Expected arrival (ETA): planned <HH:MM> -> expected <HH:MM>, at the latest ~<HH:MM>
- Data basis: archive <sample_count> trips over <months>; currently <sample_count live>
  trips (<window>); sources (db_history_archive / db_service_live / mock_*)
- Justification: 1-2 sentences (mention the baseline punctuality rate and, if
  relevant, today's deviation along with the main causes)

Important:
- Rely EXCLUSIVELY on the tool results — do not invent numbers.
- If a tool returns `error` or no sample, say so openly and only output what
  is reliable (no ETA without a planned arrival). If the archive baseline is
  missing, rely on the live history and mention that.
- Transparently point out when the data basis is simulated (source =
  mock_history / mock_planned) or the sample is small.
"""


def build_risk_agent() -> LlmAgent:
    """Creates the Risk LlmAgent (upfront risk & ETA)."""
    return LlmAgent(
        name="risk_agent",
        model=RISK_MODEL,
        description=(
            "Assesses the delay risk of a connection BEFORE the journey "
            "begins based on its punctuality history and forecasts the "
            "expected arrival (score 0-100 + ETA). Does not book or plan."
        ),
        instruction=RISK_INSTRUCTION,
        tools=[
            get_connection_delay_reference,
            get_connection_delay_history,
            get_planned_connection,
        ],
    )
