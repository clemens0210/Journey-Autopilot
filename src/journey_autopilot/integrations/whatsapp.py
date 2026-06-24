"""Function tools of the WhatsApp communicator.

This bundles all functionality around the drafter agent — the agent only drafts
the message; sending and managing it happens here:

- **Approval queue**: thread-safe in-memory store with a 5-min timeout.
  One open approval slot per traveler number (PoC).
- **Sender**: sends approval requests and final messages via Twilio.

As in the read tools package (`journey_autopilot/tools/read_tools.py`), these are plain typed
Python functions — the plug-in points for real queue/sending backends.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import uuid
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from twilio.base.exceptions import TwilioRestException
from twilio.rest import Client

from .whatsapp_models import DisruptionEvent, Recipient

logger = logging.getLogger(__name__)


# --- Approval queue (SQLite-backed) -------------------------------------------
# The queue is shared across processes: the demo/agent enqueues an approval in
# one process while the webhook server (uvicorn) looks it up in another when the
# traveler replies YES/NO/EDIT. An in-memory dict can't bridge those, so the
# queue is persisted to a small SQLite file. The path is derived from this
# module so every process agrees regardless of CWD; override with
# WHATSAPP_QUEUE_DB.

EXPIRY_SECONDS = 300  # 5 minutes

_DB_PATH = os.getenv("WHATSAPP_QUEUE_DB") or str(
    Path(__file__).resolve().parent / "approvals.db"
)


@dataclass
class PendingMessage:
    message_id: str
    draft: str
    recipient: Recipient      # the final recipient (not the traveler)
    event: DisruptionEvent
    traveler_number: str      # E.164 — also the primary key in the store
    created_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )


@contextmanager
def _db():
    """Opens a SQLite connection, ensures the schema, commits and closes."""
    conn = sqlite3.connect(_DB_PATH, timeout=5.0)
    try:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS pending_approvals ("
            "traveler_number TEXT PRIMARY KEY, "
            "message_id TEXT NOT NULL, "
            "created_at TEXT NOT NULL, "
            "data TEXT NOT NULL)"
        )
        yield conn
        conn.commit()
    finally:
        conn.close()


def _serialize(msg: PendingMessage) -> str:
    return json.dumps(
        {
            "message_id": msg.message_id,
            "draft": msg.draft,
            "recipient": asdict(msg.recipient),
            "event": asdict(msg.event),
            "traveler_number": msg.traveler_number,
            "created_at": msg.created_at.isoformat(),
        }
    )


def _deserialize(data: str) -> PendingMessage:
    d = json.loads(data)
    ev = d["event"]
    event = DisruptionEvent(
        traveler_name=ev["traveler_name"],
        original_train=ev["original_train"],
        delay_minutes=ev["delay_minutes"],
        reroute_summary=ev["reroute_summary"],
        meeting_time_original=ev["meeting_time_original"],
        meeting_time_new=ev["meeting_time_new"],
        recipients=[Recipient(**r) for r in ev["recipients"]],
    )
    return PendingMessage(
        message_id=d["message_id"],
        draft=d["draft"],
        recipient=Recipient(**d["recipient"]),
        event=event,
        traveler_number=d["traveler_number"],
        created_at=datetime.fromisoformat(d["created_at"]),
    )


def enqueue(
    draft: str,
    recipient: Recipient,
    event: DisruptionEvent,
    traveler_number: str,
) -> str:
    """Puts a draft into the approval queue and returns the message_id."""
    msg = PendingMessage(
        message_id=str(uuid.uuid4()),
        draft=draft,
        recipient=recipient,
        event=event,
        traveler_number=traveler_number,
    )
    with _db() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO pending_approvals "
            "(traveler_number, message_id, created_at, data) VALUES (?, ?, ?, ?)",
            (traveler_number, msg.message_id, msg.created_at.isoformat(), _serialize(msg)),
        )
    return msg.message_id


def get(traveler_number: str) -> PendingMessage | None:
    """Returns the open draft for a traveler number (or None)."""
    with _db() as conn:
        row = conn.execute(
            "SELECT data FROM pending_approvals WHERE traveler_number = ?",
            (traveler_number,),
        ).fetchone()
    return _deserialize(row[0]) if row else None


def remove(traveler_number: str) -> PendingMessage | None:
    """Removes the open draft for a traveler number and returns it."""
    with _db() as conn:
        row = conn.execute(
            "SELECT data FROM pending_approvals WHERE traveler_number = ?",
            (traveler_number,),
        ).fetchone()
        if row is None:
            return None
        conn.execute(
            "DELETE FROM pending_approvals WHERE traveler_number = ?",
            (traveler_number,),
        )
    return _deserialize(row[0])


def update_draft(traveler_number: str, new_draft: str) -> None:
    """Replaces the text of an open draft (EDIT workflow)."""
    with _db() as conn:
        row = conn.execute(
            "SELECT data FROM pending_approvals WHERE traveler_number = ?",
            (traveler_number,),
        ).fetchone()
        if row is None:
            return
        msg = _deserialize(row[0])
        msg.draft = new_draft
        conn.execute(
            "UPDATE pending_approvals SET data = ? WHERE traveler_number = ?",
            (_serialize(msg), traveler_number),
        )


def cleanup_expired() -> list[PendingMessage]:
    """Removes and returns all drafts older than EXPIRY_SECONDS."""
    now = datetime.now(timezone.utc)
    expired: list[PendingMessage] = []
    with _db() as conn:
        rows = conn.execute(
            "SELECT traveler_number, data FROM pending_approvals"
        ).fetchall()
        for traveler_number, data in rows:
            msg = _deserialize(data)
            if (now - msg.created_at).total_seconds() > EXPIRY_SECONDS:
                expired.append(msg)
                conn.execute(
                    "DELETE FROM pending_approvals WHERE traveler_number = ?",
                    (traveler_number,),
                )
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
