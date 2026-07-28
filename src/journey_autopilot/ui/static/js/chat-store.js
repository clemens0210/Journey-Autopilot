/* Conversation objects and their sessionStorage mirror.
 *
 * All conversations are mirrored into sessionStorage on every render, so opening
 * a second chat and coming back — or reloading the page — resumes each one
 * exactly where it left off. One entry per trip, plus one for the trip-less
 * "ask the autopilot" chat.
 *
 * sessionStorage, not localStorage, is deliberate: a transcript is only useful
 * while the agent still remembers it, and its memory lives in the server's
 * InMemoryRunner (ui/chat.py). Tying chats to the tab keeps the two roughly in
 * step and means a fresh tab is genuinely a fresh start — which is what
 * scripts/reset_demo.py assumes. Warm chats that must outlive the tab come from
 * the server instead (see adoptPreloadedChats).
 *
 * They can still fall out of step — a server restart with the tab open leaves
 * dead session ids behind. chat_turn() handles that by opening a fresh,
 * trip-seeded session and flagging ``session_restarted``; real continuity would
 * need ADK's DatabaseSessionService instead of the InMemoryRunner in ui/chat.py.
 *
 * This module owns the shape of a conversation; chat.js owns the screen and the
 * turn against the orchestrator.
 */

import { state } from "./state.js";
import { fmtEur } from "./format.js";

// The automatic first turn of a trip chat. Kept as constants because
// scripts/preload_demo_chats.py runs the same turn ahead of a demo and
// adoptPreloadedChats() rebuilds the transcript from them — the preloaded
// conversation must be indistinguishable from a live one. DEMO_MONITOR_PROMPT
// in ui/routes/chat.py is the server-side copy of MONITOR_PROMPT.
export const MONITOR_PROMPT =
  "Monitor my trip: check the live status and current disruption risk, and tell me if I need to do anything.";
export const MONITOR_NOTICE = {
  role: "notice",
  text: "Automatic check — the autopilot is monitoring this trip (live status, risk, calendar).",
};

const CHAT_STORAGE_PREFIX = "ja_chats:";
// Pre-multi-chat single-chat key. Never read, but "delete all data" wipes it.
export const LEGACY_CHAT_STORAGE_KEY = "ja_chat";
const MAX_STORED_CHATS = 20;

// Chats are per-account; "anon" can only occur before /api/me has resolved,
// which is never a state we persist from.
export function chatStorageKey() {
  return CHAT_STORAGE_PREFIX + (state.account?.user_id || "anon");
}

// One conversation per trip; the trip-less chat gets its own fixed slot.
export function chatKey(trip) {
  return trip && trip.trip_id ? `trip:${trip.trip_id}` : "general";
}

export function chatGreeting(trip) {
  const tomorrow = new Date(Date.now() + 86400000).toISOString().slice(0, 10);
  return trip
    ? `Hi ${state.account.first_name}! I'm keeping an eye on your ${trip.origin} → ${trip.destination} trip — `
      + `running a live check for you now. Ask me anything in the meantime.`
    : `Hi ${state.account.first_name}! I'm your monitoring assistant. Describe any trip — e.g. `
      + `"risk for an ICE from Cologne Hbf to Hamburg Hbf on ${tomorrow} at 09:00" — and I'll check the `
      + `delay risk, reroute options, and your calendar deadlines. No booking needed.`;
}

export function newChat(trip) {
  const key = chatKey(trip);
  state.chats[key] = {
    key,
    sessionId: null,
    trip,
    busy: false,
    messages: [{ role: "assistant", text: chatGreeting(trip) }],
    lastActiveAt: Date.now(),
  };
  return state.chats[key];
}

// Builds the assistant bubble from a /api/chat response. Shared with
// adoptPreloadedChats() so a preloaded conversation renders identically to a
// live one — option cards, trace, proposal id and all.
export function assistantMessage(data) {
  return {
    role: "assistant",
    text: data.reply,
    trace: data.trace,
    options: data.options || null,
    fallbackOptions: data.fallback_options || null,
    optionsSource: data.options_source || null,
    recommendedOptionId: data.recommended_option_id || null,
    rejectedSummary: data.rejected_summary || null,
    proposalId: data.proposal_id || null,
    proposalExpiresAt: data.proposal_expires_at || null,
  };
}

