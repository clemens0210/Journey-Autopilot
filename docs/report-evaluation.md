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
not deterministic, so a single run is an anecdote — for 10 core runs, plus 6
runs for the trade-off sweeps. The second arm is a **naive baseline**: a single
model call handed the same fixture data serialised into its prompt
(`baseline/prompts.py`) — same facts, no tools, no gate. It is not run on the
failure case, where reading no live source means an unreachable sidecar cannot
change its input by a single token; the agent arm therefore has 6 core runs to
the baseline's 4.

Each run was scored by hand from its full agent trace against six binary
checks, split into two groups because three of them are structurally
unavailable to a system without tools. Reporting those as baseline *failures*
would overstate the result; they are a capability difference.

## Results

| | Agent system | Naive single call |
|---|---|---|
| No fabricated trains/times | 5/6 | 4/4 |
| Actionable recommendation produced | 6/6 | 4/4 |
| Simulated data disclosed | 6/6 | not possible |
| Write gated behind approval | 6/6 | not possible |
| Zugbindung checked before reroute | 6/6 | not possible |
| Tokens per run | 126,475 | 4,968 |
| Cost per run | $0.19 | $0.03 |

On the two checks both arms can attempt, the baseline is not behind — and on
one it is ahead. Both produced an actionable recommendation every time, but the
agent fabricated where the baseline did not: in one edge-case run it invented a
service, *"ICE 2862 (Sonderfahrt)"*, attributed it to DB staff and suggested
asking for it at the platform. No such train exists in any fixture or tool
result. The plausible reason is instructive — that run's booking had just failed
revalidation, and the longer, improvising trajectory is exactly where a tool-using
agent has room to invent that a single summarising call does not.

So orchestration does not buy better prose. The entire case for it is the lower
block: a single call can *assert* that a booking was made or that a delay is
covered, but it cannot gate the write, cannot mark a figure as simulated, and
cannot look the entitlement up — capabilities that come from the read/write
split and the policy gate, not from a better prompt.

## Cost, and where it goes

Token usage was captured per model call via a LiteLLM callback and attributed
to the calling agent by a role tag (`eval/instrumentation.py`); cost was priced
from Bedrock's published rates (Sonnet 4.6, $3.30/$16.50 per 1M input/output
tokens). Per-role attribution is only possible this way — the AWS console can
split spend by *model*, and every role runs the same model, so the bill alone
cannot say which agent is expensive. Instrumented totals were cross-checked
against the AWS billing figure for the same window ($2.42 measured over 18
runs and 211 model calls, vs «$N» billed).

| Role | Model | Share of run cost |
|---|---|---|
| Planner | Sonnet 4.6 | 38.2% |
| Orchestrator | Sonnet 4.6 | 36.7% |
| Monitoring | Sonnet 4.6 | 20.5% |
| Executor | Sonnet 4.6 | 4.6% |
| Communicator | Sonnet 4.6 | 0% — never invoked |

The Communicator never ran: the Orchestrator called `send_whatsapp_to_user`
itself in all 18 runs. The two agents that *reason* take three quarters of the
bill and the Executor, which does the booking, takes 4.6% — deciding is
expensive, acting is cheap.

At $0.19 per run, cost scales linearly with monitored trips: one traveller
checked hourly over a 4-hour journey is ~$0.75/day, and 1,000 travellers
~$750/day — an upper bound, since it prices every check as a full
disruption-handling run rather than a monitoring-only pass.
The driver is **input** tokens — the ReAct loop resends the growing transcript
on every step, so cost grows super-linearly in the number of agent turns, not
in the size of the answer.

## Trade-offs

**Agentic vs. deterministic.** All punctuality statistics are computed in plain
Python (`risk/`); the agent only interprets them. Instructing Monitoring to
compute them itself raised its output tokens by 58% (2,212 → 3,487) and run
cost by 14% ($0.1377 → $0.1576) — and did not remove the dependency: in both
runs the model still called `get_historical_delay_baseline` and then re-derived
the figures on top of the answer it had been given. The variant bought a
costlier derivation of a number the system already had. It was run *pre-trip*,
the only branch that consults those statistics at all.

**Autonomy vs. control.** Sweeping `global_autonomy_level` moved the approvals
the traveller must give from **1 per run** at *conservative* to **0** at
*balanced* and *aggressive*, while run cost stayed flat (−0% and −2%): autonomy
buys control, not compute. *Aggressive* matching *balanced* is by design — hotel
bookings, participant emails and confirmed-appointment moves keep asking at
every level, because the gate's default follows reversibility rather than the
traveller's stance, and this scenario's one gated write (a €0 rebooking on a
lifted Zugbindung) is already automatic at *balanced*.

**Capability vs. cost.** The orchestrated arm costs **5.4× the single call per
run** ($0.1882 vs $0.0348) and burns 25× the tokens: the ReAct loop, the tool
results carried forward, and the gate are all billed. That is the price of the
three capabilities the single call cannot attempt at all — the answer quality
alone would not justify it.

**Limitations.** n = 2 per cell — enough to catch gross instability, not enough
to claim a variance figure; sweeps were run on the happy path only; scoring was
performed by one author, so the checks are reproducible but not inter-rater
validated. Model tier was held fixed at Sonnet 4.6 for every role, so nothing
here speaks to what a cheaper model would cost or give up. The pre-trip cells
run a shifted clock and are not the same scenario as the core matrix, and **no
reproducibility claim is made**: the deterministic reference itself returned
MEDIUM on one run and HIGH on the other, which at n = 2 rules the comparison
out rather than supporting it. Finally, the `no_train_alternative` cell is not a
clean comparison: `demo/accounts.py` hardcodes the happy-path booking, so the
agent's tools reported the traveller on **ICE 528** while the baseline was fed
the fixture's **ICE 1006**. The disruption, reroutes and calendar are the
scenario's own; only the booked train identity disagrees.
