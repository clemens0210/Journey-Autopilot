/* The onboarding wizard: nine steps plus the Back/Next/Skip navigation.
 *
 * After each step, only the changed part of the profile is saved as a patch —
 * so the user can cancel and pick up again later at any time. The same screens
 * double as edit screens: `state.editReturn` makes Next behave as Save and
 * return to the profile instead of advancing.
 */

import { state, STEPS } from "./state.js";
import { api, saveProfile } from "./api.js";
import {
  $, el, escapeHtml, hideSmsBanner, screen, setNav, showSmsBanner, toast,
  updateTopbarAccount,
} from "./dom.js";
import { fmtDate, fmtTime, seatLabel } from "./format.js";
import { tripCardHTML } from "./components.js";
import { setupHomeStationAutocomplete } from "./stations.js";
import { go, registerScreens } from "./router.js";
import { startOutlookConnect } from "./outlook.js";
import { adoptPreloadedChats } from "./chat-store.js";
import { AUTONOMY_TO_LEVEL } from "./policy.js";

// -- 0: Welcome ---------------------------------------------------------
function welcome() {
  screen.replaceChildren(el(`
    <div class="card hero">
      <img class="hero-logo" src="/static/db-logo.png" alt="DB Logo">
      <h1>Your Journey Autopilot</h1>
      <p class="muted">Travels with you. Thinks ahead. Replans before you have to.</p>
      <ul class="feature-list">
        <li><span class="feature-icon">📡</span><span><b>Detect disruptions early</b> — risk forecasts hours in advance, not just on the platform.</span></li>
        <li><span class="feature-icon">🔀</span><span><b>Automatic replanning</b> — alternatives that match your appointments and preferences.</span></li>
        <li><span class="feature-icon">💶</span><span><b>Passenger rights, automatically</b> — compensation is detected and prepared for you.</span></li>
        <li><span class="feature-icon">✋</span><span><b>You keep the final say</b> — no booking, no message without your approval.</span></li>
      </ul>
    </div>
    <p class="muted" style="padding: 0 6px">To set things up we need your DB account (required), plus an optional phone number and calendar. All data stays local — you can view, change, or delete your profile at any time (GDPR).</p>
  `));
  setNav({ back: false, next: "Let's go" });
}

// -- 1: DB account login -------------------------------------------------------
function login() {
  screen.replaceChildren(el(`
    <div class="card">
      <h2>Sign in with your DB account <span class="badge required">Required</span></h2>
      <p class="muted">Sign in with your bahn.de account. We'll import your booked trips and your BahnBonus profile — no need to type anything in.</p>
      <form id="login-form">
        <label class="field">Email address
          <input type="email" id="login-email" autocomplete="username" required value="lucas.wild@example.com">
        </label>
        <label class="field">Password
          <input type="password" id="login-password" autocomplete="current-password" required value="demo123">
        </label>
        <p class="error" id="login-error"></p>
        <button class="btn primary block" type="submit">Sign in &amp; import trips</button>
      </form>
    </div>
  `));
  setNav({ back: true, next: "Next", nextEnabled: false });

  $("#login-form").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    $("#login-error").textContent = "";
    try {
      const data = await api("/api/auth/db-login", {
        method: "POST",
        body: { email: $("#login-email").value, password: $("#login-password").value },
      });
      state.token = data.token;
      sessionStorage.setItem("ja_token", data.token);
      state.account = data.account;
      state.profile = data.profile;
      state.trips = data.trips;
      state.complaints = data.complaints || [];
      updateTopbarAccount();
      // A fresh demo tab arrives here rather than through boot(), so this is
      // where preloaded chats get picked up for a live onboarding run.
      adoptPreloadedChats(data.preloaded_chats);
      toast(`Welcome, ${data.account.first_name}! ${data.trips.length} trips imported.`);
      // Anyone who already finished onboarding lands straight in the dashboard.
      go(data.profile.onboarding_completed ? "dashboard" : "trips");
    } catch (err) {
      $("#login-error").textContent = err.message;
    }
  });
}

