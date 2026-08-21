## Table 1 — Agent vs. naive baseline (core runs)

| scenario | arm | n | calls/run | tokens/run | cost/run | wall clock s |
|---|---|---|---|---|---|---|
| happy_path | agent | 2 | 14.0 | 102,634 | $0.1408 | 97.6 |
| happy_path | baseline | 2 | 1.0 | 4,941 | $0.0326 | 29.8 |
| no_train_alternative | agent | 2 | 22.0 | 183,692 | $0.2929 | 211.6 |
| no_train_alternative | baseline | 2 | 1.0 | 4,994 | $0.0369 | 35.4 |
| sidecar_offline | agent | 2 | 13.0 | 93,098 | $0.1309 | 93.4 |

## Table 2 — Where the tokens go (agent arm, core runs)

| role | calls | input tok | cached in tok | output tok | cost | share |
|---|---|---|---|---|---|---|
| planner | 32 | 302,651 | 274,025 | 13,535 | $0.4318 | 38.2% |
| orchestrator | 38 | 308,047 | 274,686 | 11,267 | $0.4141 | 36.7% |
| monitoring | 16 | 71,213 | 57,828 | 9,520 | $0.2314 | 20.5% |
| executor | 12 | 41,265 | 37,061 | 1,351 | $0.0518 | 4.6% |

## Table 3 — Trade-off sweeps (happy path, agent arm)

| variant | n | tokens/run | cost/run | Δ cost | vs | asks/run |
|---|---|---|---|---|---|---|
| default (en route, balanced autonomy, deterministic risk) | 2 | 102,634 | $0.1408 | — | — | 0.0 |
| autonomy_conservative | 2 | 99,067 | $0.1405 | -0% | default | 1.0 |
| autonomy_aggressive | 2 | 98,349 | $0.1383 | -2% | default | 0.0 |
| pretrip_default | 2 | 101,215 | $0.1377 | -2% | default | 1.0 |
| pretrip_llm_risk | 2 | 101,488 | $0.1576 | +14% | pretrip_default | 1.0 |

_Costs are warm-cache: a discarded warm-up run precedes the matrix so no measured run pays the cache-write premium the rest then read from._

## Table 4 — Hand-scored checks (core runs)

| group | check | agent | baseline |
|---|---|---|---|
| quality | no_fabrication | 5/6 | 4/4 |
| quality | task_completed | 6/6 | 4/4 |
| capability | source_disclosed | 6/6 | n/a |
| capability | write_gated | 6/6 | n/a |
| capability | rights_checked | 6/6 | n/a |

_The baseline was given every fixture slice the agents' read tools return, so the comparison isolates orchestration from data access._

_18 runs, 211 model calls. Total measured spend: $2.4163. Unpriced calls (model absent from LiteLLM's cost map): 0._

_Live DB requests: 78 (4 failed). No unexpected fixture fallbacks._

_Quality and capability checks are scored by hand into `eval/output/scoring_sheet.csv` from the transcripts; Table 4 reports them once filled._