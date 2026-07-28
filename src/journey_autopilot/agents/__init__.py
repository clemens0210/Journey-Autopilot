"""Specialized worker agents (orchestrator-workers pattern).

Each agent owns one job, receives only the context slice it needs, and holds
only the tools that job requires — capability isolation is what keeps a read
path from acquiring side effects:

- ``monitoring`` — read-only risk detection (live trip + pre-trip delay risk/ETA).
- ``planner``    — read-only reroute generation under the user's constraints,
                   plus the passenger-rights lookup (during a trip: is the
                   ticket's train binding lifted? after it: what is owed?).
- ``communicator`` — write, third parties only: drafts the notice email to an
                   appointment contact and sends it after the user's approval.
- ``executor``   — write, the traveler's own plans: reroute, hotel, calendar
                   reschedule, compensation claim — each through the
                   policy/veto gate. Sends no messages.

The Orchestrator (``journey_autopilot.orchestrator``) wraps all four as
``AgentTool`` and keeps one tool of its own, the WhatsApp push to the traveler.
"""
