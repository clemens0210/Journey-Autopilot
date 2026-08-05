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

Most side effects are simulated for the prototype (no real booking) — what
matters is that the *policy decision* is enforced before any effect, and that
no tool takes a cost, a delay, or an appointment's firmness from conversation
text: each reads it from authoritative state instead. ``reschedule_outlook_event``
is the one exception that writes back to a real system: when Outlook is
connected with the ``Calendars.ReadWrite`` scope consented, it issues an actual
Graph ``PATCH`` that moves the appointment; without that scope (or without a
connected calendar) it falls back to a simulated move, same as every other
tool here.

See docs/journey-autopilot-build-spec.md §5/§8 and docs/adr/0004-veto-gate.md.
"""

from __future__ import annotations

import asyncio
import logging
import re
import secrets
from datetime import datetime, timezone

from .. import policy
from ..integrations.db import ops as db_api
from .constraints import (
    arrives_after_home_limit,
    mode_eligibility_violations,
    parse_datetime,
)
from .read_tools import (
    PSEUDO_OUTLOOK_ALIAS_RE,
    classify_window_conflicts,
    get_user_calendar,
    is_calendar_connected,
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

    if not is_calendar_connected():
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
        from ..persistence import store
        from ..request_context import current_user_id

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
    from ..persistence import store
    from ..request_context import current_session_id, current_user_id

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
    reasons = mode_eligibility_violations(
        option,
        preferences=preferences,
        mobility=mobility,
        home=home,
        recompute_transfer_buffer=True,
    )
    if option.get("mode", "train") == "train":
        departure = parse_datetime(option.get("departure"))
        if departure is None:
            reasons.append("missing_departure")
        else:
            now = datetime.now(departure.tzinfo) if departure.tzinfo else datetime.now()
            if departure <= now:
                reasons.append("already_departed")
    if arrives_after_home_limit(option, profile):
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
    if not is_calendar_connected() or not option.get("new_arrival"):
        return {"hard_conflicts": 0, "conflicts": []}
    payload = proposal.get("proposal") or {}
    travel_date = payload.get("travel_date") or str(option.get("new_arrival"))[:10]
    calendar = await get_user_calendar(travel_date)
    if calendar.get("error"):
        return ["calendar_revalidation_unavailable"]
    classified = classify_window_conflicts(
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
        and the message was logged instead; ``status="skipped"`` means nothing
        was sent and nothing should be (no verified number, or a notice already
        went out this turn) — say so plainly rather than retrying;
        ``status="failed"`` means the send itself broke, so the traveler was
        NOT reached — never report it as delivered.
    """
    from .. import request_context
    from ..integrations import whatsapp

    name = "send_whatsapp_to_user"
    to_number = request_context.current_notify_phone.get()
    if not to_number:
        # Recorded, not silent: the web layer reads the last outcome back to
        # tell the traveler why no notice arrived (see ui/static/js/chat.js).
        # A skip delivers nothing, so it never arms the guard below.
        request_context.record_whatsapp_send(
            {"to": None, "sent": False, "reason": "no_phone"}
        )
        return {
            "status": "skipped",
            "tool": name,
            "reason": "no_verified_phone",
            "action_summary": (
                "No WhatsApp notice sent — the traveler has no verified number "
                "on file. Deliver the information in the chat reply instead."
            ),
        }

    # One DELIVERED notice per turn, enforced rather than merely instructed. The
    # chat turn is replayed once when the model emits malformed tool-call JSON
    # (ui/chat.py), and a model can always call a tool twice in one loop — both
    # would otherwise buzz the traveler's phone repeatedly for one disruption.
    # A failed attempt is deliberately not counted: nothing reached the phone,
    # so refusing the retry would strand the traveler with no notice at all.
    if request_context.whatsapp_delivered():
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
    if not (delivery.get("sent") or delivery.get("demo")):
        logger.warning("WhatsApp notice not delivered to %s: %s", to_number, delivery)
        return {
            "status": "failed",
            "tool": name,
            "channel": "whatsapp",
            "delivery": delivery,
            "action_summary": (
                f"The WhatsApp notice to {to_number} could NOT be delivered "
                f"({delivery.get('error') or 'send failed'})."
            ),
            "instruction_for_agent": (
                "Do not tell the traveler they were notified on their phone. "
                "You may retry this tool once; if it fails again, put the "
                "information in the chat answer and say the push failed."
            ),
        }
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
    simply *chosen*, not booked. The cost veto still applies to it — at 0 EUR
    the policy normally resolves to "auto", so no approval is asked, but a
    traveler on the most cautious autonomy level is asked even for a free
    reroute. Never promise the traveler that a free option cannot need
    approval; act on the ``status`` this tool returns.

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
    # the executed result (clash_note), not a veto. The companion reschedule/notify
    # step remains a separate, independently gated action.
    #
    # The cost decision belongs to the policy layer, including for a free reroute:
    # at 0 EUR the configured threshold resolves it to "auto" anyway, so a
    # traveler on the balanced/aggressive level sees no extra prompt — but one who
    # chose "notify only" (conservative) still gets asked, which a hardcoded
    # free-reroute exemption would have silently denied them.
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

    from ..persistence import store

    if not store.claim_reroute_proposal_execution(
        proposal["user_id"], proposal_id, option_id
    ):
        return _revalidation_error(
            "The reroute proposal was already consumed or expired.",
            proposal_id=proposal_id,
            option_id=option_id,
        )
    rerouted_trip = _adopt_reroute_as_trip(proposal, option)
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
        trip_updated=rerouted_trip is not None,
        monitored_arrival=(rerouted_trip or {}).get("planned_arrival"),
    )


