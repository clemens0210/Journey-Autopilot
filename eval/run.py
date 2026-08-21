"""Evaluation harness — the trade-off and cost/token deliverables.

Runs the scenarios and the naive baseline and records tokens, cost, latency,
and a transcript per run, then aggregates the tables the report needs. The
autonomy sweep here is what produces the autonomy/control trade-off numbers
ADR 0004 promises.

Usage
-----
    python -m eval.run                     # full matrix (1 warm-up + 10 core + 8 sweep)
    python -m eval.run --reps 1 --no-sweeps  # 5-run smoke test first
    python -m eval.run --aggregate-only    # rebuild tables from existing CSV

Outputs land in ``eval/output/``:

    calls.csv          one row per *model call* (role-attributed)
    runs.csv           one row per run (tokens, cost, latency, live-data counts)
    scoring_sheet.csv  one row per run with blank check columns, to fill by hand
    transcripts/       full trace per run — the evidence the checks are scored from
    tables.md          the aggregated Markdown tables, ready to paste

Design notes
------------
**One subprocess per run.** ``demo.mock_data`` reads ``JA_FIXTURES`` and anchors
the whole fixture clock at *import* time, so a scenario cannot be switched
inside a live process. Forking per run also gives every run an identical
relative setup — the trip always departed ``JA_DEMO_TRIP_LEAD_MIN`` minutes
before that run started — which is what makes repeated runs comparable even
though the absolute wall clock moves.

**Variants configure, they do not fork the code.** The autonomy sweep sets the
swept level on the demo *profile* — the field ``policy._effective_level`` reads
first — and writes the matching ``policy.yaml`` behind it so the two cannot
disagree. Only the deterministic-vs-agentic variant needs a patch, and it is
applied in this process, to this run, never to the shipped agent.

**One discarded warm-up run.** Bedrock bills the request that first writes a
cache prefix at 1.25x and every later read at 0.1x, so without a warm-up
whichever run goes first subsidises the rest: in the previous matrix that made
``happy_path-agent-default-001`` cost 53% more than its identical repeat, and
handed every later variant a ~20% "saving" that was pure run order. The warm-up
is spawned before the plan and its rows are deleted, so every measured run sees
the same warm cache.

No variant swaps models. Every role runs Sonnet 4.6, fixed by
``config/settings.yaml``, so every run in the matrix is comparable on model
choice by construction and no number here is a statement about model tier.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import os
import ssl as _ssl
import subprocess
import sys
import time
import uuid
from collections import defaultdict
from pathlib import Path
from statistics import mean
from typing import Any

_ROOT = Path(__file__).resolve().parents[1]
_SRC = _ROOT / "src"
if _SRC.is_dir() and str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

OUTPUT_DIR = _ROOT / "eval" / "output"
RAW_DIR = OUTPUT_DIR / "raw"
TRANSCRIPT_DIR = OUTPUT_DIR / "transcripts"

# Per-run facts that no model call carries, so they cannot come from the
# per-call log: the wall clock, the run's own failure, and how much live DB
# data it consumed. Written per run and merged back into ``runs.csv``.
META_FIELDS = (
    "wall_clock_s",
    "error",
    "db_requests",
    "db_errors",
    "db_blocked",
    "db_endpoints",
    # The veto gate's decisions (instrumentation.install_policy_probe). This is
    # the autonomy trade-off's real metric: the level barely moves cost, what it
    # moves is how often the traveler is asked — and the transcript cannot show
    # that, because an "auto" write and an approved "ask" leave the same call.
    "gate_auto",
    "gate_ask",
    "gate_detail",
)

# Pinned so every run sees the same relative timeline (trip departed 90 min
# ago, hard meeting ~4.5 h out). Without this the scenario's difficulty drifts
# with whatever the environment happens to hold, and repeated runs stop being
# repeats. See demo/__init__.py for the clock contract.
DEMO_TRIP_LEAD_MIN = "90"

# The pre-trip cells run the clock the other way: departure two hours from now,
# so the journey has not started. Monitoring picks its branch from the request
# (agents/monitoring.py) and only the pre-trip branch reads the deterministic
# punctuality figures, so this is the clock the `llm_risk` comparison needs.
PRETRIP_LEAD_MIN = "-120"

# The demo account every run travels as (demo/accounts.py). Named here because
# both the per-run reset and the agent turns need the same identity.
# The warm-up run's id. Named, not inlined, because two places have to agree
# about which files to delete afterwards.
_WARMUP_ID = "warmup-000"

DEMO_USER_ID = "u-lucas-wild"
DEMO_EMAIL = "lucas.wild@example.com"
DEMO_PASSWORD = "demo123"

# --- The matrix ---------------------------------------------------------------

# Scenario -> environment that defines it. The failure case is the happy path
# with the live-data sidecar made unreachable: it exercises the documented
# live-then-mock fallback and the `source` disclosure contract rather than an
# invented fault. Port 9 is the discard port — refuses instantly.
SCENARIOS: dict[str, dict[str, str]] = {
    "happy_path": {"JA_FIXTURES": "happy_path"},
    "no_train_alternative": {"JA_FIXTURES": "no_train_alternative"},
    "sidecar_offline": {"JA_FIXTURES": "happy_path", "DB_API_URL": "http://127.0.0.1:9"},
}

# The two arms. The baseline is data-fed and has no other setting: it receives
# every fixture slice the agents' read tools return (baseline/prompts.py), so
# the comparison isolates orchestration from data access instead of measuring
# whether tools beat no tools.
ARMS = ("agent", "baseline")

# The baseline reads no live source, so removing the sidecar cannot change its
# input by a single token — running it here would re-run `happy_path` under a
# second name and invite the reader to treat one condition as two. The agent
# arm still runs it, because for the agent the fallback is the whole point.
NO_BASELINE_SCENARIOS = {"sidecar_offline"}

# Trade-off sweeps. Agent arm, happy path only — stated as a limitation in the
# report rather than silently generalised. Each variant names the group its
# delta is measured against; "default" means the core happy_path agent runs.
#
# There is no model-tier variant: the system runs one model (Sonnet 4.6) on
# every role, so a tier sweep would compare a configuration that does not
# exist. Table 2 still attributes cost per role — with the price per token
# held constant, that difference is purely how much work each role does.
SWEEP_VARIANTS: dict[str, str] = {
    "autonomy_conservative": "default",
    "autonomy_aggressive": "default",
    # `llm_risk` moves Monitoring's arithmetic into the model, so it can only
    # be measured where that arithmetic happens — and in the core matrix it
    # does not happen at all. Monitoring has two branches and picks by what the
    # request carries: a trip_id is a running journey (live status, network
    # disruptions, no statistics), a planned connection is a forecast
    # (`get_historical_delay_baseline` + `get_recent_delay_history` — the
    # deterministic figures ADR 0003 is about). The core matrix is en route, so
    # the first sweep patched an instruction the run never reached and measured
    # nothing. These two cells run pre-trip instead, and `pretrip_llm_risk` is
    # compared against `pretrip_default` rather than against the en-route
    # default: same clock, same question, one variable.
    "pretrip_default": "default",
    "pretrip_llm_risk": "pretrip_default",
}

# Cells that ask the pre-departure question on the pre-departure clock.
PRETRIP_VARIANTS = frozenset({"pretrip_default", "pretrip_llm_risk"})

# The onboarding choice matching each autonomy level, so a swept profile stays
# self-consistent instead of claiming "approve each" while resolving as
# aggressive. policy.py maps these the other way round.
_AUTONOMY_CHOICE = {
    "conservative": "notify_only",
    "balanced": "approve_each",
    "aggressive": "auto_within_limits",
}

# The checks, scored by hand from each transcript. Split into two groups
# because three of them are structurally unavailable to a single model call
# with no tools: reporting those as baseline "failures" would overstate the
# result. They are a capability difference, not a quality difference.
#
# ``deadline_respected`` (no proposed option arrives after the hard constraint)
# was removed rather than left to be scored: the agent reaches the calendar
# through Graph, so with Outlook connected it queried a real, empty calendar
# while the baseline had the fixture's appointments handed to it in prose. The
# check measured that access gap, not deadline reasoning. Restoring it means
# forcing the mock calendar path for eval runs, not re-adding the column.
CHECKS = {
    "quality": [
        "no_fabrication",  # every train/time named exists in the fixture
        "task_completed",  # a concrete, actionable recommendation was produced
    ],
    "capability": [
        "source_disclosed",  # simulated data declared, not presented as live
        "write_gated",  # nothing claimed as booked without approval
        "rights_checked",  # Zugbindung looked up rather than assumed
    ],
}
ALL_CHECKS = CHECKS["quality"] + CHECKS["capability"]


# --- Single run (child process) -----------------------------------------------


def _patch_windows_ssl() -> None:
    """Reuse the cert-store workaround every entry script needs on Windows.

    Without it, aiohttp's ``ssl.create_default_context()`` raises
    ``ASN1: NOT_ENOUGH_DATA`` at ADK/LiteLLM import time.
    """
    if not sys.platform.startswith("win"):
        return
    original = _ssl.SSLContext.load_default_certs

    def patched(self, purpose: _ssl.Purpose = _ssl.Purpose.SERVER_AUTH) -> None:
        try:
            original(self, purpose)
        except _ssl.SSLError as exc:
            if "NOT_ENOUGH_DATA" not in str(exc):
                raise

    _ssl.SSLContext.load_default_certs = patched


def _apply_llm_risk_variant() -> None:
    """Make Monitoring do the delay arithmetic itself instead of reading it.

    The shipped system computes every punctuality figure in ``risk/`` as plain
    Python and lets the agent only interpret the result (ADR 0003). This
    variant moves that work into the model, which is the concrete form of the
    'agentic vs deterministic' trade-off — it should cost more tokens and give
    up reproducibility. Patched here, before the agent graph is imported, so
    the shipped instruction is never modified.

    Only meaningful on a PRE-TRIP run: the instruction it rewrites governs
    Monitoring's forecast branch, and an en-route request never enters it. Run
    this against an already-departed trip and the patch is inert — which is
    exactly what the first sweep measured.
    """
    from journey_autopilot.agents import monitoring

    monitoring.MONITORING_INSTRUCTION = (
        monitoring.MONITORING_INSTRUCTION
        + "\n\nEVALUATION VARIANT — deterministic statistics are unavailable.\n"
        "Do NOT rely on any precomputed mean/median/p90 delay, on-time rate or "
        "risk band supplied by a tool. Instead, take the raw per-train delay "
        "history and compute those statistics yourself, showing the arithmetic "
        "step by step, then derive the risk band and ETA from your own numbers."
    )


def _seed_demo_state() -> None:
    """Re-import the demo account's trips onto *this* run's clock.

    The fixture is rebased to today on every import, but the trips saved in
    SQLite are not: a row left over from an earlier session still carries that
    session's dates. ``get_live_trip_status`` reads the planned times from the
    **stored** trip, so a stale row makes the tool conclude the journey is long
    over — the agent then reports an arrived trip and never plans anything, and
    every run in the matrix quietly measures the wrong scenario.

    This is the simulated bahn.de login the web app performs, reused verbatim:
    it refreshes the imported bookings, drops stale ``DB-…`` ids, and preserves
    both the onboarded profile and any reroute the traveler already took. It
    must run *inside* the run's own process, because the times it composes come
    from the ``DEMO_DAY``/``DEMO_TIME_SHIFT`` anchor fixed at import time here.

    A full ``scripts/reset_demo.py`` would be wrong: it deletes the profile too,
    and the Planner ranks options against that profile.
    """
    from journey_autopilot.persistence import store
    from journey_autopilot.ui.routes.auth import LoginRequest, db_login

    # Drop the account's own bookings first, so the login re-imports them
    # pristine. ``db_login`` deliberately PRESERVES a trip the traveler already
    # rerouted — right for the product, wrong for a repeated experiment: turn 2
    # of every agent run books a reroute, so without this run 1 leaves the demo
    # trip rewritten onto its replacement trains and every later run inherits
    # that. ``rerouted_at`` then makes ``get_live_trip_status`` skip the
    # scripted disruption entirely, and runs 2..N quietly measure a punctual
    # live journey instead of the scenario. Deleting rather than un-stamping,
    # because a reroute rewrites the trip's ``legs`` as well as its markers.
    # Only ``DB-…`` ids: locally booked ``BK-…`` trips and the onboarded
    # profile are the login's to keep.
    imported = [
        trip["trip_id"]
        for trip in store.get_trips(DEMO_USER_ID) or []
        if str(trip.get("trip_id", "")).startswith("DB-")
    ]
    store.delete_trips(DEMO_USER_ID, imported)

    db_login(LoginRequest(email=DEMO_EMAIL, password=DEMO_PASSWORD))

    # The autonomy sweep lands here, on the profile, because that is where
    # ``policy._effective_level`` looks FIRST: profile.policy.
    # global_autonomy_level, then profile.autonomy, and only then the YAML that
    # ``JA_POLICY_PATH`` points at. The seeded profile always carries a level
    # ("balanced", persistence/store.py), so the YAML was never consulted and
    # the first sweep ran conservative and aggressive at balanced under two
    # different names. Set after the login, since that is what writes the
    # profile this run will read.
    #
    # Written on EVERY run, not only a swept one: the profile lives in SQLite
    # and ``db_login`` deliberately preserves it, so a sweep that set
    # "aggressive" would otherwise leak into every run that followed it in the
    # matrix — the same class of cross-run contamination the trip reset above
    # exists to prevent.
    level = os.getenv("JA_EVAL_AUTONOMY") or "balanced"
    profile = store.get_profile(DEMO_USER_ID) or {}
    store.update_profile(DEMO_USER_ID, {
        "autonomy": _AUTONOMY_CHOICE[level],
        "policy": {**(profile.get("policy") or {}), "global_autonomy_level": level},
    })


def _agent_prompt(variant: str = "default") -> str:
    """The request the Orchestrator receives.

    Two phrasings, because Monitoring picks its branch from what the request
    carries (agents/monitoring.py): a ``trip_id`` is a journey already running
    and takes the EN ROUTE branch, while a planned connection that has not
    departed takes the PRE-TRIP branch and with it the punctuality-forecast
    tools. The core matrix asks the en-route question — that is the product's
    headline scenario, and the one the hand-scored checks are written for. The
    pre-trip cells ask the other one, so that the deterministic statistics are
    actually computed and ``llm_risk`` has something to displace.

    The en-route wording is the one scenarios/happy_path.py sends.
    """
    from journey_autopilot.demo.mock_data import DEMO_TRIP

    if variant in PRETRIP_VARIANTS:
        return (
            f"I am booked on {DEMO_TRIP['train']} from {DEMO_TRIP['origin']} "
            f"to {DEMO_TRIP['destination']}, scheduled to depart "
            f"{DEMO_TRIP['planned_departure']} and arrive "
            f"{DEMO_TRIP['planned_arrival']}. The journey has not started yet. "
            "How likely is this connection to be delayed, and when should I "
            "realistically expect to arrive?"
        )
    return (
        f"Please monitor my trip with trip_id {DEMO_TRIP['trip_id']} "
        f"from {DEMO_TRIP['origin']} to {DEMO_TRIP['destination']} "
        f"on {DEMO_TRIP['planned_departure'][:10]} "
        f"(train {DEMO_TRIP['train']}, planned departure {DEMO_TRIP['planned_departure']}, "
        f"planned arrival {DEMO_TRIP['planned_arrival']}) "
        "and tell me if I need to do anything."
    )


def _render_trace(entries: Any, out: list[str]) -> None:
    """Append ``chat_turn``'s trace to the transcript.

    Tool calls and their results are kept, not just the final text: three of
    the six checks are decided by what the agent *did*, which the answer text
    alone cannot show.
    """
    for entry in entries or []:
        if isinstance(entry, dict):
            out.append(
                " | ".join(f"{k}={v}" for k, v in entry.items() if v not in (None, "", [], {}))
            )
        else:
            out.append(str(entry))


async def _run_agent(variant: str = "default") -> tuple[str, list[str]]:
    """Two turns through the product's own chat entry point.

    A single turn stops where the design says it should: the Planner presents a
    shortlist and the traveler is asked to pick. ``book_alternative_connection``
    needs the server-issued ``proposal_id`` that only a selection carries, so a
    one-turn run never reaches the Executor — the whole write path, the policy
    gate, and therefore the autonomy trade-off go unmeasured while the numbers
    still look complete. The second turn selects the recommended option, which
    is what the browser does when the traveler taps a card.

    ``ui.chat.chat_turn`` rather than a bespoke ``InMemoryRunner``: it is the
    path the product actually runs — it seeds the trip/account context, binds
    the request identity the booking authority requires, and carries the
    proposal between turns. Evaluating it means evaluating the real system.
    """
    from journey_autopilot.demo.mock_data import DEMO_TRIP
    from journey_autopilot.persistence import store
    from journey_autopilot.ui import chat

    from eval import instrumentation as inst

    user_id = DEMO_USER_ID
    account = store.get_account(user_id)
    profile = store.get_profile(user_id) or {}
    notify_phone = (profile.get("notifications") or {}).get("phone")
    trip = next(
        (t for t in (store.get_trips(user_id) or []) if t.get("trip_id") == DEMO_TRIP["trip_id"]),
        None,
    )

    transcript: list[str] = []
    prompt = _agent_prompt(variant)
    transcript += [f"USER (turn 1): {prompt}", "--- trace ---"]
    first = await chat.chat_turn(
        None, prompt, trip, account, notify_phone=notify_phone, user_id=user_id
    )
    _render_trace(first.get("trace"), transcript)
    final = first.get("reply") or ""
    transcript += ["--- turn 1 answer ---", final]

    # Turn 2 — the card tap. Without a finalized proposal there is nothing to
    # select, which is itself worth recording: it means the Planner produced no
    # executable shortlist and the run cannot exercise the write path.
    proposal_id = first.get("proposal_id")
    option_id = first.get("recommended_option_id") or next(
        (o.get("option_id") for o in (first.get("options") or []) if o.get("option_id")), None
    )
    if proposal_id and option_id:
        follow_up = f"Yes, please book option {option_id}."
        transcript += ["", f"USER (turn 2): {follow_up}", "--- trace ---"]
        second = await chat.chat_turn(
            first.get("session_id"),
            follow_up,
            None,
            account,
            notify_phone=notify_phone,
            user_id=user_id,
            proposal_id=proposal_id,
            selected_option_id=option_id,
        )
        _render_trace(second.get("trace"), transcript)
        final = second.get("reply") or final
        transcript += ["--- turn 2 answer ---", second.get("reply") or "(none)"]
    else:
        transcript += [
            "",
            f"NO SELECTION MADE — proposal_id={proposal_id!r} option_id={option_id!r}; "
            "the write path was not exercised in this run.",
        ]

    # Inside the loop on purpose: the async success hooks are tasks on *this*
    # loop, and returning here lets asyncio.run tear it down before they run.
    await inst.async_drain()
    return final, transcript


def _run_baseline() -> tuple[str, list[str]]:
    """One model call, same facts, no tools, no gate."""
    import litellm

    from baseline.prompts import build_prompt
    from baseline.single_shot import MODEL, AWS_REGION

    prompt = build_prompt()
    response = litellm.completion(
        model=MODEL,
        messages=[{"role": "user", "content": prompt}],
        aws_region_name=AWS_REGION,
    )
    content = response.choices[0].message.content
    answer = content if isinstance(content, str) else str(content)
    return answer, [f"USER: {prompt}", "--- single call, no tools ---"]


def run_once(scenario: str, arm: str, run_id: str, variant: str) -> int:
    """Execute one run in this process and persist its rows + transcript."""
    _patch_windows_ssl()
    os.environ.setdefault("LITELLM_LOG", "CRITICAL")

    from eval import instrumentation as inst

    inst.install()
    inst.install_db_probe()
    inst.install_policy_probe()

    if variant == "pretrip_llm_risk" and arm == "agent":
        _apply_llm_risk_variant()

    seed_error = ""
    try:
        _seed_demo_state()
    except Exception as exc:  # recorded, not fatal — the transcript shows it
        seed_error = f"demo state not re-seeded: {type(exc).__name__}: {exc}"
        print(f"  [!] {run_id}: {seed_error}", file=sys.stderr)

    error = ""
    started = time.monotonic()
    with inst.run_context(run_id, scenario, arm, variant):
        try:
            if arm == "agent":
                final, transcript = asyncio.run(_run_agent(variant))
            else:
                final, transcript = _run_baseline()
        except Exception as exc:  # a failed run is data, not a crash
            final, transcript = "", [f"RUN FAILED: {type(exc).__name__}: {exc}"]
            error = f"{type(exc).__name__}: {exc}"

    TRANSCRIPT_DIR.mkdir(parents=True, exist_ok=True)
    path = TRANSCRIPT_DIR / f"{run_id}.txt"
    header = [f"# {run_id} | {scenario} | {arm} | {variant}"]
    if seed_error:
        # Surfaced in the transcript because it invalidates the run: a stale
        # trip makes the agent answer about a journey that already ended.
        header.append(f"# WARNING: {seed_error}")
    path.write_text(
        "\n".join([*header, *transcript, "--- final answer ---", final or "(none)"]),
        encoding="utf-8",
    )
    inst.write_csv(RAW_DIR / f"{run_id}.csv")
    # Wall clock is what the traveler actually waits. Summing the per-call
    # latencies would overstate it wherever the Orchestrator issues calls in
    # parallel, and understate the gaps between them — so both are reported,
    # under names that say which is which.
    with (RAW_DIR / f"{run_id}.meta.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["run_id", *META_FIELDS])
        writer.writeheader()
        writer.writerow({
            "run_id": run_id,
            "wall_clock_s": round(time.monotonic() - started, 2),
            "error": error or seed_error,
            # How much live DB this run actually consumed, and whether it got
            # it: a run with db_errors > 0 answered from the fixtures for at
            # least one tool, which is the scenario for `sidecar_offline` and a
            # contaminated measurement anywhere else. See instrumentation.
            **inst.db_stats(),
            **inst.gate_stats(),
        })
    if error:
        print(f"  [!] {run_id}: {error}", file=sys.stderr)
    return 1 if error else 0


# --- Variant environments (parent process) ------------------------------------


def _variant_env(variant: str) -> dict[str, str]:
    """Config-file overrides that realise a variant, written to eval/output/."""
    import yaml

    if variant != "default" and variant not in SWEEP_VARIANTS:
        raise SystemExit(f"unknown variant: {variant}")
    if not variant.startswith("autonomy_"):
        return {}

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    level = variant.split("_", 1)[1]
    source = yaml.safe_load((_ROOT / "config" / "policy.yaml").read_text(encoding="utf-8"))
    source["global_autonomy_level"] = level
    path = OUTPUT_DIR / f"policy.{variant}.yaml"
    path.write_text(yaml.safe_dump(source, sort_keys=False), encoding="utf-8")
    # Both, deliberately: JA_EVAL_AUTONOMY sets the profile field that decides,
    # and the YAML sets the config default underneath it, so the two layers
    # cannot disagree about what this run was.
    return {"JA_POLICY_PATH": str(path), "JA_EVAL_AUTONOMY": level}


def _spawn(scenario: str, arm: str, run_id: str, variant: str) -> int:
    env = {
        **os.environ,
        **SCENARIOS[scenario],
        **_variant_env(variant),
        "JA_DEMO_TRIP_LEAD_MIN": (
            PRETRIP_LEAD_MIN if variant in PRETRIP_VARIANTS else DEMO_TRIP_LEAD_MIN
        ),
        "LITELLM_LOG": "CRITICAL",
        "PYTHONPATH": os.pathsep.join([str(_SRC), str(_ROOT)]),
    }
    cmd = [sys.executable, "-m", "eval.run", "--once",
           "--scenario", scenario, "--arm", arm, "--run-id", run_id, "--variant", variant]
    return subprocess.run(cmd, cwd=str(_ROOT), env=env).returncode


# --- Aggregation ---------------------------------------------------------------


def _load_meta() -> dict[str, dict[str, Any]]:
    """The per-run meta rows (wall clock, error, live-data counts), by run_id."""
    meta: dict[str, dict[str, Any]] = {}
    for path in sorted(RAW_DIR.glob("*.meta.csv")):
        with path.open(encoding="utf-8") as handle:
            for row in csv.DictReader(handle):
                meta[row["run_id"]] = row
    return meta


def _load_calls() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    # `*.meta.csv` sits in the same directory and has different columns —
    # sweeping it in here would inject junk rows into every aggregate.
    for path in sorted(p for p in RAW_DIR.glob("*.csv") if not p.name.endswith(".meta.csv")):
        with path.open(encoding="utf-8") as handle:
            for row in csv.DictReader(handle):
                # `.get` rather than `[...]`: raw files written before the cache
                # columns existed have no such key. Assigning it here also
                # normalizes every row to the same key set, which the calls.csv
                # writer below needs — it takes its fieldnames from row zero.
                for key in ("input_tokens", "output_tokens",
                            "cache_read_input_tokens", "cache_creation_input_tokens"):
                    row[key] = int(row.get(key) or 0)
                for key in ("cost_usd", "latency_s"):
                    row[key] = float(row[key] or 0.0)
                rows.append(row)
    return rows


# Verdict vocabulary. The sheet is filled by a human, so accept the spellings a
# human actually types; anything else is treated as unscored rather than
# silently counted as a pass.
_PASS = {"pass", "y", "yes", "true", "1", "ok"}
_FAIL = {"fail", "n", "no", "false", "0"}
_NA = {"n/a", "na", "-", "excluded"}


def _load_scores() -> dict[str, dict[str, str]]:
    """The hand-filled scoring sheet, by run_id. Empty when it is not there."""
    sheet = OUTPUT_DIR / "scoring_sheet.csv"
    if not sheet.exists():
        return {}
    with sheet.open(encoding="utf-8") as handle:
        return {row["run_id"]: row for row in csv.DictReader(handle)}


def _tally(rows: list[dict[str, str]], check: str) -> tuple[int, int, int, int]:
    """(passed, failed, not_applicable, unscored) for one check over some rows."""
    passed = failed = na = unscored = 0
    for row in rows:
        value = (row.get(check) or "").strip().lower()
        if value in _PASS:
            passed += 1
        elif value in _FAIL:
            failed += 1
        elif value in _NA:
            na += 1
        else:
            unscored += 1
    return passed, failed, na, unscored


def _per_run(calls: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    runs: dict[str, dict[str, Any]] = {}
    for row in calls:
        agg = runs.setdefault(
            row["run_id"],
            {"run_id": row["run_id"], "scenario": row["scenario"], "arm": row["arm"],
             "variant": row["variant"], "calls": 0, "input_tokens": 0,
             "output_tokens": 0, "cache_read_input_tokens": 0,
             "cache_creation_input_tokens": 0,
             "cost_usd": 0.0, "model_time_s": 0.0,
             "wall_clock_s": 0.0, "unpriced_calls": 0,
             # Seeded so runs.csv keeps stable columns even for a run whose
             # meta row is missing (a child killed before it wrote one).
             "db_requests": 0, "db_errors": 0, "db_blocked": 0, "db_endpoints": "",
             "gate_auto": 0, "gate_ask": 0, "gate_detail": ""},
        )
        agg["calls"] += 1
        agg["input_tokens"] += row["input_tokens"]
        agg["output_tokens"] += row["output_tokens"]
        agg["cache_read_input_tokens"] += row["cache_read_input_tokens"]
        agg["cache_creation_input_tokens"] += row["cache_creation_input_tokens"]
        agg["cost_usd"] += row["cost_usd"]
        agg["model_time_s"] += row["latency_s"]
        if row["cost_usd"] == 0.0:
            agg["unpriced_calls"] += 1
    for run_id, meta in _load_meta().items():
        if run_id not in runs:
            continue
        runs[run_id]["wall_clock_s"] = float(meta.get("wall_clock_s") or 0.0)
        for field in ("db_requests", "db_errors", "db_blocked", "gate_auto", "gate_ask"):
            runs[run_id][field] = int(meta.get(field) or 0)
        runs[run_id]["db_endpoints"] = meta.get("db_endpoints") or ""
        runs[run_id]["gate_detail"] = meta.get("gate_detail") or ""
    return runs


def _table(headers: list[str], rows: list[list[str]]) -> str:
    out = ["| " + " | ".join(headers) + " |",
           "|" + "|".join("---" for _ in headers) + "|"]
    out += ["| " + " | ".join(row) + " |" for row in rows]
    return "\n".join(out)


def _fmt(value: float, places: int = 2) -> str:
    return f"{value:,.{places}f}"


def aggregate() -> str:
    calls = _load_calls()
    if not calls:
        return "_No calls recorded — run the matrix first._"
    runs = _per_run(calls)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    with (OUTPUT_DIR / "calls.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(calls[0].keys()))
        writer.writeheader()
        writer.writerows(calls)
    run_rows = sorted(runs.values(), key=lambda r: (r["scenario"], r["arm"], r["run_id"]))
    with (OUTPUT_DIR / "runs.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(run_rows[0].keys()))
        writer.writeheader()
        writer.writerows(run_rows)

    # Scoring sheet: one row per run, checks blank. Pre-filled with "n/a" where
    # a check cannot apply to an arm, so a blank always means "not yet scored"
    # rather than "not applicable".
    sheet = OUTPUT_DIR / "scoring_sheet.csv"
    if not sheet.exists():
        with sheet.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(
                handle, fieldnames=["run_id", "scenario", "arm", "variant", *ALL_CHECKS, "notes"]
            )
            writer.writeheader()
            for run in run_rows:
                row = {k: run[k] for k in ("run_id", "scenario", "arm", "variant")}
                for check in ALL_CHECKS:
                    row[check] = "n/a" if (
                        run["arm"] == "baseline" and check in CHECKS["capability"]
                    ) else ""
                writer.writerow(row)

    parts: list[str] = ["## Table 1 — Agent vs. naive baseline (core runs)", ""]
    core = [r for r in run_rows if r["variant"] == "default"]
    rows = []
    for scenario in SCENARIOS:
        for arm in ARMS:
            group = [r for r in core if r["scenario"] == scenario and r["arm"] == arm]
            if not group:
                continue
            rows.append([
                scenario, arm, str(len(group)),
                _fmt(mean(r["calls"] for r in group), 1),
                _fmt(mean(r["input_tokens"] + r["output_tokens"] for r in group), 0),
                "$" + _fmt(mean(r["cost_usd"] for r in group), 4),
                _fmt(mean(r["wall_clock_s"] for r in group), 1),
            ])
    parts += [
        _table(
            ["scenario", "arm", "n", "calls/run", "tokens/run", "cost/run", "wall clock s"],
            rows,
        ),
        "",
    ]

    parts += ["## Table 2 — Where the tokens go (agent arm, core runs)", ""]
    by_role: dict[str, dict[str, float]] = defaultdict(
        lambda: {"calls": 0, "in": 0, "out": 0, "cached": 0, "cost": 0.0}
    )
    core_ids = {r["run_id"] for r in core if r["arm"] == "agent"}
    for row in calls:
        if row["run_id"] not in core_ids:
            continue
        entry = by_role[row["role"]]
        entry["calls"] += 1
        entry["in"] += row["input_tokens"]
        entry["out"] += row["output_tokens"]
        entry["cached"] += row["cache_read_input_tokens"]
        entry["cost"] += row["cost_usd"]
    total_cost = sum(e["cost"] for e in by_role.values()) or 1.0
    # "cached in tok" is the share of "input tok" that billed at the cache-read
    # rate rather than the full input rate — the two are not additive.
    rows = [
        [role, str(int(e["calls"])), _fmt(e["in"], 0), _fmt(e["cached"], 0), _fmt(e["out"], 0),
         "$" + _fmt(e["cost"], 4), _fmt(100 * e["cost"] / total_cost, 1) + "%"]
        for role, e in sorted(by_role.items(), key=lambda kv: -kv[1]["cost"])
    ]
    parts += [
        _table(
            ["role", "calls", "input tok", "cached in tok", "output tok", "cost", "share"],
            rows,
        ),
        "",
    ]

    sweeps = [r for r in run_rows if r["variant"] != "default"]
    if sweeps:
        parts += ["## Table 3 — Trade-off sweeps (happy path, agent arm)", ""]
        # Two reference groups, not one: the autonomy cells are measured
        # against the en-route default, the pre-trip cells against each other.
        # A single reference would compare `pretrip_llm_risk` with a run that
        # asked a different question on a different clock, and report the
        # scenario difference as the variant's effect.
        groups: dict[str, list[dict[str, Any]]] = {
            "default": [r for r in core if r["scenario"] == "happy_path" and r["arm"] == "agent"]
        }
        for variant in SWEEP_VARIANTS:
            if group := [r for r in sweeps if r["variant"] == variant]:
                groups[variant] = group

        def _mean_cost(name: str) -> float:
            group = groups.get(name) or []
            return mean(r["cost_usd"] for r in group) if group else 0.0

        labels = {"default": "default (en route, balanced autonomy, deterministic risk)"}
        rows = []
        for name in ("default", *SWEEP_VARIANTS):
            group = groups.get(name)
            if not group:
                continue
            against = SWEEP_VARIANTS.get(name, "")
            reference = _mean_cost(against) if against else 0.0
            cost = _mean_cost(name)
            delta = (
                f"{100 * (cost - reference) / reference:+.0f}%"
                if against and reference else "—"
            )
            rows.append([
                labels.get(name, name),
                str(len(group)),
                _fmt(mean(r["input_tokens"] + r["output_tokens"] for r in group), 0),
                "$" + _fmt(cost, 4),
                delta,
                against or "—",
                # Averaged rather than summed: it is the number of approvals
                # ONE traveler faces per run, which is what the trade-off is
                # about, not how many the whole matrix produced.
                _fmt(mean(r["gate_ask"] for r in group), 1),
            ])
        parts += [
            _table(
                ["variant", "n", "tokens/run", "cost/run", "Δ cost", "vs", "asks/run"],
                rows,
            ),
            "",
            "_Costs are warm-cache: a discarded warm-up run precedes the matrix so "
            "no measured run pays the cache-write premium the rest then read from._",
            "",
        ]

    # Table 4 — the hand-scored checks. Read back from the sheet rather than
    # recomputed: these verdicts come from a person reading transcripts, and
    # this is the only place they reach the report. A check whose column is
    # entirely "n/a" is shown as such, so a check excluded on methodological
    # grounds stays visible instead of vanishing into a blank.
    scores = _load_scores()
    if scores:
        parts += ["## Table 4 — Hand-scored checks (core runs)", ""]
        scored_core = [
            scores[r["run_id"]] for r in core if r["run_id"] in scores
        ]
        rows = []
        pending = 0
        for group, checks in CHECKS.items():
            for check in checks:
                cells = [group, check]
                for arm in ARMS:
                    arm_rows = [s for s in scored_core if s.get("arm") == arm]
                    if not arm_rows:
                        cells.append("—")
                        continue
                    passed, failed, na, missing = _tally(arm_rows, check)
                    pending += missing
                    decided = passed + failed
                    if decided:
                        cell = f"{passed}/{decided}"
                        if missing:
                            cell += f" (+{missing} unscored)"
                    elif na:
                        cell = "n/a"
                    else:
                        cell = "unscored"
                    cells.append(cell)
                rows.append(cells)
        parts += [_table(["group", "check", *ARMS], rows), ""]
        excluded = [
            check for checks in CHECKS.values() for check in checks
            if all(
                (s.get(check) or "").strip().lower() in _NA
                for s in scored_core
            ) and scored_core
        ]
        if excluded:
            parts += [
                "_Excluded from scoring (recorded as `n/a` for every run, see the "
                "sheet's `notes` column for why): "
                + ", ".join(f"`{c}`" for c in excluded)
                + "._",
                "",
            ]
        if pending:
            parts += [f"_{pending} check cells still unscored._", ""]

    unpriced = sum(r["unpriced_calls"] for r in run_rows)
    # Live-data provenance. `sidecar_offline` is *supposed* to appear here (its
    # sidecar is unreachable by design); any OTHER scenario in this count
    # answered from the fixtures without saying so, and its row in Table 1 is
    # measuring the fallback rather than the condition it is named after.
    degraded = sorted(
        {r["scenario"] for r in run_rows if r["db_errors"]} - NO_BASELINE_SCENARIOS
    )
    blocked = sum(r["db_blocked"] for r in run_rows)
    live_note = (
        f"Live DB requests: {sum(r['db_requests'] for r in run_rows)} "
        f"({sum(r['db_errors'] for r in run_rows)} failed"
        + (f", {blocked} anti-bot blocked" if blocked else "")
        + "). "
    )
    live_note += (
        "Scenarios that fell back to fixtures unexpectedly: "
        + ", ".join(f"`{name}`" for name in degraded)
        + " — those runs measure the fallback, not the live path."
        if degraded
        else "No unexpected fixture fallbacks."
    )
    if any(r["arm"] == "baseline" for r in run_rows):
        # Stated on every report rather than left implicit: a baseline number
        # only means what it means because the arm was handed the same facts.
        parts += [
            "_The baseline was given every fixture slice the agents' read tools "
            "return, so the comparison isolates orchestration from data access._",
            "",
        ]

    parts += [
        f"_{len(run_rows)} runs, {len(calls)} model calls. "
        f"Total measured spend: ${_fmt(sum(r['cost_usd'] for r in run_rows), 4)}. "
        f"Unpriced calls (model absent from LiteLLM's cost map): {unpriced}._",
        "",
        f"_{live_note}_",
        "",
        "_Quality and capability checks are scored by hand into "
        "`eval/output/scoring_sheet.csv` from the transcripts; Table 4 "
        "reports them once filled._",
    ]

    text = "\n".join(parts)
    (OUTPUT_DIR / "tables.md").write_text(text, encoding="utf-8")
    return text


# --- Entry point ---------------------------------------------------------------


def _warm_up() -> None:
    """One discarded run, so no measured run pays for the others' cache.

    Bedrock bills the request that first writes a cache prefix at 1.25x and
    every later read at 0.1x, and the prefix outlives a run: it is the shared
    instruction and tool schemas, not this run's conversation. So whichever run
    goes first subsidises the whole matrix — in the previous set that made
    `happy_path-agent-default-001` cost 53% more than its identical repeat,
    dragged the Table 3 reference up with it, and gave every later variant a
    ~20% "saving" that was nothing but run order.

    A happy-path agent run warms what matters: the pre-trip cells ask a
    different question, but the cached prefix is the instruction and the tool
    schemas, which they share. Its rows are deleted rather than filtered at
    aggregation time — a file that must be remembered to be excluded eventually
    is not.
    """
    print("[warm-up] one discarded run to warm the prompt cache")
    _spawn("happy_path", "agent", _WARMUP_ID, "default")
    for path in RAW_DIR.glob(f"{_WARMUP_ID}*.csv"):
        path.unlink()
    (TRANSCRIPT_DIR / f"{_WARMUP_ID}.txt").unlink(missing_ok=True)


def main() -> None:
    # The tables carry non-ASCII (Δ, —) and the Windows console defaults to
    # cp1252, which raises rather than degrading. tables.md stays UTF-8 either
    # way; this only concerns what is echoed to the terminal.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true", help="internal: execute a single run")
    parser.add_argument("--scenario", default="happy_path")
    parser.add_argument("--arm", default="agent", choices=ARMS)
    parser.add_argument("--run-id", default="")
    parser.add_argument("--variant", default="default")
    parser.add_argument("--reps", type=int, default=2, help="core runs per scenario per arm")
    parser.add_argument(
        "--baseline-reps", type=int, default=None,
        help="runs per baseline cell (defaults to --reps); the baseline is a "
             "reference point, not a claim-bearing arm, so it may weigh less",
    )
    parser.add_argument(
        "--sweep-reps", type=int, default=None,
        help="runs per sweep variant (defaults to --reps); sweeps are not hand-scored",
    )
    parser.add_argument("--no-sweeps", action="store_true", help="core matrix only")
    parser.add_argument(
        "--no-warmup", action="store_true",
        help="skip the discarded cache-warming run (leaves run 001 paying the "
             "cache writes every later run reads)",
    )
    parser.add_argument("--aggregate-only", action="store_true")
    args = parser.parse_args()

    if args.once:
        raise SystemExit(run_once(args.scenario, args.arm, args.run_id or uuid.uuid4().hex[:8], args.variant))

    if args.aggregate_only:
        print(aggregate())
        return

    baseline_reps = args.baseline_reps if args.baseline_reps is not None else args.reps
    plan: list[tuple[str, str, str]] = [
        (scenario, arm, "default")
        for scenario in SCENARIOS
        for arm in ARMS
        if not (arm == "baseline" and scenario in NO_BASELINE_SCENARIOS)
        for _ in range(baseline_reps if arm == "baseline" else args.reps)
    ]
    if not args.no_sweeps:
        # Sweeps carry their own rep count: they are read off the aggregated
        # numbers, never hand-scored, so adding one costs machine time only —
        # unlike a core run, which costs a transcript someone has to read.
        sweep_reps = args.sweep_reps if args.sweep_reps is not None else args.reps
        plan += [
            ("happy_path", "agent", variant)
            for variant in SWEEP_VARIANTS
            for _ in range(sweep_reps)
        ]

    RAW_DIR.mkdir(parents=True, exist_ok=True)
    print(
        f"Planned runs: {len(plan)}  (reps={args.reps}, "
        f"baseline reps={baseline_reps}, "
        f"sweeps={'no' if args.no_sweeps else 'yes'}, "
        f"warm-up={'no' if args.no_warmup else 'yes'})"
    )
    if not args.no_warmup:
        _warm_up()
    failures = 0
    for index, (scenario, arm, variant) in enumerate(plan, start=1):
        run_id = f"{scenario}-{arm}-{variant}-{index:03d}"
        print(f"[{index}/{len(plan)}] {run_id}")
        failures += 1 if _spawn(scenario, arm, run_id, variant) else 0

    print(f"\nCompleted {len(plan)} runs ({failures} failed).\n")
    print(aggregate())
    print(f"\nArtifacts in {OUTPUT_DIR}")


if __name__ == "__main__":
    main()