// ``busy`` is deliberately not persisted — a turn interrupted by a reload must
// not come back as a permanently disabled input.
function serializeChat(chat) {
  return {
    key: chat.key,
    sessionId: chat.sessionId,
    trip: chat.trip,
    messages: chat.messages,
    lastActiveAt: chat.lastActiveAt || 0,
  };
}

export function persistChats() {
  const storageKey = chatStorageKey();
  // Most-recently-used first, so both the cap and the quota fallback below drop
  // the least interesting conversations.
  const ordered = Object.values(state.chats)
    .sort((a, b) => (b.lastActiveAt || 0) - (a.lastActiveAt || 0))
    .slice(0, MAX_STORED_CHATS);
  const activeKey = state.chat ? state.chat.key : null;
  // Agent traces make transcripts bulky, so a write can blow the quota. Retry
  // with progressively fewer conversations (oldest dropped first) until one
  // fits: keeping recent history beats the all-or-nothing of a single attempt.
  for (let keep = ordered.length; keep > 0; keep = Math.floor(keep / 2)) {
    try {
      sessionStorage.setItem(storageKey, JSON.stringify({
        activeKey,
        chats: ordered.slice(0, keep).map(serializeChat),
      }));
      return;
    } catch {
      // Over quota, or storage unavailable — narrow the payload and retry.
    }
  }
  try {
    // Nothing fits (or storage is unusable): drop the key rather than leave a
    // stale payload that would restore as a silently truncated conversation.
    sessionStorage.removeItem(storageKey);
  } catch {
    // Private mode: chat keeps working in memory, it just won't survive a reload.
  }
}

// Rehydrates every stored conversation (called once from boot()). Returns true
// if one of them was the active chat, so boot() can land on it directly instead
// of the dashboard.
export function restoreChats() {
  state.chats = {};
  state.chat = null;
  let saved;
  try {
    saved = JSON.parse(sessionStorage.getItem(chatStorageKey()) || "null");
  } catch {
    saved = null;
  }
  if (!saved || !Array.isArray(saved.chats)) {
    sessionStorage.removeItem(chatStorageKey());
    return false;
  }
  for (const entry of saved.chats) {
    if (!entry || !entry.key || !Array.isArray(entry.messages)) continue;
    // Prefer the freshly-fetched trip (from /api/me) over the stored snapshot;
    // fall back to the snapshot if the trip is no longer in the current list.
    const freshTrip = entry.trip?.trip_id
      ? state.trips.find((t) => t.trip_id === entry.trip.trip_id)
      : null;
    state.chats[entry.key] = {
      key: entry.key,
      sessionId: entry.sessionId || null,
      trip: freshTrip || entry.trip || null,
      busy: false,
      messages: entry.messages,
      lastActiveAt: entry.lastActiveAt || 0,
    };
  }
  state.chat = (saved.activeKey && state.chats[saved.activeKey]) || null;
  return Boolean(state.chat);
}

export function adoptPreloadedChats(entries) {
  let adopted = 0;
  for (const entry of entries || []) {
    if (!entry || !entry.trip || !entry.session_id || entry.error) continue;
    const key = chatKey(entry.trip);
    if (state.chats[key]) continue;
    const freshTrip = state.trips.find((t) => t.trip_id === entry.trip.trip_id) || entry.trip;
    const chat = newChat(freshTrip);
    chat.sessionId = entry.session_id;
    chat.messages.push(MONITOR_NOTICE, assistantMessage(entry));
    if (entry.complaint_created) {
      // No toast here (unlike a live turn): the complaint is already in the
      // list /api/me just returned, so announcing it on boot would be noise.
      chat.messages.push({
        role: "notice",
        text: `Drafted a complaint for this trip — est. ${fmtEur(entry.complaint_created.compensation_eur)} compensation.`,
        complaintId: entry.complaint_created.complaint_id,
      });
    }
    adopted++;
  }
  if (adopted) persistChats();
  return adopted;
}
