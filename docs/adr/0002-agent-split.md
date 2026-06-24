# ADR 0002 — Agent split: one orchestrator + specialized workers

Status: Accepted

## Context
The system needs to monitor a trip, plan reroutes under constraints, ask for
confirmation, and communicate — too much for one prompt to do well (build spec
§4/§5).

## Decision
Use the **orchestrator-workers** pattern. One Orchestrator (`orchestrator.py`,
`root_agent`) owns the flow and routes; specialized workers each do one job and
receive only the context slice they need:

- **Monitoring** (read-only) — risk detection (pre-trip risk/ETA + en-route live).
- **Planner** (read-only) — ranked reroute options under hard/soft constraints.
- **Communicator** (write) — drafts messages, carries the veto, notifies parties.
- **Executor** (write) — executes the approved option (scaffold; see ADR 0004 / M4).

Read/write separation is structural, not just instructions: Monitoring and
Planner are given **no write tools at all** (capability isolation).

## Consequences
- Workers stay focused and cheap; the Orchestrator keeps the control logic in one
  place.
- The write agents (Communicator, Executor) are the only ones that can cause side
  effects, which is exactly where the policy/veto gate applies.
- The multi-stakeholder Negotiator is explicitly out of scope (build spec §3.5).
