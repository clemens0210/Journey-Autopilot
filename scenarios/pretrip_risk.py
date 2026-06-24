"""Verbose standalone demo of the pre-trip risk path — risk & ETA before departure.

Makes every step of the pre-trip risk assessment transparent — BEFORE the trip
has started:

  1) Data basis  — every single connection (long-distance arrival) that went
     into the analysis, with its actual real-world delay.
  2) Metrics     — how the statistics and ETA building blocks are
     deterministically computed from these trips (median, p90, on-time rate ...).
  3) Agent trace — the full ReAct trace of the `monitoring_agent` on the
     pre-trip path: every thought,
     every tool call (with arguments), and every tool result (raw).
  4) Response    — the final assessment delivered to the user (score + ETA).

Usage:
    python scenarios/pretrip_risk.py

Requires: a configured Uni-GPT backend in .env (UNI_GPT_*; see README). If the
db_service sidecar is running, delay data comes live from the DB arrivals
board; otherwise the simulated history is used (see the `source` field).
"""

from __future__ import annotations

import asyncio
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

# Make the src/ layout importable when run directly without an editable install.
_SRC = Path(__file__).resolve().parents[1] / "src"
if _SRC.is_dir() and str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from google.adk.runners import InMemoryRunner
from google.genai import types

try:
    from dotenv import load_dotenv

    load_dotenv()
    load_dotenv("journey_autopilot/.env")
except ImportError:
    pass

from journey_autopilot.tools import read_tools as tools
# Risk is folded into the Monitoring agent; this demo drives that agent on the
# pre-trip path (delay risk + ETA before departure).
from journey_autopilot.agents.monitoring import build_monitoring_agent

APP_NAME = "journey_autopilot_risk"
USER_ID = "lucas"

ORIGIN = "Köln Hbf"
DESTINATION = "Bonn Hbf"
TRAIN = "IC 2007"
DEPARTURE = "10:32"

# Pre-trip request: the journey has not started yet.
PROMPT = (
    f"I've booked a connection from {ORIGIN} to {DESTINATION} on {TRAIN} "
    f"(departure {DEPARTURE}). What's the delay risk and "
    "when am I expected to arrive?"
)


def _hhmm(iso: str | None) -> str:
    """ISO time -> 'HH:MM', tolerant of missing/broken values."""
    if not iso:
        return "??:??"
    try:
        return datetime.fromisoformat(iso).strftime("%H:%M")
    except ValueError:
        return str(iso)


def _eta(iso: str | None, add_minutes: float | None) -> str:
    """Planned arrival + expected delay -> 'HH:MM'."""
    if not iso or add_minutes is None:
        return "??:??"
    try:
        return (datetime.fromisoformat(iso) + timedelta(minutes=add_minutes)).strftime("%H:%M")
    except ValueError:
        return "??:??"


def _delay_label(minutes: float | None, status: str) -> str:
    """Make the delay readable (+X min / on time / status note)."""
    if status != "counted":
        return status
    if minutes is None:
        return status
    if minutes <= 0:
        return "on time" if minutes == 0 else f"{minutes:+.0f} min"
    return f"+{minutes:.0f} min"


