import logging
import os

from fastapi import FastAPI, Form, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import Response
from twilio.request_validator import RequestValidator
from twilio.twiml.messaging_response import MessagingResponse

from . import tools

logger = logging.getLogger(__name__)

app = FastAPI(title="Journey Autopilot — Communicator")


def _twiml(text: str) -> Response:
    resp = MessagingResponse()
    resp.message(text)
    return Response(content=str(resp), media_type="application/xml")


@app.post("/whatsapp/reply")
async def whatsapp_reply(
    request: Request,
    Body: str = Form(...),
    From: str = Form(...),
):
    # Validate Twilio signature to prevent spoofed approvals/edits.
    signature = request.headers.get("X-Twilio-Signature", "")
    auth_token = os.getenv("TWILIO_AUTH_TOKEN", "")
    if not auth_token:
        logger.error("TWILIO_AUTH_TOKEN is not set; cannot validate Twilio signature")
        return _twiml("Server misconfigured. Please try again later.")

    form = await request.form()
    validator = RequestValidator(auth_token)
    if not signature or not validator.validate(str(request.url), dict(form), signature):
        logger.warning("Invalid Twilio signature for inbound reply from %s", From)
        # Return 200 so Twilio doesn't retry; do not process the command.
        return _twiml("Invalid request.")

    # Always return 200 — Twilio retries on non-2xx, causing duplicate sends.
    try:
        return await run_in_threadpool(_handle_reply, Body=Body, From=From)
    except Exception:
        logger.exception("Unhandled error processing reply from %s", From)
        return _twiml("An internal error occurred. Please try again.")

def _handle_reply(*, Body: str, From: str) -> Response:
    # Twilio sends From as "whatsapp:+49171..."
    traveler_number = From.removeprefix("whatsapp:")

    # Drop (do NOT send) any messages that timed out since the last webhook
    for expired in tools.cleanup_expired():
        logger.info(
            "action=auto_cancelled message_id=%s recipient=%s",
            expired.message_id,
            expired.recipient.name,
        )
        if expired.traveler_number == traveler_number:
            return _twiml(
                f"Your message to {expired.recipient.name} was NOT sent "
                f"(5-min timeout reached without approval)."
            )

    pending = tools.get(traveler_number)
    if pending is None:
        return _twiml(
            "No pending messages found. Send a disruption alert via "
            "Journey Autopilot to get started."
        )

    text = Body.strip()
    cmd = text.upper()

    if cmd == "YES":
        tools.dispatch_message(pending.draft, pending.recipient)
        tools.remove(traveler_number)
        logger.info(
            "action=approved message_id=%s recipient=%s",
            pending.message_id,
            pending.recipient.name,
        )
        return _twiml(f"Message sent to {pending.recipient.name}.")

    if cmd == "NO":
        tools.remove(traveler_number)
        logger.info(
            "action=cancelled message_id=%s recipient=%s",
            pending.message_id,
            pending.recipient.name,
        )
        return _twiml(f"Message to {pending.recipient.name} cancelled.")

    if cmd.startswith("EDIT "):
        new_draft = text[5:].strip()
        tools.update_draft(traveler_number, new_draft)
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
