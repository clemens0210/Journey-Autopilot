# ADR 0004 — Veto-per-action gate + configurable autonomy

Status: Accepted (policy enforcement: scaffold, lands in M4)

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
  `integrations/whatsapp.py`: a draft is queued and the traveler replies
  YES / NO / EDIT (5-minute timeout) before anything is sent — the ADK analogue
  of LangGraph's `interrupt()`.

## Consequences
- Sweeping `global_autonomy_level` produces the autonomy/control trade-off
  numbers (eval, M6).
- Today the read/write split is enforced at the agent level (Monitoring/Planner
  hold no write tools); per-tool `auto`/`ask` enforcement against
  `config/policy.yaml` is wired when the Executor + write tools land (M4).
- Open defaults stubbed in config: cost threshold for booking (50 EUR),
  compensation claim auto vs. notify-after (build spec §12).