def _adopt_reroute_as_trip(proposal: dict, option: dict) -> dict | None:
    """Make the chosen connection the trip the traveler is monitored on.

    Taking a reroute changes the journey in progress rather than adding a second
    one, so the option is spliced into the stored trip under its existing
    ``trip_id`` (see ``reroute_apply``). That id is what keeps this chat, the
    reroute proposals and the complaint de-duplication pointing at the same
    journey — a fresh id would strand all three on the abandoned itinerary.

    Only train reroutes rewrite an itinerary: a Flinkster car or a Call-a-Bike
    covers a last mile, it does not replace the booked journey. Best-effort by
    design — the reroute itself is already executed and must not be reported as
    failed because the trip row could not be updated.
    """
    if option.get("mode", "train") != "train":
        return None
    user_id, trip_id = proposal.get("user_id"), proposal.get("trip_id")
    if not user_id or not trip_id:
        return None  # trip-less "ask the autopilot" chat: nothing to rewrite

    from ..persistence import store
    from ..reroute_apply import apply_reroute
    from ..request_context import turn_workspace

    try:
        trip = next(
            (t for t in store.get_trips(user_id) if t.get("trip_id") == trip_id), None
        )
        if trip is None:
            return None
        updated = apply_reroute(trip, option, proposal_id=proposal.get("proposal_id"))
        store.save_trips(user_id, [updated])
    except Exception:
        logger.warning("could not adopt reroute %s as trip %s", option.get("option_id"), trip_id, exc_info=True)
        return None

    # The Executor runs behind an AgentTool, whose result never reaches the
    # top-level event stream ui.chat iterates — the turn workspace is how the
    # updated trip gets back to the browser (same gap the rights lookup crosses).
    turn_workspace()["rerouted_trip"] = updated
    logger.info(
        "reroute adopted as trip %s: option=%s trains=%s arrival=%s kept_legs=%s",
        trip_id,
        option.get("option_id"),
        updated.get("trains"),
        updated.get("planned_arrival"),
        (updated.get("rerouted_from") or {}).get("kept_legs"),
    )
    return updated


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

    from ..persistence import store

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


_ISO_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")


def _proposal_travel_date() -> str | None:
    """The travel day of this chat's active reroute proposal, if there is one.

    Authoritative and — unlike the turn workspace — still available a turn
    later, which is when the traveler usually asks for the appointment to be
    moved. Best effort: no proposal (or no bound identity) simply contributes
    no candidate day.
    """
    try:
        from ..persistence import store
        from ..request_context import current_session_id, current_user_id

        user_id, session_id = current_user_id.get(), current_session_id.get()
        if not user_id or not session_id:
            return None
        proposal = store.get_active_reroute_proposal(user_id, session_id)
        travel_date = ((proposal or {}).get("proposal") or {}).get("travel_date") or ""
        return travel_date[:10] if _ISO_DATE_RE.fullmatch(travel_date[:10]) else None
    except Exception:
        return None


