"""Write tools — side-effectful, gated by the policy layer (the veto gate).

Every tool here is classified ``write``: it changes state in the outside world,
so each one runs through ``policy.resolve(...)`` before it fires. The pattern is
the same for all of them:

  resolution = policy.resolve(tool_name, profile=<user profile>, **context)
  - "auto"  → perform the (simulated) effect, return ``status="executed"``.
  - "ask"   → do NOT perform the effect; return ``status="veto_required"`` with a
              human-readable ``action_summary``. The Executor surfaces this to the
              user, who approves in the next turn — then the tool is called again
              with ``user_approved=True`` and proceeds.

``user_approved`` defaults to ``False``, so the safe failure mode is always "ask
again" — nothing fires autonomously that the policy said needs a veto. This is
the ADK analogue of LangGraph's ``interrupt()`` realized inside a chat turn.

The side effects are deliberately simulated for the prototype (no real Twilio /
booking / Graph calls here) — the WhatsApp sender + approval queue in
``integrations/whatsapp.py`` is the real-channel insertion point. What matters
for the milestone is that the *policy decision* is enforced before any effect.

See docs/journey-autopilot-build-spec.md §5/§8 and docs/adr/0004-veto-gate.md.
"""

from __future__ import annotations

import logging
import secrets
from datetime import datetime, timezone

