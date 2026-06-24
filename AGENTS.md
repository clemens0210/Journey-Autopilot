# AGENTS.md

Compact guidance for OpenCode sessions. Read alongside `README.md` (full overview) and `CONTEXT_RECORD.md` (architectural decisions).

## Verify before committing

- **No test suite, no lint, no typecheck.** There is no `pytest`, `ruff`, `mypy`, `Makefile`, or `pyproject.toml`. Do not assume `pytest` / `npm test` exists.
- Verify by running the entry scripts (need a configured `.env`, see below):
  - `python scenarios/happy_path.py` — Orchestrator (Monitoring + Planner) + WhatsApp demo
  - `python scenarios/pretrip_risk.py` — Monitoring Agent pre-trip path (risk + ETA)
  - `python run_onboarding.py` — web app on http://127.0.0.1:8000 (login `lucas.wild@example.com` / `demo123`)
  - `python scripts/check_db.py` — DB sidecar smoke test (sidecar must be running on :3000)
  - `adk web` / `adk run src/journey_autopilot` — ADK-native UI/CLI

## Architecture map

`src/journey_autopilot/agent.py` exposes `root_agent` — a ReAct `LlmAgent` from `orchestrator.py` that wraps sub-agents as `AgentTool`. This importable `root_agent` is what `adk` expects.

Real packages:
- `agent.py` / `orchestrator.py` — Orchestrator (`root_agent`)
- `agents/monitoring.py`, `agents/planner.py` — live-trip specialists
- `tools/risk_model.py`, `scripts/build_db_delay_reference.py` — deterministic risk KPIs and archive rebuild
- `agents/communicator.py`, `integrations/whatsapp.py`, `integrations/whatsapp_webhook.py` — drafter, Twilio sender + approval queue, webhook (FastAPI)
- `onboarding/` — `accounts.py` (simulated DB/Outlook; the single swap point for a real integration)
- `persistence/store.py` — SQLite, stdlib only
- `ui/` — `server.py` (FastAPI web app), `chat.py` (runs the orchestrator per trip)
- `integrations/rights_rag/` — `rag_store.py` (ChromaDB), `crawler.py`, `rights_service.py`
- `integrations/db_ops.py`, `integrations/stations.py` — HTTP client for the Node sidecar
- `integrations/outlook/` — `auth.py`, `client.py`, `mapper.py` (MS Graph via Entra device-code flow)
- `config.py` — one `LiteLlm` per role, all Uni-Cologne-GPT (OpenAI-compatible, `openai/` provider prefix)
- `tools/read_tools.py` — function tools the agents call; the live/mock insertion point

## Path gotchas

- DB live-data client is `src/journey_autopilot/integrations/db_ops.py` + `stations.py`.
- Delay-reference build script is `scripts/build_db_delay_reference.py`.
- There is **no Makefile**. Sidecar: `cd db_service && npm start` (port 3000).

## `.env` loading (ADK quirk)

ADK discovers `.env` from the **agent directory**, not the project root. The run scripts compensate by loading both:
```python
load_dotenv()
load_dotenv("journey_autopilot/.env")
```
Fill `.env` at root from `.env.example`, then also copy it to `journey_autopilot/.env` (or rely on the scripts' double-load). Required: `UNI_GPT_*`. Optional: `TWILIO_*`, `MS_ENTRA_*`, `DB_API_URL`.

## Tool data contract — preserve it

`tools/read_tools.py` follows a **live-then-mock-fallback**: each tool tries the real source (db_service sidecar / MS Entra / archive JSON), falls back to `mock_data`, and tags every result with a `source` field (`db_service_live` / `db_history_archive` / `mock_*`). The Orchestrator instruction requires the agent to disclose `mock_*` sources to the user. Keep this contract when editing/adding tools.

## Hard-won gotchas

- **Windows SSL**: `scenarios/happy_path.py` patches `ssl.SSLContext.load_default_certs` to swallow `ASN1: NOT_ENOUGH_DATA` errors from the Windows cert store. Reuse this patch in any new entry script that imports ADK/LiteLLM on Windows, or `aiohttp`'s `ssl.create_default_context()` crashes at import.
- **OpenTelemetry pin**: ADK 2.2.x caps OTel at `<=1.41.1` in `requirements.txt`; `chromadb` would otherwise pull a newer exporter stack and break ADK. Keep the pin when touching deps.
- **`LITELLM_LOG=CRITICAL`** is set by default in `config.py`/`scenarios/happy_path.py` to suppress LiteLLM telemetry noise. Set `LITELLM_LOG=ERROR`/`DEBUG` when diagnosing the LLM backend.
- **Port 8000 conflict**: `run_onboarding.py` (web app) and `uvicorn journey_autopilot.integrations.whatsapp_webhook:app --port 8000` (WhatsApp reply webhook) both default to 8000. Use different ports.
- **`scripts/run_crawler.py`** adds `src/` to `sys.path` when run directly. Prefer `python scripts/run_crawler.py`.
- **ADK 2.x** has breaking changes vs 1.x (Agent API, event/session model). Many online tutorials show 1.x — don't follow them. Docs: https://google.github.io/adk-docs/

## Demo scenario anchors (do not drift)

- Demo date `2026-06-19`; demo user `lucas` / `lucas.wild@example.com` / `demo123`.
- Lucas' Munich → Berlin trip (`DEMO_TRIP` in `mock_data.py`) is pinned across `onboarding/accounts.booked_trips()`, the dashboard trip chat, and the monitoring/planner/calendar fixtures — keep them in sync.
- `scenarios/pretrip_risk.py` uses a **different** hardcoded route (Köln → Bonn, IC 2007) — not `DEMO_TRIP`.

## Persistence

- SQLite at `src/journey_autopilot/data/journey_autopilot.db` (gitignored via `*.db`), configurable via `JA_DB_PATH`. Profile stored as a JSON blob; no migrations. Schema in `persistence/store.py`.
- ChromaDB for passenger-rights RAG at `src/journey_autopilot/data/chromadb/` (gitignored), configurable via `CHROMA_PATH`. Rebuild: `python scripts/run_crawler.py`.
- `src/journey_autopilot/data/db_delay_reference.json` (~370 kB, committed, CC BY 4.0) is the pre-aggregated delay baseline; runtime reads only this JSON. Rebuild with `scripts/build_db_delay_reference.py` (needs `pyarrow` + `huggingface_hub`, not in requirements).

## Single-user prototype

Agent tools read "the latest" profile via `store.any_profile()`; there is no user_id threading through the agent stack. Multi-user support is an open question — don't assume session/user context exists.