def _candidate_event_dates(new_start: str, travel_date: str) -> list[str]:
    """Days to search for the appointment, most likely first.

    A bare "HH:MM" ``new_start`` carries no day at all, and defaulting to today
    would make every appointment on a trip that does not depart today
    unresolvable. So the day comes from the explicit ``travel_date`` argument
    first, then from ``new_start`` when it is a full ISO datetime, and only
    lastly from today. When neither argument named a day — the ambiguous case —
    the chat's active reroute proposal is consulted as well, which is what makes
    a "move it to 16:30" on next week's trip resolvable at all. Extra candidates
    are harmless: the event is matched by its exact calendar id, so a wrong day
    simply yields no match.
    """
    candidates = [(travel_date or "")[:10], (new_start or "")[:10]]
    if not any(_ISO_DATE_RE.fullmatch(day) for day in candidates):
        candidates.append(_proposal_travel_date() or "")
    candidates.append(datetime.now().date().isoformat())
    return list(dict.fromkeys(d for d in candidates if _ISO_DATE_RE.fullmatch(d)))


_TIME_ONLY_RE = re.compile(r"^\d{1,2}:\d{2}$")


def _full_local_datetime(date_str: str, value: str) -> datetime | None:
    """Combine an appointment day with a bare "HH:MM", or parse a full ISO value."""
    value = (value or "").strip()
    if not value:
        return None
    if _TIME_ONLY_RE.match(value):
        value = f"{date_str}T{value}"
    parsed = parse_datetime(value)
    return parsed.replace(tzinfo=None) if parsed is not None else None


# Graph event ids are base64url — plain ASCII only ([A-Za-z0-9_-]+=*) — so none
# of these ever appear in a real one. An id that passes through chat prose
# (the Planner reports it in running text, not a code span) or a copy/paste
# can have its literal "-" silently swapped for a typographic look-alike by
# "smart punctuation" autocorrection, which then fails an exact-match lookup
# even though the id is otherwise identical. Undoing that swap is cheap and
# safe on both sides of the comparison.
_DASH_LOOKALIKES = str.maketrans({c: "-" for c in "‐‑‒–—―−"})


def _canonicalize_event_id(value: str) -> str:
    """Undo typographic dash substitution in a pasted/retyped event id."""
    return (value or "").translate(_DASH_LOOKALIKES)


async def _find_calendar_event(
    event_id: str, dates: list[str]
) -> tuple[dict, str] | None:
    """Resolve an appointment by its exact id, returning it with its day.

    Reading the event (rather than trusting the model's description of it) is
    what makes the tentative/confirmed veto trustworthy; the day it was found
    on is what makes a bare "HH:MM" new start unambiguous afterwards.
    """
    event_id = _canonicalize_event_id(event_id)
    for date in dates:
        calendar = await get_user_calendar(date)
        for event in calendar.get("events") or []:
            if _canonicalize_event_id(event.get("id") or "") == event_id:
                return event, date
    return None


async def _find_calendar_event_by_title(
    title: str, dates: list[str], old_start: str = ""
) -> tuple[dict, str] | list[dict] | None:
    """Resolve an appointment by title (+ day, + optional current start time).

    Used when the caller has no Graph event id — the traveler named an
    appointment directly in chat rather than one the Planner already flagged
    as a calendar clash (the usual source of an id). Still resolved against a
    real calendar read, same as ``_find_calendar_event``; only the SELECTION
    of which appointment is aided by title/time, never its tentative/confirmed
    status or anything else the veto relies on.

    Returns ``(event, date)`` on exactly one match, ``None`` on no match, or
    the list of candidate events when the title is ambiguous — the caller
    surfaces that rather than guessing which one the traveler meant.
    """
    needle = title.strip().lower()
    if not needle:
        return None
    matches: list[tuple[dict, str]] = []
    for date in dates:
        calendar = await get_user_calendar(date)
        for event in calendar.get("events") or []:
            haystack = (event.get("title") or "").strip().lower()
            if needle not in haystack and haystack not in needle:
                continue
            if old_start:
                old_dt = _full_local_datetime(date, old_start)
                event_start = parse_datetime(event.get("start"))
                if (
                    old_dt is not None
                    and event_start is not None
                    and old_dt != event_start.replace(tzinfo=None)
                ):
                    continue
            matches.append((event, date))
    if len(matches) == 1:
        return matches[0]
    if not matches:
        return None
    return [m[0] for m in matches]


