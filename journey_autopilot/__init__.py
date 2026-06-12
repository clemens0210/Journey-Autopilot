"""Journey Autopilot — agent-based system (baseline).

`adk web` / `adk run journey_autopilot` expect an importable package with
an `agent` module that defines `root_agent`.
"""

from . import agent

__all__ = ["agent"]
