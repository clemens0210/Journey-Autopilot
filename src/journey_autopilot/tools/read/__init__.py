"""Read-side agent tools, one module per read concern.

Split out of a single 2300-line ``read_tools`` module: the five jobs below
share almost nothing but the constraint helpers, so keeping them apart means a
change to reroute pruning cannot reach the monitoring path, and the shared
state (the turn workspace) has one obvious home instead of sitting in the
middle of the tool definitions.

- ``monitoring``   — live trip status and network disruptions (Monitoring agent).
- ``reroute``      — reroute discovery, ecosystem alternatives, and the final
                     selectable shortlist (Planner agent).
- ``calendar``     — Outlook connection state, calendar reads, and the conflict
                     classification the write path reuses at execution time.
- ``rights``       — passenger-rights lookup (read half; filing is a write tool).
- ``pretrip_risk`` — the multi-month punctuality baseline, today's delay
                     history, and the scheduled-connection ETA anchor.

Two supporting modules carry no tools: ``profile`` (the onboarding-profile
accessors) and ``workspace`` (the turn-local reroute stash that discovery
writes and finalization reads).

The whole public surface is re-exported here, and again through the
``tools.read_tools`` facade, so consumers keep importing names rather than
file layout.
"""

from __future__ import annotations

from .calendar import (
    CALENDAR_TRAVEL_BUFFER_MINUTES,
    PSEUDO_OUTLOOK_ALIAS_RE,
    check_options_against_calendar,
    classify_window_conflicts,
    get_user_calendar,
    is_calendar_connected,
)
from .monitoring import get_live_trip_status, get_network_disruptions
from .pretrip_risk import (
    get_historical_delay_baseline,
    get_planned_connection,
    get_recent_delay_history,
    recent_delay_history,
)
from .profile import get_user_profile
from .reroute import (
    finalize_reroute_options,
    find_mobility_alternatives,
    find_partner_hotels,
    find_reroute_options,
)
from .rights import get_passenger_rights, turn_rights_result
from .workspace import turn_reroute_state

# The surface splits by AUDIENCE, and the naming follows that split:
#
# ADK tools — the LLM reads these names and calls them. The verb carries the
# contract: ``get_*`` fetches one fact from a source, ``find_*`` searches and
# returns candidates, ``check_*``/``finalize_*`` are the two ordered steps of
# the planning protocol (finalize refuses to run before check).
_AGENT_TOOLS = [
    # Monitoring agent
    "get_live_trip_status",
    "get_network_disruptions",
    "get_historical_delay_baseline",
    "get_recent_delay_history",
    "get_planned_connection",
    # Planner agent
    "get_user_profile",
    "get_passenger_rights",
    "find_reroute_options",
    "find_mobility_alternatives",
    "find_partner_hotels",
    "check_options_against_calendar",
    "finalize_reroute_options",
]

# Plain Python — never registered as tools, never seen by a model. Called by
# the write path, ui.chat, and the Planner's instruction provider. ``is_*`` is
# a predicate, ``turn_*`` reads state scoped to the current chat turn.
_INTERNAL_API = [
    "is_calendar_connected",
    "get_user_calendar",
    "classify_window_conflicts",
    "recent_delay_history",
    "turn_reroute_state",
    "turn_rights_result",
    "CALENDAR_TRAVEL_BUFFER_MINUTES",
    "PSEUDO_OUTLOOK_ALIAS_RE",
]

__all__ = _AGENT_TOOLS + _INTERNAL_API
