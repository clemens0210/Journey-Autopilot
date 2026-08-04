/* Trip chat: runs the ReAct orchestrator (the scenarios/happy_path.py flow).
 *
 * The screen, the transcript rendering, and one turn against /api/chat. How a
 * conversation is shaped and persisted lives in chat-store.js.
 */

import { state } from "./state.js";
import { api } from "./api.js";
import { $, el, escapeHtml, screen, toast } from "./dom.js";
import { fmtDate, fmtEur, fmtTime, tripStatusBadge } from "./format.js";
import { handleComplaintCreated } from "./components.js";
import { go, registerScreens } from "./router.js";
import {
  MONITOR_NOTICE, MONITOR_PROMPT, assistantMessage, chatKey, newChat, persistChats,
} from "./chat-store.js";
import { renderMarkdown, renderTrace } from "./markdown.js";
import { renderOptionCards } from "./chat-options.js";

export function openChat(trip = null) {
  const existing = state.chats[chatKey(trip)];
  if (existing) {
    // Resume: keep the transcript and the ADK session id, refresh the trip
    // snapshot (live status may have moved since), and skip the greeting and
    // the automatic monitoring turn below. A chat adopted from the demo
    // preload lands here too — already complete, so nothing is re-run.
    if (trip) existing.trip = trip;
    existing.lastActiveAt = Date.now();
    state.chat = existing;
    persistChats();
    go("chat");
    return;
  }
  state.chat = newChat(trip);
  persistChats();
  go("chat");
  // Trip chats start with an automatic monitoring turn: opening the chat IS
  // the "monitor my trip" intent, so the live status/risk check (and, on a
  // detected risk band, the proactive WhatsApp notice) runs without the user
  // having to type anything. Only once per freshly opened chat — reopening or
  // restoring a conversation never re-triggers it.
  if (trip) runChatTurn(MONITOR_PROMPT, { display: MONITOR_NOTICE });
}

// The trip block in the chat header, rebuilt in place. Taking a reroute changes
// the trip under an open conversation, and renderChatLog() only touches the log
// — without this the header would keep naming the abandoned train and arrival.
function renderChatHead() {
  const head = $(".chat-trip");
  if (!head) return;
  const trip = state.chat.trip;
  if (!trip) return;
  head.innerHTML = `
    <span class="chat-route">${escapeHtml(trip.origin || "")} → ${escapeHtml(trip.destination || "")}</span>
    <span class="chat-sub">${escapeHtml(trip.train || "Connection")} · ${escapeHtml(fmtDate(trip.planned_departure))} · ${escapeHtml(fmtTime(trip.planned_departure))}</span>`;
  const badge = head.nextElementSibling;
  if (badge && badge.classList.contains("trip-status")) {
    badge.outerHTML = tripStatusBadge(trip);
  }
}

function renderChatLog() {
  persistChats();
  const log = $("#chat-log");
  if (!log) return;
  const parts = state.chat.messages.map((m, messageIndex) => {
    if (m.role === "user") return `<div class="bubble user">${escapeHtml(m.text)}</div>`;
    if (m.role === "error") return `<div class="bubble error">⚠️ ${escapeHtml(m.text)}</div>`;
    if (m.role === "notice") {
      const link = m.complaintId
        ? `<button type="button" class="notice-link" data-complaint-id="${escapeHtml(m.complaintId)}">Review complaint →</button>`
        : "";
      return `<div class="bubble notice">
        <div>${escapeHtml(m.text)}</div>
        ${link}
      </div>`;
    }
    const trace = m.trace && m.trace.length ? renderTrace(m.trace) : "";
    const cards = m.options && m.options.length ? renderOptionCards(m.options, m.optionsSource, m, { messageIndex }) : "";
    const fallbacks = m.fallbackOptions && m.fallbackOptions.length
      ? `<div class="option-fallback-title">Outside your current limits</div>${renderOptionCards(m.fallbackOptions, m.optionsSource, m, { fallback: true, messageIndex })}`
      : "";
    return `<div class="bubble assistant"><div class="md">${renderMarkdown(m.text)}</div>${cards}${fallbacks}${trace}</div>`;
  });
  if (state.chat.busy) parts.push(`<div class="bubble assistant typing"><i></i><i></i><i></i></div>`);
  log.innerHTML = parts.join("");
  log.querySelectorAll(".notice-link").forEach((btn) => {
    btn.addEventListener("click", () => {
      state.complaintId = btn.dataset.complaintId;
      go("complaint_detail");
    });
  });
  log.scrollTop = log.scrollHeight;
}

