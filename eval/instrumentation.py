"""Per-call token, cost, and latency capture for the evaluation runs.

Every model call in this system goes through LiteLLM — the ADK agents via
``google.adk.models.lite_llm.LiteLlm``, the naive baseline via ``litellm``
directly. So one global LiteLLM success callback sees *all* of it, including
calls made inside a sub-agent, which never reach ``ui/chat.py``'s event stream
because ``AgentTool`` forwards only the sub-agent's final text.

Prices come from LiteLLM's bundled cost map, which carries Bedrock's own
partner rates for the two model IDs this project uses (Bedrock is ~10% above
Anthropic's first-party list). Nothing here calls out to a pricing API, so the
numbers are reproducible offline and identical between runs.
"""

from __future__ import annotations

import asyncio
import csv
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

import litellm
from litellm.integrations.custom_logger import CustomLogger

# Columns of the raw per-call log. One row per model call, NOT per run: a
# single ReAct turn that consults Monitoring and Planner emits several rows,
# which `run.py` sums by `run_id`.
CSV_FIELDS = [
    "run_id",
    "timestamp",
    "scenario",
    "arm",
    "variant",
    "role",
    "model",
    "input_tokens",
    "output_tokens",
    "cost_usd",
    "latency_s",
]


@dataclass
class _RunContext:
    """Which run the calls arriving right now belong to.

    Ambient rather than threaded through the agents, because the calls we most
    need to label are made deep inside ADK where we have no seam to pass an
    argument through. Runs execute one at a time, so a single guarded slot is
    enough — `run.py` never overlaps two runs.
    """

    run_id: str = "unassigned"
    scenario: str = "unassigned"
    arm: str = "unassigned"
    variant: str = "default"


_ctx = _RunContext()
_rows: list[dict[str, Any]] = []
_lock = threading.Lock()
_installed = False


def set_run(run_id: str, scenario: str, arm: str, variant: str = "default") -> None:
    """Label subsequent model calls. Sticky — see ``run_context``."""
    global _ctx
    _ctx = _RunContext(run_id=run_id, scenario=scenario, arm=arm, variant=variant)


def drain(timeout: float = 20.0, quiet: float = 0.5) -> int:
    """Block until callbacks stop arriving, and return how many rows landed.

    LiteLLM dispatches success callbacks on a background worker, so the row for
    a call can land *after* the code that made it has returned — measured at up
    to one full call behind. Waiting for the queue to go quiet before the next
    run is relabelled is what keeps rows attached to the run that produced them.
    """
    deadline = time.monotonic() + timeout
    last_count = len(rows())
    last_change = time.monotonic()
    while time.monotonic() < deadline:
        time.sleep(0.05)
        current = len(rows())
        if current != last_count:
            last_count, last_change = current, time.monotonic()
        elif time.monotonic() - last_change >= quiet:
            break
    return last_count


async def async_drain(timeout: float = 20.0, quiet: float = 0.5) -> int:
    """``drain`` for use *inside* a running event loop.

    The async success hook is dispatched as a task on the same loop that made
    the call, so ``asyncio.run(...)`` tears it down the moment the coroutine
    returns — the final call of every agent run would lose its row. Awaiting
    this before returning gives those tasks a turn to run. A blocking
    ``time.sleep`` here would not do: it holds the loop and the tasks never
    get scheduled at all.
    """
    deadline = time.monotonic() + timeout
    last_count = len(rows())
    last_change = time.monotonic()
    while time.monotonic() < deadline:
        await asyncio.sleep(0.05)
        current = len(rows())
        if current != last_count:
            last_count, last_change = current, time.monotonic()
        elif time.monotonic() - last_change >= quiet:
            break
    return last_count


