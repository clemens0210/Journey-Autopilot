/* Journey Autopilot — entry point.
 *
 * A small state-based wizard with no framework and no build step: each screen
 * module registers its renderers with the router, `go(step)` draws one, and the
 * navbar (Back/Skip/Next) is configured per step.
 *
 * This file only assembles the app: it imports every screen module (importing
 * one is what registers it), wires the chrome that lives in index.html and
 * outlives all screens, and boots. Loaded as <script type="module">, so it runs
 * after the document is parsed — every $("#…") below is safe.
 *
 * Module map:
 *   state / api / dom / format  — the foundations, no screen knowledge
 *   router                       — screen registry + go()/rerender()
 *   components / stations / markdown / chat-options — shared render helpers
 *   chat-store                   — conversation objects + sessionStorage mirror
 *   outlook                      — the connect flow, shared by two screens
 *   onboarding / dashboard / profile / policy / book / tripdetail / chat
 *                                — the screens
 */

import { state } from "./state.js";
import { api } from "./api.js";
import { $, updateTopbarAccount } from "./dom.js";
import { go } from "./router.js";
import { adoptPreloadedChats, restoreChats } from "./chat-store.js";
import { wireSimulatedConsentModal } from "./outlook.js";
import { back, next, skip } from "./onboarding.js";

// Imported for the side effect of registering their screens with the router.
import "./dashboard.js";
import "./profile.js";
import "./policy.js";
import "./book.js";
import "./tripdetail.js";
import "./chat.js";

// --- Events & startup -------------------------------------------------------

$("#btn-next").addEventListener("click", next);
$("#btn-back").addEventListener("click", back);
$("#btn-skip").addEventListener("click", skip);

// Tapping the simulated SMS fills the code input — one tap instead of typing.
$("#sms-banner").addEventListener("click", () => {
  const input = $("#phone-code");
  if (input) {
    input.value = $("#sms-code").textContent;
    input.focus();
  }
});

wireSimulatedConsentModal();

// Tab bar: Book / Trips / Profile navigation
$("#tab-book").addEventListener("click", () => go("book"));
$("#tab-trips").addEventListener("click", () => go("dashboard"));
$("#tab-profile").addEventListener("click", () => {
  state.complaintId = null;
  go("profile");
});

async function boot() {
  if (state.token) {
    try {
      const data = await api("/api/me");
      state.account = data.account;
      state.profile = data.profile;
      state.trips = data.trips;
      state.complaints = data.complaints || [];
      updateTopbarAccount();
      // Active session: users who finished onboarding land in the dashboard,
      // everyone else continues after the login step. A chat in progress
      // (this browser tab, same session) takes priority over the dashboard.
      // Stored chats win over preloaded ones; adopt runs second and only fills
      // trips the browser has no conversation for.
      if (state.profile.onboarding_completed) {
        const resumed = restoreChats();
        adoptPreloadedChats(data.preloaded_chats);
        go(resumed ? "chat" : "dashboard");
      } else {
        adoptPreloadedChats(data.preloaded_chats);
        go("trips");
      }
      return;
    } catch {
      sessionStorage.removeItem("ja_token");
      state.token = null;
    }
  }
  go("welcome");
}

// Live status-bar clock — replaces the static "9:41" iOS mock time with the
// real current time (24h, no leading zero on the hour, like the iOS original).
// Ticks every 15s so it never lags behind by more than that.
function startStatusClock() {
  const el = document.getElementById("sb-time");
  if (!el) return;
  const tick = () => {
    const now = new Date();
    el.textContent = `${now.getHours()}:${String(now.getMinutes()).padStart(2, "0")}`;
  };
  tick();
  setInterval(tick, 15000);
}

startStatusClock();
boot();
