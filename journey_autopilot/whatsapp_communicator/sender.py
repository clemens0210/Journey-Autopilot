import logging
import os
from datetime import datetime, timezone

from twilio.base.exceptions import TwilioRestException
from twilio.rest import Client

from . import queue as msg_queue
from .models import DisruptionEvent, Recipient

logger = logging.getLogger(__name__)


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
    """Queue the draft and send an approval request to the traveler.

    Returns the message_id for tracking.
    """
    traveler = _traveler(event)

    message_id = msg_queue.enqueue(
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
        f"Auto-sends in 5 min if no reply."
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
    """Send a message to a given recipient."""
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
        "action=dispatched ts=%s recipient=%s number=%s preview=%.80s",
        datetime.now(timezone.utc).isoformat(),
        recipient.name,
        recipient.whatsapp_number,
        draft,
    )