@contextmanager
def run_context(run_id: str, scenario: str, arm: str, variant: str = "default") -> Iterator[None]:
    """Label every model call made inside the block as belonging to this run.

    Deliberately does **not** restore the previous label on exit: a callback
    still in flight when the block ends would otherwise be filed under the
    wrong run. The label stays until the next ``set_run``, and the drain on
    exit gives stragglers time to arrive first.
    """
    set_run(run_id, scenario, arm, variant)
    try:
        yield
    finally:
        drain()


def _role_of(kwargs: dict) -> str:
    """The ``ja_role`` tag injected by ``config._model_for``.

    Absent for the baseline arm (a bare ``litellm.completion`` with no agent
    behind it), which is exactly the distinction we want to see in the log.
    """
    metadata = (kwargs.get("litellm_params") or {}).get("metadata") or {}
    role = metadata.get("ja_role")
    if role:
        return str(role)
    return "baseline" if _ctx.arm == "baseline" else "untagged"


def _usage_of(response: Any) -> tuple[int, int]:
    usage = getattr(response, "usage", None)
    if usage is None:
        return 0, 0
    return (
        int(getattr(usage, "prompt_tokens", 0) or 0),
        int(getattr(usage, "completion_tokens", 0) or 0),
    )


def _cost_of(response: Any, model: str) -> float:
    """Price the call from LiteLLM's bundled map.

    Returns 0.0 for a model the map does not know (the Uni-GPT endpoint has no
    published per-token price). A zero here means "not priced", not "free" —
    `run.py` reports how many calls were unpriced so a zero total can never be
    mistaken for a real one.
    """
    try:
        return float(litellm.completion_cost(completion_response=response, model=model))
    except Exception:
        return 0.0


def _record(kwargs: dict, response: Any, start_time: Any, end_time: Any) -> None:
    """Record one completed model call. Must never raise — it runs inside the SDK."""
    try:
        model = str(kwargs.get("model") or getattr(response, "model", "") or "unknown")
        prompt_tokens, completion_tokens = _usage_of(response)
        try:
            latency = (end_time - start_time).total_seconds()
        except Exception:
            latency = 0.0
        row = {
            "run_id": _ctx.run_id,
            "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "scenario": _ctx.scenario,
            "arm": _ctx.arm,
            "variant": _ctx.variant,
            "role": _role_of(kwargs),
            "model": model,
            "input_tokens": prompt_tokens,
            "output_tokens": completion_tokens,
            "cost_usd": round(_cost_of(response, model), 6),
            "latency_s": round(latency, 3),
        }
        with _lock:
            _rows.append(row)
    except Exception:
        # A broken measurement must not fail the run being measured.
        pass


class _Recorder(CustomLogger):
    """Both halves of LiteLLM's success path.

    ``litellm.success_callback`` fires only for the
    *synchronous* ``completion``. ADK calls ``acompletion``, so a sync-only
    callback silently records nothing for the entire agent arm while the runs
    themselves succeed and bill normally. A ``CustomLogger`` registered on
    ``litellm.callbacks`` covers both entry points, which is the only reason
    the agent arm produces cost rows at all.
    """

    def log_success_event(self, kwargs, response_obj, start_time, end_time) -> None:
        _record(kwargs, response_obj, start_time, end_time)

    async def async_log_success_event(self, kwargs, response_obj, start_time, end_time) -> None:
        _record(kwargs, response_obj, start_time, end_time)


def install() -> None:
    """Register the recorder with LiteLLM. Idempotent."""
    global _installed
    if _installed:
        return
    litellm.callbacks = [*(litellm.callbacks or []), _Recorder()]
    _installed = True


def rows() -> list[dict[str, Any]]:
    with _lock:
        return list(_rows)


def reset() -> None:
    with _lock:
        _rows.clear()


def write_csv(path: Path) -> Path:
    """Write the raw per-call log. Rewritten in full after every run, so a
    crash midway through a 30-run matrix still leaves the completed runs on
    disk."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS)
        writer.writeheader()
        writer.writerows(rows())
    return path
