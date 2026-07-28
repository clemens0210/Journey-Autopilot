"""Shared typed vocabulary for data that crosses module boundaries.

The build spec (§6) sketches a single ``ContextRecord`` holding the whole run.
That shape belongs to the LangGraph reference design, where the record IS the
graph state persisted by a checkpointer. Under ADK it would be dead weight:
live run state is transported by the ``SessionService``, the reroute shortlist
lives in a request-scoped workspace (``tools/read_tools``), and the durable
profile/trip/proposal data lives in ``persistence/store``. Nothing ever holds
the whole record, so nothing is typed for it.

What remains here is the vocabulary that genuinely travels between modules and
benefits from a name. See docs/adr/0001-framework-adk.md for the mapping.
"""

from __future__ import annotations

from typing import Literal, TypedDict


class ToolFailure(TypedDict, total=False):
    """One degraded tool call: the live source missed and a fallback answered.

    Produced by ``errors.with_resilience`` whenever a tool falls back from its
    live source (DB sidecar, MS Graph) to cached or mock data, so the reason is
    reportable rather than silent.
    """

    tool: str
    attempt: int
    fallback_taken: str


PolicyMode = Literal["conservative", "balanced", "aggressive"]
"""Global autonomy level. Shifts every write tool's default resolution.

Defined here rather than in ``policy`` so the onboarding UI can name the levels
without importing the gate itself.
"""
