"""In-process capture of the last reroute result (for the chat UI).

The Orchestrator wraps the Planner in a base ``AgentTool``, which runs the
sub-agent in its own runner and returns only the merged final *text*. The
``find_reroute_options`` function_response therefore never reaches the
top-level event stream that ``ui.chat`` iterates, so the browser can't see
the structured option list via ADK events.

Workaround: the tools stash structured planning state in the turn workspace
(see request_context) while they run, and ``ui.chat.chat_turn`` reads only
the finalized shortlist after the run. Discovery candidates and
constraint-breaking fallbacks remain separate so raw tool results can never
become selectable UI cards. The workspace is bound per chat turn, so nothing
has to be cleared between turns and concurrent turns cannot mix.

This module holds no tools — only the stash the discovery tools write and
``reroute.finalize_reroute_options`` / the UI read.
"""

from __future__ import annotations

from ...request_context import turn_workspace


def turn_reroute_state() -> dict | None:
    """Return this turn's reroute workspace, or ``None``.

    ``options`` is intentionally empty until ``finalize_reroute_options`` has
    applied every hard constraint. ``candidate_options`` is internal planning
    state and must never be rendered as selectable UI cards.
    """
    return turn_workspace().get("reroute")


def _rebuild_reroute_stash() -> None:
    """Rebuild flattened planning fields from the explicit family entries."""
    workspace = turn_reroute_state()
    if workspace is None:
        return
    candidate_options: list[dict] = []
    fallback_options: list[dict] = []
    rejected_options: list[dict] = []
    rejected_summary: dict[str, int] = {}
    for family_data in workspace.get("families", {}).values():
        candidate_options.extend(family_data.get("options") or [])
        fallback_options.extend(family_data.get("fallback_options") or [])
        rejected_options.extend(family_data.get("rejected_options") or [])
        for reason, count in (family_data.get("rejected_summary") or {}).items():
            rejected_summary[reason] = rejected_summary.get(reason, 0) + int(count)

    workspace["candidate_options"] = candidate_options
    workspace["fallback_options"] = fallback_options
    workspace["rejected_options"] = rejected_options
    workspace["rejected_summary"] = rejected_summary

    # Any corrected family invalidates calendar verdicts and the prior UI
    # shortlist. A later calendar/finalize call rebuilds both against this batch.
    workspace["calendar_checked"] = False
    workspace["calendar_verdicts"] = {}
    workspace["finalized"] = False
    workspace["options"] = []
    workspace["recommended_option_id"] = None


def _stash_options(
    options: list[dict],
    *,
    family: str,
    origin: str = "",
    destination: str = "",
    source: str = "",
    fallback_options: list[dict] | None = None,
    rejected_options: list[dict] | None = None,
    rejected_summary: dict[str, int] | None = None,
) -> list[dict]:
    """Replace one explicit mode family in the turn-local planning workspace.

    Replacement happens even for an empty list. The ``mobility`` family owns
    both C# and B# options, so a car-only re-run removes stale bikes as well.
    """
    workspace = turn_reroute_state()
    if workspace is None:
        workspace = {
            "origin": origin,
            "destination": destination,
            "families": {},
            "candidate_options": [],
            "options": [],
            "fallback_options": [],
            "source": source,
            "finalized": False,
        }
        turn_workspace()["reroute"] = workspace
    family_data = {
        "origin": origin,
        "destination": destination,
        "source": source,
        "options": list(options),
        "fallback_options": list(fallback_options or []),
        "rejected_options": list(rejected_options or []),
        "rejected_summary": dict(rejected_summary or {}),
    }
    workspace["families"][family] = family_data

    # Prefer the train route as the aggregate route; otherwise use the most
    # recently replaced family. Per-option source remains authoritative in UI.
    route_data = workspace["families"].get("train") or family_data
    workspace["origin"] = route_data.get("origin", "")
    workspace["destination"] = route_data.get("destination", "")
    workspace["source"] = route_data.get("source", "")
    _rebuild_reroute_stash()
    return options