def print_data_basis() -> tuple[dict, dict]:
    """Sections 0-3: baseline archive, considered connections, metrics, ETA."""
    reference = tools.get_connection_delay_reference(ORIGIN, DESTINATION, TRAIN)
    # Same live/mock fallback the agent's tool uses, but with details=True so the
    # individual considered arrivals (samples) are available for verbose output.
    history = tools._connection_delay_history(ORIGIN, DESTINATION, TRAIN, details=True)
    planned = tools.get_planned_connection(ORIGIN, DESTINATION, DEPARTURE)

    print("--- 0) Historical punctuality reference (baseline, monthly archive) -")
    if "error" in reference:
        print(f"  ({reference['error']})")
    else:
        print(f"  Destination station: {reference.get('station_name')}  |  Basis: {reference.get('basis')}"
              f"  |  Months: {', '.join(reference.get('months') or [])}")
        print(f"  Sample size (trips)       : {reference.get('sample_count'):,}")
        print(f"  On time (<=5 min)         : {reference.get('on_time_rate_pct')} %")
        print(f"  Median / p90 / mean       : {reference.get('median_delay_minutes')} / "
              f"{reference.get('p90_delay_minutes')} / {reference.get('mean_delay_minutes')} min")
        print(f"  Cancellation rate         : {reference.get('cancellation_rate_pct')} %")
        print(f"  Source                    : {reference.get('source')} "
              f"({reference.get('source_url')}, {reference.get('license')})")
    print()

    print("--- 1) Current situation: considered connections (last hours) ------")
    print(f"Source: {history.get('source')}  |  Window: {history.get('window', '-')}")
    samples = history.get("samples")
    if samples:
        print(f"Long-distance arrivals in {DESTINATION} (N={len(samples)}):")
        for s in samples:
            train = (s.get("train") or "?").ljust(10)
            origin = (s.get("from") or "?").ljust(22)[:22]
            arr = _hhmm(s.get("planned_arrival"))
            print(f"  {train} from {origin} plan {arr}   {_delay_label(s.get('delay_minutes'), s.get('status'))}")
    elif "error" in history:
        print(f"  (no data: {history['error']})")
    else:
        print("  (simulated aggregate history — no individual trips available)")

    print("\n--- 2) Metrics (computed deterministically in risk_model.py) ------")
    if history.get("sample_count"):
        median = history.get("median_delay_minutes")
        p90 = history.get("p90_delay_minutes")
        print(f"  Sample size (trips)       : {history.get('sample_count')}")
        print(f"  On time (<=5 min)         : {history.get('on_time_rate_pct')} %   (share of trips with delay <= 5 min)")
        print(f"  Significantly delayed (>=15): {history.get('delayed_over_15_rate_pct')} %")
        print(f"  Median delay              : {median} min   -> expected delay (typical)")
        print(f"  90th percentile (p90)     : {p90} min   -> unfavorable case (worst-case buffer)")
        print(f"  Mean / max                : {history.get('mean_delay_minutes')} / {history.get('max_delay_minutes')} min")
        print(f"  Cancellations in window   : {history.get('cancellations')}")
        print(f"  Most common causes        : {', '.join(history.get('common_causes') or []) or '-'}")

        planned_arrival = planned.get("planned_arrival")
        print("\n  ETA calculation (planned arrival + expected delay):")
        print(f"    planned arrival         : {_hhmm(planned_arrival)}")
        print(f"    typical ETA (+median)   : {_hhmm(planned_arrival)} + {median} min = {_eta(planned_arrival, median)}")
        print(f"    unfavorable ETA (+p90)  : {_hhmm(planned_arrival)} + {p90} min = {_eta(planned_arrival, p90)}")
        print("  (The agent derives the final score and ETA from exactly these numbers.)")
    else:
        print("  (no reliable metrics — the agent must disclose this)")

    print("\n--- 3) Planned connection (ETA anchor) ------------------------------")
    if "error" in planned:
        print(f"  (no planned connection: {planned['error']})")
    else:
        print(
            f"  Train {planned.get('train')} | Departure {_hhmm(planned.get('planned_departure'))} "
            f"| planned arrival {_hhmm(planned.get('planned_arrival'))} "
            f"| Transfers {planned.get('transfers')} | Source {planned.get('source')}"
        )

    return history, planned


def _describe_event(event) -> None:
    """Verbose: tool calls (with args), tool results (raw), and agent text."""
    author = getattr(event, "author", "?")
    content = getattr(event, "content", None)
    if content is None or not getattr(content, "parts", None):
        return

    for part in content.parts:
        call = getattr(part, "function_call", None)
        response = getattr(part, "function_response", None)
        text = getattr(part, "text", None)

        if call is not None:
            args = json.dumps(dict(call.args or {}), ensure_ascii=False)
            print(f"  [{author}] -> calls: {call.name}({args})")
        elif response is not None:
            payload = getattr(response, "response", None)
            print(f"  [{author}] <- result {response.name}:")
            dump = json.dumps(payload, ensure_ascii=False, indent=2, default=str)
            for line in dump.splitlines():
                print(f"        {line}")
        elif text and text.strip():
            print(f"  [{author}] thinks/answers: {text.strip()}")


async def main() -> None:
    print("=" * 72)
    print("Journey Autopilot — Demo run (Monitoring Agent: pre-trip risk & ETA) [VERBOSE]")
    print("=" * 72)
    print(f"User: {PROMPT}\n")

    print_data_basis()

    monitoring_agent = build_monitoring_agent()
    runner = InMemoryRunner(agent=monitoring_agent, app_name=APP_NAME)
    session = await runner.session_service.create_session(
        app_name=APP_NAME, user_id=USER_ID
    )

    print("\n--- 4) Agent trace (ReAct: think -> tool -> observe) ----------------")
    message = types.Content(role="user", parts=[types.Part(text=PROMPT)])
    final_text = ""
    try:
        async for event in runner.run_async(
            user_id=USER_ID, session_id=session.id, new_message=message
        ):
            _describe_event(event)
            if event.is_final_response() and event.content and event.content.parts:
                final_text = "".join(
                    p.text for p in event.content.parts if getattr(p, "text", None)
                )
    except Exception as exc:
        print("\n[!] Run aborted.")
        print(f"    {type(exc).__name__}: {exc}")
        print("    Check UNI_GPT_BASE_URL (incl. /v1), UNI_GPT_API_KEY and UNI_GPT_MODEL")
        print("    in .env — and whether google-adk[extensions] is installed")
        print("    (pip install -r requirements.txt).")
        return

    print("\n--- 5) Response to the user ------------------------------------------")
    print(final_text or "(no text response)")


if __name__ == "__main__":
    asyncio.run(main())