// -- 2: Imported trips ------------------------------------------------------
function trips() {
  const cards = state.trips
    .map((t) => tripCardHTML(t, { foot: `Order ${t.order_number} · imported from DB account` }))
    .join("");

  screen.replaceChildren(el(`
    <div class="success-banner">✓ DB account connected — ${state.trips.length} upcoming trips imported</div>
    <div class="card" style="padding: 12px 16px">
      <div class="summary-row"><span class="k">Account</span><span class="v">${state.account.display_name}</span></div>
      <div class="summary-row"><span class="k">BahnCard</span><span class="v">${state.account.bahncard}</span></div>
      <div class="summary-row"><span class="k">BahnBonus</span><span class="v">${state.account.bahnbonus_status} · ${state.account.bahnbonus_points.toLocaleString("en-US")} points</span></div>
    </div>
    <h2 style="margin: 16px 4px 10px">Your upcoming trips</h2>
    ${cards || '<p class="muted">No upcoming trips found.</p>'}
    <p class="muted" style="padding: 0 6px">The autopilot will now monitor these trips automatically.</p>
  `));
  setNav({ back: false, next: "Next" });
}

// -- 3: Phone number ---------------------------------------------------------------
function phone() {
  const verified = state.profile?.notifications?.phone_verified;
  screen.replaceChildren(el(`
    <div class="card">
      <h2>Confirm phone number <span class="badge optional">Optional</span></h2>
      <p class="muted">Every minute counts during a disruption: with a confirmed number we can reach you with alerts and replanning suggestions via SMS/WhatsApp — even when the app is closed.</p>
      ${verified ? `
        <div class="success-banner">✓ ${state.profile.notifications.phone} is confirmed</div>
      ` : `
        <label class="field">Phone number
          <input type="tel" id="phone-input" placeholder="+49 151 12345678" autocomplete="tel" value="${state.profile?.notifications?.phone || ""}">
        </label>
        <button class="btn primary block" id="phone-send" type="button">Send code</button>
        <div id="phone-confirm-area" hidden>
          <label class="field" style="margin-top:16px">Confirmation code
            <input type="text" id="phone-code" class="code-input" inputmode="numeric" maxlength="4" placeholder="····">
          </label>
          <button class="btn primary block" id="phone-verify" type="button">Confirm</button>
        </div>
        <p class="error" id="phone-error"></p>
      `}
    </div>
  `));
  setNav({ back: true, next: "Next", skip: verified ? null : "Skip", nextEnabled: !!verified });
  if (verified) return;

  $("#phone-send").addEventListener("click", async () => {
    $("#phone-error").textContent = "";
    try {
      const data = await api("/api/verify/phone/start", {
        method: "POST", body: { phone: $("#phone-input").value },
      });
      $("#phone-confirm-area").hidden = false;
      $("#phone-code").focus();
      showSmsBanner(data.demo_code);
    } catch (err) {
      $("#phone-error").textContent = err.message;
    }
  });

  $("#phone-verify").addEventListener("click", async () => {
    $("#phone-error").textContent = "";
    try {
      const data = await api("/api/verify/phone/confirm", {
        method: "POST", body: { code: $("#phone-code").value },
      });
      state.profile = data.profile;
      hideSmsBanner();
      toast("✓ Number confirmed");
      phone(); // re-render the screen with success state
    } catch (err) {
      $("#phone-error").textContent = err.message;
    }
  });
}

// -- 4: Outlook calendar -----------------------------------------------------------
function outlook() {
  // Deliberately NOT state.profile.connections.outlook: a demo pre-connects
  // Outlook so the warm-up runs the real calendar flow, but the wizard should
  // still show the sign-in from scratch and only flip to "connected" once the
  // presenter runs it here (which reuses the cached login instantly).
  const connected = state.outlookConnectedThisStep;
  const events = state.outlookEvents.map((e) => `
    <div class="event-row">
      <span class="event-when">${fmtDate(e.start).slice(0, 10)}<br>${fmtTime(e.start)}</span>
      <span><span class="event-title">${escapeHtml(e.title)}</span>
        <span class="event-loc">${escapeHtml(e.location)}</span>
        ${e.hard_constraint ? '<span class="event-hard">Hard deadline</span>' : ""}
      </span>
    </div>
  `).join("");

  screen.replaceChildren(el(`
    <div class="card">
      <h2>Connect Outlook calendar <span class="badge optional">Optional</span></h2>
      <p class="muted">The autopilot reads your appointments to protect hard deadlines (e.g. on-site client meetings) during every replan — and adds new connections directly to your calendar.</p>
      ${connected ? `
        <div class="success-banner">✓ Connected${state.profile?.connections?.outlook_email ? ` as ${state.profile.connections.outlook_email}` : " — Outlook calendar"}</div>
        ${events ? `<h2 style="font-size:14px">Detected events</h2>${events}` : ""}
        <button class="btn danger block" id="outlook-disconnect" type="button" style="margin-top:12px">Disconnect</button>
      ` : `
        <button class="btn primary block" id="outlook-connect" type="button">Sign in with Microsoft</button>
        <div id="outlook-device-flow"></div>
      `}
    </div>
  `));
  setNav({ back: true, next: "Next", skip: connected ? null : "Skip" });

  if (connected) {
    $("#outlook-disconnect").addEventListener("click", async () => {
      const data = await api("/api/connect/outlook", { method: "DELETE" });
      state.profile = data.profile;
      state.outlookEvents = [];
      state.outlookConnectedThisStep = false;
      outlook();
    });
  } else {
    $("#outlook-connect").addEventListener("click", () => startOutlookConnect());
  }
}

