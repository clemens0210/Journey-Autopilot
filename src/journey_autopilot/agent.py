"""ADK entry point.

`adk web` / `adk run journey_autopilot` discover the agent via a module named
``agent`` that exposes ``root_agent``. The actual orchestrator lives in
``orchestrator.py``; this thin shim keeps the ADK discovery contract stable
while the implementation sits under its architecture-aligned name.
"""

from __future__ import annotations

from .orchestrator import root_agent

__all__ = ["root_agent"]
