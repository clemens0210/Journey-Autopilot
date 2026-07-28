# Context Record — Journey Autopilot

A locked snapshot of all decisions, constraints, and open questions captured during the clarification phase. Serves as the single source of truth for why the architecture looks the way it does.


---

## Table of Contents

- [Feature Streams](#feature-streams)
- [Tech Stack & Tool Architecture](#tech-stack--tool-architecture)

---

## Feature Streams

### Onboarding & Profile 

**Status:** `implemented (prototype)` | **Priority:** Must | **Responsible Person:** Hendrik

**Description:** Capture a personal preference profile (class, seat, speed-vs-comfort trade-off, home constraints) via quick onboarding; import existing DB / BahnBonus account.

#### Decisions
- **Standalone web app in DB Navigator look & feel** (`onboarding/`, FastAPI + vanilla JS), not an actual DB Navigator integration: DB offers no official API or extension point for third parties. The UI replicates the Navigator design (DB red, card layout, phone frame) so the demo conveys the "integrated into DB Navigator" vision.
- **DB account login and trip import are simulated** (`onboarding/accounts.py`) behind realistic API contracts (`POST /api/auth/db-login` returns account + booked trips). A real integration would swap only this module. Same for Outlook OAuth consent and SMS verification (no registered Microsoft app / SMS gateway in a uni project).
- **Mandatory vs. voluntary:** DB account login is mandatory (source of trips = the product's reason to exist). Mobile number verification and Outlook calendar are optional/skippable; travel preferences, home constraints, notifications and autonomy level have sensible defaults so the wizard is never blocking.
- **Onboarding captures:** DB account (+BahnCard/BahnBonus), upcoming trips, verified mobile number, Outlook calendar consent, class, seat (window/aisle, open/compartment, quiet zone), speed-vs-comfort (0–100 slider), max transfers, home station (live DB autocomplete via db_service sidecar), latest arrival home, hotel/taxi acceptance, notification channels + quiet hours, autonomy level (notify-only / approve-each / auto-within-limits).
- **Persistence:** SQLite (`src/journey_autopilot/data/journey_autopilot.db`, `persistence/store.py`), profile as JSON blob (prototype-friendly, no migrations). Agents read it via the `get_user_profile` tool — the Planner ranks reroute options against the onboarded profile, and `policy.resolve` reads the autonomy block from the same store.
- **GDPR:** one-click full deletion (`DELETE /api/profile`) and a privacy note on the welcome screen.

#### Constraints
- No official DB API for account login / ticket import → simulation is the only honest option; keep the swap surface small (one module).
- Single-user prototype: agent tools read "the latest" profile (`store.any_profile()`); multi-user needs a session/user context through the agent stack.

#### Open Questions
- ~~How do we integrate our tool into the DB Navigator technically and logically?~~ → Not possible officially; standalone app with Navigator UX (see decisions).
- ~~What is all part of the onboarding? What is mandatory and what is voluntary?~~ → See decisions.
- ~~In which UI happens the onboarding?~~ → Own web app, DB Navigator look & feel.
- How to pass the logged-in user's identity into the agent runs (ADK session state)?

#### Justification
- Simulated integrations with real API contracts keep the demo honest and the path to production clear: each mock module is the single swap point for a real integration.

### Disruption Monitoring & Risk Prediction

**Status:** `in progress` | **Priority:** Must | **Responsible Person:** Clemens & Hendrik

**Description:** Continuously ingest live ops data; score disruption risk **hours in advance** using live data, weather, historical patterns and large events. (Basic systems only react; prediction is the differentiator.)

#### Decisions
- **Pre-trip risk is folded into the Monitoring Agent** (`journey_autopilot/agents/monitoring.py`), not a separate agent: the same read-only agent covers both horizons — it scores delay risk and predicts an ETA **before the route starts** (pre-trip) and watches a running trip via live status and disruptions (en route). The Orchestrator always calls Monitoring first, then branches to the Planner only on elevated risk.
- **Split of labor — deterministic stats, agentic scoring:** `tools/risk_model.py` computes the punctuality KPIs (mean/median/p90 delay, on-time rate, cancellations, top causes) in pure Python; the LLM agent only *interprets* them into a 0–100 score, a NIEDRIG/MITTEL/HOCH band and an ETA (planned arrival + expected delay). Keeps the math robust and the verdict explainable.
- **Two complementary "past data" sources:**
  - **Historical baseline (months) — the reference:** a real punctuality *archive* from [piebro/deutsche-bahn-data](https://github.com/piebro/deutsche-bahn-data) (DB data, CC BY 4.0). Pre-aggregated once into compact arrival-delay KPIs per `(station EVA, train_type)` (`scripts/build_db_delay_reference.py` → `src/journey_autopilot/data/db_delay_reference.json`, ~370 kB). Runtime reads only the JSON — no heavy deps, works offline (EVA via sidecar, else a name index). This is the long-run normal case the score is anchored on.
  - **Live recent past (~5 h) — today's situation:** the destination's DB arrival board, sampled backward in ~1 h chunks (single call is API-capped to ~1 h), realized delays of trains that already arrived. Catches today's disruptions on top of the baseline.
- **Combining the two sources:** `tools/risk_model.py` computes both sets of KPIs in pure Python; the Monitoring Agent combines the baseline + today's deviation into a 0–100 score, a NIEDRIG/MITTEL/HOCH band and an ETA — it interprets, it does not do the math.
- **Live-with-mock-fallback,** like the rest of the tools: tools try the archive/sidecar, fall back to a simulated history (`mock_data.CONNECTION_DELAY_HISTORY` / `PLANNED_CONNECTIONS`), and tag the output with `source` (`db_history_archive` / `db_service_live` / `mock_*`) so the agent and user see what the basis is.
- **Target: trained Risk-Modell, not LLM judgment (decision, not yet implemented):** the long-term goal is a trained model exposed as a Monitoring Agent *tool* — it delivers a score plus confidence instead of an LLM estimate, making the score deterministic, evaluable, and reproducible. It feeds on the stored journey-history as feature input, so "improves with usage" is real, not a slogan. Current code (`risk.py`) uses an LLM agent that interprets deterministic KPIs from `delay_stats.py`; the trained model replaces the LLM scoring step, not the stats pipeline.

#### Constraints
- The empirical realtime horizon of the live board is only ~5–6 h (older queries return the static timetable, no delays) — hence the archive for the long-run baseline.
- `db-vendo-client` itself has no historical archive; we depend on the piebro dataset for history. The committed reference is a static snapshot (currently 2025-08…10) — refresh by re-running the build script.
- Weather and large-events signals are not yet wired in — current score rests on punctuality history (archive + live) only.

#### Open Questions
- Which DB ops APIs are actually available?
- How to add weather / large-event signals to the score?
- Refresh cadence for the historical reference (re-run the build script monthly? automate?).

#### Justification
- Scoring delay risk and ETA before departure is the product's differentiator ("basic systems only react"). Anchoring it on a real months-long DB punctuality archive (baseline) and adjusting with today's live board makes the score both robust and current; pre-aggregating the archive keeps the runtime light and offline-capable.

### Replanning / Rerouting

**Status:** `planned` | **Priority:** Must | **Responsible Person:** Clemens

**Description:** On a detected or predicted disruption, generate alternative routes within the DB ecosystem, optimizing arrival time and comfort under the user's constraints.

#### Decisions
- 

#### Constraints
- 

#### Open Questions
- How to reroute? Using the alternative routes from the Navigator? Advanced with risk score? Just mockking the data?


#### Justification
- 

### Booking & Execution

**Status:** `planned` | **Priority:** Should | **Responsible Person:** TBD

**Description:** Book or modify tickets, hotels, Flinkster (car-share), Call-a-Bike and taxis as needed; integrate with the consultancy/corporate booking system. Actions must be idempotent and reversible where possible.

#### Decisions
- 

#### Constraints
- Idempotency requirement for external integrations
- Reversibility where possible

#### Open Questions
- Which booking systems to integrate with first?
- How to handle partial failures in multi-booking scenarios?

#### Justification
- 

### Calendar & Constraint Handling

**Status:** `planned` | **Priority:** Must | **Responsible Person:** Martin

**Description:** Read the calendar for meeting times/locations, private appointments; add new routes to the calendar.

#### Decisions
- **Outlook via MS Graph (Entra device-code flow):** implemented in `integrations/outlook/` (`auth.py`, `client.py`, `mapper.py`); reads events and maps the Outlook category `Journey-Autopilot/Hard` to the internal `hard_constraint` flag the Planner respects. The mapper also carries the event `id`, `end`, and a `tentative`/`confirmed` `status` — the write side needs both to address and to gate a move.
- **Reschedule distinction (implemented, Executor):** `reschedule_outlook_event` is `auto` for `tentative` events (reversible) and `ask` for `confirmed` events (not reversible) — the event's verbindlichkeit decides the autonomy mode. The status is **read from the calendar**, never accepted as a tool argument: letting the model assert an appointment is tentative would let it talk its way past its own veto. The Graph write-back itself is still simulated; the policy decision is not.

#### Constraints
- Reading is autonomous (read tool); writing (reschedule) is a write tool gated by the Policy-Layer.

#### Open Questions
- Which calendar providers to support beyond Outlook (Google, etc.)?
- Permission model for reading/writing calendar events?

#### Justification
- Outlook is the primary corporate calendar; the tentative/confirmed split keeps the autonomy matrix tied to reversibility, not to the tool name.

### Stakeholder Communication & Negotiation

**Status:** `communication implemented (prototype); negotiation deferred to Future Work` | **Priority:** Should | **Responsible Person:** Martin

**Description:** Proactively inform clients, colleagues and private contacts (WhatsApp, Outlook); coordinate across multiple travelers heading to the same meeting.

#### Decisions
- **Communication is the Communicator Agent's job (implemented):** the Communicator (`src/journey_autopilot/agents/communicator.py`) drafts role-appropriate messages; `src/journey_autopilot/integrations/whatsapp.py` sends them via Twilio with an approval queue (5-min timeout). The Communicator is also the channel through which the user's veto comes back (YES/NO/EDIT via the webhook). It brackets execution: before the veto it presents options and captures the decision; after execution it confirms to traveler and meeting participants.
- **Multi-stakeholder negotiation is out of active scope (Future Work):** agent-to-agent coordination between travelers heading to the same meeting is the most speculative, hardest-to-evaluate part. The Negotiator agent is dropped from the active setup — it lives in Future Work, not in the current code.

#### Constraints
- All outbound messages to non-traveler recipients go through the approval workflow.

#### Open Questions
- WhatsApp integration approach (official API vs. third-party)? → settled on Twilio (sandbox suffices for the prototype).

#### Justification
- The Communicator brackets execution: before the veto it presents options and captures the user's decision, after execution it confirms to traveler and meeting participants. Dropping the Negotiator keeps the active scope evaluable.

### User Control & Autonomy

**Status:** `approval workflow implemented; Policy-Layer planned` | **Priority:** Must | **Responsible Person:** TBD

**Description:** Every external action (message, booking, claim) passes a veto/approval step with a clear time window; configurable autonomy levels (notify-only → approve-each → auto within limits).

#### Decisions
- **Read/write tool split:** read tools (data fetch, scoring, ranking) run autonomously — no external effect. Write tools (book, reschedule, send, claim) are approval-required by default. The Orchestrator enforces this boundary. This is the single rule that separates "runs on its own" from "needs a human."
- **Policy-Layer (planned, config-driven, at the Orchestrator):** maps each write tool to a mode — `auto` / `ask` / `ask > X` (ask only above threshold X). A global regulator shifts all defaults from conservative to aggressive in one move, giving the Autonomy-vs-Control trade-off concrete knobs.
- **Autonomy matrix (defaults):**

  | Action | Default | Reason |
  |---|---|---|
  | `send_whatsapp_to_user` | `auto` | User is the recipient — and it's the channel through which the veto arrives. |
  | `file_compensation_claim` | `auto` | Purely beneficial, low downside. |
  | `reschedule_outlook_event` | `auto` if tentative, `ask` if confirmed | Reversibility decides: tentative is reversible, confirmed is not. |
  | `book_alternative_connection` | `ask > threshold` | Autonomous below a cost threshold, ask above. Threshold is open. |
  | `book_hotel` | `ask` | Cost + overnight stay = high commitment — always ask. |

- **Veto per action, globally adjustable:** the default is approval per write action, but the Policy-Layer can dial it up (everything asks) or down (more auto). This is the concrete expression of the autonomy spectrum.

#### Constraints
- All external actions require approval mechanism
- Configurable autonomy levels

#### Open Questions
- Default approval timeouts per action type?
- How to handle time-critical decisions (e.g., last-minute rebooking)?
- How granular is the global autonomy regulator — fixed steps or continuous?

#### Justification
- Per-action veto with a config-driven policy keeps the user in control by default while making "more autonomy" a single regulator change, not a code edit per tool.

### Compensation (Fahrgastrechte)

**Status:** `planned` | **Priority:** Must | **Responsible Person:** TBD

**Description:** Detect delay-compensation eligibility per DB passenger-rights rules (e.g. ≥60 min and ≥120 min delay thresholds), auto-prepare and file claims with the required evidence, and track claim status — no manual filing by the user.

#### Decisions
- 

#### Constraints
- Must follow official DB Fahrgastrechte rules
- ≥60 min and ≥120 min delay thresholds

#### Open Questions
- Which compensation channels to support (DB directly, third-party claim services)?
- How to track claim status over time?

#### Justification
- 

### Memory & Learning Across Trips

**Status:** `planned` | **Priority:** Should | **Responsible Person:** TBD

**Description:** Persist preferences, constraints and outcomes between journeys; prediction quality improves with usage history.

#### Decisions
- 

#### Constraints
- 

#### Open Questions
- What metrics to track for learning?
- How long to retain historical data?

#### Justification
- 

## Tech Stack & Tool Architecture

### 
**Responsible Person:** Clemens


### Decisions

- **Pattern: Orchestrator-Workers.** One Orchestrator agent coordinates specialized workers as tools in a ReAct loop. The Orchestrator holds the state, enforces the policy, routes workers, and handles errors — it does not solve requests itself. Currently active: Orchestrator + Monitoring + Planner + Drafter (Communicator); Executor is planned. The Risk Agent is a pre-trip specialist the Orchestrator calls before departure.
- **Read/write tool split.** Read tools (fetch, score, rank) have no external effect and run autonomously. Write tools (book, send, reschedule, claim) are gated by the Policy-Layer. This is the single rule that separates "runs on its own" from "needs a human." See *User Control & Autonomy*.
- **Cross-session state in SQLite (no extra container).** Profile, constraints, channel preferences, policy, and journey-history persist in SQLite across sessions — no Redis/postgres container needed for the one-command setup. Optional TTL cache for short-lived live data; Redis only if the cache part truly requires it.
- **Risk-Modell as a trained model, not LLM judgment (target).** A trained model exposed as a Monitoring Agent tool delivers score + confidence — deterministic, reproducible, and it consumes the stored journey-history as feature input so "improves with usage" is real. Current code uses an LLM agent (`risk.py`) over deterministic KPIs (`delay_stats.py`); the trained model replaces the LLM scoring step. See *Disruption Monitoring & Risk Prediction*.
- **Policy-Layer at the Orchestrator (planned).** Config-driven; maps each write tool to `auto` / `ask` / `ask > X`; a global regulator shifts all defaults. See *User Control & Autonomy* for the autonomy matrix.

- **Language & Framework:**
  - Backend: Python
  - Orchestration: Google ADK 2.x (ReAct pattern)
  - LLM Integration: LiteLLM with University of Cologne GPT endpoint (OpenAI-compatible, `openai/` provider prefix)

- **Data & APIs:**
  - University of Cologne GPT (OpenAI-compatible endpoint) as primary LLM
  - DB live data via `db-vendo-client` sidecar (Node, port 3000)
  - Historical delay archive from `piebro/deutsche-bahn-data` (CC BY 4.0)
  - Calendar: MS Graph via Entra device-code flow
  - WhatsApp: Twilio (sandbox suffices)

### Constraints

- ADK 2.x has breaking changes vs 1.x — many tutorials show 1.x, don't follow them.
- Single-user prototype: no user_id threading through the agent stack; tools read "the latest" profile via `store.any_profile()`.

### Open Questions

- Risk-Modell: own container or in-process in the setup?
- Context Record (shared state) schema: which fields belong verbindlich — pin as a team.
- Real-time data ingestion architecture for live disruption monitoring?
- Deployment target (cloud platform, on-premise)?
- How to pass the logged-in user's identity into agent runs (ADK session state)?

### Justification

- Orchestrator-Workers keeps control flow flexible (the Orchestrator decides who to call) while the read/write split + Policy-Layer make the autonomy-vs-control trade-off a config decision, not a code change per tool.
- SQLite for cross-session state matches the one-command setup goal; the trained Risk-Modell makes the score evaluable and lets stored history feed back into prediction quality.
