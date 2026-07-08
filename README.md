# Journey Autopilot

Agent-based system that proactively monitors DB train journeys, detects
disruptions, plans reroutes, and notifies affected parties via WhatsApp — with
full veto control by the traveler. University project, built on **Google ADK 2.x**
with the University of Cologne GPT as the model backend (OpenAI-compatible
endpoint, via LiteLLM).

---

## Prerequisites

- **Miniconda/Anaconda** or `venv` — pull the Python version into the
  environment; a system-wide Python install is not required.
- Python **3.11+** (required by ADK 2.0).
- **University of Cologne GPT** (OpenAI-compatible) — Key, endpoint, and
  model name from the Uni GPT service. ADK talks to the endpoint via LiteLLM;
  the agent code remains untouched by this.
- **Twilio account** (optional) — only for the WhatsApp Communicator. Sandbox
  access suffices. Without Twilio configuration, `scenarios/happy_path.py` runs
  in dry-run mode and only prints the generated messages to the console.
- **Node.js 18+** — only needed for the DB live data sidecar (`db_service/`). If
  you're working without real DB data (mock mode), you don't need Node.

## Setup

```bash
# 1. Into the project directory
cd journey-autopilot

# 2. Create & activate environment (Conda)
conda create -n journey-autopilot python=3.11
conda activate journey-autopilot
#   ... or venv:
#   python -m venv .venv
#   source .venv/bin/activate        # Windows: .venv\Scripts\activate

# 3. Install the package (editable, src/ layout) + dependencies
pip install -e .
#   ... or just the deps:  pip install -r requirements.txt
#   (the run scripts add src/ to sys.path themselves, so they work either way)

# 4. Store credentials
cp .env.example .env        # Windows (PowerShell): copy .env.example .env
# Open .env and fill in the values (UNI_GPT_* required, Twilio optional)

# 5. Prepare DB live data sidecar (Node) — optional, only for real DB data
cd db_service && npm install && cd ..
```

### Configure Backend (Uni-Cologne-GPT)

Key, endpoint, and model name are stored in `.env`:

```ini
UNI_GPT_API_KEY=your_uni_key
UNI_GPT_BASE_URL=https://your-uni-endpoint/v1   # incl. /v1
UNI_GPT_MODEL=your_uni_model_name
```

`config.py` builds `LiteLlm` models for all four roles (Orchestrator,
Monitoring, Planner, Drafter) from these. LiteLLM comes with the `extensions`
extra in `requirements.txt` (`google-adk[extensions]`).

> **Note on `.env`:** ADK loads `.env` from the respective
> **agent directory**. The easiest approach is to copy the completed `.env` into the
> agent folder(s) (`cp .env <agent>/.env`). If `adk` cannot find the file, check
> the current ADK docs on `.env` discovery.

### Configure WhatsApp Communicator (optional)

```ini
TWILIO_ACCOUNT_SID=ACxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
TWILIO_AUTH_TOKEN=your_auth_token
TWILIO_WHATSAPP_FROM=whatsapp:+14155238886

DEMO_TRAVELER_NUMBER=+49171xxxxxxx   # Must be registered in the Twilio Sandbox
DEMO_CLIENT_NUMBER=+49172xxxxxxx
DEMO_COLLEAGUE_NUMBER=+49173xxxxxxx
```

