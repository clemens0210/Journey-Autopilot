"""Specialized worker agents (orchestrator-workers pattern).

Each agent owns one job and receives only the context slice it needs:

- ``monitoring`` — read-only risk detection (live trip + pre-trip delay risk/ETA).
- ``planner``    — read-only reroute generation under the user's constraints.
- ``communicator`` — write: drafts traveler/participant messages, carries the veto.
- ``executor``   — write: executes the approved option through the policy/veto
                   gate; holds the only write tools in the system.
"""
