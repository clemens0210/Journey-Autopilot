# Evaluation & Trade-offs

> Draft for the report section (1 page). Every `«…»` is a placeholder for a
> measured number — run `python -m eval.run`, score
> `eval/output/scoring_sheet.csv` by hand from the transcripts, and substitute.
> Current length without placeholders: ~1 page including both tables.

## How the system was tested

There is no labelled real-world dataset for "correct disruption handling", so
correctness is assessed against **scripted fixtures whose correct outcome is
known by construction**. Three scenarios were used: a *happy path* (delay with
viable rail alternatives), an *edge case* (`no_train_alternative` — a full
overhead-wire failure where every train reroute misses the hard 14:00 meeting,
forcing the Planner into car, bike and hotel options), and a *failure case*
(the live-data sidecar made unreachable, exercising the documented
live-then-mock fallback). Each scenario ran **twice per arm** — model output is
not deterministic, so a single run is an anecdote — for 10 core runs, plus 8
runs for the trade-off sweeps. The baseline is not run on the failure case: it
reads no live source, so an unreachable sidecar cannot change its input by a
single token, and running it there would re-run the happy path under a second
name. The agent arm therefore has 6 core runs to the baseline's 4.

Each run was scored by hand from its full agent trace against six binary
checks, split into two groups because three of them are structurally
unavailable to a system without tools. Reporting those as baseline *failures*
would overstate the result; they are a capability difference.

## Comparison against a naive baseline

The baseline is a **single model call with the same facts supplied**: every
fixture slice the agents' read tools would have returned — trip, live status,
disruptions, alternatives, calendar, hotels, passenger-rights rules — is
serialised into one prompt (`baseline/prompts.py`). Both arms therefore answer
the same question from the same information; only the orchestration differs.

| | Agent system | Naive single call |
|---|---|---|
| Deadline respected | «x/6» | «x/4» |
| No fabricated trains/times | «x/6» | «x/4» |
| Actionable recommendation produced | «x/6» | «x/4» |
| Simulated data disclosed | «x/6» | not possible |
| Write gated behind approval | «x/6» | not possible |
| Zugbindung checked before reroute | «x/6» | not possible |
| Tokens per run | «N» | «N» |
| Cost per run | «$N» | «$N» |

The upper block is where the two arms are genuinely comparable. The lower block
is the architectural claim: a single call can *assert* that a booking was made
or that a delay is covered, but it cannot gate the write, cannot mark a figure
as simulated, and cannot look the entitlement up — capabilities that come from
the read/write split and the policy gate, not from a better prompt.

## Cost, and where it goes

Token usage was captured per model call via a LiteLLM callback and attributed
to the calling agent by a role tag (`eval/instrumentation.py`); cost was priced
from Bedrock's published rates (Sonnet 4.6 $3.30/$16.50, Haiku 4.5 $1.10/$5.50
per 1M input/output tokens). Per-role attribution is only possible this way —
the AWS console can split spend by *model*, and three roles share the Sonnet
tier, so the bill alone cannot say which agent is expensive. Instrumented
totals were cross-checked against the AWS billing figure for the same window
(«$N» measured vs «$N» billed).

| Role | Model | Share of run cost |
|---|---|---|
| Planner | Sonnet 4.6 | «N%» |
| Orchestrator | Sonnet 4.6 | «N%» |
| Executor | Sonnet 4.6 | «N%» |
| Monitoring | Haiku 4.5 | «N%» |
| Communicator | Haiku 4.5 | «N%» |

At «$N» per run, cost scales linearly with monitored trips: one traveller
checked hourly over a 4-hour journey is «$N/day», and 1,000 travellers «$N/day».
The driver is **input** tokens — the ReAct loop resends the growing transcript
on every step, so cost grows super-linearly in the number of agent turns, not
in the size of the answer.

## Trade-offs

**Agentic vs. deterministic.** All punctuality statistics are computed in plain
Python (`risk/`); the agent only interprets them. Moving that arithmetic into
the model raised tokens per run by «N%» («$N» → «$N») and made the risk band
non-reproducible between runs. Keeping the maths deterministic is therefore
both the cheaper and the more defensible choice — and is the main lever for
further reduction: every judgement that can be made a rule stops being billed.

**Autonomy vs. control.** Sweeping `global_autonomy_level` moved the number of
approvals the traveller must give from «N» (conservative) to «N» (aggressive)
per run, at «N» and «N» actions executed without confirmation. This is the
trade-off the policy layer exists to make adjustable, and it is a config value
rather than a code change.

**Model tier.** Running Monitoring on Sonnet instead of Haiku changed run cost
by «N%» and changed the resulting risk band in «N» of 2 runs — evidence for
keeping the cheap tier on the high-frequency role.

**Limitations.** n = 2 per cell — enough to catch gross instability, not enough
to claim a variance figure; sweeps were run on the happy path only; scoring was
performed by one author, so the checks are reproducible but not inter-rater
validated.
