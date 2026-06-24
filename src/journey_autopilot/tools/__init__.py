"""Agent tools, classified by side effect (read/write separation is the backbone).

- ``read_tools``  — safe, runs autonomously (live status, reroutes, calendar, rights).
- ``write_tools`` — side-effectful, gated by the policy layer (stub; see build spec M4).
- ``risk_model``  — deterministic delay statistics exposed to Monitoring as a tool
                    (risk scoring is a model/heuristic, never an LLM judgment).
"""
