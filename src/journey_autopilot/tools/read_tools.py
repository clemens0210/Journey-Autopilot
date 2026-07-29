"""Function tools for the agents.

In ADK, a typed Python function with a docstring is enough: the framework wraps
it automatically into a FunctionTool and derives the parameter schema from the
type hints and docstring. Therefore docstrings and types here are not decoration
but part of the API that the LLM sees.

DB-related functions are live-first via `integrations.db.ops` and fall back to
`demo.mock_data` when the sidecar is unavailable. Calendar and demo account data
keep the same presentation-safe fallback pattern.

The implementations live in the ``read`` subpackage, one module per read
concern (monitoring, reroute, calendar, rights, pre-trip risk) — see
``tools/read/__init__.py``. This module stays as the facade so the import path
``tools.read_tools`` keeps working for the agents, the write tools, and the UI;
its surface is exactly ``read.__all__``.
"""

from __future__ import annotations

from .read import *  # noqa: F401,F403 — surface is defined by read.__all__
from .read import __all__  # noqa: F401 — re-export the surface list itself
