import logging

from fastapi import FastAPI, Form
from fastapi.responses import Response
from twilio.twiml.messaging_response import MessagingResponse

from . import queue as msg_queue
from . import sender

logger = logging.getLogger(__name__)

app = FastAPI(title="Journey Autopilot — Communicator")


def _twiml(text: str) -> Response:
    resp = MessagingResponse()
    resp.message(text)
    return Response(content=str(resp), media_type="application/xml")


@app.post("/whatsapp/reply")
async def whatsapp_reply(
    Body: str = Form(...),
    From: str = Form(...),
):
    # Always return 200 — Twilio retries on non-2xx, causing duplicate sends.
    try:
        return _handle_reply(Body=Body, From=From)
    except Exception:
        logger.exception("Unhandled error processing reply from %s", From)
        return _twiml("An internal error occurred. Please try again.")


def _handle_reply(*, Body: str, From: str) -> Response:
    # Twilio sends From as "whatsapp:+49171..."
    traveler_number = From.removeprefix("whatsapp:")

    # Auto-dispatch any messages that timed out since the last webhook
    for expired in msg_queue.cleanup_expired():
        sender.dispatch_message(expired.draft, expired.recipient)
        logger.info(
            "action=auto_dispatched message_id=%s recipient=%s",
            expired.message_id,
            expired.recipient.name,
        )
        if expired.traveler_number == traveler_number:
            return _twiml(
                f"Your message to {expired.recipient.name} was sent automatically "
                f"(5-min timeout reached)."
            )

    pending = msg_queue.get(traveler_number)
    if pending is None:
        return _twiml(
            "No pending messages found. Send a disruption alert via "
            "Journey Autopilot to get started."
        )

    text = Body.strip()
    cmd = text.upper()

    if cmd == "YES":
        sender.dispatch_message(pending.draft, pending.recipient)
        msg_queue.remove(traveler_number)
        logger.info(
            "action=approved message_id=%s recipient=%s",
            pending.message_id,
            pending.recipient.name,
        )
        return _twiml(f"Message sent to {pending.recipient.name}.")

    if cmd == "NO":
        msg_queue.remove(traveler_number)
        logger.info(
            "action=cancelled message_id=%s recipient=%s",
            pending.message_id,
            pending.recipient.name,
        )
        return _twiml(f"Message to {pending.recipient.name} cancelled.")

    if cmd.startswith("EDIT "):
        new_draft = text[5:].strip()
        msg_queue.update_draft(traveler_number, new_draft)
        logger.info(
            "action=edited message_id=%s recipient=%s",
            pending.message_id,
            pending.recipient.name,
        )
        return _twiml(
            f"Draft updated for {pending.recipient.name}:\n"
            f"———\n{new_draft}\n———\n\n"
            "Reply YES to send, NO to cancel, or EDIT <text> to revise again."
        )

    return _twiml(
        "Unrecognised reply. Valid options:\n"
        "  YES — send\n"
        "  NO — cancel\n"
        "  EDIT <new text> — revise the draft"
    )