// -- 5: Travel preferences ---------------------------------------------------------------
function preferences() {
  const p = state.profile.preferences;
  const h = state.profile.home;
  const mob = state.profile.mobility || {};
  screen.replaceChildren(el(`
    <div class="card">
      <h2>Your travel preferences</h2>
      <p class="muted">These guide all replanning suggestions. You can change everything later in your profile.</p>

      <label class="field">Class</label>
      <div class="choices" data-group="travel_class">
        <button type="button" class="choice" data-value="2"><span class="choice-title">2nd class</span><span class="choice-sub">Standard</span></button>
        <button type="button" class="choice" data-value="1"><span class="choice-title">1st class</span><span class="choice-sub">More peace &amp; space</span></button>
      </div>

      <label class="field" style="margin-top:16px">Seat</label>
      <div class="choices cols-3" data-group="seat_location">
        <button type="button" class="choice" data-value="window"><span class="choice-title">Window</span></button>
        <button type="button" class="choice" data-value="aisle"><span class="choice-title">Aisle</span></button>
        <button type="button" class="choice" data-value="any"><span class="choice-title">No preference</span></button>
      </div>
      <div class="choices cols-3" style="margin-top:9px" data-group="seat_area">
        <button type="button" class="choice" data-value="open_plan"><span class="choice-title">Open seating</span></button>
        <button type="button" class="choice" data-value="compartment"><span class="choice-title">Compartment</span></button>
        <button type="button" class="choice" data-value="any"><span class="choice-title">No preference</span></button>
      </div>

      <div class="switch-row" style="margin-top:8px">
        <span>Prefer quiet zone<span class="sub">Reserve in the quiet car when possible</span></span>
        <label class="switch"><input type="checkbox" id="quiet-zone" ${p.quiet_zone ? "checked" : ""}><span class="track"></span></label>
      </div>
    </div>

    <div class="card">
      <h2>Fast or comfortable?</h2>
      <p class="muted">How should the autopilot weigh trade-offs during a disruption?</p>
      <div class="slider-row">
        <span class="end">🛋️ Maximum comfort</span>
        <input type="range" id="speed-comfort" min="0" max="100" step="5" value="${p.speed_vs_comfort}">
        <span class="end right">⚡ Fastest arrival</span>
      </div>
      <div class="slider-value" id="speed-comfort-label"></div>

      <label class="field" style="margin-top:14px">Maximum transfers when replanning</label>
      <div class="choices cols-3" data-group="max_transfers">
        <button type="button" class="choice" data-value="0"><span class="choice-title">Direct</span></button>
        <button type="button" class="choice" data-value="2"><span class="choice-title">Up to 2</span></button>
        <button type="button" class="choice" data-value="9"><span class="choice-title">No preference</span></button>
      </div>
    </div>
    ${state.editReturn ? `
      <div class="card">
        <h2>Home &amp; hard limits</h2>
        <label class="field">Home station
          <span class="hint">Search uses live DB data once db_service is running</span>
          <span class="autocomplete">
            <input type="text" id="home-station" placeholder="e.g. München Hbf" autocomplete="off" value="${h.home_station?.name || ""}">
            <span id="station-suggestions"></span>
          </span>
        </label>

        <label class="field">Latest arrival home
          <span class="hint">After this, the autopilot prefers to suggest a hotel</span>
          <input type="time" id="latest-arrival" value="${h.latest_arrival_home}">
        </label>

        <div class="switch-row">
          <span>Hotel stay okay<span class="sub">A hotel may be suggested if you're stranded</span></span>
          <label class="switch"><input type="checkbox" id="hotel-ok" ${h.hotel_ok ? "checked" : ""}><span class="track"></span></label>
        </div>
        <div class="switch-row">
          <span>Taxi for the last mile okay<span class="sub">If the last connection falls through</span></span>
          <label class="switch"><input type="checkbox" id="taxi-ok" ${h.taxi_ok ? "checked" : ""}><span class="track"></span></label>
        </div>
        <div class="switch-row">
          <span>🚗 Car sharing okay (Flinkster)<span class="sub">Suggest a rental car when trains are disrupted</span></span>
          <label class="switch"><input type="checkbox" id="car-sharing-ok" ${mob.car_sharing_ok !== false ? "checked" : ""}><span class="track"></span></label>
        </div>
        <div class="switch-row">
          <span>🚲 Bike sharing okay (Call-a-Bike)<span class="sub">Suggest an e-bike for short last-mile legs</span></span>
          <label class="switch"><input type="checkbox" id="bike-sharing-ok" ${mob.bike_sharing_ok !== false ? "checked" : ""}><span class="track"></span></label>
        </div>
      </div>
    ` : ""}
  `));
  setNav({ back: true, next: state.editReturn ? "Save" : "Next" });

  // Initialize tile groups
  const groups = { travel_class: String(p.travel_class), seat_location: p.seat_location, seat_area: p.seat_area, max_transfers: String(p.max_transfers) };
  screen.querySelectorAll(".choices").forEach((box) => {
    const group = box.dataset.group;
    box.querySelectorAll(".choice").forEach((btn) => {
      if (btn.dataset.value === groups[group]) btn.classList.add("selected");
      btn.addEventListener("click", () => {
        box.querySelectorAll(".choice").forEach((b) => b.classList.remove("selected"));
        btn.classList.add("selected");
      });
    });
  });

  const sliderLabel = () => {
    const v = Number($("#speed-comfort").value);
    $("#speed-comfort-label").textContent =
      v < 25 ? "Comfort first — arrive later, but relaxed"
      : v < 50 ? "Leaning comfort, but speed still matters"
      : v < 75 ? "Leaning speed, but comfort still matters"
      : "Speed first — get there as fast as possible";
  };
  $("#speed-comfort").addEventListener("input", sliderLabel);
  sliderLabel();
  if (state.editReturn) setupHomeStationAutocomplete(h);
}

