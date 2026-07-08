"""Policy layer — resolves write tools to auto / ask (the veto gate).

Config-driven (``config/policy.yaml``): each ``write`` tool resolves to ``auto``
(runs autonomously) or ``ask`` (pauses for the user's veto), taking the global
``policy_mode`` into account. A single global level shifts all defaults — the
knob swept to produce the autonomy/control trade-off numbers.

STATUS: partially live. The read/write split exists at the agent level
(Monitoring/Planner hold no write tools), and the first write action consumes
this module: ``tools/write_tools.py`` resolves ``send_email_to_participants``
here and implements its "ask" as the propose → user-approval → send split.
Config-driven per-tool rules against ``config/policy.yaml`` still land with
the Executor (build spec M4).

See docs/journey-autopilot-build-spec.md §8 and docs/adr/0004-veto-gate.md.
"""

from __future__ import annotations

from typing import Literal

Resolution = Literal["auto", "ask"]
PolicyMode = Literal["conservative", "balanced", "aggressive"]


def resolve(tool_name: str, *, policy_mode: PolicyMode = "balanced", **context) -> Resolution:
    """Resolve a write tool to ``auto`` or ``ask``.

    TODO(M4): load config/policy.yaml and apply per-tool rules (cost thresholds,
    tentative-vs-confirmed calendar events, third-party effects) under
    ``policy_mode``. Conservative default until then: ask for everything except
    messaging the user themselves.
    """
    if tool_name == "send_whatsapp_to_user":
        return "auto"  # the user is the recipient and the veto channel
    return "ask"
