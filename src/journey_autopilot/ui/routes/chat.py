"""Chat endpoint (runs the ReAct orchestrator) and the demo chat preload.

Both routes drive ``ui.chat.chat_turn`` — the live one per user message, the
preload one ahead of a presentation — which is why they share a module and the
``_PRELOADED_CHATS`` buffer between them.
"""

from __future__ import annotations

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel

from ...persistence import store
from .. import chat
from .deps import current_user_id

router = APIRouter(tags=["chat"])

# Demo chats warmed ahead of a presentation: user_id -> [turn result + trip].
# Deliberately in memory, next to the ADK sessions they point at: the whole
# point is to survive a `reset_demo.py` DB wipe (so onboarding can be shown
# from scratch while the expensive first chat turn is already done) while
# dying with the server, since a restart kills the InMemoryRunner sessions
# these transcripts reference. Populated by POST /api/demo/preload.
_PRELOADED_CHATS: dict[str, list[dict]] = {}

# Kept in sync with MONITOR_PROMPT in ui/static/js/chat-store.js, which sends the same
# text when a trip chat is opened live. A drift between the two only changes
# the wording of the preloaded first turn, never correctness.
DEMO_MONITOR_PROMPT = (
    "Monitor my trip: check the live status and current disruption risk, "
    "and tell me if I need to do anything."
)


class ChatRequest(BaseModel):
    message: str
    session_id: str | None = None
    trip: dict | None = None
    proposal_id: str | None = None
    selected_option_id: str | None = None


class PreloadRequest(BaseModel):
    trip_ids: list[str] | None = None   # None/empty => every imported trip
    message: str = ""                   # falls back to DEMO_MONITOR_PROMPT
    notify: bool = False                # send the proactive WhatsApp during preload


def chat_bootstrap(user_id: str) -> dict:
    """Chat bookkeeping the browser needs before it can render conversations.

    ``preloaded_chats`` hands over any conversations warmed by
    scripts/preload_demo_chats.py. Returned by both /api/me and the login
    response, because a fresh demo tab logs in rather than booting with an
    existing token.
    """
    return {
        "complaints": store.get_complaints(user_id),
        "preloaded_chats": _PRELOADED_CHATS.get(user_id, []),
    }


@router.post("/api/chat")
async def chat_endpoint(
    body: ChatRequest, authorization: str | None = Header(default=None)
) -> dict:
    """Drives the ReAct orchestrator from the chat UI.

    Clicking a trip opens a chat; each message is handed to ``root_agent``
    (the same orchestrator ``scenarios/happy_path.py`` uses). On the first message the
    selected trip is added as context so the orchestrator monitors it. The
    agent/tool trace and the final answer are returned for display.

    ADK + a configured Uni-GPT backend (.env) are required here; errors are
    returned as ``error`` (HTTP 200) so the chat UI can show them inline.
    """
    user_id = current_user_id(authorization)  # chat is behind the login like the rest of the API
    # The traveler's saved number — used for the proactive HIGH-risk WhatsApp
    # alert. May be None if the phone step was skipped during onboarding.
    profile = store.get_profile(user_id) or {}
    notify_phone = (profile.get("notifications") or {}).get("phone")
    try:
        account = store.get_account(user_id)
        result = await chat.chat_turn(
            body.session_id,
            body.message,
            body.trip,
            account,
            notify_phone=notify_phone,
            user_id=user_id,
            proposal_id=body.proposal_id,
            selected_option_id=body.selected_option_id,
        )
        return result
    except Exception as exc:  # surfaced inline in the chat instead of a 500
        return {
            "session_id": body.session_id,
            "reply": None,
            "trace": [],
            "error": f"{type(exc).__name__}: {exc}",
        }


@router.post("/api/demo/preload")
async def preload_demo_chats_endpoint(
    body: PreloadRequest, authorization: str | None = Header(default=None)
) -> dict:
    """Warm the expensive first chat turn for one or more trips, ahead of a demo.

    Runs exactly what opening a trip chat would run, then parks the result in
    ``_PRELOADED_CHATS`` so the browser can adopt the finished conversation
    instead of waiting on the orchestrator during a presentation. The ADK
    session created here stays live in this process, so the chat can be
    *continued* — the agent keeps its memory for the rest of the server's life.

    Driven by ``scripts/preload_demo_chats.py``; see that script for the demo
    preparation sequence.
    """
    user_id = current_user_id(authorization)
    account = store.get_account(user_id)
    profile = store.get_profile(user_id) or {}
    # The proactive WhatsApp alert is opt-in here: preloading happens *before*
    # the presentation, so firing it by default would send the notice to the
    # presenter's phone during setup and spend the demo moment early.
    notify_phone = (profile.get("notifications") or {}).get("phone") if body.notify else None

    wanted = set(body.trip_ids or [])
    selected = [t for t in store.get_trips(user_id) if not wanted or t["trip_id"] in wanted]
    missing = sorted(wanted - {t["trip_id"] for t in selected})
    if not selected:
        raise HTTPException(
            status_code=400,
            detail=f"No matching trips to preload (unknown ids: {missing})." if missing
            else "No trips imported for this account yet.",
        )

    preloaded: list[dict] = []
    failures: list[dict] = []
    for trip in selected:
        try:
            result = await chat.chat_turn(
                None,  # fresh ADK session, so chat_turn seeds the trip context
                body.message or DEMO_MONITOR_PROMPT,
                trip,
                account,
                notify_phone=notify_phone,
                user_id=user_id,
            )
        except Exception as exc:
            failures.append({"trip_id": trip["trip_id"], "error": f"{type(exc).__name__}: {exc}"})
            continue
        result["trip"] = trip
        result["trip_id"] = trip["trip_id"]
        preloaded.append(result)

    # Replace only the trips just warmed, so preloading one trip doesn't discard
    # a conversation warmed by an earlier run.
    fresh_ids = {entry["trip_id"] for entry in preloaded}
    kept = [e for e in _PRELOADED_CHATS.get(user_id, []) if e["trip_id"] not in fresh_ids]
    _PRELOADED_CHATS[user_id] = kept + preloaded

    return {
        "preloaded": [
            {
                "trip_id": entry["trip_id"],
                "session_id": entry.get("session_id"),
                "risk_band": entry.get("risk_band"),
                "reply_chars": len(entry.get("reply") or ""),
                "options": len(entry.get("options") or []),
                "complaint_created": bool(entry.get("complaint_created")),
            }
            for entry in preloaded
        ],
        "failures": failures,
        "total_cached": len(_PRELOADED_CHATS[user_id]),
    }
