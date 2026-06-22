"""UI layer — the web app (FastAPI server + static DB Navigator front-end).

Everything user-facing lives here: the FastAPI app (``server.py``), the
orchestrator-backed chat (``chat.py``), and the static assets in ``static/``.

The presentation layer is deliberately separate from the onboarding *logic*:
``server.py`` imports the simulated accounts and the SQLite store from
``journey_autopilot.onboarding`` (the "functions"), and the chat endpoint
runs the same ReAct orchestrator (``journey_autopilot.agent.root_agent``) as
``run_demo.py``. ADK is only imported lazily inside the chat path, so the
onboarding flow keeps working even without the agent dependencies installed.
"""
