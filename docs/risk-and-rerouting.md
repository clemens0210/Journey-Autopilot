# How risk is calculated and when rerouting is triggered

Short reference for the demo — answers "how is the risk calculated?" and
"when exactly is a reroute proposed?".

## The risk signal is a model, never an LLM guess

All numbers come from deterministic Python (`risk/predictor.py`,
`tools/risk_model.py`); the agents only *interpret* them.

**Per-leg expected delay** (`risk/predictor.py::forecast_leg`):

```
expected_delay = current_live_delay + historical_mean_delay(destination, train_type)
```

- `current_live_delay` — the leg's live arrival delay from the db_service
  sidecar (0 when no live data).
- `historical_mean_delay` — from `data/delay_stats.json`, pre-aggregated from
  the piebro/deutsche-bahn-data punctuality archive per (station, train type),
  with graceful fallback station → network-wide.

**Risk band per leg** (`_level`): `low` < 5 min expected delay ≤ `medium`
< 15 min ≤ `high`. The trip's band is the WORST leg band
(`read_tools._overall_level`). A transfer whose buffer is eaten by the
expected delay raises the leg to `high` (`connection_risks`).

**Risk score 0–100** — the historical on-time rate inverted
(`100 * (1 - on_time_rate)`): how delay-prone the route normally is,
independent of today.

**Today's situation** (`tools/risk_model.py::connection_delay_history`) — the
live arrival board of the destination over the last ~5 h condensed into
mean/median/p90 delay and cancellations. The Monitoring agent is instructed to
raise the band when today looks much worse than the historical norm.

**Mock path**: when a trip only exists in the fixture (`LIVE_TRIP_STATUS`),
the band is derived from the scripted delay: ≥ 30 min → HIGH, ≥ 10 → MEDIUM,
else LOW.

## Connection feasibility ("can I still make my transfer?")

Two deterministic checks, used in different places:

- **Chat / monitoring** (`risk.connection_risks`): a transfer is flagged when
  `expected_delay >= transfer_buffer` — i.e. live delay *plus* historical mean
  would eat the buffer. Conservative on purpose ("may miss").
- **Trip-detail screen** (`risk.live_connection_risks`): warns only when the
  train is *actually* running late enough (`live_delay >= buffer`). This keeps
  speculative warnings out of the UI.
- **Reroute options vs. calendar** (`check_options_against_calendar`): each
  option's arrival + a fixed 30-min station-to-appointment buffer is compared
  against every appointment; a `hard_constraint` clash makes the option
  non-viable.

## When rerouting is triggered

The Orchestrator (see `orchestrator.py`, step 2) always calls Monitoring
first, then branches on its answer:

| Monitoring result | Orchestrator action |
|---|---|
| ARRIVED (trip over) | No reroute. Passenger-rights check with the confirmed final delay; eligible claims become a draft complaint in the app. |
| EN ROUTE + risk LOW | Brief all-clear. Planner is NOT called. |
| EN ROUTE + risk MEDIUM or HIGH | Planner is called → reroute options (R#), widened to Flinkster/Call-a-Bike/hotels (C#/B#/H#) when no train option is viable. Options are only *proposed*; booking runs through the policy/veto gate. |

The proactive WhatsApp notice to the traveler fires on every turn whose reply
carries a risk band (HIGH gets a warning header); turns without a band send
nothing.

The `thresholds.at_risk_band` value in `config/settings.yaml` (default
MEDIUM) documents the intended trigger band for the future background
monitoring loop; in the chat flow the branching above applies.
