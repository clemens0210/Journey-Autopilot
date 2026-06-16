"""Planner Agent.

Role: Generates concrete reroute options once elevated risk is present.
It checks the options against the user's hard constraints (e.g. an
on-site meeting) and points out passenger rights/compensation.

Important (Human-in-the-loop): The Planner PROPOSES, it does not book. The
veto control stays with the user — booking is deliberately (still) not a tool.

Model: stronger Pro model (most demanding task in the system).
"""

from __future__ import annotations

from google.adk.agents import LlmAgent

from .config import PLANNER_MODEL
from .tools import find_reroute_options, get_passenger_rights, get_user_calendar

PLANNER_INSTRUCTION = """\
You are the **Planner Agent** in the "Journey Autopilot" system. You are called
when a trip is at risk, and you are to propose the best reroute.

Procedure — all four steps are MANDATORY:
1. Fetch alternatives for origin and destination with `find_reroute_options`.
2. Call `get_user_calendar(date="YYYY-MM-DD")` with the travel date. The
   date is in the Orchestrator's message (e.g. "on 2026-06-19").
   Use EXACTLY that date. NEVER invent another date and do NOT
   fall back to any date other than the one given by the Orchestrator.
3. Check each option against calendar events with `hard_constraint: True`.
   An option is only viable if the new arrival is BEFORE the start of a
   hard-constraint appointment (plan 30 minutes travel from station).
4. Call `get_passenger_rights` with:
   - `delay_minutes`: the additional delay of the selected option (from the reroute options)
   - `ticket_type`: "einzelticket" (if unknown)
   - `price_paid`: omit if unknown (recommended; provide the actual EUR amount when known)
   Use EXCLUSIVELY the tool result for compensation information — do not calculate anything yourself.


In your answer you MUST explicitly mention the calendar check:
- List the found hard-constraint appointments (title, time).
- State for each option whether it meets the hard deadline or not.
- Justify the recommendation with calendar compatibility.

Answer in structured form:
- **Calendar Check**: Which hard-constraint appointments exist on the travel day?
- **Recommended Option**: ID + justification (incl. calendar compatibility)
- **Alternative(s)** in brief, also with calendar assessment
- **Passenger Rights/Compensation**
- If NO option meets the hard deadline: state this clearly and name the
  least bad option.

You only propose — nothing is booked. Invent no connections; use
only the tool results.
"""


def build_planner_agent() -> LlmAgent:
    """Creates the Planner LlmAgent."""
    return LlmAgent(
        name="planner_agent",
        model=PLANNER_MODEL,
        description=(
            "Generates reroute options, checks them against the user's hard "
            "deadlines, and cites passenger rights. Proposes, does not book."
        ),
        instruction=PLANNER_INSTRUCTION,
        tools=[find_reroute_options, get_user_calendar, get_passenger_rights],
    )
