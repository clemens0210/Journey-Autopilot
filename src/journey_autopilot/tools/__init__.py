"""Agent tools, classified by side effect (read/write separation is the backbone).

- ``read_tools``  — safe, runs autonomously (live status, reroutes, calendar, rights).
- ``write_tools`` — side-effectful, gated by the policy layer (the veto gate); held
                    by the Executor agent. Each call resolves to auto/ask first.
- ``risk_model``  — deterministic delay statistics exposed to Monitoring as a tool
                    (risk scoring is a model/heuristic, never an LLM judgment).
"""
