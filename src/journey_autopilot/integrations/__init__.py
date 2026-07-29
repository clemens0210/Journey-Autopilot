"""External integrations — all mocked behind clean interfaces.

Real DB live-ops, Outlook, and WhatsApp APIs are not available; each module is
the single swap point for a real integration:

- ``db``         — DB live data via the db_service sidecar (``ops``, ``stations``).
- ``outlook``    — Outlook/Microsoft Graph calendar.
- ``whatsapp``   — Twilio sender + approval (veto) queue + the reply webhook.
- ``rights_rag`` — passenger-rights knowledge base (RAG).
- ``hotels``     — partner hotels near a station; a single module, so it stays flat.

One package per external system: everything that knows a given provider's
protocol, error codes, or auth dance lives behind its package boundary, and the
rest of the app calls a plain-Python interface. ``outlook.device_flow`` is the
clearest case — the web layer only calls ``start`` / ``poll`` / ``forget`` and
never sees an MSAL or AADSTS detail.
"""
