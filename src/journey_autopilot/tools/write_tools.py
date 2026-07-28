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

Two tools bypass ``user_approved`` by design, and for opposite reasons:

- ``send_whatsapp_to_user`` is never gated — the traveler is the recipient AND
  the channel their veto arrives through, so gating it would deadlock the gate.
  It is also the one tool here with a REAL external side effect (Twilio), which
  is why it enforces one send per turn itself rather than trusting the prompt.
- ``propose_appointment_notice_email`` needs no flag because it *is* the ask:
  it only stages a draft, and the paired send refuses to run without the
  single-use approval id that staging produced.

The remaining side effects are simulated for the prototype (no real booking or
Graph write) — what matters is that the *policy decision* is enforced before any
effect, and that no tool takes a cost, a delay, or an appointment's firmness
from conversation text: each reads it from authoritative state instead.

See docs/journey-autopilot-build-spec.md §5/§8 and docs/adr/0004-veto-gate.md.
"""

from __future__ import annotations

import asyncio
import logging
import re
import secrets
from datetime import datetime, timezone

from .. import policy
from ..integrations import db_ops as db_api
from .read_tools import (
    PSEUDO_OUTLOOK_ALIAS_RE,
    _arrives_after_home_limit,
    _calendar_configured,
    _classify_window_conflicts,
    _mode_eligibility_violations,
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


_REROUTE_REVALIDATION_HINT = (
    "Do not execute or claim this option was booked. Tell the user the "
    "proposal must be refreshed with a new reroute search."
)


def _revalidation_error(
    message: str,
    *,
    tool: str = "reroute_execution",
    instruction: str = _REROUTE_REVALIDATION_HINT,
    **details,
) -> dict:
    """Fail closed when authoritative state cannot back the requested action."""
    return {
        "status": "revalidation_failed",
        "tool": tool,
        "error": message,
        "details": details,
        "instruction_for_agent": instruction,
    }


def _selected_proposal_option(proposal_id: str, option_id: str) -> tuple[dict, dict, dict] | dict:
    """Load an owned, unexpired, explicitly selected finalized option.

    Booking authority requires a genuinely bound request identity — unlike the
    general-purpose ``_profile()`` accessor, this never falls back to an
    arbitrary "most recently onboarded" profile, since that fallback would let
    an unbound request execute against whichever profile happens to be picked.
    """
    from journey_autopilot.persistence import store
    from journey_autopilot.request_context import current_session_id, current_user_id

    user_id = current_user_id.get()
    if not user_id:
        return _revalidation_error("No authenticated identity bound to this request.")
    session_id = current_session_id.get()
    if not session_id:
        return _revalidation_error("No authenticated chat session bound to this request.")
    stored_profile = store.get_profile(user_id)
    if stored_profile is None:
        return _revalidation_error("No authenticated profile owns this proposal.")
    profile = {**stored_profile, "user_id": user_id}
    proposal = store.get_reroute_proposal(user_id, proposal_id)
    if proposal is None:
        return _revalidation_error("Reroute proposal not found.", proposal_id=proposal_id)
    if proposal.get("session_id") != session_id:
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
    # Live shortlists go stale (TTL + supersession by any newer search) and must
    # be refreshed before use; offline/mock shortlists are reproducible, so the
    # shown card stays selectable — only a proposal already executed is refused.
    offline = (proposal.get("proposal") or {}).get("source") != "db_service_live"
    if offline:
        if proposal.get("status") == "executed":
            return _revalidation_error(
                "Reroute proposal already used.", proposal_id=proposal_id
            )
    elif proposal.get("expired") or proposal.get("status") not in ("active", "selected"):
        return _revalidation_error(
            "Reroute proposal expired or is no longer active.", proposal_id=proposal_id
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
    preferences = profile.get("preferences") or {}
    mobility = profile.get("mobility") or {}
    home = profile.get("home") or {}
    reasons = _mode_eligibility_violations(
        option,
        preferences=preferences,
        mobility=mobility,
        home=home,
        recompute_transfer_buffer=True,
    )
    if option.get("mode", "train") == "train":
        departure = _parse_datetime(option.get("departure"))
        if departure is None:
            reasons.append("missing_departure")
        else:
            now = datetime.now(departure.tzinfo) if departure.tzinfo else datetime.now()
            if departure <= now:
                reasons.append("already_departed")
    if _arrives_after_home_limit(option, profile):
        reasons.append("after_latest_arrival_home")
    return reasons


async def _fresh_calendar_clash(option: dict, proposal: dict) -> dict | list[str]:
    """Recheck the option's arrival against hard-constraint appointments.

    Returns a clash dict (``hard_conflicts``, ``conflicts``) — a hard-constraint
    clash no longer blocks the booking, since the traveler can still take a train
    that arrives late for one appointment; the caller surfaces this as a notice on
    the executed reroute (``clash_note``) so the traveler can decide whether to
    reschedule or notify anyone. Only a genuine revalidation failure (calendar
    unreadable right now) still fails closed — returned as a violations list
    so the caller treats it like any other execution-constraint failure,
    since a clash can't be safely confirmed as absent without a fresh read.
    """
    if not calendar_connected() or not option.get("new_arrival"):
        return {"hard_conflicts": 0, "conflicts": []}
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
    if not classified:
        return {"hard_conflicts": 0, "conflicts": []}
    return {
        "hard_conflicts": classified.get("hard_conflicts", 0),
        "conflicts": [c for c in classified.get("conflicts", []) if c.get("hard_constraint")],
    }


async def _revalidate_selected_option(
    proposal_id: str, option_id: str
) -> tuple[dict, dict, dict, dict, dict | None] | dict:
    """Refresh live trains and return authoritative execution state.

    The fifth element of the success tuple is the fresh calendar-clash detail
    (``{"hard_conflicts": int, "conflicts": [...]}``) or ``None`` when there is
    nothing to check — callers that book a same-day arrival use it to attach a
    notice to the executed reroute when it lands after a hard-constraint
    appointment, rather than silently refusing or silently booking through it.
    """
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
            # db_api.refresh_journey is a synchronous requests call; run it off
            # the event loop so a slow DB sidecar round-trip doesn't block every
            # other concurrent chat turn/tool call in this process.
            refreshed_payload = await asyncio.to_thread(
                db_api.refresh_journey, refresh_token, tickets=True
            )
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
    calendar_clash = await _fresh_calendar_clash(option, proposal)
    if isinstance(calendar_clash, list):
        reasons.extend(calendar_clash)
        calendar_clash = None
    if reasons:
        return _revalidation_error(
            "The selected option no longer satisfies the execution constraints.",
            proposal_id=proposal_id,
            option_id=option_id,
            violations=sorted(set(reasons)),
            evidence=evidence,
        )
    return proposal, option, profile, evidence, calendar_clash


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


async def send_whatsapp_to_user(message: str) -> dict:
    """Push a proactive WhatsApp notice to the traveler's own phone.

    Use this when something has happened that the traveler needs to know
    *without* opening the app — above all an elevated disruption risk on a trip
    that is about to start or already running. It is a one-way heads-up, not a
    question: it reaches them on their phone while the full answer stays in the
    chat.

    Send it at most ONCE per turn, and only when there is real news. A follow-up
    answer inside a conversation the traveler is already reading does not
    warrant a push.

    Never gated by the policy layer: the traveler is the recipient AND the
    channel the veto arrives through, so gating it would deadlock the gate. The
    recipient is their verified number from onboarding — it is not an argument
    here, and this tool can only ever message the traveler themselves.

    Args:
        message: The notice text. Keep it short (a few lines) — it is read on a
            phone. Lead with the trip and the risk, then what to do.

    Returns:
        A status dict. ``status="executed"`` with ``delivery.sent`` true means
        WhatsApp accepted it; ``delivery.demo`` means Twilio is not configured
        and the message was logged instead; ``status="skipped"`` means the
        traveler has no verified number, which is not an error — say so plainly
        rather than retrying.
    """
    from journey_autopilot import request_context
    from journey_autopilot.integrations import whatsapp

    name = "send_whatsapp_to_user"
    to_number = request_context.current_notify_phone.get()
    if not to_number:
        return {
            "status": "skipped",
            "tool": name,
            "reason": "no_verified_phone",
            "action_summary": (
                "No WhatsApp notice sent — the traveler has no verified number "
                "on file. Deliver the information in the chat reply instead."
            ),
        }

    # One notice per turn, enforced rather than merely instructed. The chat turn
    # is replayed once when the model emits malformed tool-call JSON
    # (ui/chat.py), and a model can always call a tool twice in one loop — both
    # would otherwise buzz the traveler's phone repeatedly for one disruption.
    already_sent = request_context.current_whatsapp_sink.get()
    if already_sent:
        return {
            "status": "skipped",
            "tool": name,
            "reason": "already_sent_this_turn",
            "action_summary": (
                "A WhatsApp notice already went out for this turn — the "
                "traveler has been informed. Do not send another."
            ),
        }

    # Twilio's client is blocking; keep it off the event loop so a slow send
    # doesn't stall every other concurrent chat turn in this process.
    delivery = await asyncio.to_thread(whatsapp.send_disruption_alert, to_number, message)
    request_context.record_whatsapp_send({"to": to_number, **delivery})
    return _done(
        name,
        f"Sent a WhatsApp notice to the traveler ({to_number}).",
        message=message,
        channel="whatsapp",
        delivery=delivery,
        note=(
            "Real send attempt via Twilio (falls back to a logged demo message "
            "when Twilio is not configured — see delivery.demo)."
        ),
    )


async def book_alternative_connection(
    proposal_id: str,
    option_id: str,
    user_approved: bool = False,
) -> dict:
    """Record the traveler's choice of an alternative train from an authoritative proposal.

    A train reroute is not a purchase: a DB ticket is valid on any reasonable
    alternative connection, so a free (added_cost_eur == 0) train option is
    simply *chosen*, not booked — no cost veto applies. An option that carries
    a real fare difference (e.g. a different operator/route) still goes
    through the normal cost veto below, same as before.

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
    proposal, option, profile, evidence, calendar_clash = validated
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
    is_free_train_reroute = mode == "train" and cost_status == "known" and cost_eur == 0
    summary = (
        f"Choose connection {option_id} ({description}) — already covered by your ticket"
        if is_free_train_reroute
        else f"Choose connection {option_id} ({description}) — additional cost {cost_label}"
    )
    hard_conflicts = calendar_clash.get("hard_conflicts") if calendar_clash else 0
    clash_note = None
    if hard_conflicts:
        titles = ", ".join(
            c.get("title") or "an appointment" for c in calendar_clash.get("conflicts") or []
        )
        summary += f" — arrives after hard-constraint appointment(s): {titles}"
        clash_note = (
            f"This connection arrives after your hard-constraint appointment(s): "
            f"{titles}. The reroute was applied anyway — flag this to the traveler "
            f"and offer to reschedule the appointment or notify its contact."
        )
    # A calendar clash no longer blocks the reroute: it is surfaced as a notice on
    # the executed result (clash_note), not a veto. A free train reroute is not a
    # purchase decision, so with the clash downgraded it needs no veto at all; a
    # paid reroute still goes through the normal cost/autonomy veto. The companion
    # reschedule/notify step remains a separate, independently gated action.
    if is_free_train_reroute:
        needs_veto = False
    else:
        resolution = policy.resolve(name, profile=profile, cost_eur=policy_cost_eur)
        needs_veto = cost_status != "known" or resolution == "ask"
    if needs_veto and not user_approved:
        return _veto(
            name,
            summary,
            proposal_id=proposal_id,
            option_id=option_id,
            cost_eur=cost_eur,
            cost_status=cost_status,
            quoted_fare_eur=option.get("price_eur"),
            revalidation=evidence,
            calendar_clash=calendar_clash,
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
        calendar_clash=calendar_clash,
        clash_note=clash_note,
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
    proposal, option, profile, evidence, _calendar_clash = validated
    if option.get("mode") != "hotel":
        return _revalidation_error(
            "The selected option is not a hotel.",
            proposal_id=proposal_id,
            option_id=option_id,
        )
    name_of_hotel = option.get("name") or option.get("description") or option_id
    nights = max(1, int(option.get("nights") or 1))
    cost_status, cost_eur, policy_cost_eur, cost_label = _booking_cost(option)
    name = "book_hotel"
    summary = f"Book {nights} night(s) at {name_of_hotel} — cost {cost_label}"
    resolution = policy.resolve(name, profile=profile, cost_eur=policy_cost_eur)
    if (cost_status != "known" or resolution == "ask") and not user_approved:
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


async def _find_calendar_event(event_id: str, new_start: str) -> dict | None:
    """Resolve an appointment by id from the connected calendar.

    ``new_start`` may be a full ISO datetime or a bare "HH:MM" on the travel
    day, so the day to read is taken from it when it carries one and falls back
    to today otherwise. Reading the event (rather than trusting the model's
    description of it) is what makes the tentative/confirmed veto trustworthy.
    """
    dates = [datetime.now().date().isoformat()]
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", new_start[:10]):
        dates.insert(0, new_start[:10])
    for date in dict.fromkeys(dates):
        calendar = await get_user_calendar(date)
        for event in calendar.get("events") or []:
            if event.get("id") == event_id:
                return event
    return None


async def reschedule_outlook_event(
    event_id: str,
    new_start: str,
    new_end: str = "",
    user_approved: bool = False,
) -> dict:
    """Move a calendar appointment a disrupted trip would make the traveler miss.

    The companion action to choosing a late-arriving reroute: rather than only
    notifying the contact, the appointment itself is pushed back.

    The appointment's own firmness decides the veto, not the model's judgement:
    the event is looked up in the connected calendar and its
    ``tentative``/``confirmed`` status is what ``policy.resolve`` gates on — a
    tentative slot is reversible and moves automatically, a confirmed one asks
    first. The status is therefore never accepted as an argument.

    Args:
        event_id: Calendar id of the appointment (the ``id`` field of the
            clashing event the Planner reported).
        new_start: Proposed new start — ISO datetime or "HH:MM" on the same day.
        new_end: Proposed new end. Optional; omitted keeps the original
            duration.
        user_approved: True only after the user approved a gated move.

    Returns:
        ``status="executed"``, ``status="veto_required"``, or
        ``status="revalidation_failed"`` when the event cannot be resolved.
    """
    name = "reschedule_outlook_event"
    not_found_hint = (
        "Do not claim the appointment was moved. Ask the Planner for a fresh "
        "calendar check to get the current event id."
    )
    event_id = (event_id or "").strip()
    new_start = (new_start or "").strip()
    if not event_id or not new_start:
        return _revalidation_error(
            "Both the calendar event id and the new start time are required.",
            tool=name,
            instruction=not_found_hint,
        )

    event = await _find_calendar_event(event_id, new_start)
    if event is None:
        return _revalidation_error(
            f"No calendar event with id '{event_id}' on the travel day.",
            tool=name,
            instruction=not_found_hint,
            event_id=event_id,
        )

    title = event.get("title") or "the appointment"
    event_status = event.get("status") or "confirmed"
    summary = (
        f"Move '{title}' from {event.get('start') or 'its current slot'} to "
        f"{new_start} ({event_status} appointment)"
    )
    resolution = policy.resolve(name, profile=_profile(), event_status=event_status)
    if resolution == "ask" and not user_approved:
        return _veto(
            name,
            summary,
            event_id=event_id,
            title=title,
            event_status=event_status,
            old_start=event.get("start"),
            new_start=new_start,
            new_end=new_end or None,
            attendees=event.get("attendee_emails") or [],
        )
    logger.info(
        "calendar event rescheduled: id=%s status=%s %s -> %s",
        event_id,
        event_status,
        event.get("start"),
        new_start,
    )
    return _done(
        name,
        summary,
        event_id=event_id,
        title=title,
        event_status=event_status,
        old_start=event.get("start"),
        new_start=new_start,
        new_end=new_end or None,
        attendees=event.get("attendee_emails") or [],
        note=(
            "Simulated effect (prototype) — the move is not written back to "
            "Microsoft Graph. Attendees are NOT notified by this action; "
            "offer the notice email separately."
        ),
    )


def file_compensation_claim(user_approved: bool = False) -> dict:
    """File the passenger-rights compensation claim for a concluded trip.

    The counterpart to the Planner's read-only rights lookup: that tool tells
    the traveler what they are owed, this one actually files it — through the
    policy/veto gate like every other write.

    Takes NO delay or amount arguments on purpose. Both are read from the
    settled ``get_passenger_rights`` result of this turn (which only exists once
    the trip has concluded), never from conversation text — the same rule the
    reroute path enforces via ``proposal_id``. A claim can therefore not be
    filed for a delay the traveler has not experienced, nor for an amount the
    model reconstructed.

    Args:
        user_approved: Set to true ONLY after the user explicitly approved a
            previously returned ``veto_required``.

    Returns:
        ``status="executed"`` on filing, ``status="veto_required"`` when the
        policy asks first, or ``status="revalidation_failed"`` when no settled
        rights result backs the claim.
    """
    from .read_tools import last_passenger_rights

    name = "file_compensation_claim"
    rights = last_passenger_rights()
    no_claim_hint = (
        "Do not claim anything was filed. Ask the Planner for a passenger-rights "
        "lookup on the concluded trip first, then try again."
    )
    if rights is None:
        return _revalidation_error(
            "No settled passenger-rights result backs this claim. A claim can "
            "only be filed after the rights lookup ran for a CONCLUDED trip "
            "(trip_concluded=true).",
            tool=name,
            instruction=no_claim_hint,
        )
    if not rights.get("eligible"):
        return _revalidation_error(
            "The settled rights result found no eligible claim.",
            tool=name,
            instruction=no_claim_hint,
            reason=rights.get("reason"),
        )

    delay_minutes = int(rights.get("delay_minutes") or 0)
    amount_eur = float(rights.get("compensation_eur") or 0.0)
    summary = (
        f"File compensation claim for {delay_minutes} min confirmed delay "
        f"({amount_eur:.2f} EUR)"
    )
    if policy.resolve(name, profile=_profile()) == "ask" and not user_approved:
        return _veto(name, summary, delay_minutes=delay_minutes, amount_eur=amount_eur)
    return _done(
        name,
        summary,
        delay_minutes=delay_minutes,
        amount_eur=amount_eur,
        reason=rights.get("reason"),
        legal_sources=rights.get("legal_sources") or [],
        claim_ref="SIM-CLAIM",
    )


# The Executor's toolbelt: everything that changes the traveler's plans or
# files something on their behalf. Each entry is gated by policy.resolve.
EXECUTOR_WRITE_TOOLS = [
    book_alternative_connection,
    book_hotel,
    reschedule_outlook_event,
    file_compensation_claim,
]

# The Orchestrator's own outbound channel. It sits on the Orchestrator rather
# than the Executor because it is not part of executing a chosen plan — it is
# how the system reaches the traveler at all, and the channel their veto comes
# back through. Never policy-gated (see policy.resolve).
ORCHESTRATOR_WRITE_TOOLS = [
    send_whatsapp_to_user,
]

# The Communicator's toolbelt: the notice-email propose/approve pair. Listed
# here so all write capability in the system is enumerated in one place.
COMMUNICATOR_WRITE_TOOLS = [
    propose_appointment_notice_email,
    send_approved_notice_email,
]
