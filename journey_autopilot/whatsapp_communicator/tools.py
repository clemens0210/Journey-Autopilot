"""Function tools of the WhatsApp communicator.

This bundles all functionality around the drafter agent — the agent only drafts
the message; sending and managing it happens here:

- **Approval queue**: thread-safe in-memory store with a 5-min timeout.
  One open approval slot per traveler number (PoC).
- **Sender**: sends approval requests and final messages via Twilio.

As in the main package (`journey_autopilot/tools.py`), these are plain typed
Python functions — the plug-in points for real queue/sending backends.
"""

from __future__ import annotations

import logging
import os
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

from twilio.base.exceptions import TwilioRestException
from twilio.rest import Client

from .models import DisruptionEvent, Recipient

logger = logging.getLogger(__name__)


# --- Approval queue -----------------------------------------------------------

EXPIRY_SECONDS = 300  # 5 minutes

_lock = threading.Lock()


@dataclass
class PendingMessage:
    message_id: str
    draft: str
    recipient: Recipient      # the final recipient (not the traveler)
    event: DisruptionEvent
    traveler_number: str      # E.164 — also the key in _pending
    created_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )


# One open approval slot per traveler WhatsApp number (PoC).
_pending: dict[str, PendingMessage] = {}


def enqueue(
    draft: str,
    recipient: Recipient,
    event: DisruptionEvent,
    traveler_number: str,
) -> str:
    """Puts a draft into the approval queue and returns the message_id."""
    message_id = str(uuid.uuid4())
    with _lock:
        _pending[traveler_number] = PendingMessage(
            message_id=message_id,
            draft=draft,
            recipient=recipient,
            event=event,
            traveler_number=traveler_number,
        )
    return message_id


def get(traveler_number: str) -> PendingMessage | None:
    """Returns the open draft for a traveler number (or None)."""
    with _lock:
        return _pending.get(traveler_number)


def remove(traveler_number: str) -> PendingMessage | None:
    """Removes the open draft for a traveler number and returns it."""
    with _lock:
        return _pending.pop(traveler_number, None)


def update_draft(traveler_number: str, new_draft: str) -> None:
    """Replaces the text of an open draft (EDIT workflow)."""
    with _lock:
        if traveler_number in _pending:
            _pending[traveler_number].draft = new_draft


def cleanup_expired() -> list[PendingMessage]:
    """Removes and returns all drafts older than EXPIRY_SECONDS."""
    now = datetime.now(timezone.utc)
    with _lock:
        expired = [
            msg for msg in _pending.values()
            if (now - msg.created_at).total_seconds() > EXPIRY_SECONDS
        ]
        for msg in expired:
            _pending.pop(msg.traveler_number, None)
    return expired


# --- Sender (Twilio) ----------------------------------------------------------


def _client() -> Client:
    return Client(
        os.environ["TWILIO_ACCOUNT_SID"],
        os.environ["TWILIO_AUTH_TOKEN"],
    )


def _from_number() -> str:
    val = os.environ.get("TWILIO_WHATSAPP_FROM")
    if not val:
        raise EnvironmentError(
            "TWILIO_WHATSAPP_FROM is not set. "
            "Add it to .env (e.g. whatsapp:+14155238886)."
        )
    return val


def _traveler(event: DisruptionEvent) -> Recipient:
    return next(r for r in event.recipients if r.role == "traveler")


def send_for_approval(
    event: DisruptionEvent, draft: str, recipient: Recipient
) -> str:
    """Queues the draft and sends an approval request to the traveler.

    Returns the message_id for tracking.
    """
    traveler = _traveler(event)

    message_id = enqueue(
        draft=draft,
        recipient=recipient,
        event=event,
        traveler_number=traveler.whatsapp_number,
    )

    body = (
        f"*Journey Autopilot — Approval Required*\n\n"
        f"Draft for *{recipient.name}* ({recipient.role}):\n"
        f"———\n{draft}\n———\n\n"
        f"Reply:\n"
        f"  *YES* — send as-is\n"
        f"  *NO* — cancel\n"
        f"  *EDIT <new text>* — replace draft\n\n"
        f"Not sent if no reply within 5 min."
    )

    try:
        _client().messages.create(
            from_=_from_number(),
            to=f"whatsapp:{traveler.whatsapp_number}",
            body=body,
        )
    except TwilioRestException as exc:
        logger.error(
            "Twilio error sending approval request to %s [id=%s]: %s",
            traveler.name,
            message_id,
            exc,
        )
        raise

    logger.info(
        "action=approval_sent traveler=%s recipient=%s message_id=%s",
        traveler.name,
        recipient.name,
        message_id,
    )
    return message_id


def dispatch_message(draft: str, recipient: Recipient) -> None:
    """Sends a message directly to a recipient."""
    try:
        _client().messages.create(
            from_=_from_number(),
            to=f"whatsapp:{recipient.whatsapp_number}",
            body=draft,
        )
    except TwilioRestException as exc:
        logger.error(
            "Twilio error dispatching to %s (%s): %s",
            recipient.name,
            recipient.whatsapp_number,
            exc,
        )
        raise

    logger.info(
        "action=dispatched ts=%s recipient=%s number=%s",
        datetime.now(timezone.utc).isoformat(),
        recipient.name,
        recipient.whatsapp_number,
    )
