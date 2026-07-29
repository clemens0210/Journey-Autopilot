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

## Update — write capability split three ways

The Executor is implemented, and building it showed that "the write agents" is
one band too coarse. Write capability now sits in three places, each drawn along
*who is affected*:

- **Executor** — the traveler's own plans: reroute, hotel, calendar reschedule,
  compensation claim. Every one policy-gated. It sends no messages.
- **Communicator** — third parties: the notice email, behind its own
  propose → user approval → send gate.
- **Orchestrator** — the traveler themselves: `send_whatsapp_to_user`. It sits
  on the root agent rather than a worker because it is not part of executing a
  chosen plan; it is how the system reaches the traveler at all, and the channel
  their veto arrives through. That is also why it is the one write that is never
  policy-gated — gating it would deadlock the gate (see ADR 0004).

Keeping "act on the plan" apart from "tell someone" means an execution failure
can never masquerade as a delivered notice, and vice versa.

Passenger rights straddle the read/write line rather than sitting on one side:
*looking up* what the traveler may do (has the delay lifted the ticket's
Zugbindung?) is a read on the Planner and is most useful mid-disruption, while
*filing* the claim afterwards is an Executor write behind the gate.
