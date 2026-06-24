"""Error policy — retry, fallback, graceful degradation.

Every tool call should be wrapped: retry with backoff -> fallback (cached
timetable / RAG instead of the live API) -> graceful degradation (tell the user
what couldn't be done). A broken tool call must be recoverable inside the loop,
not a crash — this is the failure-case deliverable.

``with_resilience`` is the single reusable wrapper the read tools share: it runs
a ``primary`` callable (the live source) and, on a raised exception OR a result
the ``accept`` predicate rejects (e.g. an empty sample), falls back to a
``fallback`` callable (cached/mock). The outcome carries a ``ToolFailure`` record
(see ``state.ToolFailure``) the Communicator can surface — or a scenario can log
into the Context Record.

See docs/journey-autopilot-build-spec.md §9.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Awaitable, Callable, Generic, TypeVar

from .state import ToolFailure

logger = logging.getLogger(__name__)

T = TypeVar("T")


@dataclass
class ToolOutcome(Generic[T]):
    """Result of a resilient tool call.

    Attributes:
        value: the chosen result (primary if it succeeded, else fallback).
        used_fallback: True if the fallback path produced ``value``.
        failure: a ``ToolFailure`` record when the fallback was taken, else None.
    """

    value: T
    used_fallback: bool
    failure: ToolFailure | None


def _accept_not_none(result: object) -> bool:
    return result is not None


def _make_failure(tool: str, last_error: BaseException | None, attempts: int) -> ToolFailure:
    failure: ToolFailure = {
        "tool": tool,
        "attempt": attempts,
        "fallback_taken": f"{type(last_error).__name__}: {last_error}"
        if last_error is not None
        else "empty/rejected primary result",
    }
    logger.info("tool=%s falling back (%s)", tool, failure["fallback_taken"])
    return failure


def with_resilience(
    primary: Callable[[], T],
    fallback: Callable[[], T],
    *,
    tool: str = "",
    exceptions: tuple[type[BaseException], ...] = (Exception,),
    accept: Callable[[T], bool] = _accept_not_none,
    retries: int = 1,
    backoff: float = 0.0,
) -> ToolOutcome[T]:
    """Run ``primary`` with retry; fall back to ``fallback`` on failure.

    A "failure" is either an exception in ``exceptions`` or a result that
    ``accept`` rejects (used to treat e.g. an empty live sample as a miss). The
    fallback runs at most once and its result is returned as-is.

    Args:
        primary: the live source (e.g. the db_service sidecar call).
        fallback: the cached/mock source, fully formed (it may itself return a
            degraded ``{"error": ...}`` payload — that is the graceful end state).
        tool: tool name for the ``ToolFailure`` record / logs.
        exceptions: exception types that trigger the fallback (others propagate).
        accept: predicate deciding whether a primary result is usable.
        retries: number of primary attempts before falling back (>=1).
        backoff: base seconds between retries (linear: ``backoff * attempt``).

    Returns:
        A ``ToolOutcome`` with the value, whether the fallback was used, and the
        ``ToolFailure`` record if so.
    """
    last_error: BaseException | None = None
    attempts = max(retries, 1)
    for attempt in range(1, attempts + 1):
        try:
            result = primary()
            if accept(result):
                return ToolOutcome(result, used_fallback=False, failure=None)
            last_error = None  # rejected by accept(), not an exception
        except exceptions as exc:
            last_error = exc
            logger.debug("tool=%s attempt=%d failed: %s", tool, attempt, exc)
        if attempt < attempts and backoff:
            time.sleep(backoff * attempt)

    failure = _make_failure(tool, last_error, attempts)
    return ToolOutcome(fallback(), used_fallback=True, failure=failure)


async def with_resilience_async(
    primary: Callable[[], Awaitable[T]],
    fallback: Callable[[], T],
    *,
    tool: str = "",
    exceptions: tuple[type[BaseException], ...] = (Exception,),
    accept: Callable[[T], bool] = _accept_not_none,
    retries: int = 1,
    backoff: float = 0.0,
) -> ToolOutcome[T]:
    """Async counterpart of ``with_resilience`` (``primary`` is awaitable).

    The ``fallback`` stays synchronous — fallbacks read cached/mock data and do
    no I/O. Same failure semantics and return shape as the sync version.
    """
    last_error: BaseException | None = None
    attempts = max(retries, 1)
    for attempt in range(1, attempts + 1):
        try:
            result = await primary()
            if accept(result):
                return ToolOutcome(result, used_fallback=False, failure=None)
            last_error = None
        except exceptions as exc:
            last_error = exc
            logger.debug("tool=%s attempt=%d failed: %s", tool, attempt, exc)
        if attempt < attempts and backoff:
            await asyncio.sleep(backoff * attempt)

    failure = _make_failure(tool, last_error, attempts)
    return ToolOutcome(fallback(), used_fallback=True, failure=failure)
