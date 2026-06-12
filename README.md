# Journey Autopilot

Agent-based system that proactively monitors DB train journeys, detects
disruptions, plans reroutes, and informs the user transparently — with full
veto control. University project, built on **Google ADK 2.x** with the University of
Cologne GPT as the model backend (OpenAI-compatible endpoint, via LiteLLM).

---

## Prerequisites

- **Miniconda/Anaconda** or `venv` — pull the Python version into the
  environment; a system-wide Python install is not required.
- Python **3.11+** (required by ADK 2.0).
- **University of Cologne GPT** (OpenAI-compatible) — Key, endpoint, and
  model name from the Uni GPT service. ADK talks to the endpoint via LiteLLM;
  the agent code remains untouched by this.

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
# Open .env and fill in the UNI_GPT_* values (see below)
```

### Configure Backend (Uni-Cologne-GPT)

Key, endpoint, and model name are stored in `.env`:

```ini
UNI_GPT_API_KEY=your_uni_key
UNI_GPT_BASE_URL=https://your-uni-endpoint/v1   # incl. /v1
UNI_GPT_MODEL=your_uni_model_name
UNI_GPT_API_KEY=dein_uni_key
UNI_GPT_BASE_URL=https://dein-uni-endpunkt/v1   # inkl. /v1
UNI_GPT_MODEL=dein_uni_modellname # e.g Openai GPT OSS 120B
```

`config.py` builds `LiteLlm` models for all three roles from these — the ReAct code
(`agent.py`, `monitoring.py`, `planner.py`) does not change. LiteLLM comes with
the `extensions` extra in `requirements.txt` (`google-adk[extensions]`).

> **Note on `.env`:** ADK loads `.env` from the respective
> **agent directory**. The easiest approach is to copy the completed `.env` into the
> agent folder(s) (`cp .env <agent>/.env`). If `adk` cannot find the file, check
> the current ADK docs on `.env` discovery.

## Running

Three ways, same `root_agent` (the Orchestrator):

```bash
python run_demo.py              # End-to-End demo in terminal, streams agent trace
adk web                         # Dev UI in browser — select agent & chat
adk run journey_autopilot       # directly in terminal, interactive
```

`run_demo.py` is the fastest way to see how the two agents collaborate:
It shows how the Orchestrator first calls the Monitoring Agent and —
only when risk is elevated — pulls in the Planner.

---

## Current State (Baseline)

A first runnable foundation is implemented with **two specialist agents
and an orchestrator following the ReAct pattern**. Data is deliberately mocked.

- **Orchestrator** (`journey_autopilot/agent.py`, `root_agent`) — `LlmAgent`
  that wraps the specialists as `AgentTool` and decides in a ReAct loop
  who to call when. Always calls Monitoring first, then (if risk present) Planner.
- **Monitoring Agent** (`monitoring.py`) — reads mocked live data and
  disruption status, returns a risk level (LOW/MEDIUM/HIGH).
- **Planner Agent** (`planner.py`) — generates reroute options, checks them against
  hard deadlines (calendar), and cites passenger rights. Proposes, does not book.
- **Tools & Mock Data** (`tools.py`, `mock_data.py`) — Function tools backed by
  fixtures; the insertion points for real DB/calendar/RAG sources.
- **Model Configuration** (`config.py`) — a single place where the model is set
  per role; talks to the Uni-Cologne-GPT (OpenAI-compatible) via LiteLLM.

### File Layout

```
journey_autopilot/
  __init__.py        # makes the package discoverable for adk (root_agent)
  agent.py           # Orchestrator (root_agent, ReAct)
  monitoring.py      # Monitoring Agent
  planner.py         # Planner Agent
  tools.py           # Function Tools (mocked)
  mock_data.py       # Fixtures (demo trip Munich→Berlin)
  config.py          # Model per role
run_demo.py          # Standalone End-to-End demo
```

## Target Vision (still open)

The system grows modularly along the agent roles (see
`journey_autopilot_projektgrundlage.md`):

- **Context Capture** — deterministic function, freezes constraints
- **Monitoring Agent** ✅ — polls (mocked) live data, scores disruption risk
- **Planner Agent** ✅ — generates reroute options under constraints (RAG)
- **Negotiator Agent** — multi-stakeholder coordination
- **Veto Gate** — human-in-the-loop, user retains veto
- **Communicator Agent** — notifications (WhatsApp/Outlook)

State: ADK `SessionService` (volatile, within run) + SQLite (persistent
preferences, hard constraints, trip history).

## Caveats

- ADK **2.0** has breaking changes vs. 1.x (Agent API, event model,
  session schema). Many tutorials still show 1.x — pay attention to the version.
- Data is deliberately mocked (no real DB API access) — document as an ADR and in
  the Context Record.
- Official docs: https://google.github.io/adk-docs/ and https://adk.dev/
