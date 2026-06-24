"""Journey Autopilot — agent-based system.

`adk web` / `adk run src/journey_autopilot` expect an importable package with an
`agent` module that defines `root_agent` (here a thin shim over
``orchestrator.py``). Install editable (`pip install -e .`) so the src/ layout is
importable, or rely on the run scripts that add src/ to sys.path.
"""

__all__ = ["agent"]
