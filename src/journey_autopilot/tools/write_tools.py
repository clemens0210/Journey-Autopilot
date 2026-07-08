"""Write tools — side-effectful, gated by the policy layer.

Every tool here is classified ``write``: it changes state in the outside world
and therefore runs through ``policy.py`` (auto / ask) and the veto gate before
it fires. These are the ADK-facing wrappers the Executor and Communicator call.

Implemented (build spec §5/§8):
- ``propose_appointment_notice_email`` / ``send_approved_notice_email`` —
  the ``send_email_to_participants`` action ("ask": affects third parties).
  The two-step split IS the veto gate: proposing stages a draft and returns an
  ``approval_id``; the draft is shown to the user in the chat, and only after
  the user's explicit approval does the orchestrator relay the id to the send
  tool. The send is single-use — an id can never fire twice.

Still planned:
- send_whatsapp_to_user        (auto — user is recipient + veto channel;
                                 the sender itself lives in integrations/whatsapp.py)
- book_alternative_connection  (ask if cost over threshold)
- book_hotel                   (ask)
- reschedule_outlook_event     (auto if tentative, ask if confirmed)
- file_compensation_claim      (auto — purely beneficial)
"""

from __future__ import annotations

import logging
import secrets
from datetime import datetime, timezone

from .. import policy
from .read_tools import (
    PSEUDO_OUTLOOK_ALIAS_RE,
    _calendar_configured,
    _outlook_connected,
)

logger = logging.getLogger(__name__)

# Staged email drafts awaiting the user's veto decision: approval_id -> draft.
# In-memory on purpose (single-user prototype): a server restart clears the
# queue, which simply means the draft has to be proposed again.
_PENDING_EMAILS: dict[str, dict] = {}


def propose_appointment_notice_email(
    to_address: str,
    subject: str,
    body: str,
    appointment_title: str = "",
) -> dict:
    """Stage a notice email for the user's approval. Sends NOTHING yet.

    Policy: ``send_email_to_participants`` resolves to "ask" (it affects a
    third party), so every email starts here. The returned draft and
    ``approval_id`` must be shown to the user verbatim in the chat; only when
    the user explicitly approves may ``send_approved_notice_email`` be called
    with the id.

    Args:
        to_address: Recipient — e.g. the ``organizer_email`` of the clashing
            calendar appointment.
        subject: Proposed subject line.
        body: Proposed plain-text body (the notice that the appointment might
            be missed and why).
        appointment_title: Title of the endangered appointment (context for
            the approval display).

    Returns:
        A dict with ``approval_id``, ``status="pending_user_approval"``, and
        the echoed draft. Contains "error" if the recipient address is
        obviously invalid.
    """
    to_address = (to_address or "").strip()
    if "@" not in to_address or " " in to_address:
        return {"error": f"'{to_address}' is not a usable email address."}
    if PSEUDO_OUTLOOK_ALIAS_RE.match(to_address):
        return {
            "error": (
                f"'{to_address}' is an internal Outlook alias, not a routable "
                "inbox — mail sent there is lost. Use the resolved contact "
                "from the calendar event (organizer_email after resolution, "
                "or an attendee address)."
            )
        }

    resolution = policy.resolve("send_email_to_participants")
    approval_id = secrets.token_hex(4)
    _PENDING_EMAILS[approval_id] = {
        "to_address": to_address,
        "subject": subject,
        "body": body,
        "appointment_title": appointment_title,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    logger.info(
        "email draft staged: approval_id=%s to=%s (policy=%s)",
        approval_id,
        to_address,
        resolution,
    )
    return {
        "approval_id": approval_id,
        "status": "pending_user_approval",
        "policy": resolution,
        "to_address": to_address,
        "subject": subject,
        "body": body,
        "note": (
            "Draft staged. Show it to the user verbatim and wait for their "
            "explicit approval before calling send_approved_notice_email."
        ),
    }


async def send_approved_notice_email(approval_id: str) -> dict:
    """Send a previously proposed notice email AFTER the user approved it.

    Only call this when the user's latest chat message explicitly approves
    sending the draft with this ``approval_id``. The id is single-use: it is
    consumed on the first call, successful or not.

    Sends via Microsoft Graph from the connected Outlook account when Outlook
    is configured and connected; otherwise the send is simulated (demo mode),
    which the result discloses.

    Args:
        approval_id: The id returned by ``propose_appointment_notice_email``.

    Returns:
        A dict with ``status`` ("sent" | "simulated" | "error"), the
        recipient, and — on auth errors — guidance how to fix the consent.
    """
    pending = _PENDING_EMAILS.pop(approval_id, None)
    if pending is None:
        return {
            "status": "error",
            "error": (
                f"No pending draft with approval_id '{approval_id}'. It may "
                "have been sent already or the server restarted — propose the "
                "email again."
            ),
        }

    to_address = pending["to_address"]

    if not (_calendar_configured() and _outlook_connected()):
        logger.info("email send simulated (Outlook not connected): to=%s", to_address)
        return {
            "status": "simulated",
            "to_address": to_address,
            "subject": pending["subject"],
            "note": (
                "Outlook is not connected — no real email was sent. In demo "
                "mode the approval flow completes without a side effect."
            ),
        }

    try:
        from ..integrations.outlook import send_notice_email

        await send_notice_email(to_address, pending["subject"], pending["body"])
    except Exception as exc:
        name = type(exc).__name__
        logger.warning("email send failed: %s: %s", name, exc)
        result = {
            "status": "error",
            "to_address": to_address,
            "error": f"{name}: {exc}",
        }
        if "AuthenticationRequired" in name or "Authentication" in str(exc):
            result["hint"] = (
                "The cached login has no Mail.Send consent yet. Reconnect "
                "Outlook once (onboarding or scripts/check_outlook.py --login) "
                "— calendar reading keeps working regardless."
            )
        return result

    logger.info("notice email sent: to=%s subject=%r", to_address, pending["subject"])
    return {
        "status": "sent",
        "to_address": to_address,
        "subject": pending["subject"],
    }