def _resolve_new_window(
    event: dict, event_date: str, new_start: str, new_end: str
) -> tuple[str | None, str | None]:
    """New start/end as naive local ISO strings, ready for the Graph PATCH.

    A bare "HH:MM" ``new_start``/``new_end`` is combined with ``event_date``.
    When ``new_end`` is omitted, the appointment's original duration is
    preserved by shifting the original end by the same amount as the start;
    if that original window can't be parsed, only the start moves (Graph then
    keeps the event's current end, which may leave a mismatched duration).
    """
    start_dt = _full_local_datetime(event_date, new_start)
    if start_dt is None:
        return None, None
    end_dt = _full_local_datetime(event_date, new_end) if new_end else None
    if end_dt is None:
        orig_start = parse_datetime(event.get("start"))
        orig_end = parse_datetime(event.get("end"))
        if orig_start is not None and orig_end is not None:
            end_dt = start_dt + (
                orig_end.replace(tzinfo=None) - orig_start.replace(tzinfo=None)
            )
    return (
        start_dt.isoformat(timespec="seconds"),
        end_dt.isoformat(timespec="seconds") if end_dt else None,
    )


async def reschedule_outlook_event(
    appointment_title: str = "",
    travel_date: str = "",
    old_start: str = "",
    event_id: str = "",
    new_start: str = "",
    new_end: str = "",
    user_approved: bool = False,
) -> dict:
    """Move a calendar appointment a disrupted trip would make the traveler miss.

    Identify the appointment either by ``appointment_title`` + ``travel_date``
    (the default — matched against the real calendar, narrowed by
    ``old_start`` if more than one title matches) or by ``event_id`` when one
    is already in hand verbatim. Its tentative/confirmed status is read from
    the calendar itself, never accepted as an argument, and gates the veto:
    tentative moves automatically, confirmed asks first.

    Args:
        appointment_title: The appointment's title/subject. Matched
            case-insensitively against the calendar on ``travel_date``.
        travel_date: The appointment's CURRENT day ("YYYY-MM-DD"). Required
            for a title lookup, and whenever ``new_start`` is a bare "HH:MM".
        old_start: The appointment's current start ("HH:MM" or ISO) — narrows
            a title lookup that matches more than one event.
        event_id: Calendar id, when already known verbatim; skips the title
            lookup.
        new_start: Proposed new start — ISO datetime or "HH:MM".
        new_end: Proposed new end. Omitted keeps the original duration.
        user_approved: True only after the user approved a gated move.

    Returns:
        ``status="executed"``, ``"veto_required"``, ``"revalidation_failed"``
        (not found, or ambiguous — see ``candidates``), or ``"error"`` (a
        connected Graph write failed; nothing moved).
    """
    name = "reschedule_outlook_event"
    not_found_hint = (
        "Do not claim the appointment was moved. Ask the Planner for a fresh "
        "calendar check to get the current event id, or retry with "
        "appointment_title and travel_date so it can be looked up by name."
    )
    event_id = (event_id or "").strip()
    new_start = (new_start or "").strip()
    travel_date = (travel_date or "").strip()
    appointment_title = (appointment_title or "").strip()
    old_start = (old_start or "").strip()
    if not new_start:
        return _revalidation_error(
            "The new start time is required.", tool=name, instruction=not_found_hint,
        )
    if not event_id and not appointment_title:
        return _revalidation_error(
            "Either the calendar event id or the appointment's title (with "
            "its day as travel_date) is required to identify the appointment.",
            tool=name,
            instruction=not_found_hint,
        )
    if travel_date and not _ISO_DATE_RE.fullmatch(travel_date[:10]):
        return _revalidation_error(
            f"'{travel_date}' is not a usable date — pass the appointment's "
            "day as YYYY-MM-DD.",
            tool=name,
            instruction=not_found_hint,
        )
    if not event_id and appointment_title and not travel_date:
        return _revalidation_error(
            "travel_date (the appointment's current day) is required to "
            "look up an appointment by title.",
            tool=name,
            instruction=not_found_hint,
        )

    dates = _candidate_event_dates(new_start, travel_date)
    if event_id:
        found = await _find_calendar_event(event_id, dates)
        if found is None:
            return _revalidation_error(
                f"No calendar event with id '{event_id}' on {', '.join(dates)}. "
                "If the appointment is on another day, call again with "
                "travel_date set to that day.",
                tool=name,
                instruction=not_found_hint,
                event_id=event_id,
                days_searched=dates,
            )
    else:
        resolved = await _find_calendar_event_by_title(appointment_title, dates, old_start)
        if resolved is None:
            return _revalidation_error(
                f"No appointment titled '{appointment_title}' found on "
                f"{', '.join(dates)}.",
                tool=name,
                instruction=not_found_hint,
                appointment_title=appointment_title,
                days_searched=dates,
            )
        if isinstance(resolved, list):
            return _revalidation_error(
                f"'{appointment_title}' matches more than one appointment on "
                f"{', '.join(dates)}. Ask the traveler which one, or retry "
                "with old_start to disambiguate.",
                tool=name,
                instruction=not_found_hint,
                appointment_title=appointment_title,
                candidates=[
                    {"id": e.get("id"), "title": e.get("title"), "start": e.get("start")}
                    for e in resolved
                ],
            )
        found = resolved
    event, event_date = found
    # Use the authoritative id from the resolved event (not the possibly
    # typographically-corrupted argument) for every downstream use — the
    # veto payload, the Graph write, and the returned result — so a dash
    # look-alike that happened to match here doesn't then fail the PATCH
    # itself.
    event_id = event.get("id") or event_id

    title = event.get("title") or "the appointment"
    event_status = event.get("status") or "confirmed"
    # The day is part of what the traveler approves: a bare "HH:MM" new start
    # says nothing about which day was moved, and the veto text is the only
    # place they get to check that before it happens.
    summary = (
        f"Move '{title}' on {event_date} from "
        f"{event.get('start') or 'its current slot'} to "
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
            event_date=event_date,
            old_start=event.get("start"),
            new_start=new_start,
            new_end=new_end or None,
            attendees=event.get("attendee_emails") or [],
        )
    if is_calendar_connected():
        graph_start, graph_end = _resolve_new_window(event, event_date, new_start, new_end)
        if graph_start is None:
            return _revalidation_error(
                f"'{new_start}' could not be parsed as a usable date/time.",
                tool=name,
                instruction=not_found_hint,
                event_id=event_id,
            )
        try:
            from ..integrations.outlook import reschedule_calendar_event

            await reschedule_calendar_event(event_id, start=graph_start, end=graph_end)
        except Exception as exc:
            exc_name = type(exc).__name__
            logger.warning("calendar reschedule failed: %s: %s", exc_name, exc)
            result = {
                "status": "error",
                "tool": name,
                "event_id": event_id,
                "error": f"{exc_name}: {exc}",
                "instruction_for_agent": (
                    "Do not claim the appointment was moved — the Graph write "
                    "failed."
                ),
            }
            if "AuthenticationRequired" in exc_name or "Authentication" in str(exc):
                result["hint"] = (
                    "The cached login has no Calendars.ReadWrite consent yet. "
                    "Reconnect Outlook once (onboarding or "
                    "scripts/check_outlook.py --login) — calendar reading "
                    "keeps working regardless."
                )
            return result
        note = (
            "Written back to Microsoft Graph — the appointment was actually "
            "moved. Attendees are NOT notified by this action; offer the "
            "notice email separately."
        )
    else:
        note = (
            "Outlook is not connected — the move was simulated (demo mode), "
            "no real calendar was changed. Attendees are NOT notified by "
            "this action; offer the notice email separately."
        )
    logger.info(
        "calendar event rescheduled: id=%s date=%s status=%s %s -> %s",
        event_id,
        event_date,
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
        event_date=event_date,
        old_start=event.get("start"),
        new_start=new_start,
        new_end=new_end or None,
        attendees=event.get("attendee_emails") or [],
        note=note,
    )


