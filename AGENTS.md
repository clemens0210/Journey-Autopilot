# AGENTS.md

Compact guidance for coding-agent sessions. Read alongside `README.md` (full overview) and `CONTEXT_RECORD.md` (architectural decisions).

## Verify before committing

- **No test suite, no lint, no typecheck.** There is no `pytest` suite, `ruff`, `mypy`, or `Makefile`. `pyproject.toml` exists but only declares packaging/deps — do not assume `pytest` / `npm test` runs anything.
- Verify by running the entry scripts (need a configured `.env`, see below):
  - `python scenarios/happy_path.py` — Orchestrator end-to-end + WhatsApp approval-queue demo
  - `python scenarios/no_train_alternative.py` — Planner widening into car/bike/hotel options
  - `python run_onboarding.py` — web app on http://127.0.0.1:8000 (login `lucas.wild@example.com` / `demo123`)
  - `python scripts/check_db.py` — DB sidecar smoke test (sidecar must be running on :3000)
  - `python scripts/check_outlook.py` — Graph/calendar smoke test (`--login` to reconnect)
  - `adk web` / `adk run src/journey_autopilot` — ADK-native UI/CLI

## Agent graph — who holds what

`src/journey_autopilot/agent.py` exposes `root_agent` (a thin shim ADK discovery expects); the real thing is the ReAct `LlmAgent` in `orchestrator.py`, which wraps each specialist as an `AgentTool`.