// -- 6: Home & constraints --------------------------------------------------------------
function home() {
  const h = state.profile.home;
  const mob = state.profile.mobility || {};
  screen.replaceChildren(el(`
    <div class="card">
      <h2>Home &amp; hard limits</h2>
      <p class="muted">This tells the autopilot how far a detour is allowed to go — and when a hotel is the better option over a night on the train.</p>

      <label class="field">Home station
        <span class="hint">Search uses live DB data once db_service is running</span>
        <span class="autocomplete">
          <input type="text" id="home-station" placeholder="e.g. München Hbf" autocomplete="off" value="${h.home_station?.name || ""}">
          <span id="station-suggestions"></span>
        </span>
      </label>

      <label class="field">Latest arrival home
        <span class="hint">After this, the autopilot prefers to suggest a hotel</span>
        <input type="time" id="latest-arrival" value="${h.latest_arrival_home}">
      </label>

      <div class="switch-row">
        <span>Hotel stay okay<span class="sub">A hotel may be suggested if you're stranded</span></span>
        <label class="switch"><input type="checkbox" id="hotel-ok" ${h.hotel_ok ? "checked" : ""}><span class="track"></span></label>
      </div>
      <div class="switch-row">
        <span>Taxi for the last mile okay<span class="sub">If the last connection falls through</span></span>
        <label class="switch"><input type="checkbox" id="taxi-ok" ${h.taxi_ok ? "checked" : ""}><span class="track"></span></label>
      </div>
      <div class="switch-row">
        <span>🚗 Car sharing okay (Flinkster)<span class="sub">Suggest a rental car when trains are disrupted</span></span>
        <label class="switch"><input type="checkbox" id="car-sharing-ok" ${mob.car_sharing_ok !== false ? "checked" : ""}><span class="track"></span></label>
      </div>
      <div class="switch-row">
        <span>🚲 Bike sharing okay (Call-a-Bike)<span class="sub">Suggest an e-bike for short last-mile legs</span></span>
        <label class="switch"><input type="checkbox" id="bike-sharing-ok" ${mob.bike_sharing_ok !== false ? "checked" : ""}><span class="track"></span></label>
      </div>
    </div>
  `));
  setNav({ back: true, next: state.editReturn ? "Save" : "Next" });

  setupHomeStationAutocomplete(h);
}