def file_compensation_claim(user_approved: bool = False) -> dict:
    """File the passenger-rights compensation claim for a concluded trip.

    The counterpart to the Planner's read-only rights lookup: that tool tells
    the traveler what they are owed, this one actually files it — through the
    policy/veto gate like every other write.

    Takes NO delay or amount arguments on purpose. Both are read from the
    settled ``get_passenger_rights`` result of this chat (which only exists once
    the trip has concluded), never from conversation text — the same rule the
    reroute path enforces via ``proposal_id``. A claim can therefore not be
    filed for a delay the traveler has not experienced, nor for an amount the
    model reconstructed. The lookup does not have to be repeated in the turn the
    traveler approves the filing: the settled result stays available for the
    whole conversation.

    Args:
        user_approved: Set to true ONLY after the user explicitly approved a
            previously returned ``veto_required``.

    Returns:
        ``status="executed"`` on filing, ``status="veto_required"`` when the
        policy asks first, or ``status="revalidation_failed"`` when no settled
        rights result backs the claim.
    """
    from .read_tools import settled_rights_result

    name = "file_compensation_claim"
    rights = settled_rights_result()
    no_claim_hint = (
        "Do not claim anything was filed. Ask the Planner for a passenger-rights "
        "lookup on the concluded trip first, then try again."
    )
    if rights is None:
        return _revalidation_error(
            "No settled passenger-rights result backs this claim. A claim can "
            "only be filed after the rights lookup ran for a CONCLUDED trip "
            "(trip_concluded=true) somewhere in this conversation.",
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
