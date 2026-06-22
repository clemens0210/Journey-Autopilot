"""Policy layer — resolves write tools to auto / ask (the veto gate).

Config-driven (``config/policy.yaml``): each ``write`` tool resolves to ``auto``
(runs autonomously) or ``ask`` (pauses for the user's veto), taking the global
``policy_mode`` into account. A single global level shifts all defaults — the
knob swept to produce the autonomy/control trade-off numbers.

STATUS: scaffold for milestone M4. The read/write split already exists at the
agent level (Monitoring/Planner hold no write tools); enforcement of per-tool
``auto``/``ask`` against ``config/policy.yaml`` lands with the Executor and the
write tools. Until then this is a documented placeholder.

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