// -- 7: Notifications & autonomy ----------------------------------------------------------
function notifications() {
  const n = state.profile.notifications;
  const channels = new Set(n.channels);
  screen.replaceChildren(el(`
    <div class="card">
      <h2>Notifications</h2>
      <div class="switch-row">
        <span>Push notifications<span class="sub">In the app, always up to date</span></span>
        <label class="switch"><input type="checkbox" data-channel="push" ${channels.has("push") ? "checked" : ""}><span class="track"></span></label>
      </div>
      <div class="switch-row">
        <span>WhatsApp / SMS<span class="sub">${n.phone_verified ? `To ${n.phone}` : "Delivered once your phone number is confirmed"}</span></span>
        <label class="switch"><input type="checkbox" data-channel="whatsapp" ${channels.has("whatsapp") ? "checked" : ""}><span class="track"></span></label>
      </div>
      <div class="switch-row">
        <span>Email<span class="sub">Summaries &amp; receipts</span></span>
        <label class="switch"><input type="checkbox" data-channel="email" ${channels.has("email") ? "checked" : ""}><span class="track"></span></label>
      </div>
      <label class="field" style="margin-top:12px">Quiet hours <span class="hint">No notifications except emergencies</span></label>
      <div style="display:flex; gap:10px; align-items:center">
        <input type="time" id="quiet-from" value="${n.quiet_hours.from}"> <span class="muted">to</span>
        <input type="time" id="quiet-to" value="${n.quiet_hours.to}">
      </div>
    </div>

    <div class="card">
      <h2>How independent should the autopilot be?</h2>
      <div class="choices cols-1" data-group="autonomy">
        <button type="button" class="choice" data-value="notify_only">
          <span class="choice-title">🔔 Just notify me</span>
          <span class="choice-sub">The autopilot warns and suggests — you handle everything yourself.</span>
        </button>
        <button type="button" class="choice" data-value="approve_each">
          <span class="choice-title">✋ Approve every action <i>(recommended)</i></span>
          <span class="choice-sub">Rebookings, messages &amp; claims only happen after your okay.</span>
        </button>
        <button type="button" class="choice" data-value="auto_within_limits">
          <span class="choice-title">🤖 Automatic within limits</span>
          <span class="choice-sub">Free rebookings happen automatically, everything else needs approval.</span>
        </button>
      </div>
    </div>
  `));
  setNav({ back: true, next: state.editReturn ? "Save" : "Next" });

  const box = screen.querySelector('[data-group="autonomy"]');
  box.querySelectorAll(".choice").forEach((btn) => {
    if (btn.dataset.value === state.profile.autonomy) btn.classList.add("selected");
    btn.addEventListener("click", () => {
      box.querySelectorAll(".choice").forEach((b) => b.classList.remove("selected"));
      btn.classList.add("selected");
    });
  });
}

// -- 8: Summary ------------------------------------------------------------------------
function summary() {
  const p = state.profile;
  const pref = p.preferences;
  const autonomyLabel = {
    notify_only: "Just notify me",
    approve_each: "Approve every action",
    auto_within_limits: "Automatic within limits",
  }[p.autonomy];

  screen.replaceChildren(el(`
    <div class="card">
      <h2>All set? 🚦</h2>
      <div class="summary-row"><span class="k">DB account</span><span class="v">✓ ${state.account.display_name}</span></div>
      <div class="summary-row"><span class="k">Imported trips</span><span class="v">${state.trips.length}</span></div>
      <div class="summary-row"><span class="k">Phone number</span><span class="v">${p.notifications.phone_verified ? "✓ " + p.notifications.phone : "— skipped"}</span></div>
      <div class="summary-row"><span class="k">Outlook calendar</span><span class="v">${p.connections.outlook ? (p.connections.outlook_email ? "✓ " + p.connections.outlook_email : "✓ connected") : "— skipped"}</span></div>
      <div class="summary-row"><span class="k">Class / seat</span><span class="v">${pref.travel_class === 1 ? "1st" : "2nd"} class · ${seatLabel(pref)}</span></div>
      <div class="summary-row"><span class="k">Speed vs. comfort</span><span class="v">${pref.speed_vs_comfort} / 100</span></div>
      <div class="summary-row"><span class="k">Max. transfers</span><span class="v">${pref.max_transfers >= 9 ? "no preference" : pref.max_transfers}</span></div>
      <div class="summary-row"><span class="k">Home station</span><span class="v">${p.home.home_station?.name || "—"}</span></div>
      <div class="summary-row"><span class="k">Latest arrival home</span><span class="v">${p.home.latest_arrival_home}</span></div>
      <div class="summary-row"><span class="k">Autonomy</span><span class="v">${autonomyLabel}</span></div>
    </div>
    <p class="muted" style="padding: 0 6px">Once you finish, the autopilot will start monitoring your imported trips. Every setting can be changed later in your profile.</p>
  `));
  setNav({ back: true, next: "Finish onboarding 🚀" });
}

