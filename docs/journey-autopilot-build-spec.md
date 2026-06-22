# Journey Autopilot — Architecture & Build Spec

> **How to use this:** This is the authoritative architecture spec. Save it as `docs/ARCHITECTURE.md` in the repo (or paste it into your first Claude Code prompt). Build strictly against the decisions marked **DECIDED**. Items marked **OPEN** get a sensible default + a `TODO` comment, not a long detour.

---

## 1. What we're building

A multi-agent system that turns rail-travel disruptions into solved problems for a frequent Deutsche Bahn business traveler (persona: Lucas, weekly MUC–BLN/FFM). It continuously watches a trip, predicts disruption risk, generates intra-DB rerouting options under the user's constraints, asks for confirmation on anything consequential, executes the rebooking, and keeps everyone informed.

**Final deliverable:** a Dockerized system that runs end-to-end with a single command, plus README, ADRs, a trade-off analysis with real numbers, a cost/token budget, three runnable scenarios (happy / edge / failure), and a naive baseline for comparison. Structure the repo so these fall out naturally.

---

## 2. Recommended stack

- **Language:** Python 3.11+
- **Orchestration:** **LangGraph** (`StateGraph`). It gives three things this design needs for free: a typed shared state, a **SQLite checkpointer** (= our persistence), and `interrupt()` for the **veto gate** (= human-in-the-loop). *(Framework choice is an ADR — the lighter alternative is the raw Anthropic SDK + a custom orchestration loop. Default to LangGraph unless you hit a wall.)*
- **Models (Anthropic API), tiered for cost:** Haiku for cheap/structured steps, Sonnet for Planner/Communicator reasoning, Opus only where genuinely needed. Make the per-agent model a config value — it feeds the cost/quality trade-off.
- **Risk model:** scikit-learn / LightGBM, trained offline, loaded as a tool. **Start with a transparent heuristic stub** so the pipeline runs day one; swap in the trained model later.
- **Persistence:** SQLite (LangGraph checkpointer for run state + a small app DB for profile, constraints, history, policy).
- **Packaging:** Docker + docker-compose; `docker compose up` runs the whole thing.

---

## 3. Hard rules (do not violate)

1. **Read/write separation is the backbone.** Every tool is classified `read` (safe, runs autonomously) or `write` (side-effectful, gated). The **Monitoring** and **Planner** agents are given **no write tools at all** — capability isolation, not just instructions.
2. **The veto gate is a real interrupt.** Before any `write` tool the policy marks `ask`, execution pauses for user confirmation. The user keeps veto on every consequential action.
3. **All external integrations are mocked behind interfaces.** Real DB live-ops, Outlook, and WhatsApp APIs are **not available**. Build clean adapters with mock implementations fed by fixtures so the system runs end-to-end and scenarios are reproducible. Never call a real external endpoint.
4. **Risk scoring is a model/heuristic tool, never an LLM judgment.**
5. **Out of scope: the multi-stakeholder Negotiator.** Do **not** build cross-traveler agent-to-agent negotiation. Leave at most a stub interface. It is documented future work.

---

## 4. System overview

Orchestrator-workers pattern. One orchestrator owns the flow, the shared state, the policy, and the error handling. Four specialized workers each do one job.

```
                          ┌─────────────────────┐
                          │  Orchestrator       │  owns: flow, state,
                          │  (+ Policy enforce) │  policy, error handling
                          └─────────┬───────────┘
                                    │ loads/persists
                          ┌─────────▼───────────┐        ┌──────────────────┐
                          │  Context Record     │◄──────►│ Persistence      │
                          │  (shared state)     │        │ (SQLite)         │
                          └─────────┬───────────┘        └──────────────────┘
        continuous  ┌───────────────▼────────────┐  calls  ┌──────────────┐
        ───────────►│  Monitoring  [read-only]   │◄───────►│  Risk model  │
                    └───────────────┬────────────┘         └──────────────┘
                       risk > threshold │
                    ┌───────────────▼────────────┐
                    │  Planner     [read-only]   │  ranked rerouting options
                    └───────────────┬────────────┘
                    ┌───────────────▼────────────┐  present options + capture veto
                    │  VETO GATE  (interrupt)    │◄───────►  Communicator ◄──► User
                    └───────────────┬────────────┘            [write]   (WhatsApp/Outlook)
                       approved      │
                    ┌───────────────▼────────────┐  confirmation ─► Communicator
                    │  Executor    [write]       │
                    └────────────────────────────┘
```

