# ADR 0004 — Veto-per-action gate + configurable autonomy

Status: Accepted (policy enforcement: implemented)

## Context
The traveler must keep veto over every consequential action (build spec §3.2).
Different actions carry different risk (messaging the user vs. booking a hotel),
and the team wants a single knob to trade autonomy against control.

## Decision
- Every `write` tool is classified and resolved to **auto** or **ask** by a
  config-driven policy layer (`policy.py`, `config/policy.yaml`), taking a global
  `policy_mode` (conservative | balanced | aggressive) into account.
- The veto itself is a real pause-for-confirmation. In the ADK build it is
  realized through the existing **approval/veto queue** in
  `integrations/whatsapp/messaging.py`: a draft is queued and the traveler replies
  YES / NO / EDIT (5-minute timeout) before anything is sent — the ADK analogue
  of LangGraph's `interrupt()`.

## Consequences
- Sweeping `global_autonomy_level` produces the autonomy/control trade-off
  numbers (eval, M6).
- The read/write split is enforced at the agent level (Monitoring/Planner hold
  no write tools); per-tool `auto`/`ask` is now enforced by `policy.resolve()`
  and the write tools (`tools/write_tools.py`) the Executor holds. A gated tool
  returns `status="veto_required"` instead of acting; the action only fires once
  the user approves and the tool is re-called with `user_approved=True`.
- Configurability: `config/policy.yaml` holds the defaults; each user's choices
  override them, captured in the profile (`policy` block) via onboarding and the
  "Automation & veto" settings screen, and applied on every agent run.
  Precedence: user-message channel (always auto) > per-tool user override >
  config default shifted by the effective global level.
- Onboarding autonomy → policy mode mapping: notify_only→conservative,
  approve_each→balanced, auto_within_limits→aggressive.
- Open defaults in config: cost threshold for booking (50 EUR), compensation
  claim auto vs. notify-after (build spec §12).