from .. import policy
from ..integrations import db_ops as db_api
from .read_tools import (
    PSEUDO_OUTLOOK_ALIAS_RE,
    _arrives_after_home_limit,
    _calendar_configured,
    _classify_window_conflicts,
    _minimum_transfer_buffer,
    _outlook_connected,
    _parse_datetime,
    calendar_connected,
    get_user_calendar,
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


def _profile() -> dict | None:
    """Authenticated request profile, with single-user fallback for scenarios."""
    try:
        from journey_autopilot.persistence import store
        from journey_autopilot.request_context import current_user_id

        user_id = current_user_id.get()
        if user_id:
            profile = store.get_profile(user_id)
            if profile is not None:
                return {**profile, "user_id": user_id}
        return store.any_profile()
    except Exception:
        return None


def _veto(tool_name: str, action_summary: str, **details) -> dict:
    """Uniform 'needs your approval' payload returned when policy says ``ask``."""
    return {
        "status": "veto_required",
        "tool": tool_name,
        "action_summary": action_summary,
        "details": details,
        "instruction_for_agent": (
            "Do NOT perform this action. Present the action to the user and ask "
            "for explicit approval. Only call this tool again with "
            "user_approved=true once the user has clearly approved in the chat."
        ),
    }


def _done(tool_name: str, action_summary: str, **result) -> dict:
    """Uniform 'executed' payload (simulated effect)."""
    return {
        "status": "executed",
        "tool": tool_name,
        "action_summary": action_summary,
        "executed_at": datetime.now(timezone.utc).isoformat(),
        "note": "Simulated effect (prototype) — no real external call was made.",
        **result,
    }


def _revalidation_error(message: str, **details) -> dict:
    """Fail closed when a persisted proposal cannot be executed safely."""
    return {
        "status": "revalidation_failed",
        "tool": "reroute_execution",
        "error": message,
        "details": details,
        "instruction_for_agent": (
            "Do not execute or claim this option was booked. Tell the user the "
            "proposal must be refreshed with a new reroute search."
        ),
    }


def _selected_proposal_option(proposal_id: str, option_id: str) -> tuple[dict, dict, dict] | dict:
    """Load an owned, unexpired, explicitly selected finalized option."""
    from journey_autopilot.persistence import store
    from journey_autopilot.request_context import current_session_id

    profile = _profile()
    user_id = (profile or {}).get("user_id")
    if not user_id:
        return _revalidation_error("No authenticated profile owns this proposal.")
    proposal = store.get_reroute_proposal(user_id, proposal_id)
    if proposal is None:
        return _revalidation_error("Reroute proposal not found.", proposal_id=proposal_id)
    if proposal.get("expired") or proposal.get("status") not in ("active", "selected"):
        return _revalidation_error(
            "Reroute proposal expired or is no longer active.", proposal_id=proposal_id
        )
    session_id = current_session_id.get()
    if session_id and proposal.get("session_id") != session_id:
        return _revalidation_error(
            "Reroute proposal belongs to a different chat session.", proposal_id=proposal_id
        )
    if proposal.get("selected_option_id") != option_id:
        return _revalidation_error(
            "The requested option was not explicitly selected by the user.",
            proposal_id=proposal_id,
            option_id=option_id,
            selected_option_id=proposal.get("selected_option_id"),
        )
    option = next(
        (
            candidate
            for candidate in (proposal.get("proposal") or {}).get("options") or []
            if candidate.get("option_id") == option_id
            and candidate.get("eligible") is not False
            and candidate.get("selectable") is not False
        ),
        None,
    )
    if option is None:
        return _revalidation_error(
            "The selected option is not in the finalized selectable shortlist.",
            proposal_id=proposal_id,
            option_id=option_id,
        )
    return proposal, dict(option), profile or {}


def _profile_constraint_violations(option: dict, profile: dict) -> list[str]:
    """Reapply time, cancellation, transfer, mobility, and home constraints."""
    reasons: list[str] = []
    preferences = profile.get("preferences") or {}
    mode = option.get("mode", "train")
    if mode == "train":
        if option.get("cancelled"):
            reasons.append("cancelled")
        departure = _parse_datetime(option.get("departure"))
        if departure is None:
            reasons.append("missing_departure")
        else:
            now = datetime.now(departure.tzinfo) if departure.tzinfo else datetime.now()
            if departure <= now:
                reasons.append("already_departed")
        try:
            max_transfers = max(0, int(preferences.get("max_transfers", 2)))
            min_transfer = max(0, int(preferences.get("min_transfer_minutes", 8)))
        except (TypeError, ValueError):
            max_transfers, min_transfer = 2, 8
        if (option.get("transfers") or 0) > max_transfers:
            reasons.append("too_many_transfers")
        buffer = _minimum_transfer_buffer(option)
        option["minimum_transfer_minutes"] = buffer
        if buffer is not None and buffer < min_transfer:
            reasons.append("transfer_too_short")
    elif mode == "car_sharing" and not (profile.get("mobility") or {}).get(
        "car_sharing_ok", True
    ):
        reasons.append("car_sharing_disabled")
    elif mode == "bike_sharing" and not (profile.get("mobility") or {}).get(
        "bike_sharing_ok", True
    ):
        reasons.append("bike_sharing_disabled")
    elif mode == "hotel" and not (profile.get("home") or {}).get("hotel_ok", True):
        reasons.append("hotel_disabled")
    if _arrives_after_home_limit(option, profile):
        reasons.append("after_latest_arrival_home")
    return reasons


async def _fresh_calendar_violations(option: dict, proposal: dict) -> list[str]:
    """Recheck hard calendar conflicts immediately before a booking effect."""
    if not calendar_connected() or not option.get("new_arrival"):
        return []
    payload = proposal.get("proposal") or {}
    travel_date = payload.get("travel_date") or str(option.get("new_arrival"))[:10]
    calendar = await get_user_calendar(travel_date)
    if calendar.get("error"):
        return ["calendar_revalidation_unavailable"]
    classified = _classify_window_conflicts(
        calendar.get("events") or [],
        travel_date,
        planned_departure=option.get("departure"),
        expected_arrival=option.get("new_arrival"),
    )
    if classified and classified.get("hard_conflicts", 0) > 0:
        return ["calendar_hard_conflict"]
    return []


async def _revalidate_selected_option(
    proposal_id: str, option_id: str
) -> tuple[dict, dict, dict, dict] | dict:
    """Refresh live trains and return authoritative execution state."""
    loaded = _selected_proposal_option(proposal_id, option_id)
    if isinstance(loaded, dict):
        return loaded
    proposal, option, profile = loaded
    evidence: dict = {"source": option.get("source"), "refreshed": False}
    if option.get("mode", "train") == "train" and option.get("source") == "db_service_live":
        refresh_token = option.get("_provider_refresh_token")
        if not refresh_token:
            return _revalidation_error(
                "The live journey has no provider refresh token.", proposal_id=proposal_id
            )
        try:
            refreshed_payload = db_api.refresh_journey(refresh_token, tickets=True)
            raw_journey = (
                refreshed_payload.get("journey")
                if isinstance(refreshed_payload, dict)
                else None
            )
            if not isinstance(raw_journey, dict):
                return _revalidation_error("DB returned no journey during refresh.")
            refreshed = db_api.normalize_journey(raw_journey, option_id=option_id)
        except db_api.DBServiceError as exc:
            return _revalidation_error(
                "Live journey revalidation is unavailable.", provider_error=str(exc)
            )
        option.update(
            departure=refreshed.get("departure") or refreshed.get("planned_departure"),
            new_arrival=refreshed.get("arrival") or refreshed.get("planned_arrival"),
            transfers=refreshed.get("transfers", option.get("transfers", 0)),
            cancelled=bool(refreshed.get("cancelled")),
            legs=refreshed.get("legs") or option.get("legs") or [],
            trains=refreshed.get("trains") or option.get("trains") or [],
            remarks=refreshed.get("remarks") or option.get("remarks") or [],
            price_eur=refreshed.get("price_eur"),
        )
        evidence.update(
            refreshed=True,
            quoted_fare_eur=refreshed.get("price_eur"),
            refreshed_departure=option.get("departure"),
            refreshed_arrival=option.get("new_arrival"),
        )

    reasons = _profile_constraint_violations(option, profile)
    reasons.extend(await _fresh_calendar_violations(option, proposal))
    if reasons:
        return _revalidation_error(
            "The selected option no longer satisfies the execution constraints.",
            proposal_id=proposal_id,
            option_id=option_id,
            violations=sorted(set(reasons)),
            evidence=evidence,
        )
    return proposal, option, profile, evidence


def _booking_cost(option: dict) -> tuple[str, float | None, float | None, str]:
    """Return status, displayed amount, policy amount, and safe label."""
    status = option.get("cost_status", "unknown")
    amount = option.get("added_cost_eur")
    if status in ("known", "estimate") and amount is not None:
        try:
            amount = float(amount)
        except (TypeError, ValueError):
            status, amount = "unknown", None
    else:
        status, amount = "unknown", None
    policy_amount = amount if status == "known" else None
    if status == "known":
        label = f"{amount:.2f} EUR"
    elif status == "estimate":
        label = f"approximately {amount:.2f} EUR (estimate)"
    else:
        label = "unknown"
    return status, amount, policy_amount, label


# --- Write tools (each gated by policy.resolve) -------------------------------


def send_whatsapp_to_user(message: str, user_approved: bool = False) -> dict:
    """Sends a WhatsApp/notification message to the traveler themselves.

    Always runs autonomously: the user is the recipient and the veto channel, so
    this is never gated.

    Args:
        message: The message text to send to the traveler.
        user_approved: Unused here (this action never needs a veto); kept for a
            uniform write-tool signature.

    Returns:
        A status dict (``status="executed"``).
    """
    return _done(
        "send_whatsapp_to_user",
        f"Notified the traveler: {message!r}",
        message=message,
        channel="whatsapp",
    )


def send_email_to_participants(
    subject: str, body: str, participants: str, user_approved: bool = False
) -> dict:
    """Sends an email to meeting participants / third parties (e.g. a client).

    Affects third parties in a professional context — gated by the policy layer.

    Args:
        subject: Email subject line.
        body: Email body.
        participants: Comma-separated recipients (names or addresses).
        user_approved: Set to true ONLY after the user explicitly approved.

    Returns:
        ``status="executed"`` on send, or ``status="veto_required"`` if the
        policy requires the user's approval first.
    """
    name = "send_email_to_participants"
    summary = f"Email to {participants} — subject: {subject!r}"
    if policy.resolve(name, profile=_profile()) == "ask" and not user_approved:
        return _veto(name, summary, subject=subject, body=body, participants=participants)
    return _done(name, summary, subject=subject, participants=participants)


async def book_alternative_connection(
    proposal_id: str,
    option_id: str,
    user_approved: bool = False,
) -> dict:
    """Book an explicitly selected option from an authoritative proposal.

    Descriptions and prices are loaded from the persisted finalized proposal,
    never accepted from conversation text. Live trains are refreshed and all
    current execution constraints are reapplied before the veto policy runs.

    Args:
        proposal_id: Server-issued id of the unexpired finalized shortlist.
        option_id: Option explicitly selected by the user from that proposal.
        user_approved: True only after approval of a gated or unknown-cost action.
    """
    validated = await _revalidate_selected_option(proposal_id, option_id)
    if isinstance(validated, dict):
        return validated
    proposal, option, profile, evidence = validated
    mode = option.get("mode", "train")
    if mode == "hotel":
        return _revalidation_error(
            "Hotel options must use book_hotel.",
            proposal_id=proposal_id,
            option_id=option_id,
        )

    description = option.get("description") or " / ".join(option.get("trains") or []) or mode
    cost_status, cost_eur, policy_cost_eur, cost_label = _booking_cost(option)
    name = "book_alternative_connection"
    summary = f"Book reroute {option_id} ({description}) — additional cost {cost_label}"
    resolution = policy.resolve(name, profile=profile, cost_eur=policy_cost_eur)
    if (cost_status != "known" or resolution == "ask") and not user_approved:
        return _veto(
            name,
            summary,
            proposal_id=proposal_id,
            option_id=option_id,
            cost_eur=cost_eur,
            cost_status=cost_status,
            quoted_fare_eur=option.get("price_eur"),
            revalidation=evidence,
        )

    from journey_autopilot.persistence import store

    if not store.claim_reroute_proposal_execution(
        proposal["user_id"], proposal_id, option_id
    ):
        return _revalidation_error(
            "The reroute proposal was already consumed or expired.",
            proposal_id=proposal_id,
            option_id=option_id,
        )
    return _done(
        name,
        summary,
        proposal_id=proposal_id,
        option_id=option_id,
        cost_eur=cost_eur,
        cost_status=cost_status,
        quoted_fare_eur=option.get("price_eur"),
        revalidation=evidence,
        booking_ref=f"SIM-{proposal_id}-{option_id}",
    )


async def book_hotel(
    proposal_id: str,
    option_id: str,
    user_approved: bool = False,
) -> dict:
    """Book an explicitly selected hotel using authoritative proposal fields."""
    validated = await _revalidate_selected_option(proposal_id, option_id)
    if isinstance(validated, dict):
        return validated
    proposal, option, profile, evidence = validated
    if option.get("mode") != "hotel":
        return _revalidation_error(
            "The selected option is not a hotel.",
            proposal_id=proposal_id,
            option_id=option_id,
        )
    name_of_hotel = option.get("name") or option.get("description") or option_id
    nights = max(1, int(option.get("nights") or 1))
    cost_status, cost_eur, _policy_cost_eur, cost_label = _booking_cost(option)
    name = "book_hotel"
    summary = f"Book {nights} night(s) at {name_of_hotel} — cost {cost_label}"
    if not user_approved:
        return _veto(
            name,
            summary,
            proposal_id=proposal_id,
            option_id=option_id,
            hotel=name_of_hotel,
            cost_eur=cost_eur,
            cost_status=cost_status,
            nights=nights,
            revalidation=evidence,
        )

    from journey_autopilot.persistence import store

    if not store.claim_reroute_proposal_execution(
        proposal["user_id"], proposal_id, option_id
    ):
        return _revalidation_error(
            "The reroute proposal was already consumed or expired.",
            proposal_id=proposal_id,
            option_id=option_id,
        )
    return _done(
        name,
        summary,
        proposal_id=proposal_id,
        option_id=option_id,
        hotel=name_of_hotel,
        cost_eur=cost_eur,
        cost_status=cost_status,
        nights=nights,
        revalidation=evidence,
        booking_ref=f"SIM-{proposal_id}-{option_id}",
    )


def reschedule_outlook_event(
    event_id: str,
    title: str,
    new_start: str,
    status: str = "confirmed",
    user_approved: bool = False,
) -> dict:
    """Reschedules an Outlook calendar event affected by the delay.

    Tied to reversibility: a ``tentative`` event may be moved autonomously, a
    ``confirmed`` one asks first (it is not freely reversible).

    Args:
        event_id: The calendar event id.
        title: Event title (for the summary shown to the user).
        new_start: New start time (ISO "YYYY-MM-DDTHH:MM:SS").
        status: ``"tentative"`` or ``"confirmed"`` — drives the policy decision.
        user_approved: Set to true ONLY after the user explicitly approved.

    Returns:
        ``status="executed"`` on reschedule, or ``status="veto_required"``.
    """
    name = "reschedule_outlook_event"
    summary = f"Move '{title}' ({status}) to {new_start}"
    if (
        policy.resolve(name, profile=_profile(), event_status=status) == "ask"
        and not user_approved
    ):
        return _veto(name, summary, event_id=event_id, new_start=new_start, event_status=status)
    return _done(name, summary, event_id=event_id, new_start=new_start, event_status=status)


def file_compensation_claim(
    delay_minutes: int, amount_eur: float = 0.0, user_approved: bool = False
) -> dict:
    """Files a passenger-rights compensation claim for the delay.

    Purely beneficial, low downside — typically runs autonomously, but still
    passes the policy layer so a conservative user can require approval.

    Args:
        delay_minutes: The arrival delay the claim is based on.
        amount_eur: Expected compensation amount in EUR.
        user_approved: Set to true ONLY after the user explicitly approved.

    Returns:
        ``status="executed"`` on filing, or ``status="veto_required"``.
    """
    name = "file_compensation_claim"
    summary = f"File compensation claim for {delay_minutes} min delay (~{amount_eur:.2f} EUR)"
    if policy.resolve(name, profile=_profile()) == "ask" and not user_approved:
        return _veto(name, summary, delay_minutes=delay_minutes, amount_eur=amount_eur)
    return _done(
        name,
        summary,
        delay_minutes=delay_minutes,
        amount_eur=amount_eur,
        claim_ref="SIM-CLAIM",
    )


WRITE_TOOLS = [
    send_whatsapp_to_user,
    send_email_to_participants,
    book_alternative_connection,
    book_hotel,
    reschedule_outlook_event,
    file_compensation_claim,
]
