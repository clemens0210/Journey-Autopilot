import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
import uuid

from .models import DisruptionEvent, Recipient

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


# One pending approval slot per traveler WhatsApp number (PoC).
_pending: dict[str, PendingMessage] = {}


def enqueue(
    draft: str,
    recipient: Recipient,
    event: DisruptionEvent,
    traveler_number: str,
) -> str:
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
    with _lock:
        return _pending.get(traveler_number)


def remove(traveler_number: str) -> PendingMessage | None:
    with _lock:
        return _pending.pop(traveler_number, None)


def update_draft(traveler_number: str, new_draft: str) -> None:
    with _lock:
        if traveler_number in _pending:
            _pending[traveler_number].draft = new_draft


def cleanup_expired() -> list[PendingMessage]:
    """Remove and return all messages older than EXPIRY_SECONDS."""
    now = datetime.now(timezone.utc)
    with _lock:
        expired = [
            msg for msg in _pending.values()
            if (now - msg.created_at).total_seconds() > EXPIRY_SECONDS
        ]
        for msg in expired:
            _pending.pop(msg.traveler_number, None)
    return expired
