"""Monitoring Agent.

Role: Observes a running train journey and assesses the disruption risk.
It does NOT decide on reroutes — it only delivers a reliable
risk assessment on which the Orchestrator routes further.

Model: affordable Flash model (potentially runs frequently, in a loop).
"""

from __future__ import annotations

from google.adk.agents import LlmAgent

from .config import MONITORING_MODEL
from .tools import get_live_trip_status, get_network_disruptions

MONITORING_INSTRUCTION = """\
You are the **Monitoring Agent** in the "Journey Autopilot" system. Your only
task is to capture the current state of a train journey and assess the
disruption risk. You do NOT plan any reroutes.

Procedure:
1. Call `get_live_trip_status` with the given trip_id.
2. Check `get_network_disruptions` for the disruption status of the relevant region.
3. Assess the risk on a scale: LOW / MEDIUM / HIGH.
   - Guideline: Delay < 15 min and no incidents -> LOW;
     growing delay, endangered connections, or active disruptions -> HIGH.

Answer briefly and structured:
- Risk Level: <LOW|MEDIUM|HIGH>
- Current delay and trend
- Key incidents / endangered connections
- Data source (`source` from the tool result)
- One-sentence justification

Invent no numbers — use only the tool results. If data is missing,
state that explicitly. If `source` starts with `mock_`, disclose that the
live DB sidecar was unavailable and demo fallback data was used.
"""


def build_monitoring_agent() -> LlmAgent:
    """Creates the Monitoring LlmAgent."""
    return LlmAgent(
        name="monitoring_agent",
        model=MONITORING_MODEL,
        description=(
            "Monitors a running train journey, reads live data and disruptions, "
            "and delivers a risk assessment (LOW/MEDIUM/HIGH)."
        ),
        instruction=MONITORING_INSTRUCTION,
        tools=[get_live_trip_status, get_network_disruptions],
    )
