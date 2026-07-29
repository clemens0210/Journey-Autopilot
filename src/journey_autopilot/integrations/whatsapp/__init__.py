"""WhatsApp channel — Twilio sender plus the human approval (veto) queue.

- ``messaging`` — everything the agents touch: the SQLite-backed approval queue
  (shared across processes, see the module docstring) and the Twilio sends.
  Its public surface is re-exported below, so callers keep writing
  ``whatsapp.send_disruption_alert(...)``.
- ``models``    — ``DisruptionEvent`` / ``Recipient``, the dataclasses the
  Communicator drafts against. Also re-exported.
- ``webhook``   — the FastAPI app Twilio posts the traveler's YES/NO/EDIT reply
  to. Runs as its own uvicorn process, so it is imported on demand rather than
  re-exported (same reasoning as ``outlook.device_flow``).
"""

from .messaging import (
    EXPIRY_SECONDS,
    PendingMessage,
    cleanup_expired,
    dispatch_message,
    enqueue,
    get,
    is_configured,
    remove,
    send_disruption_alert,
    send_for_approval,
    send_verification_code,
    update_draft,
)
from .models import DisruptionEvent, Recipient

__all__ = [
    "EXPIRY_SECONDS",
    "DisruptionEvent",
    "PendingMessage",
    "Recipient",
    "cleanup_expired",
    "dispatch_message",
    "enqueue",
    "get",
    "is_configured",
    "remove",
    "send_disruption_alert",
    "send_for_approval",
    "send_verification_code",
    "update_draft",
]
