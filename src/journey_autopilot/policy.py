"""Policy layer — resolves write tools to auto / ask (the veto gate).

Config-driven (``config/policy.yaml``): each ``write`` tool resolves to ``auto``
(runs autonomously) or ``ask`` (pauses for the user's veto), taking a global
autonomy level into account. A single global level shifts all defaults — the
knob swept to produce the autonomy/control trade-off numbers.

Resolution precedence (highest first):
  1. ``send_whatsapp_to_user`` is ALWAYS ``auto`` — the user is the recipient and
     the veto channel itself; gating it would deadlock the gate.
  2. A per-tool override the user set in the UI (``profile.policy.write_tools``).
  3. The per-tool default from ``config/policy.yaml`` (cost threshold for
     bookings, tentative/confirmed for calendar events), shifted by the effective
     global autonomy level.

Effective global level = ``profile.policy.global_autonomy_level`` if set, else the
level mapped from the onboarding ``profile.autonomy`` choice, else the
``global_autonomy_level`` from ``config/policy.yaml``. The shift means:
  - ``conservative`` → everything asks (except the user-message channel),
  - ``balanced``    → use the per-tool config defaults,
  - ``aggressive``  → "auto within limits": flip the defaults to ``auto`` except
    the genuinely high-commitment actions (hotel, emails to third parties,
    rescheduling a *confirmed* calendar event), which keep asking.

See docs/journey-autopilot-build-spec.md §8 and docs/adr/0004-veto-gate.md.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Literal

logger = logging.getLogger(__name__)

Resolution = Literal["auto", "ask"]
PolicyMode = Literal["conservative", "balanced", "aggressive"]

# The canonical set of write (side-effecting) tools, gated by this layer.
WRITE_TOOLS = (
    "send_whatsapp_to_user",
    "send_email_to_participants",
    "book_alternative_connection",
    "book_hotel",
    "reschedule_outlook_event",
    "file_compensation_claim",
)

# Tools that stay ``ask`` even under the ``aggressive`` level — high commitment
# (cost + overnight), third-party effects, or an irreversible calendar change.
_ALWAYS_ASK_AGGRESSIVE = ("book_hotel", "send_email_to_participants")

# The onboarding autonomy choice (3 tiles in the wizard) maps onto a policy mode.
_AUTONOMY_TO_MODE: dict[str, PolicyMode] = {
    "notify_only": "conservative",
    "approve_each": "balanced",
    "auto_within_limits": "aggressive",
}

# Baked-in defaults mirror config/policy.yaml so the layer still resolves if the
# file is missing or PyYAML is unavailable (the agent must import cleanly for ADK
# discovery regardless of optional deps).
_DEFAULTS: dict = {
    "global_autonomy_level": "balanced",
    "write_tools": {
        "send_whatsapp_to_user": "auto",
        "file_compensation_claim": "auto",
        "reschedule_outlook_event": {"tentative": "auto", "confirmed": "ask"},
        "book_alternative_connection": {"ask_if_cost_over_eur": 50},
        "book_hotel": "ask",
        "send_email_to_participants": "ask",
    },
}

_BASE_DIR = Path(__file__).resolve().parent
_POLICY_PATH = Path(
    os.getenv("JA_POLICY_PATH", str(_BASE_DIR.parent.parent / "config" / "policy.yaml"))
)


def load_policy_config() -> dict:
    """Read ``config/policy.yaml``; fall back to ``_DEFAULTS`` on any problem."""
    try:
        import yaml  # transitive dep; optional at runtime

        loaded = yaml.safe_load(_POLICY_PATH.read_text(encoding="utf-8")) or {}
        merged = {
            "global_autonomy_level": loaded.get(
                "global_autonomy_level", _DEFAULTS["global_autonomy_level"]
            ),
            "write_tools": {**_DEFAULTS["write_tools"], **(loaded.get("write_tools") or {})},
        }
        return merged
    except FileNotFoundError:
        logger.info("policy.yaml not found at %s; using defaults.", _POLICY_PATH)
    except Exception as exc:  # missing PyYAML, parse error, ...
        logger.warning("Could not read policy.yaml (%s); using defaults.", exc)
    return {k: ({**v} if isinstance(v, dict) else v) for k, v in _DEFAULTS.items()}


def _effective_level(profile: dict | None, cfg: dict) -> PolicyMode:
    """Resolve the global autonomy level that applies to this user."""
    policy = (profile or {}).get("policy") or {}
    level = policy.get("global_autonomy_level")
    if level in _AUTONOMY_TO_MODE.values():
        return level  # type: ignore[return-value]
    autonomy = (profile or {}).get("autonomy")
    if autonomy in _AUTONOMY_TO_MODE:
        return _AUTONOMY_TO_MODE[autonomy]
    return cfg.get("global_autonomy_level", "balanced")  # type: ignore[return-value]


def _config_resolution(
    cfg: dict, tool_name: str, *, cost_eur: float | None, event_status: str | None
) -> Resolution:
    """The per-tool default from config, before the global level is applied."""
    rule = cfg.get("write_tools", {}).get(tool_name)
    if rule in ("auto", "ask"):
        return rule  # type: ignore[return-value]
    if isinstance(rule, dict):
        if tool_name == "reschedule_outlook_event":
            return "auto" if (event_status or "confirmed") == "tentative" else "ask"
        if tool_name == "book_alternative_connection":
            threshold = rule.get("ask_if_cost_over_eur", 0)
            return "ask" if (cost_eur or 0) > threshold else "auto"
    return "ask"  # unknown tool -> safest default


def _profile_override(
    profile: dict | None,
    tool_name: str,
    *,
    cost_eur: float | None,
    event_status: str | None,
) -> Resolution | None:
    """A per-tool resolution the user pinned in the UI, or ``None``."""
    write_tools = ((profile or {}).get("policy") or {}).get("write_tools") or {}
    if tool_name == "reschedule_outlook_event":
        key = f"reschedule_outlook_event_{event_status or 'confirmed'}"
        val = write_tools.get(key)
    else:
        val = write_tools.get(tool_name)
    if val in ("auto", "ask"):
        return val  # type: ignore[return-value]
    if val == "ask_over_threshold" and tool_name == "book_alternative_connection":
        threshold = ((profile or {}).get("policy") or {}).get("book_cost_threshold_eur", 50)
        return "ask" if (cost_eur or 0) > threshold else "auto"
    return None


def _apply_level(tool_name: str, base: Resolution, level: PolicyMode) -> Resolution:
    """Shift a per-tool default by the effective global autonomy level."""
    if level == "conservative":
        return "ask"
    if level == "aggressive":
        if tool_name in _ALWAYS_ASK_AGGRESSIVE:
            return base  # high commitment keeps its (ask) default
        return "auto"
    return base  # balanced


def resolve(
    tool_name: str,
    *,
    profile: dict | None = None,
    policy_mode: PolicyMode | None = None,
    cost_eur: float | None = None,
    event_status: str | None = None,
    **context,
) -> Resolution:
    """Resolve a write tool to ``auto`` or ``ask`` (the veto gate decision).

    Args:
        tool_name: One of ``WRITE_TOOLS``.
        profile: The user profile (carries ``autonomy`` and the ``policy`` block
            the UI writes). Usually ``store.any_profile()``.
        policy_mode: Explicit global level override (used by the eval sweep); wins
            over the profile/config level when given.
        cost_eur: Cost of the action — gates ``book_alternative_connection``
            against the configured threshold.
        event_status: ``"tentative"`` | ``"confirmed"`` for
            ``reschedule_outlook_event``.
    """
    # 1. The user-message channel is the veto channel itself — never gate it.
    if tool_name == "send_whatsapp_to_user":
        return "auto"

    cfg = load_policy_config()
    level = policy_mode or _effective_level(profile, cfg)

    # 2. An explicit per-tool override the user set in the UI wins (but the global
    #    level can still tighten it to "ask" under conservative).
    override = _profile_override(
        profile, tool_name, cost_eur=cost_eur, event_status=event_status
    )
    if override is not None:
        return "ask" if level == "conservative" else override

    # 3. Config default for the tool, shifted by the effective global level.
    base = _config_resolution(cfg, tool_name, cost_eur=cost_eur, event_status=event_status)
    return _apply_level(tool_name, base, level)
