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
  access suffices. Without Twilio configuration, `run_demo.py` runs in dry-run
  mode and only prints the generated messages to the console.

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

# 3. Dependencies (google-adk is only on PyPI → via pip)
pip install -r requirements.txt

# 4. Store credentials
cp .env.example .env        # Windows (PowerShell): copy .env.example .env
# Open .env and fill in the values (UNI_GPT_* required, Twilio optional)
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
python run_demo.py              # End-to-End demo in terminal, streams agent trace
adk web                         # Dev UI in browser — select agent & chat
adk run journey_autopilot       # directly in terminal, interactive
```

`run_demo.py` first shows the Orchestrator run (Monitoring → Planner) and then —
provided `DEMO_TRAVELER_NUMBER` is set — the WhatsApp Communicator demo: the
Drafter Agent drafts messages for each configured recipient and (with a complete
Twilio configuration) sends them to the traveler for approval.

### Webhook server (receive WhatsApp replies)

```bash
uvicorn journey_autopilot.whatsapp_communicator.webhook:app --port 8000
```

Twilio sends the traveler's replies (YES / NO / EDIT \<text\>) to
`POST /whatsapp/reply`. The server forwards them to the approval logic in
`whatsapp_communicator/tools.py` and, upon approval, dispatches the message to the
actual recipient via Twilio.

---

## Current State (Baseline)

A first runnable foundation is implemented with **three specialist agents, an
orchestrator, and a WhatsApp communication layer**. Data is deliberately mocked.

- **Orchestrator** (`journey_autopilot/agent.py`, `root_agent`) — `LlmAgent`
  that wraps the specialists as `AgentTool` and decides in a ReAct loop
  who to call when. Always calls Monitoring first, then (if risk present) Planner.
- **Monitoring Agent** (`monitoring.py`) — reads mocked live data and
  disruption status, returns a risk level (LOW/MEDIUM/HIGH).
- **Planner Agent** (`planner.py`) — generates reroute options, checks them against
  hard deadlines (calendar), and cites passenger rights. Proposes, does not book.
- **Drafter Agent** (`whatsapp_communicator/drafter.py`) — `LlmAgent` that drafts
  role-appropriate WhatsApp messages for each recipient.
- **Communicator Tools** (`whatsapp_communicator/tools.py`) — sender (Twilio) plus
  approval queue (in-memory, 5-minute timeout).
- **Webhook** (`whatsapp_communicator/webhook.py`) — FastAPI endpoint for
  YES / NO / EDIT replies.
- **Tools & Mock Data** (`tools.py`, `mock_data.py`) — Function tools backed by
  fixtures; the insertion points for real DB/calendar/RAG sources.
- **Model Configuration** (`config.py`) — a single place where the model is set
  per role; talks to the Uni-Cologne-GPT (OpenAI-compatible) via LiteLLM.

### File Layout

```
journey_autopilot/
  __init__.py                  # makes the package discoverable for adk (root_agent)
  agent.py                     # Orchestrator (root_agent, ReAct)
  monitoring.py                # Monitoring Agent
  planner.py                   # Planner Agent
  tools.py                     # Function Tools (mocked)
  mock_data.py                 # Fixtures (demo trip Munich→Berlin)
  config.py                    # Model per role (UNI_GPT_*)
  whatsapp_communicator/
    __init__.py
    models.py                  # Recipient, DisruptionEvent
    drafter.py                 # Drafter Agent (LlmAgent)
    tools.py                   # Sender (Twilio) + approval queue
    webhook.py                 # FastAPI webhook (YES/NO/EDIT)
run_demo.py                    # End-to-End demo (agents + WhatsApp)
```

## Target Vision (still open)

The system grows modularly along the agent roles (see
`journey_autopilot_projektgrundlage.md`):

- **Context Capture** — deterministic function, freezes constraints
- **Monitoring Agent** ✅ — polls (mocked) live data, scores disruption risk
- **Planner Agent** ✅ — generates reroute options under constraints (RAG)
- **Communicator Agent** ✅ — WhatsApp messages with approval workflow (Twilio)
- **Negotiator Agent** — multi-stakeholder coordination
- **Veto Gate** — human-in-the-loop, user retains veto
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