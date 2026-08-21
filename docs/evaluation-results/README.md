# Evaluation results — frozen run of 2026-08-21

The measured evidence behind [`docs/report-evaluation.md`](../report-evaluation.md).
Every number quoted in that section comes from these files.

This is a **frozen copy**, not a working directory. `eval/output/` is where
`python -m eval.run` writes, and it is gitignored because the next run
overwrites it. These files are the specific run the report describes.

## The run

| | |
|---|---|
| Executed | 2026-08-21, 07:54–08:25 UTC |
| Runs | 18 measured (10 core + 8 trade-off sweep), plus one discarded cache warm-up |
| Model calls | 211, all on `eu.anthropic.claude-sonnet-4-6` (one tier, every role) |
| Measured spend | $2.4163 |
| Repetitions | n = 2 per cell |

Three scenarios (`happy_path`, `no_train_alternative`, `sidecar_offline`) × two
arms (orchestrated agent, single-call baseline), with the baseline omitted from
`sidecar_offline` — it reads no live source, so an unreachable sidecar cannot
change its input. That is why the agent has 6 core runs to the baseline's 4.

## Files

| file | what it is |
|---|---|
| `tables.md` | the four aggregated tables, as pasted into the report |
| `runs.csv` | one row per run — tokens, cost, cache split, wall clock, live-DB counts, veto-gate decisions |
| `calls.csv` | one row per model call, attributed to the calling agent by role tag |
| `scoring_sheet.csv` | the hand-scored checks, with a note per run recording what the verdict rests on |
| `transcripts/` | the full trace and answers for all 18 runs — the evidence the scoring was read from |

`scoring_sheet.csv` is the one artifact that cannot be regenerated: the verdicts
are a person's reading of the transcripts, not a computation. Only the 10
`default`-variant rows are scored; Table 4 ignores the sweep rows.

Reproducing the *mechanism* is `python -m eval.run` (needs AWS Bedrock
credentials and the `db_service` sidecar running). Reproducing the *values* is
not possible — model output is not deterministic, so a fresh run gives
different costs and may give different verdicts.

## Redactions

Two real values were replaced in these copies, and nowhere else:

| original | replacement | occurrences |
|---|---|---|
| the developer's phone number | `+49XXXXXXXXXX` | 5 |
| the developer's Microsoft account | `outlook-user@example.com` | 4 |

Both reached the transcripts through the traveller profile: `baseline/prompts.py`
serialises the whole profile into the baseline prompt, and one agent run quotes
the phone number back in its WhatsApp confirmation. Nine lines across four
transcripts differ from the originals in `eval/output/`; nothing else was
altered, and no measured value was touched.

Every other name and address in these files is fictional demo data
(`lucas.wild@example.com`, `anna.client@example.com`, and the like).