function onOptionCardClick(ev) {
  const card = ev.target.closest(".option-card");
  if (!card || card.disabled) return;
  if (state.chat.busy) return;
  const optionId = card.dataset.optionId;
  if (!optionId) return;
  // Resolve the originating message by its render-time index (embedded on the
  // card itself), not by scanning history for a matching option_id — option
  // ids like "R1" are reused across separate proposals, so a text-based
  // search here could attach an older card's click to a newer proposal.
  const messageIndex = Number(card.dataset.messageIndex);
  const m = state.chat.messages[messageIndex];
  const proposalId = m ? m.proposalId || null : null;
  if (!m || !proposalId) {
    toast("This reroute proposal is no longer active. Please run a fresh search.", 6000);
    return;
  }
  m.chosenOption = optionId;
  runChatTurn(`Take option ${optionId}`, {
    display: { role: "user", text: `Take option ${optionId}` },
    selection: { proposalId, optionId },
  });
}

async function onChatSubmit(ev) {
  ev.preventDefault();
  const input = $("#chat-text");
  const text = input.value.trim();
  if (!text || state.chat.busy) return;
  input.value = "";
  await runChatTurn(text);
}

// One chat turn against the orchestrator. ``display`` overrides the bubble
// shown for this turn (the auto-monitor turn shows a notice instead of a
// fake user message); the ``text`` is what the agent actually receives.
export async function runChatTurn(text, { display = null, selection = null } = {}) {
  if (state.chat.busy) return;
  state.chat.messages.push(display || { role: "user", text });
  state.chat.busy = true;
  state.chat.lastActiveAt = Date.now(); // keeps the MRU order used when pruning
  if ($("#chat-send")) $("#chat-send").disabled = true;
  renderChatLog();

  const chat = state.chat; // keep a handle in case the user navigates away
  try {
    const data = await api("/api/chat", {
      method: "POST",
      body: {
        session_id: chat.sessionId,
        message: text,
        trip: chat.trip,
        proposal_id: selection?.proposalId || null,
        selected_option_id: selection?.optionId || null,
      },
    });
    if (data.session_id) chat.sessionId = data.session_id;
    // chat_turn() silently opened a fresh ADK session because the old one died
    // with a server restart, and re-seeded it with this trip. The transcript
    // above is still ours, but the agent no longer remembers it — say so
    // rather than letting it look inexplicably forgetful.
    if (data.session_restarted) {
      chat.messages.push({
        role: "notice",
        text: "The server restarted, so the autopilot lost the earlier conversation. "
          + "It picked this trip's details back up and answered from there.",
      });
    }
    if (data.error) {
      chat.messages.push({ role: "error", text: data.error });
    } else {
      // A proactive WhatsApp notice is sent on every monitoring turn; the band
      // (when detected) only shapes the message. Surface the send result.
      if (data.alert) {
        const phone = state.profile?.notifications?.phone;
        const tag = data.risk_band === "HIGH" ? "⚠️ HIGH risk — " : "";
        if (data.alert.sent) {
          toast(`📲 ${tag}WhatsApp notice sent to ${phone}`, 6000);
        } else if (data.alert.demo) {
          toast(`📲 ${tag}WhatsApp notice prepared for ${phone} (demo: Twilio not configured)`, 7000);
        } else if (data.alert.reason === "no_phone") {
          toast(`⚠️ No phone number saved — add one to get WhatsApp notices`, 7000);
        } else if (data.alert.error) {
          toast(`⚠️ Could not send WhatsApp notice: ${data.alert.error}`, 7000);
        }
      }
      chat.messages.push(assistantMessage(data));
      // A booked reroute rewrote this trip server-side (same trip_id, spliced
      // itinerary). Adopt it everywhere it is mirrored — the conversation's own
      // snapshot, the header, and the dashboard list — so nothing keeps showing
      // the abandoned connection until the next reload.
      if (data.trip) {
        chat.trip = data.trip;
        if (data.trips) state.trips = data.trips;
        if (state.chat === chat) renderChatHead();
        const trains = (data.trip.trains || [data.trip.train]).filter(Boolean).join(" → ");
        chat.messages.push({
          role: "notice",
          text: `Reroute applied — this trip now runs ${trains}, arriving `
            + `${fmtTime(data.trip.planned_arrival)}. The autopilot is monitoring the new connection.`,
        });
      }
      if (data.complaint_created) {
        handleComplaintCreated(data.complaint_created);
        chat.messages.push({
          role: "notice",
          text: `Drafted a complaint for this trip — est. ${fmtEur(data.complaint_created.compensation_eur)} compensation.`,
          complaintId: data.complaint_created.complaint_id,
        });
      }
    }
  } catch (err) {
    chat.messages.push({ role: "error", text: err.message });
  } finally {
    chat.busy = false;
    // Persist unconditionally: if the user opened another conversation while
    // this turn was in flight, renderChatLog() below won't run, and the reply
    // would otherwise live only in memory until the next reload dropped it.
    persistChats();
    if (state.chat === chat) {
      if ($("#chat-send")) $("#chat-send").disabled = false;
      renderChatLog();
      if ($("#chat-text")) $("#chat-text").focus();
    }
  }
}

