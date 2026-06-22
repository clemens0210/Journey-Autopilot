# ADR 0003 — Risk scoring lives in the Monitoring agent (model, not LLM)

Status: Accepted (supersedes the earlier "separate Risk agent" note in the Context Record)

## Context
Disruption risk has two horizons: **pre-trip** (delay risk + ETA before
departure, from punctuality history) and **en-route** (a running trip's live
status). An earlier prototype split these into two agents (a dedicated
`risk_agent` plus the live Monitoring agent). The build spec (§4/§5) instead
folds risk into a single **Monitoring** agent and treats risk scoring as a
model/heuristic tool, never an LLM judgment (hard rule §3.4).

## Decision
1. **One Monitoring agent** covers both horizons, choosing the path that fits the
   request. The separate `risk_agent` is removed.
2. **Risk is a model/heuristic tool.** The punctuality KPIs (mean/median/p90
   delay, on-time rate, cancellations, causes) are computed deterministically in
   `tools/risk_model.py`; the agent only *interprets* them into a 0–100 score, a
   LOW/MEDIUM/HIGH band, and an ETA.

## Consequences
- Aligns with the spec's four-agent design and keeps the math robust and the
  verdict explainable.
- The Monitoring agent holds five read tools (live status, network disruptions,
  delay reference, delay history, planned connection) and no write tools.
- Two complementary history sources feed the model: a months-long DB punctuality
  **archive** (baseline) and the **live** arrival board (~5–6 h, today's
  situation), with mock fallback tagged via `source`.
- Bug fixed in the move: the archive JSON path now resolves to the package
  `data/` dir, so the baseline actually loads (it previously pointed at a
  non-existent directory and silently returned nothing).
