"""Evaluation harness — the trade-off and cost/token deliverables.

Runs the scenarios and the baseline and records: latency, tokens, cost,
intervention rate, success (build spec §10/§11, M6). Sweeping the global autonomy
level (``config/policy.yaml``) here produces the autonomy/control trade-off
numbers.

STATUS: stub. Lands with M6 (baseline + eval).
"""

# TODO(M6): execute scenarios/* and baseline/* over the fixtures, capture ADK
# usage metadata (tokens) + wall-clock latency, and emit a comparison table.

if __name__ == "__main__":
    raise SystemExit("eval harness is a stub (build spec M6).")
