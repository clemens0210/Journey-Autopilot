"""Journey Autopilot — agentisches System (Basis).

`adk web` / `adk run journey_autopilot` erwarten ein importierbares Paket mit
einem `agent`-Modul, das `root_agent` definiert.
"""

from . import agent

__all__ = ["agent"]
