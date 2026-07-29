/* Dashboard — the monitored trips, and the entry point into every chat.
 *
 * Profile settings live in the Profile tab only: the dashboard used to
 * duplicate them (preferences, policy, connections, GDPR delete) and both
 * copies had to be kept in sync.
 */

import { state } from "./state.js";
import { api } from "./api.js";
import { $, el, screen, setActiveTab, setNav, toast } from "./dom.js";
import { fmtDate, fmtTime, isPastTrip, isUpcomingTrip, sortTripsByDate } from "./format.js";
import { tripCardHTML } from "./components.js";
import { go, registerScreens } from "./router.js";
import { openTripDetail } from "./tripdetail.js";
import { openChat } from "./chat.js";
import { chatKey, persistChats } from "./chat-store.js";

// Single-trip delete. Attached directly to each .trip-delete button (per-
// element, in the dashboard renderer) so ev.stopPropagation() fires ON the
// button — blocking the parent .trip-card click (which opens the chat) from
// ever firing. (A delegated handler on #screen would run AFTER the card's
// handler, by which point state.step is already "chat".)
async function onDeleteTripClick(ev) {
  const btn = ev.target.closest(".trip-delete");
  if (!btn) return;
  const tripId = btn.dataset.tripDeleteId;
  if (!tripId) return;
  ev.stopPropagation();
  if (!confirm("Delete this trip? This cannot be undone.")) return;
  try {
    const data = await api(`/api/trips/${encodeURIComponent(tripId)}`, { method: "DELETE" });
    state.trips = data.trips;
    // The deleted trip's conversation goes with it; other chats are untouched.
    const deletedKey = chatKey({ trip_id: tripId });
    if (state.chats[deletedKey]) {
      if (state.chat === state.chats[deletedKey]) state.chat = null;
      delete state.chats[deletedKey];
      persistChats();
    }
    toast("✓ Trip deleted");
    // Trash buttons only appear on the dashboard (trips page); stay there.
    go("dashboard");
  } catch (err) {
    toast(`⚠️ ${err.message}`);
  }
}

function dashboard() {
  const now = new Date();
  const sortedTrips = sortTripsByDate(state.trips);
  const nextTrip = sortedTrips.find((t) => isUpcomingTrip(t, now));
  const cards = sortedTrips
    .map((t, i) => {
      const past = isPastTrip(t, now);
      return tripCardHTML(t, {
        foot: past ? "Past trip" : "Monitored by the autopilot · tap to chat",
        live: !past,
        index: i,
        deletable: true,
      });
    })
    .join("");

  screen.replaceChildren(el(`
    <div class="dash-greeting">
      <h1>Hi ${state.account.first_name} 👋</h1>
      <p class="muted">${nextTrip
        ? `Your next trip starts ${fmtDate(nextTrip.planned_departure)} at ${fmtTime(nextTrip.planned_departure)} — the autopilot is watching.`
        : "No upcoming trips — past trips are kept for your records."}</p>
    </div>

    <div class="section-title">
      <h2>Monitored trips</h2>
      <div class="section-actions">
        <button id="add-trip" type="button">+ Add trip</button>
      </div>
    </div>
    ${cards || '<div class="card"><p class="muted">No trips imported.</p></div>'}
    <div class="card clickable" id="general-chat-card" style="cursor:pointer; display:flex; flex-direction:column; gap:4px">
      <span style="font-weight:600">💬 Ask the autopilot about any trip</span>
      <span class="muted">No booking needed — describe a route to check delay risk, reroutes, and calendar deadlines.</span>
    </div>

  `));
  setNav({ back: false, next: "Next" });
  $("#navbar").hidden = true;
  $("#progress").hidden = true;
  $("#tabbar").hidden = false; // mock tab bar of the DB Navigator
  setActiveTab("trips");

  // Clicking a monitored trip opens the journey detail screen (delay + forecast).
  screen.querySelectorAll(".trip-card.clickable").forEach((cardEl) => {
    cardEl.addEventListener("click", () => openTripDetail(sortedTrips[Number(cardEl.dataset.tripIndex)]));
  });

  // Attach the delete handler directly to each trash button (per-element) so
  // its stopPropagation fires on the button and blocks the card click above.
  screen.querySelectorAll(".trip-delete").forEach((btn) => {
    btn.addEventListener("click", onDeleteTripClick);
  });
  $("#general-chat-card")?.addEventListener("click", () => openChat(null));

  $("#add-trip").addEventListener("click", () => go("book"));
}

registerScreens({ dashboard });
