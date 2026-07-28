"""Agent tools, classified by side effect (read/write separation is the backbone).

- ``read_tools``  — safe, runs autonomously: live status, delay history,
                    reroute discovery, calendar checks, profile, and the
                    passenger-rights lookup.
- ``write_tools`` — side-effectful, every one gated by ``policy.resolve``.
                    Grouped by the agent that holds them:
                    ``EXECUTOR_WRITE_TOOLS`` (reroute, hotel, reschedule,
                    compensation claim), ``COMMUNICATOR_WRITE_TOOLS`` (the
                    notice-email propose/send pair) and
                    ``ORCHESTRATOR_WRITE_TOOLS`` (the WhatsApp push to the
                    traveler — never gated, it carries the veto itself).
- ``risk_model``  — deterministic delay statistics exposed to Monitoring as a tool
                    (risk scoring is a model/heuristic, never an LLM judgment).
- ``constraints`` — no tools, no I/O: the pure eligibility rules (transfers,
                    cancellation, mobility opt-outs, latest-arrival-home) that
                    the read side applies when building the shortlist and the
                    write side reapplies before executing the chosen option.
                    Shared so the two cannot silently diverge.

Passenger rights straddle the line on purpose: looking up what the traveler is
entitled to is a read (Planner), filing the claim is a write (Executor).
"""