| Agent | Kind | Tools |
|---|---|---|
| **Orchestrator** | ReAct root | the four agents below, plus `send_whatsapp_to_user` — its own outbound channel to the traveler |
| **Monitoring** | read | live trip status, network disruptions, delay reference/history, planned connection |
| **Planner** | read | profile, reroute discovery, mobility, hotels, batched calendar check, finalizer, **passenger-rights lookup** |
| **Communicator** | write (third parties) | `propose_appointment_notice_email` → user approval → `send_approved_notice_email` |
| **Executor** | write (the traveler's plans) | `book_alternative_connection`, `book_hotel`, `reschedule_outlook_event`, `file_compensation_claim` |

Three rules this table encodes — breaking any of them is an architecture regression:

1. **Passenger rights are split.** *Looking up* what the traveler may do is a read on the Planner (`get_passenger_rights`); during a running trip it answers whether the delay lifted the ticket's Zugbindung, which decides whether a reroute is even covered. *Filing* the claim is a write on the Executor, behind the policy gate.
2. **The Executor sends nothing.** Messages to third parties go through the Communicator's propose/approve pair; the notice to the traveler is the Orchestrator's own tool.
3. **No write tool accepts money, delay, or firmness from conversation text.** Reroute/hotel resolve through the server-issued `proposal_id`; the claim reads the settled rights result; the reschedule reads the appointment's real tentative/confirmed status from the calendar.

The "if risk of disruption" gate between Monitoring and Planner is config-driven: `config.AT_RISK_BAND` (from `config/settings.yaml`) is formatted into the Orchestrator's instruction, so prompt and config cannot disagree.

## Module map

- `orchestrator.py` — the root agent and its instruction template
- `agents/` — `monitoring.py`, `planner.py`, `communicator.py`, `executor.py`
- `tools/read_tools.py` — function tools the read agents call; the live/mock insertion point
- `tools/write_tools.py` — gated writes, grouped as `EXECUTOR_` / `ORCHESTRATOR_` / `COMMUNICATOR_WRITE_TOOLS`
- `tools/constraints.py` — pure option-eligibility rules, shared by the read side (building the shortlist) and the write side (revalidating before execution). No tools, no I/O
- `tools/risk_model.py`, `risk/` — deterministic delay statistics (risk is never an LLM judgment)
- `policy.py` + `config/policy.yaml` — the veto gate; `GATED_ACTIONS` names the policy actions
- `request_context.py` — per-turn identity plus the **turn workspace**: the one place structured results cross the `AgentTool` boundary (trace, WhatsApp sends, reroute shortlist, settled rights). Bound per chat turn, so nothing leaks between turns or between concurrent users
- `onboarding/accounts.py` — simulated DB/Outlook account (the single swap point for a real integration)
- `persistence/store.py` — SQLite, stdlib only (profile, trips, proposals, complaints)
- `ui/server.py` (FastAPI) + `ui/chat.py` (runs the orchestrator per trip)
- `integrations/` — `db_ops.py`+`stations.py` (Node sidecar), `outlook/` (MS Graph), `whatsapp*.py` (Twilio + approval queue), `rights_rag/` (ChromaDB + rule logic)
- `config.py` — one `LiteLlm` per agent role, resolved from `config/settings.yaml`

## Path gotchas

- Delay-reference build script is `scripts/build_delay_stats.py`.
- There is **no Makefile**. Sidecar: `cd db_service && npm start` (port 3000).

## `.env` loading (ADK quirk)

ADK discovers `.env` from the **agent directory**, not the project root. The run scripts compensate by loading both:
```python
load_dotenv()
load_dotenv("journey_autopilot/.env")
```
Fill `.env` at root from `.env.example`, then also copy it to `journey_autopilot/.env` (or rely on the scripts' double-load). Required: `UNI_GPT_*`. Optional: `TWILIO_*`, `MS_ENTRA_*`, `DB_API_URL`, `AWS_*` (only for the `bedrock_*` model aliases).

## Tool data contract — preserve it

`tools/read_tools.py` follows a **live-then-mock-fallback**: each tool tries the real source (db_service sidecar / MS Entra / archive JSON), falls back to `mock_data`, and tags every result with a `source` field (`db_service_live` / `db_history_archive` / `mock_*`). The Orchestrator instruction requires the agent to disclose `mock_*` sources to the user. Keep this contract when editing/adding tools.

`ui/server.py`'s `/api/journeys/search` is a documented exception — UI-only, live-or-nothing, no mock.

## Hard-won gotchas

- **Windows SSL**: `scenarios/happy_path.py` patches `ssl.SSLContext.load_default_certs` to swallow `ASN1: NOT_ENOUGH_DATA` errors from the Windows cert store. Reuse this patch in any new entry script that imports ADK/LiteLLM on Windows, or `aiohttp`'s `ssl.create_default_context()` crashes at import.
- **OpenTelemetry pin**: ADK 2.2.x caps OTel at `<=1.41.1` in `requirements.txt`; `chromadb` would otherwise pull a newer exporter stack and break ADK. Keep the pin when touching deps.
- **`LITELLM_LOG=CRITICAL`** is set by default in `config.py`/`scenarios/happy_path.py` to suppress LiteLLM telemetry noise. Set `LITELLM_LOG=ERROR`/`DEBUG` when diagnosing the LLM backend.
- **Port 8000 conflict**: `run_onboarding.py` (web app) and `uvicorn journey_autopilot.integrations.whatsapp_webhook:app --port 8000` (WhatsApp reply webhook) both default to 8000. Use different ports.
- **`AgentTool` hides nested results.** ADK forwards only a sub-agent's final *text* to the parent, so a sub-agent's tool results never reach `ui/chat.py`'s event stream. Three things work around this with request-scoped stashes: the reroute workspace, the settled passenger-rights slot, and the WhatsApp send record. If you add a tool whose structured result the browser needs, follow the same pattern rather than scanning the trace.
- **ADK 2.x** has breaking changes vs 1.x (Agent API, event/session model). Many online tutorials show 1.x — don't follow them. Docs: https://google.github.io/adk-docs/

## Demo scenario anchors (do not drift)

- Demo date `2026-06-19`; demo user `lucas` / `lucas.wild@example.com` / `demo123`.
- Lucas' Munich → Berlin trip (`DEMO_TRIP` in `mock_data.py`) is pinned across `onboarding/accounts.booked_trips()`, the dashboard trip chat, and the monitoring/planner/calendar fixtures — keep them in sync.
- Calendar fixtures carry `id`, `end`, and `status`; the Outlook mapper produces the same fields. `reschedule_outlook_event` needs both, so don't drop them from either side.

## Persistence

- SQLite at `src/journey_autopilot/data/journey_autopilot.db` (gitignored via `*.db`), configurable via `JA_DB_PATH`. Profile stored as a JSON blob; no migrations. Schema in `persistence/store.py`.
- ADK run state is **not** persisted: `ui/chat.py` uses an `InMemoryRunner`, so a server restart starts conversations over (`session_restarted` tells the UI). Swapping in a `DatabaseSessionService` at the runner is the one-line change that would make them durable.
- ChromaDB for passenger-rights RAG at `src/journey_autopilot/data/chromadb/` (gitignored), configurable via `CHROMA_PATH`. Rebuild: `python scripts/run_crawler.py`.
- `src/journey_autopilot/data/db_delay_reference.json` (~370 kB, committed, CC BY 4.0) is the pre-aggregated delay baseline; runtime reads only this JSON. Rebuild with `scripts/build_delay_stats.py` (needs `pyarrow` + `huggingface_hub`, not in requirements).

## Single-user prototype

Agent tools fall back to "the latest" profile via `store.any_profile()` when no request identity is bound. Booking authority does **not**: `write_tools._selected_proposal_option` requires a genuinely bound `user_id` + `session_id`. Multi-user support is an open question — don't assume session/user context exists outside a chat turn.