The **Communicator brackets execution**: it presents options and captures the veto *before* the Executor acts, then sends confirmation + notifies participants *after*.

---

## 5. Agents and tools

| Agent | Type | Responsibility | Tools |
|---|---|---|---|
| **Orchestrator** | control | Sequences workers, enforces the autonomy policy, owns error/fallback policy, checkpoints state. | `load_context`, `route_workers`, `enforce_policy`, `handle_errors`, `checkpoint_state` |
| **Monitoring** | read-only | Detects risk before it materializes; mostly deterministic (poll → features → model → threshold). | `get_current_trip`, `get_disruption_data`, `get_weather`, `get_large_events`, `get_historical_data`, `call_risk_model`, `evaluate_threshold` |
| **Planner** | read-only | Generates and ranks rerouting options under hard + soft constraints; flags compensation eligibility. | `get_alternative_connections` (incl. Sprinter, Flinkster, Call-a-Bike, hotel), `get_passenger_rights` (RAG), `rank_options` |
| **Communicator** | write | Talks to the traveler in their style; the channel through which the veto arrives; notifies participants. | `draft_message`, `apply_style_profile`, `send_whatsapp_to_user`, `send_email_to_participants`, `present_options_capture_veto` |
| **Executor** | write | Executes the approved option; every action runs through the policy. | `book_alternative_connection`, `book_hotel`, `reschedule_outlook_event`, `file_compensation_claim` |

---

## 6. Shared state — Context Record (initial schema, refine as needed)

This is the LangGraph state object, persisted via the checkpointer.

```python
class ContextRecord(TypedDict):
    session_id: str
    trip: Trip                    # id, origin, dest, segments, scheduled times, booking refs
    user_profile: UserProfile     # name, home, bahnbonus_id, channel_prefs, style_profile
    constraints: Constraints      # hard: [TimeWindow|HardRule] (Kita/Sport/Schlaf, meeting arrival)
                                  # soft: [Preference]
    calendar: list[CalEvent]      # id, title, start, end, status: tentative|confirmed, participants
    risk: RiskAssessment | None   # at_risk, score, confidence, eta_impact_min, rationale
    options: list[RerouteOption]  # legs, eta, cost, satisfies[], violates[], compensation_eligible
    decision: Decision | None     # chosen_option_id, user_veto, user_modifications
    execution: ExecutionResult | None  # bookings[], reschedules[], compensation_claim, status
    notifications: list[NotificationLog]
    errors: list[ToolFailure]     # tool, attempt, fallback_taken
    policy_mode: Literal["conservative", "balanced", "aggressive"]
```

Agents receive only the slice they need (context isolation) — don't pass the whole record into every prompt; it wastes tokens and dilutes focus.

---

## 7. Cross-cutting components

- **Risk model** (`tools/risk_model.py`): exposed to Monitoring as `call_risk_model`. Heuristic stub first (e.g. weighted features: live delay, historical lateness on this connection, weather, event load → score + confidence). Trained model later; the persisted journey history is its feature source.
- **Policy layer** (`policy.py`): config-driven. Resolves each `write` tool to `auto` / `ask`, taking `policy_mode` into account. A single global level shifts all defaults — this is what you sweep to get numbers for the autonomy/control trade-off.
- **Persistence** (`persistence/`): SQLite. Holds profile, constraints, channel prefs, policy, and journey history across sessions. In-process for the one-command setup; a separate container is an OPEN option, not a starting requirement.

---

## 8. Autonomy policy (config draft → `config/policy.yaml`)

