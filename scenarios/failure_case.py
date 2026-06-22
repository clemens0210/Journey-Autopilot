"""Failure-case scenario — broken tool call -> recovery.

One of the three runnable scenarios (build spec §10/§11, M5). A tool call fails
(e.g. the db_service sidecar is down) and the system recovers inside the loop:
retry -> fallback (cached/mock) -> graceful degradation, without crashing.

STATUS: stub. The fallback pattern already exists inline in the tools (sidecar
-> mock, tagged via ``source``); M5 extracts the reusable wrapper (``errors.py``)
and wires this scenario around it.
"""

# TODO(M5): force a tool failure and assert the run completes with a degraded,
# user-facing explanation plus a ToolFailure logged in the Context Record.

if __name__ == "__main__":
    raise SystemExit("failure_case scenario is a stub (build spec M5).")
