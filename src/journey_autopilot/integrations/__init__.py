"""External integrations — all mocked behind clean interfaces.

Real DB live-ops, Outlook, and WhatsApp APIs are not available; each module is
the single swap point for a real integration:

- ``db_ops`` / ``stations`` — DB live data via the db_service sidecar.
- ``outlook``               — Outlook/Microsoft Graph calendar.
- ``whatsapp`` / ``whatsapp_webhook`` — Twilio sender + approval (veto) queue.
- ``rights_rag``            — passenger-rights knowledge base (RAG).
"""
