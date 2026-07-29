# ADR 0001 — Orchestration framework: Google ADK (not LangGraph)

Status: Accepted

## Context
The build spec (§2) recommends LangGraph for typed shared state, a SQLite
checkpointer, and `interrupt()` for the veto gate. The project, however, is
built on **Google ADK** with the University of Cologne GPT as the model backend
(OpenAI-compatible endpoint via LiteLLM) — that is a fixed constraint for this
work.

## Decision
Keep **Google ADK** and realize the spec's architecture on ADK primitives. The
architecture itself (orchestrator-workers, read/write tool separation, policy
layer, context record, mock-everything integrations, persistence) is
framework-agnostic and is adopted as written. Only the LangGraph-specific
mechanics are mapped to ADK equivalents:

| Build spec (LangGraph) | ADK realization |
|---|---|
| `graph.py` / `StateGraph` | `orchestrator.py`: a `root_agent` (LlmAgent) that wraps the workers as `AgentTool` and routes in a ReAct loop |
| `ContextRecord` as live graph state | `state.py` as a typed **data contract**; ADK transports run state via `SessionService` |
| `checkpointer.py` (SQLite) | ADK `SessionService` (`InMemoryRunner` today; `DatabaseSessionService` optional) |
| `interrupt()` veto gate | the approval/veto queue in `integrations/whatsapp/messaging.py` (YES/NO/EDIT) |

## Consequences
- The orchestrator decides dynamically (ReAct) whether the Planner is needed,
  rather than a hard-wired graph — flexible, at the cost of less explicit control
  flow.
- The cross-session app data (profile, constraints, history) lives in
  `persistence/store.py` (SQLite), independent of ADK session state.
- Model choice is decoupled via LiteLLM; per-agent model is a config value.