For incoming replies (YES/NO/EDIT), Twilio needs a publicly reachable URL.
Locally, the easiest option is [ngrok](https://ngrok.com):

```bash
ngrok http 8000
# Enter the tunnel URL in .env as WEBHOOK_BASE_URL
# In the Twilio Console: set the webhook URL to https://<tunnel>/whatsapp/reply
```

## Running

### Agent demo (Monitoring + Planner)

Three ways, same `root_agent` (the Orchestrator):

```bash
python scenarios/happy_path.py  # End-to-End demo in terminal, streams agent trace
adk web                         # Dev UI in browser — select agent & chat
adk run src/journey_autopilot   # directly in terminal, interactive (src/ layout)
```

`scenarios/happy_path.py` first shows the Orchestrator run (Monitoring → Planner) and then —
provided `DEMO_TRAVELER_NUMBER` is set — the WhatsApp Communicator demo: the
Drafter Agent drafts messages for each configured recipient and (with a complete
Twilio configuration) sends them to the traveler for approval.

### DB live data (db_service sidecar)

Real DB live data (delays, routing, prices) comes via
[`db-vendo-client`](https://github.com/public-transport/db-vendo-client) — a
Node library with DB-Navigator-accurate data. Since our backend is Python, it
runs as a small **sidecar** (`db_service/`): a local JSON service that the
Python side talks to over HTTP.

```
[ ADK agents ] -> read_tools.py -> db_ops.py --HTTP--> db_service (Node) -> DB
```

Start the sidecar (separate terminal):

```bash
cd db_service && npm start      # runs on http://127.0.0.1:3000
```

Test the connection (sidecar must be running):

```bash
python scripts/check_db.py              # health check + EVA resolution + one connection
```

Endpoints and options are documented in `db_service/README.md`. The Python
client is configured via `DB_API_URL` / `DB_API_TIMEOUT` in `.env`.

> **Status:** The sidecar and Python client (`integrations/db_ops.py`,
> `integrations/stations.py`) are finished and independently testable. The read
> tools try the sidecar first and fall back to `mock_data`, tagging the result
> with a `source` field.

### Historical delay reference (Monitoring Agent, pre-trip risk)

The Monitoring Agent bases its pre-trip baseline on a real punctuality **archive** spanning
several months. The committed `src/journey_autopilot/data/db_delay_reference.json`
(~370 kB) is pre-aggregated from the
[`piebro/deutsche-bahn-data`](https://github.com/piebro/deutsche-bahn-data)
dataset (real DB stops, **CC BY 4.0**) — arrival delay metrics per station and
train type. At runtime, only this JSON is read (no heavy dependencies, usable
offline).

Rebuild/update (downloads Parquet from Hugging Face, additionally needs
`pyarrow` and `huggingface_hub`):

```bash
python scripts/build_db_delay_reference.py 2025-08 2025-09 2025-10
```

> **License/attribution:** Data © Deutsche Bahn, provided by
> `piebro/deutsche-bahn-data` under CC BY 4.0. The `_meta` section of the JSON
> records the source, license, and covered months.

## Onboarding, Profile & Trip Chat (Web App)

The web app runs in the **DB Navigator look** (FastAPI + SQLite). It is split
into the presentation layer (`src/journey_autopilot/ui/`) and the onboarding logic
(`src/journey_autopilot/onboarding/`); see
[`src/journey_autopilot/ui/README.md`](src/journey_autopilot/ui/README.md) for details.

```bash
python run_onboarding.py        # -> http://127.0.0.1:8000
```

Or fully containerized — see the next section.

### Running with Docker (web app + DB sidecar)

`docker compose up` starts both services wired together: **app** (FastAPI web
app incl. LLM agent and rights RAG) and **db-service** (Node sidecar for DB
live data). Prerequisite: Docker Desktop is running (whale icon shows
"Engine running").

**One-time setup / first start:**

```bash
cp .env.example .env            # once; fill in UNI_GPT_* so the trip chat works
docker compose up --build       # -> http://127.0.0.1:8000
```

The first build downloads ~3–4 GB (Python base image, CPU-only torch, and the
passenger-rights embedding model, which is baked into the image so the
container never downloads it at runtime). On the first start the entrypoint
crawls bahn.de once to build the Chroma rights index — watch for
`[entrypoint] Passenger-rights index empty — building it ...` in the log.
Subsequent builds and starts come from the cache and take seconds.

**Everyday start/stop:**

```bash
docker compose up -d            # start in the background
docker compose logs -f app      # follow app logs (agent trace, Outlook device code)
docker compose down             # stop & remove containers — data survives
```

Then open http://127.0.0.1:8000. `docker compose stop` / `start` also work
(pause/resume without removing containers), but `up -d` / `down` is the
simplest pair to remember.

**After changing code or `.env`:**

```bash
docker compose up -d --build    # rebuild image + recreate containers
```

The image contains a snapshot of `src/` — local code edits are **not** live
inside a running container, and `.env` is only read when a container is
(re)created. Both are picked up by the command above; thanks to layer caching
a code-only rebuild takes seconds, not the full 3–4 GB.

**Status & health:**

```bash
docker compose ps               # both services should report "healthy"
curl http://localhost:3000/health   # sidecar directly: {"ok":true,...}
```

**Reset all app data (profile, trips, rights index):**

```bash
docker compose down -v          # removes the app-data volume
```

SQLite and the Chroma index live in the named volume `app-data` (mounted at
`/data` in the container), so they survive `down`, rebuilds, and image
updates. After `down -v`, the next start runs onboarding from scratch and
re-crawls the rights index.

**Notes:**

- Running containerized and locally (`python run_onboarding.py`) at the same
  time collides on ports 8000/3000 — stop one of them first. The two setups
  keep separate data (volume `app-data` vs. `src/journey_autopilot/data/`).
- The Outlook device-code login prints its URL + code to the app log — have
  `docker compose logs -f app` open when connecting the calendar.
- The Python side reaches the sidecar via the compose network
  (`DB_API_URL=http://db-service:3000` is set in `docker-compose.yml`); the
  `DB_API_URL` in your `.env` only applies to non-Docker runs.

Demo access: `lucas.wild@example.com` / `demo123` (also shown on the
login screen). The wizard walks through: DB account login with trip import →
mobile number verification (SMS code, simulated) → Outlook calendar (simulated
OAuth consent) → travel preferences (class, seat, speed-vs-comfort) →
home constraints (home station, latest return time, hotel/taxi) →
notifications & autonomy level → summary → dashboard.

- The **only requirement** is the DB login; mobile number and Outlook can be
  skipped, all preferences have defaults.
- **Trip chat:** tapping a monitored trip on the dashboard opens a chat that
  runs the ReAct orchestrator live (the same flow as `scenarios/happy_path.py`) and shows
  the reply plus a collapsible agent trace. Lucas' Munich → Berlin trip is the
  scripted demo scenario. Requires a configured Uni-GPT backend in `.env`.
- **Simulated** are DB login/trip import, Microsoft consent, and SMS sending
  (no official APIs for a university project) — but the API contracts match
  what a real integration would need to deliver (swap point:
  `src/journey_autopilot/onboarding/accounts.py`). Rationale in the Context Record.
- **Real** is the home station search: if the `db_service` sidecar is running,
  station suggestions come live from the DB API (green dot), otherwise a
  static fallback list is used.
- **Persistence:** SQLite under `src/journey_autopilot/data/journey_autopilot.db`. The agents read
  the profile via the `get_user_profile` / `get_upcoming_trips` tools — the
  Planner weighs reroute options using it. GDPR deletion with one click in the
  dashboard.

### Webhook server (receive WhatsApp replies)

```bash
uvicorn journey_autopilot.integrations.whatsapp_webhook:app --port 8000
```

Twilio sends the traveler's replies (YES / NO / EDIT \<text\>) to
`POST /whatsapp/reply`. The server forwards them to the approval logic in
`integrations/whatsapp.py` and, upon approval, dispatches the message to the
actual recipient via Twilio.

`scenarios/happy_path.py` is the fastest way to see the agents working together:
it shows how the Orchestrator first calls the Monitoring Agent and — only if
risk is elevated — brings in the Planner afterward. `scenarios/pretrip_risk.py`
drives the Monitoring agent on its **pre-trip** path: upfront delay risk
(score 0-100) and predicted arrival (ETA), **before** the trip has started.

---

## Current State (Baseline)

A runnable foundation is implemented with an **orchestrator and its specialist
workers** plus a WhatsApp communication layer, reorganized into the target
architecture (see `docs/journey-autopilot-build-spec.md` and `docs/adr/`). Data
is deliberately mocked.

- **Orchestrator** (`orchestrator.py`, `root_agent`) — `LlmAgent` that wraps the
  workers as `AgentTool` and decides in a ReAct loop who to call when. Calls
  Monitoring first, then (if risk present) Planner, and — once the user approves
  an option — the Executor (write path, behind the veto gate). `agent.py` is a
  thin shim exposing `root_agent` for ADK discovery.
- **Monitoring Agent** (`agents/monitoring.py`) — read-only risk detection. Both
  pre-trip (delay risk + ETA from punctuality history) and en-route (live status
  + disruptions). Risk is a deterministic model tool, not an LLM judgment.
- **Planner Agent** (`agents/planner.py`) — read-only. Generates reroute options,
  checks them against hard deadlines (calendar), cites passenger rights.
- **Communicator Agent** (`agents/communicator.py`) — write. Drafts
  role-appropriate WhatsApp messages for each recipient.
- **Read tools / risk model** (`tools/read_tools.py`, `tools/risk_model.py`) —
  function tools + deterministic delay statistics, backed by fixtures
  (`mock_data.py`); insertion points for real DB/calendar/RAG sources.
- **Integrations** (`integrations/`) — DB sidecar (`db_ops`/`stations`), Outlook
  (`outlook/`), WhatsApp Twilio sender + approval/veto queue (`whatsapp.py`,
  `whatsapp_webhook.py`), passenger-rights RAG (`rights_rag/`). All mocked behind
  interfaces.
- **Persistence** (`persistence/store.py`) — SQLite profile/constraints/trips.
- **Policy layer / veto gate** (`policy.py`, `tools/write_tools.py`,
  `agents/executor.py`) — `policy.resolve()` maps each write tool to `auto`/`ask`
  from `config/policy.yaml`, shifted by a global autonomy level and overridden by
  the user's profile (`policy` block, set in the "Automation & veto" UI). The
  Executor holds the (simulated) write tools; a gated action returns
  `veto_required` and only fires after the user approves in the chat.
- **Scaffolds** (target architecture, not yet wired): `state.py`, `errors.py`,
  `persistence/checkpointer.py`, plus `scenarios/`, `baseline/`, `eval/`.
- **Docker** (`Dockerfile`, `docker-compose.yml`, `docker_entrypoint.py`) —
  containerized web app + DB sidecar; see "Onboarding, Profile & Trip Chat".
- **Model Configuration** (`config.py`) — a single place where the model is set
  per role; talks to the Uni-Cologne-GPT (OpenAI-compatible) via LiteLLM.

### File Layout

```
config/                        # policy.yaml, settings.yaml (scaffold)
data/                          # sqlite + chromadb (gitignored)
docs/adr/                      # architecture decision records
scenarios/                     # happy_path.py + edge/failure stubs
scripts/                       # check_db, calendar_demo, run_crawler, build_*
baseline/  eval/               # naive baseline + eval harness (stubs)
src/journey_autopilot/
  __init__.py                  # package marker (adk discovery)
  agent.py                     # shim: re-exports root_agent for adk
  orchestrator.py              # Orchestrator (root_agent, ReAct)
  state.py  errors.py        # context record + error policy (scaffold)
  policy.py                  # veto gate: resolves write tools auto/ask (active)
  config.py  mock_data.py
  agents/      monitoring.py  planner.py  communicator.py  executor.py
  tools/       read_tools.py  write_tools.py  risk_model.py
  integrations/  db_ops.py  stations.py  outlook/  whatsapp.py  whatsapp_webhook.py
                 whatsapp_models.py  rights_rag/
  persistence/   store.py  checkpointer.py(stub)
  onboarding/    accounts.py
  ui/            server.py  chat.py  static/
run_onboarding.py              # launches the web app
```

## Target Vision (still open)

The system grows modularly along the agent roles (see
[`CONTEXT_RECORD.md`](CONTEXT_RECORD.md) and
[`docs/journey-autopilot-build-spec.md`](docs/journey-autopilot-build-spec.md)):

- **Context Capture** — deterministic function, freezes constraints
- **Monitoring Agent** ✅ — polls (mocked) live data, scores disruption risk
- **Planner Agent** ✅ — generates reroute options under constraints (RAG)
- **Communicator Agent** ✅ — WhatsApp messages with approval workflow (Twilio)
- **Negotiator Agent** — multi-stakeholder coordination
- **Veto Gate** ✅ — human-in-the-loop, user retains veto (policy layer + Executor)
- **Booking Agent** — book tickets, hotels, and mobility options (reversible)
- **Memory & Learning** — persist preferences (SQLite)

State: ADK `SessionService` (volatile, within run) + SQLite (persistent
preferences, hard constraints, trip history).

## Caveats

- ADK **2.0** has breaking changes vs. 1.x (Agent API, event model,
  session schema). Many tutorials still show 1.x — pay attention to the version.
- Data is deliberately mocked (no real DB API access) — document as an ADR and in
  the Context Record.
- Official docs: https://google.github.io/adk-docs/ and https://adk.dev/
