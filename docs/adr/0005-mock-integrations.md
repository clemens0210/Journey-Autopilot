# ADR 0005 — Mock every external integration behind clean interfaces

Status: Accepted

## Context
Real DB live-ops, Outlook, WhatsApp, and DB account/ticket APIs are not
available to a university project (no official DB API, no registered Microsoft
app, no SMS gateway). The system must still run end-to-end and the scenarios must
be reproducible (build spec §3.3).

## Decision
Every external integration sits behind an interface in `integrations/`, with a
mock/simulated implementation and a single swap point for the real one:

- `db_ops` / `stations` — DB live data via the `db_service` (Node) sidecar; tools
  fall back to `mock_data` and tag the result with `source`.
- `outlook` — Outlook/Microsoft Graph; falls back to mock calendar without Entra
  credentials.
- `whatsapp` / `whatsapp_webhook` — Twilio sender + approval queue; dry-run
  prints without Twilio config.
- `rights_rag` — passenger-rights knowledge base (crawler + Chroma RAG) +
  deterministic compensation rules.
- `onboarding/accounts` — simulated bahn.de login / trip import / SMS / Outlook
  consent, behind realistic API contracts.

## Consequences
- The demo is honest and the path to production is clear: each mock module is the
  one place a real integration plugs in.
- Where live data exists (db_service), tools try live first and fall back to mock
  — the `source` field makes the basis transparent to agent and user.
- Never call a real external endpoint from tests/scenarios.
