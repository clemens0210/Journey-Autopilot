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

from datetime import datetime, timezone

from .. import policy


def _profile() -> dict | None:
    """The single prototype profile (carries the user's policy settings)."""
    try:
        from journey_autopilot.persistence import store

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


def book_alternative_connection(
    option_id: str,
    description: str,
    cost_eur: float = 0.0,
    user_approved: bool = False,
) -> dict:
    """Books an alternative train connection (a reroute the Planner proposed).

    Gated by the policy layer, with a cost threshold: cheap/free rebookings may
    run autonomously, more expensive ones ask for approval first.

    Args:
        option_id: The reroute option id from the Planner.
        description: Human-readable connection description.
        cost_eur: Additional cost of the rebooking in EUR (0 if free).
        user_approved: Set to true ONLY after the user explicitly approved.

    Returns:
        ``status="executed"`` on booking, or ``status="veto_required"``.
    """
    name = "book_alternative_connection"
    summary = f"Book reroute {option_id} ({description}) — extra cost {cost_eur:.2f} EUR"
    if (
        policy.resolve(name, profile=_profile(), cost_eur=cost_eur) == "ask"
        and not user_approved
    ):
        return _veto(name, summary, option_id=option_id, cost_eur=cost_eur)
    return _done(
        name,
        summary,
        option_id=option_id,
        cost_eur=cost_eur,
        booking_ref=f"SIM-{option_id}",
    )


def book_hotel(
    name_of_hotel: str,
    cost_eur: float = 0.0,
    nights: int = 1,
    user_approved: bool = False,
) -> dict:
    """Books an overnight hotel stay when the traveler is stranded.

    High commitment (cost + overnight) — gated by the policy layer.

    Args:
        name_of_hotel: Hotel name.
        cost_eur: Total cost in EUR.
        nights: Number of nights.
        user_approved: Set to true ONLY after the user explicitly approved.

    Returns:
        ``status="executed"`` on booking, or ``status="veto_required"``.
    """
    name = "book_hotel"
    summary = f"Book {nights} night(s) at {name_of_hotel} — {cost_eur:.2f} EUR"
    if policy.resolve(name, profile=_profile(), cost_eur=cost_eur) == "ask" and not user_approved:
        return _veto(name, summary, hotel=name_of_hotel, cost_eur=cost_eur, nights=nights)
    return _done(
        name,
        summary,
        hotel=name_of_hotel,
        cost_eur=cost_eur,
        nights=nights,
        booking_ref="SIM-HOTEL",
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