```yaml
global_autonomy_level: balanced        # conservative | balanced | aggressive

write_tools:
  send_whatsapp_to_user:        auto    # user is the recipient + the veto channel
  file_compensation_claim:      auto    # purely beneficial, low downside        (OPEN: auto vs notify-after)
  reschedule_outlook_event:
    tentative:                  auto
    confirmed:                  ask
  book_alternative_connection:
    ask_if_cost_over_eur:       50       # OPEN: confirm threshold with team
  book_hotel:                   ask      # cost + overnight = high commitment
  send_email_to_participants:   ask      # affects third parties, professional context
```

---

## 9. Control flow & resilience

- **Trigger:** Monitoring runs on a poll loop; when `evaluate_threshold` flips `at_risk`, the orchestrator wakes the Planner.
- **Gate:** after Planner, the Communicator presents options; the graph hits an `interrupt()` for any approval the policy marks `ask`. Resume with the user's choice/veto.
- **Error policy** (`errors.py`): every tool call is wrapped — retry with backoff → fallback (cached timetable / RAG instead of live API) → graceful degradation (tell the user what couldn't be done). This is exactly the **failure-case** deliverable; design the wrapper so a broken tool call is recoverable inside the loop, not a crash.

---

## 10. Repo structure

```
journey-autopilot/
  docker-compose.yml
  Dockerfile
  README.md
  pyproject.toml
  config/
    policy.yaml
    settings.yaml            # model tiers per agent, thresholds, poll interval
  data/
    fixtures/                # mock trips, disruptions, weather, events, calendar, rights
    journey_autopilot.db     # sqlite (gitignored)
  docs/
    adr/                     # 0001-framework.md, 0002-agent-split.md, 0003-risk-model.md, ...
  src/journey_autopilot/
    state.py                 # ContextRecord + sub-types
    graph.py                 # StateGraph: nodes, edges, gate interrupt
    orchestrator.py          # routing, policy enforcement, error policy
    policy.py
    errors.py
    agents/      monitoring.py  planner.py  communicator.py  executor.py
    tools/       read_tools.py  write_tools.py  risk_model.py
    integrations/  db_ops.py  outlook.py  whatsapp.py  rights_rag.py   # mock impls behind interfaces
    persistence/   store.py  checkpointer.py
  scenarios/
    happy_path.py            # clean reroute
    edge_case.py             # cascading disruption
    failure_case.py          # broken tool call -> recovery
  baseline/
    single_shot.py           # "just ask Opus once" naive baseline
  eval/
    run.py                   # latency, tokens, cost, intervention rate, success
```

---

## 11. Build order

- **M0 — Scaffold.** Repo, Docker, config, SQLite, typed `ContextRecord`. `docker compose up` runs a health check end-to-end.
- **M1 — Mock integrations + fixtures.** One realistic dataset: MUC→BLN with a disruption, a calendar with one confirmed meeting, the user profile + constraints.
- **M2 — Read path.** Read tools + risk heuristic → Monitoring produces a `RiskAssessment`.
- **M3 — Planner.** Ranked options against constraints; `get_passenger_rights` can start from a small local rules file before full RAG.
- **M4 — Gate + write path.** Policy layer + `interrupt()` veto gate + Communicator (present/veto) + Executor (writes against mocks).
- **M5 — Resilience + scenarios.** Error wrappers → failure-case recovery; wire all three scenarios.
- **M6 — Baseline + eval.** Naive single-shot baseline + eval harness for the trade-off and cost/token deliverables.

---

## 12. Open decisions (stub a default, surface as TODO)

- Cost threshold for `book_alternative_connection` (default 50 EUR in config).
- `file_compensation_claim`: fully `auto` vs. notify-after (default `auto`, configurable).
- Risk model: in-process (default) vs. own container.
- Autonomy granularity: 3 discrete levels (default) vs. continuous.
- Exact `ContextRecord` fields: start from §6, refine against the first scenarios.

---

## 13. ADRs worth writing as you go

Framework choice (LangGraph vs. SDK), the four-agent split, risk-as-model vs. LLM, veto-per-action + configurable autonomy, SQLite for persistence, mock-everything integration strategy. One short doc each under `docs/adr/`.
