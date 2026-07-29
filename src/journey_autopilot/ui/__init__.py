"""UI layer — the web app (FastAPI server + static DB Navigator front-end).

Everything user-facing lives here: the FastAPI app assembly (``server.py``), one
router per theme (``routes/``), the orchestrator-backed chat (``chat.py``), and
the static assets in ``static/``.

The presentation layer is deliberately separate from the logic it drives: the
routers pull the simulated accounts from ``journey_autopilot.demo``, the
complaint drafts from ``journey_autopilot.onboarding``, and persistence from
``journey_autopilot.persistence``; the chat endpoint runs the same ReAct
orchestrator (``journey_autopilot.agent.root_agent``) as
``scenarios/happy_path.py``. ADK is only imported lazily inside the chat path, so the
onboarding flow keeps working even without the agent dependencies installed.
"""