// Named chatScreen, not chat: `chat` is the local handle for a conversation
// object throughout this module, and the two must not read as the same thing.
function chatScreen() {
  const trip = state.chat.trip;
  const headInner = trip
    ? `<div class="chat-trip">
        <span class="chat-route">${escapeHtml(trip.origin || "")} → ${escapeHtml(trip.destination || "")}</span>
        <span class="chat-sub">${escapeHtml(trip.train || "Connection")} · ${escapeHtml(fmtDate(trip.planned_departure))} · ${escapeHtml(fmtTime(trip.planned_departure))}</span>
      </div>
      ${tripStatusBadge(trip)}`
    : `<div class="chat-trip">
        <span class="chat-route">Ask the autopilot</span>
        <span class="chat-sub">Any trip — no booking needed</span>
      </div>`;
  screen.replaceChildren(el(`
    <div class="chat-head">
      <button class="chat-back" id="chat-back" type="button" aria-label="Back">‹</button>
      ${headInner}
    </div>
    <div class="chat-log" id="chat-log"></div>
    <form class="chat-input" id="chat-form">
      <input type="text" id="chat-text" placeholder="${trip ? "Ask the autopilot about this trip…" : "Describe a trip or ask about disruptions…"}" autocomplete="off">
      <button class="chat-send" id="chat-send" type="submit" aria-label="Send">
        <svg viewBox="0 0 24 24" width="20" height="20" fill="none"><path d="M4 12 20 4l-4 16-4-7-8-1Z" stroke="currentColor" stroke-width="2" stroke-linejoin="round"/></svg>
      </button>
    </form>
  `));
  // The chat owns the full screen — hide the wizard chrome.
  $("#navbar").hidden = true;
  $("#tabbar").hidden = true;
  $("#progress").hidden = true;

  renderChatLog();
  // Leave the chat object in place — reopening this trip resumes it (see openChat).
  $("#chat-back").addEventListener("click", () => go(state.tripDetail ? "tripdetail" : "dashboard"));
  $("#chat-form").addEventListener("submit", onChatSubmit);
  // Delegated click handler for reroute option cards — one listener on the
  // log survives re-renders. Clicking sends "Take option <id>" as the next
  // user turn and marks the batch as chosen so the cards grey out.
  $("#chat-log").addEventListener("click", onOptionCardClick);
  $("#chat-text").focus();
}

registerScreens({ chat: chatScreen });
