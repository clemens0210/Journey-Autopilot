"""Error policy — retry, fallback, graceful degradation.

Every tool call should be wrapped: retry with backoff -> fallback (cached
timetable / RAG instead of the live API) -> graceful degradation (tell the user
what couldn't be done). A broken tool call must be recoverable inside the loop,
not a crash — this is the failure-case deliverable.

STATUS: scaffold for milestone M5. The fallback *pattern* already exists inline
in the tools (``read_tools.py`` / ``risk_model.py`` fall back from the
db_service sidecar to mock data and tag the result with ``source``). M5 extracts
that into a single reusable wrapper used by every tool.

See docs/journey-autopilot-build-spec.md §9.
"""

from __future__ import annotations

from typing import Callable, TypeVar

T = TypeVar("T")


def with_resilience(fn: Callable[..., T], *, retries: int = 2, fallback: Callable[..., T] | None = None):
    """Wrap a tool call with retry -> fallback -> graceful degradation.

    TODO(M5): implement backoff retries, structured ToolFailure logging into the
    Context Record (state.ToolFailure), and a typed degraded result the
    Communicator can surface to the user.
    """
    raise NotImplementedError("Error wrapper lands in M5; tools currently fall back inline.")