// --- Navigation: save the step, then move on --------------------------------

async function persistCurrentStep() {
  switch (state.step) {
    case "preferences": {
      const groupVal = (g) => screen.querySelector(`[data-group="${g}"] .choice.selected`)?.dataset.value;
      const patch = {
        preferences: {
          travel_class: Number(groupVal("travel_class")),
          seat_location: groupVal("seat_location"),
          seat_area: groupVal("seat_area"),
          quiet_zone: $("#quiet-zone").checked,
          speed_vs_comfort: Number($("#speed-comfort").value),
          max_transfers: Number(groupVal("max_transfers")),
        },
      };
      if ($("#home-station")) {
        patch.home = {
          home_station: screen._getHomeStation(),
          latest_arrival_home: $("#latest-arrival").value,
          hotel_ok: $("#hotel-ok").checked,
          taxi_ok: $("#taxi-ok").checked,
        };
        patch.mobility = {
          car_sharing_ok: $("#car-sharing-ok").checked,
          bike_sharing_ok: $("#bike-sharing-ok").checked,
        };
      }
      await saveProfile(patch);
      break;
    }
    case "home":
      await saveProfile({
        home: {
          home_station: screen._getHomeStation(),
          latest_arrival_home: $("#latest-arrival").value,
          hotel_ok: $("#hotel-ok").checked,
          taxi_ok: $("#taxi-ok").checked,
        },
        mobility: {
          car_sharing_ok: $("#car-sharing-ok").checked,
          bike_sharing_ok: $("#bike-sharing-ok").checked,
        },
      });
      break;
    case "notifications": {
      const channels = [...screen.querySelectorAll("[data-channel]")]
        .filter((c) => c.checked).map((c) => c.dataset.channel);
      const autonomy = screen.querySelector('[data-group="autonomy"] .choice.selected')?.dataset.value;
      await saveProfile({
        notifications: {
          channels,
          quiet_hours: { from: $("#quiet-from").value, to: $("#quiet-to").value },
        },
        autonomy,
        // Seed the policy/veto global level from the onboarding choice; the
        // "Automation & veto" screen can refine it per action later.
        ...(autonomy ? { policy: { global_autonomy_level: AUTONOMY_TO_LEVEL[autonomy] } } : {}),
      });
      break;
    }
  }
}

export async function next() {
  try {
    await persistCurrentStep();
  } catch (err) {
    toast(`⚠️ ${err.message}`);
    return;
  }

  if (state.step === "summary") {
    await api("/api/onboarding/complete", { method: "POST" });
    state.profile.onboarding_completed = true;
    toast("🎉 Onboarding complete — have a great trip!");
    go("dashboard");
    return;
  }
  if (state.editReturn) {
    const ret = state.editReturn;
    state.editReturn = null;
    toast("✓ Saved");
    go(ret);
    return;
  }
  go(STEPS[STEPS.indexOf(state.step) + 1]);
}

export function back() {
  if (state.editReturn) { const ret = state.editReturn; state.editReturn = null; go(ret); return; }
  const idx = STEPS.indexOf(state.step);
  // From the phone step back to the trip overview, not to login
  go(STEPS[Math.max(0, idx - 1)]);
}

// Skip behaves like Next without saving: an optional step the user passed on.
export function skip() {
  if (state.editReturn) { const ret = state.editReturn; state.editReturn = null; go(ret); return; }
  go(STEPS[STEPS.indexOf(state.step) + 1]);
}

registerScreens({
  welcome, login, trips, phone, outlook, preferences, home, notifications, summary,
});
