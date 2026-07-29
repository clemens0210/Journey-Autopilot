"""Agent tools, classified by side effect (read/write separation is the backbone).

- ``read``        — safe, runs autonomously: live status, delay history,
                    reroute discovery, calendar checks, profile, and the
                    passenger-rights lookup. One module per concern
                    (``monitoring``, ``reroute``, ``calendar``, ``rights``,
                    ``pretrip_risk``); ``read_tools`` re-exports the whole
                    surface and stays the import path everything else uses.
- ``write_tools`` — side-effectful, every one gated by ``policy.resolve``.
                    Grouped by the agent that holds them:
                    ``EXECUTOR_WRITE_TOOLS`` (reroute, hotel, reschedule,
                    compensation claim), ``COMMUNICATOR_WRITE_TOOLS`` (the
                    notice-email propose/send pair) and
                    ``ORCHESTRATOR_WRITE_TOOLS`` (the WhatsApp push to the
                    traveler — never gated, it carries the veto itself).
- ``constraints`` — no tools, no I/O: the pure eligibility rules (transfers,
                    cancellation, mobility opt-outs, latest-arrival-home) that
                    the read side applies when building the shortlist and the
                    write side reapplies before executing the chosen option.
                    Shared so the two cannot silently diverge.

No domain model lives here. The delay statistics behind the risk tools are the
``risk`` package's job (``risk.predictor`` for the historical baseline,
``risk.live_stats`` for today's arrival board); ``read/pretrip_risk`` only wraps
them in the live-or-mock fallback every tool follows.

Passenger rights straddle the line on purpose: looking up what the traveler is
entitled to is a read (Planner), filing the claim is a write (Executor).
"""
