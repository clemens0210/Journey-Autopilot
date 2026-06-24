"""Naive baseline — "just ask the model once".

A single-shot, single-prompt baseline (no orchestration, no read/write split, no
veto gate) for the trade-off comparison against the multi-agent system: latency,
tokens, cost, intervention rate, success (build spec §10/§11, M6).

STATUS: stub. Lands with M6 (baseline + eval). Keeping the file here makes the
deliverable fall out of the repo structure naturally.
"""

# TODO(M6): one LlmAgent, one prompt containing the whole scenario, no tools
# (or all tools flat). Run the same fixtures as scenarios/ and record metrics
# for eval/run.py to compare.

if __name__ == "__main__":
    raise SystemExit("single_shot baseline is a stub (build spec M6).")
