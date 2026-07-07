/* Journey Autopilot — onboarding wizard in DB Navigator style.
 *
 * A small state-based wizard with no framework: render(step) draws the
 * screen, and the navbar (Back/Skip/Next) is configured per step.
 * After each step, only the changed part of the profile is saved as a
 * patch — so you can cancel and pick up again later at any time.
 */

"use strict";

// ---------------------------------------------------------------------------
// State & API
// ---------------------------------------------------------------------------

const state = {
  token: sessionStorage.getItem("ja_token") || null,
  account: null,
  profile: null,
  trips: [],
  outlookEvents: [],
  step: "welcome",
  editReturn: null, // "dashboard" / "profile" = return target after editing
  phone: { sent: false, verifiedThisSession: false },
  chat: null, // { sessionId, trip, messages: [...], busy } when a trip chat is open
};

const STEPS = [
  "welcome", "login", "trips", "phone", "outlook",
  "preferences", "home", "notifications", "summary",
];

async function api(path, { method = "GET", body } = {}) {
  const headers = { "Content-Type": "application/json" };
  if (state.token) headers["Authorization"] = `Bearer ${state.token}`;
  const resp = await fetch(path, {
    method,
    headers,
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const data = await resp.json().catch(() => ({}));
  if (!resp.ok) throw new Error(data.detail || `Error ${resp.status}`);
  return data;
}

async function saveProfile(patch) {
  const data = await api("/api/profile", { method: "PUT", body: patch });
  state.profile = data.profile;
}

// ---------------------------------------------------------------------------
// DOM helpers
// ---------------------------------------------------------------------------

const $ = (sel) => document.querySelector(sel);
const screen = $("#screen");

function el(html) {
  const t = document.createElement("template");
  t.innerHTML = html.trim();
  return t.content;
}

// Escape user/agent text before injecting it into chat bubbles.
function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

// Inline SVGs in DB Navigator style — brand mark and icons for the trip cards.
const SVG = {
  dbLogo: `<svg viewBox="0 0 64 44"><rect width="64" height="44" rx="9" fill="#EC0016"/><rect x="5" y="5" width="54" height="34" rx="5" fill="#fff"/><text x="32" y="33" font-size="27" font-weight="900" fill="#EC0016" text-anchor="middle" font-family="'Arial Black',Arial,sans-serif">DB</text></svg>`,
  origin: `<svg class="ic" viewBox="0 0 24 24" fill="none"><circle cx="12" cy="12" r="7" stroke="currentColor" stroke-width="2"/><circle cx="12" cy="12" r="2.6" fill="currentColor"/></svg>`,
  pin: `<svg class="ic" viewBox="0 0 24 24" fill="none"><path d="M12 22s7-6.2 7-12a7 7 0 1 0-14 0c0 5.8 7 12 7 12Z" stroke="currentColor" stroke-width="2" stroke-linejoin="round"/><circle cx="12" cy="10" r="2.4" fill="currentColor"/></svg>`,
  calendar: `<svg class="ic" viewBox="0 0 24 24" fill="none"><rect x="3.5" y="5" width="17" height="16" rx="2.5" stroke="currentColor" stroke-width="2"/><path d="M3.5 9.5h17M8 3v4M16 3v4" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg>`,
  seat: `<svg class="ic" viewBox="0 0 24 24" fill="none"><path d="M7 4v8a2 2 0 0 0 2 2h6M7 20v-4M17 20v-7" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>`,
  bell: `<svg class="ic" viewBox="0 0 24 24" fill="none"><path d="M6 9a6 6 0 1 1 12 0c0 5 2 6 2 6H4s2-1 2-6Z" stroke="currentColor" stroke-width="2" stroke-linejoin="round"/><path d="M10 19a2 2 0 0 0 4 0" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg>`,
  download: `<svg class="ic" viewBox="0 0 24 24" fill="none"><path d="M12 4v10m0 0 4-4m-4 4-4-4M5 20h14" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>`,
  qr: `<svg viewBox="0 0 24 24" fill="#111"><path d="M3 3h7v7H3V3Zm2 2v3h3V5H5Zm9-2h7v7h-7V3Zm2 2v3h3V5h-3ZM3 14h7v7H3v-7Zm2 2v3h3v-3H5Zm11-2h2v2h-2v-2Zm3 0h2v2h-2v-2Zm-3 3h2v2h-2v-2Zm0 3h2v2h-2v-2Zm3-3h2v2h-2v-2Zm0 3h2v2h-2v-2Z"/></svg>`,
};

// A trip card in DB Navigator layout: DB logo + train, purpose of travel,
// origin/destination with dot/pin markers, date/time, and a footer status.
// When `index` is given the card becomes clickable (opens the trip chat).
function tripCardHTML(t, { foot, live = false, index = null } = {}) {
  const clickable = index !== null;
  return `
    <div class="trip-card${clickable ? " clickable" : ""}"${clickable ? ` data-trip-index="${index}"` : ""}>
      <div class="trip-head">
        <span class="db-logo">${SVG.dbLogo}</span>
        <span class="train">${t.train}</span>
        <span class="trip-head-right">
          <span>${t.travel_class}. Kl.</span>
          <span class="qr">${SVG.qr}</span>
        </span>
      </div>
      <div class="trip-fare">${t.purpose}</div>
      <hr class="trip-divider">
      <div class="trip-body">
        <div class="route">
          <span class="marker">${SVG.origin}</span><span class="station">${t.origin}</span>
          <span class="dots"><i></i><i></i><i></i></span><span></span>
          <span class="marker">${SVG.pin}</span><span class="station">${t.destination}</span>
        </div>
        <div class="trip-meta-row">${SVG.calendar} ${fmtDate(t.planned_departure)} · ${fmtTime(t.planned_departure)} – ${fmtTime(t.planned_arrival)}</div>
        <div class="trip-meta-row">${SVG.seat} ${t.platform} · ${t.coach}, ${t.seat}</div>
      </div>
      ${foot ? `<div class="trip-foot ${live ? "live" : ""}">${live ? SVG.bell : SVG.download} ${foot}</div>` : ""}
    </div>`;
}

let toastTimer = null;
function toast(msg, ms = 4200) {
  const node = $("#toast");
  node.textContent = msg;
  node.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { node.hidden = true; }, ms);
}

// Policy / veto gate — display labels and the onboarding-autonomy mapping.
const POLICY_LEVEL_LABEL = {
  conservative: "Conservative — asks before everything",
  balanced: "Balanced",
  aggressive: "Automatic within limits",
};
const AUTONOMY_TO_LEVEL = {
  notify_only: "conservative",
  approve_each: "balanced",
  auto_within_limits: "aggressive",
};
function policyOverrideCount(p) {
  const wt = (p.policy && p.policy.write_tools) || {};
  return Object.values(wt).filter((v) => v && v !== "default").length;
}

// Display labels for the internally stored profile values
const LABELS = {
  fenster: "Window", gang: "Aisle", egal: "No preference",
  grossraum: "Open seating", abteil: "Compartment",
};
const seatLabel = (pref) =>
  `${LABELS[pref.seat_location]}, ${LABELS[pref.seat_area]}${pref.quiet_zone ? ", quiet zone" : ""}`;

const fmtDate = (iso) => new Date(iso).toLocaleDateString("en-US", {
  weekday: "short", day: "2-digit", month: "2-digit", year: "numeric",
});
const fmtTime = (iso) => new Date(iso).toLocaleTimeString("en-US", {
  hour: "2-digit", minute: "2-digit",
});

function setNav({ back = true, next = "Next", skip = null, nextEnabled = true } = {}) {
  $("#tabbar").hidden = true; // tab bar only in the dashboard (see renderers.dashboard)
  $("#navbar").hidden = false;
  $("#btn-back").style.visibility = back ? "visible" : "hidden";
  $("#btn-next").textContent = next;
  $("#btn-next").disabled = !nextEnabled;
  $("#btn-skip").hidden = !skip;
  if (skip) $("#btn-skip").textContent = skip;
}

function setProgress(step) {
  const idx = STEPS.indexOf(step);
  const wizard = idx > 0; // Welcome & dashboard have no progress bar
  $("#progress").hidden = !wizard || step === "dashboard";
  if (wizard) {
    $("#progress-fill").style.width = `${(idx / (STEPS.length - 1)) * 100}%`;
    $("#progress-label").textContent = `Step ${idx} of ${STEPS.length - 1}`;
  }
}

function setupHomeStationAutocomplete(home) {
  const input = $("#home-station");
  const sugBox = $("#station-suggestions");
  if (!input || !sugBox) return;

  let selected = home.home_station || null;
  let debounce = null;

  input.addEventListener("input", () => {
    selected = null;
    clearTimeout(debounce);
    debounce = setTimeout(async () => {
      const q = input.value.trim();
      sugBox.innerHTML = "";
      if (q.length < 2) return;
      const data = await api(`/api/stations?query=${encodeURIComponent(q)}`).catch(() => ({ stations: [] }));
      if (!data.stations.length) return;
      const list = document.createElement("div");
      list.className = "suggestions";
      data.stations.forEach((s) => {
        const b = document.createElement("button");
        b.type = "button";
        b.textContent = data.source === "db-live" ? `🟢 ${s.name}` : s.name;
        b.addEventListener("click", () => {
          selected = s;
          input.value = s.name;
          sugBox.innerHTML = "";
        });
        list.appendChild(b);
      });
      sugBox.replaceChildren(list);
    }, 250);
  });

  screen._getHomeStation = () => selected || (input.value.trim() ? { id: null, name: input.value.trim() } : null);
}

function updateTopbarAccount() {
  const node = $("#topbar-account");
  if (state.account) {
    node.textContent = state.account.first_name;
    node.hidden = false;
  } else {
    node.hidden = true;
  }
}

function setActiveTab(tab) {
  document.querySelectorAll(".tab").forEach((t) => t.classList.remove("active"));
  const el = tab === "trips" ? $("#tab-trips") : tab === "profile" ? $("#tab-profile") : null;
  if (el) el.classList.add("active");
}

// ---------------------------------------------------------------------------
// Screens
// ---------------------------------------------------------------------------

const renderers = {

  // -- 0: Welcome ---------------------------------------------------------
  welcome() {
    screen.replaceChildren(el(`
      <div class="card hero">
        <svg class="hero-logo" viewBox="0 0 64 44"><rect width="64" height="44" rx="9" fill="#EC0016"/><rect x="5" y="5" width="54" height="34" rx="5" fill="#fff"/><text x="32" y="33" font-size="27" font-weight="900" fill="#EC0016" text-anchor="middle" font-family="'Arial Black',Arial,sans-serif">DB</text></svg>
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
  },

  // -- 1: DB account login -------------------------------------------------------
  login() {
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
        <div class="demo-hint">🎓 <b>Demo mode:</b> DB login is simulated (no official DB API). Credentials: <code>lucas.wild@example.com</code> / <code>demo123</code></div>
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
        updateTopbarAccount();
        toast(`Welcome, ${data.account.first_name}! ${data.trips.length} trips imported.`);
        // Anyone who already finished onboarding lands straight in the dashboard.
        go(data.profile.onboarding_completed ? "dashboard" : "trips");
      } catch (err) {
        $("#login-error").textContent = err.message;
      }
    });
  },

  // -- 2: Imported trips ------------------------------------------------------
  trips() {
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
  },

  // -- 3: Phone number ---------------------------------------------------------------
  phone() {
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
          <div class="demo-hint">🎓 <b>Demo mode:</b> No real SMS is sent — the code is shown as a notification.</div>
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
        toast(`📱 SMS to ${data.phone} (demo): your code is ${data.demo_code}`, 10000);
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
        toast("✓ Number confirmed");
        renderers.phone(); // re-render the screen with success state
      } catch (err) {
        $("#phone-error").textContent = err.message;
      }
    });
  },

  // -- 4: Outlook calendar -----------------------------------------------------------
  outlook() {
    const connected = state.profile?.connections?.outlook;
    const events = state.outlookEvents.map((e) => `
      <div class="event-row">
        <span class="event-when">${fmtDate(e.start).slice(0, 10)}<br>${fmtTime(e.start)}</span>
        <span><span class="event-title">${e.title}</span>
          <span class="event-loc">${e.location}</span>
          ${e.hard_constraint ? '<span class="event-hard">Hard deadline</span>' : ""}
        </span>
      </div>
    `).join("");

    screen.replaceChildren(el(`
      <div class="card">
        <h2>Connect Outlook calendar <span class="badge optional">Optional</span></h2>
        <p class="muted">The autopilot reads your appointments to protect hard deadlines (e.g. on-site client meetings) during every replan — and adds new connections directly to your calendar.</p>
        ${connected ? `
          <div class="success-banner">✓ Outlook calendar connected</div>
          ${events ? `<h2 style="font-size:14px">Detected events</h2>${events}` : ""}
          <button class="btn danger block" id="outlook-disconnect" type="button" style="margin-top:12px">Disconnect</button>
        ` : `
          <button class="btn primary block" id="outlook-connect" type="button">Sign in with Microsoft</button>
          <div id="outlook-device-flow"></div>
          <div class="demo-hint">🎓 <b>Demo mode:</b> Without a configured Microsoft Entra app, login is simulated — sample events will be loaded.</div>
        `}
      </div>
    `));
    setNav({ back: true, next: "Next", skip: connected ? null : "Skip" });

    if (connected) {
      $("#outlook-disconnect").addEventListener("click", async () => {
        const data = await api("/api/connect/outlook", { method: "DELETE" });
        state.profile = data.profile;
        state.outlookEvents = [];
        renderers.outlook();
      });
    } else {
      $("#outlook-connect").addEventListener("click", () => startOutlookConnect());
    }
  },

  // -- 5: Travel preferences ---------------------------------------------------------------
  preferences() {
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
          <button type="button" class="choice" data-value="fenster"><span class="choice-title">Window</span></button>
          <button type="button" class="choice" data-value="gang"><span class="choice-title">Aisle</span></button>
          <button type="button" class="choice" data-value="egal"><span class="choice-title">No preference</span></button>
        </div>
        <div class="choices cols-3" style="margin-top:9px" data-group="seat_area">
          <button type="button" class="choice" data-value="grossraum"><span class="choice-title">Open seating</span></button>
          <button type="button" class="choice" data-value="abteil"><span class="choice-title">Compartment</span></button>
          <button type="button" class="choice" data-value="egal"><span class="choice-title">No preference</span></button>
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
  },

  // -- 6: Home & constraints --------------------------------------------------------------
  home() {
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
  },

  // -- 7: Notifications & autonomy ----------------------------------------------------------
  notifications() {
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
          <span>WhatsApp / SMS<span class="sub">${n.phone_verified ? `To ${n.phone}` : "Requires a confirmed phone number"}</span></span>
          <label class="switch"><input type="checkbox" data-channel="whatsapp" ${channels.has("whatsapp") ? "checked" : ""} ${n.phone_verified ? "" : "disabled"}><span class="track"></span></label>
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
  },

  // -- 8: Summary ------------------------------------------------------------------------
  summary() {
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
        <div class="summary-row"><span class="k">Outlook calendar</span><span class="v">${p.connections.outlook ? "✓ connected" : "— skipped"}</span></div>
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
  },

  // -- Dashboard -------------------------------------------------------------------------
  dashboard() {
    const p = state.profile;
    const pref = p.preferences;
    const nextTrip = state.trips[0];
    const cards = state.trips
      .map((t, i) => tripCardHTML(t, { foot: "Monitored by the autopilot · tap to chat", live: true, index: i }))
      .join("");

    screen.replaceChildren(el(`
      <div class="dash-greeting">
        <h1>Hi ${state.account.first_name} 👋</h1>
        <p class="muted">${nextTrip
          ? `Your next trip starts ${fmtDate(nextTrip.planned_departure)} at ${fmtTime(nextTrip.planned_departure)} — the autopilot is watching.`
          : "No upcoming trips — the autopilot is ready."}</p>
      </div>

      <div class="section-title"><h2>Monitored trips</h2></div>
      ${cards || '<div class="card"><p class="muted">No trips imported.</p></div>'}

      <div class="section-title">
        <h2>Your profile</h2>
        <div class="section-actions">
          <button id="edit-prefs" type="button">Edit profile</button>
        </div>
      </div>
      <div class="card" style="padding: 12px 16px">
        <div class="summary-row"><span class="k">Class / seat</span><span class="v">${pref.travel_class === 1 ? "1st" : "2nd"} class · ${seatLabel(pref)}</span></div>
        <div class="summary-row"><span class="k">Speed vs. comfort</span><span class="v">${pref.speed_vs_comfort} / 100</span></div>
        <div class="summary-row"><span class="k">Home station</span><span class="v">${p.home.home_station?.name || "—"}</span></div>
        <div class="summary-row"><span class="k">Autonomy</span><span class="v">${{ notify_only: "Just notify me", approve_each: "Approve every action", auto_within_limits: "Automatic within limits" }[p.autonomy]}</span></div>
      </div>

      <div class="section-title"><h2>Automation &amp; veto</h2><button id="edit-policy" type="button">Manage</button></div>
      <div class="card" style="padding: 12px 16px">
        <div class="summary-row"><span class="k">Autonomy level</span><span class="v">${POLICY_LEVEL_LABEL[(p.policy && p.policy.global_autonomy_level) || "balanced"]}</span></div>
        <div class="summary-row"><span class="k">Pinned action rules</span><span class="v">${policyOverrideCount(p)}</span></div>
      </div>

      <div class="section-title"><h2>Connections</h2><button id="edit-connections" type="button">Manage</button></div>
      <div class="card" style="padding: 12px 16px">
        <div class="summary-row"><span class="k">DB account</span><span class="v">✓ ${state.account.email}</span></div>
        <div class="summary-row"><span class="k">Phone number</span><span class="v">${p.notifications.phone_verified ? "✓ " + p.notifications.phone : "not confirmed"}</span></div>
        <div class="summary-row"><span class="k">Outlook</span><span class="v">${p.connections.outlook ? "✓ connected" : "not connected"}</span></div>
      </div>

      <div class="card">
        <p class="muted" style="margin-top:0">Your data belongs to you: with one click you can permanently delete your profile, connections, and imported trips (GDPR Art. 17).</p>
        <button class="btn danger block" id="delete-profile" type="button">Delete profile &amp; data</button>
      </div>
    `));
    setNav({ back: false, next: "Next" });
    $("#navbar").hidden = true;
    $("#progress").hidden = true;
    $("#tabbar").hidden = false; // mock tab bar of the DB Navigator
    setActiveTab("trips");

    // Clicking a monitored trip opens the chat that runs the orchestrator demo.
    screen.querySelectorAll(".trip-card.clickable").forEach((cardEl) => {
      cardEl.addEventListener("click", () => openChat(state.trips[Number(cardEl.dataset.tripIndex)]));
    });

    $("#edit-prefs").addEventListener("click", () => { state.editReturn = "dashboard"; go("preferences"); });
    $("#edit-connections").addEventListener("click", () => { state.editReturn = "dashboard"; go("connections"); });
    $("#edit-policy").addEventListener("click", () => go("policy"));
    $("#delete-profile").addEventListener("click", async () => {
      if (!confirm("Really delete all data? This cannot be undone.")) return;
      await api("/api/profile", { method: "DELETE" });
      sessionStorage.removeItem("ja_token");
      sessionStorage.removeItem(CHAT_STORAGE_KEY);
      Object.assign(state, { token: null, account: null, profile: null, trips: [], outlookEvents: [], editReturn: null, chat: null });
      updateTopbarAccount();
      toast("All data deleted. See you soon!");
      go("welcome");
    });
  },

  // -- Profile (reachable via the Profile tab in the bottom tab bar) ---------------
  profile() {
    const p = state.profile;
    const pref = p.preferences;
    const h = p.home;
    const mob = p.mobility || {};

    screen.replaceChildren(el(`
      <div class="dash-greeting">
        <h1>Profile</h1>
      </div>

      <div class="card" style="padding: 12px 16px">
        <div class="summary-row"><span class="k">Name</span><span class="v">${state.account.display_name}</span></div>
        <div class="summary-row"><span class="k">Email</span><span class="v">${state.account.email}</span></div>
        <div class="summary-row"><span class="k">BahnCard</span><span class="v">${state.account.bahncard}</span></div>
        <div class="summary-row"><span class="k">BahnBonus</span><span class="v">${state.account.bahnbonus_status} · ${state.account.bahnbonus_points.toLocaleString("en-US")} points</span></div>
      </div>

      <div class="section-title"><h2>Home station</h2></div>
      <div class="card">
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
        <button class="btn primary block" id="save-home" type="button" style="margin-top:14px">Save home settings</button>
      </div>

      <div class="section-title"><h2>Travel preferences</h2><button id="edit-prefs" type="button">Edit</button></div>
      <div class="card" style="padding: 12px 16px">
        <div class="summary-row"><span class="k">Class / seat</span><span class="v">${pref.travel_class === 1 ? "1st" : "2nd"} class · ${seatLabel(pref)}</span></div>
        <div class="summary-row"><span class="k">Speed vs. comfort</span><span class="v">${pref.speed_vs_comfort} / 100</span></div>
        <div class="summary-row"><span class="k">Max. transfers</span><span class="v">${pref.max_transfers >= 9 ? "no preference" : pref.max_transfers}</span></div>
        <div class="summary-row"><span class="k">Autonomy</span><span class="v">${{ notify_only: "Just notify me", approve_each: "Approve every action", auto_within_limits: "Automatic within limits" }[p.autonomy]}</span></div>
      </div>

      <div class="section-title"><h2>Automation &amp; veto</h2><button id="edit-policy" type="button">Manage</button></div>
      <div class="card" style="padding: 12px 16px">
        <div class="summary-row"><span class="k">Autonomy level</span><span class="v">${POLICY_LEVEL_LABEL[(p.policy && p.policy.global_autonomy_level) || "balanced"]}</span></div>
        <div class="summary-row"><span class="k">Pinned action rules</span><span class="v">${policyOverrideCount(p)}</span></div>
      </div>

      <div class="section-title"><h2>Connections</h2><button id="edit-connections" type="button">Manage</button></div>
      <div class="card" style="padding: 12px 16px">
        <div class="summary-row"><span class="k">DB account</span><span class="v">✓ ${state.account.email}</span></div>
        <div class="summary-row"><span class="k">Phone number</span><span class="v">${p.notifications.phone_verified ? "✓ " + p.notifications.phone : "not confirmed"}</span></div>
        <div class="summary-row"><span class="k">Outlook</span><span class="v">${p.connections.outlook ? "✓ connected" : "not connected"}</span></div>
      </div>

      <div class="card">
        <p class="muted" style="margin-top:0">Your data belongs to you: with one click you can permanently delete your profile, connections, and imported trips (GDPR Art. 17).</p>
        <button class="btn danger block" id="delete-profile" type="button">Delete profile &amp; data</button>
      </div>
    `));

    $("#navbar").hidden = true;
    $("#progress").hidden = true;
    $("#tabbar").hidden = false;
    setActiveTab("profile");

    setupHomeStationAutocomplete(h);

    $("#save-home").addEventListener("click", async () => {
      try {
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
        toast("✓ Home settings saved");
      } catch (err) {
        toast(`⚠️ ${err.message}`);
      }
    });

    $("#edit-prefs").addEventListener("click", () => { state.editReturn = "profile"; go("preferences"); });
    $("#edit-connections").addEventListener("click", () => { state.editReturn = "profile"; go("connections"); });
    $("#edit-policy").addEventListener("click", () => go("policy"));
    $("#delete-profile").addEventListener("click", async () => {
      if (!confirm("Really delete all data? This cannot be undone.")) return;
      await api("/api/profile", { method: "DELETE" });
      sessionStorage.removeItem("ja_token");
      sessionStorage.removeItem(CHAT_STORAGE_KEY);
      Object.assign(state, { token: null, account: null, profile: null, trips: [], outlookEvents: [], editReturn: null, chat: null });
      updateTopbarAccount();
      toast("All data deleted. See you soon!");
      go("welcome");
    });
  },

  // -- Connections (reachable via "Manage" on the profile/dashboard) ---------------
  connections() {
    const phoneVerified = state.profile?.notifications?.phone_verified;
    const outlookConnected = state.profile?.connections?.outlook;
    const events = state.outlookEvents.map((e) => `
      <div class="event-row">
        <span class="event-when">${fmtDate(e.start).slice(0, 10)}<br>${fmtTime(e.start)}</span>
        <span><span class="event-title">${e.title}</span>
          <span class="event-loc">${e.location}</span>
          ${e.hard_constraint ? '<span class="event-hard">Hard deadline</span>' : ""}
        </span>
      </div>
    `).join("");

    screen.replaceChildren(el(`
      <div class="dash-greeting">
        <h1>Connections</h1>
        <p class="muted">Manage your linked accounts and notification channels.</p>
      </div>

      <div class="section-title"><h2>Phone number</h2></div>
      <div class="card">
        ${phoneVerified ? `
          <div class="success-banner">✓ ${state.profile.notifications.phone} is confirmed</div>
          <p class="muted" style="margin-top:10px">To change your number, disconnect first and re-verify.</p>
          <button class="btn danger block" id="phone-disconnect" type="button">Remove number</button>
        ` : `
          <p class="muted">With a confirmed number we can reach you with alerts and replanning suggestions via SMS/WhatsApp — even when the app is closed.</p>
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
          <div class="demo-hint">🎓 <b>Demo mode:</b> No real SMS is sent — the code is shown as a notification.</div>
        `}
      </div>

      <div class="section-title"><h2>Outlook calendar</h2></div>
      <div class="card">
        <p class="muted">The autopilot reads your appointments to protect hard deadlines (e.g. on-site client meetings) during every replan — and adds new connections directly to your calendar.</p>
        ${outlookConnected ? `
          <div class="success-banner">✓ Outlook calendar connected</div>
          ${events ? `<h2 style="font-size:14px">Detected events</h2>${events}` : ""}
          <button class="btn danger block" id="outlook-disconnect" type="button" style="margin-top:12px">Disconnect</button>
        ` : `
          <button class="btn primary block" id="outlook-connect" type="button">Sign in with Microsoft</button>
          <div id="outlook-device-flow"></div>
          <div class="demo-hint">🎓 <b>Demo mode:</b> Without a configured Microsoft Entra app, login is simulated — sample events will be loaded.</div>
        `}
      </div>
    `));

    $("#navbar").hidden = true;
    $("#progress").hidden = true;
    $("#tabbar").hidden = false;
    setActiveTab("profile");

    // --- Phone handlers ---
    if (phoneVerified) {
      $("#phone-disconnect").addEventListener("click", async () => {
        const data = await api("/api/verify/phone", { method: "DELETE" });
        state.profile = data.profile;
        toast("Phone number removed");
        renderers.connections();
      });
    } else {
      $("#phone-send").addEventListener("click", async () => {
        $("#phone-error").textContent = "";
        try {
          const data = await api("/api/verify/phone/start", {
            method: "POST", body: { phone: $("#phone-input").value },
          });
          $("#phone-confirm-area").hidden = false;
          $("#phone-code").focus();
          toast(`📱 SMS to ${data.phone} (demo): your code is ${data.demo_code}`, 10000);
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
          toast("✓ Number confirmed");
          renderers.connections();
        } catch (err) {
          $("#phone-error").textContent = err.message;
        }
      });
    }

    // --- Outlook handlers ---
    if (outlookConnected) {
      $("#outlook-disconnect").addEventListener("click", async () => {
        const data = await api("/api/connect/outlook", { method: "DELETE" });
        state.profile = data.profile;
        state.outlookEvents = [];
        renderers.connections();
      });
    } else {
      $("#outlook-connect").addEventListener("click", () => startOutlookConnect());
    }
  },

  // -- Automation & veto (policy layer) — per-write-tool auto/ask + global level ----
  policy() {
    const pol = state.profile.policy || { global_autonomy_level: "balanced", book_cost_threshold_eur: 50, write_tools: {} };
    const wt = pol.write_tools || {};
    const level = pol.global_autonomy_level || "balanced";
    const thr = pol.book_cost_threshold_eur ?? 50;

    const opt = (value, label, current) =>
      `<option value="${value}" ${(current || "default") === value ? "selected" : ""}>${label}</option>`;
    const toolSelect = (key, withThreshold = false) => `
      <select data-tool="${key}" class="policy-select">
        ${opt("default", "Default (by level)", wt[key])}
        ${opt("auto", "Always auto", wt[key])}
        ${opt("ask", "Always ask", wt[key])}
        ${withThreshold ? opt("ask_over_threshold", "Ask if over limit", wt[key]) : ""}
      </select>`;
    const toolRow = (label, sub, control) => `
      <div class="switch-row">
        <span>${label}<span class="sub">${sub}</span></span>
        ${control}
      </div>`;

    screen.replaceChildren(el(`
      <div class="dash-greeting">
        <h1>Automation &amp; veto</h1>
        <p class="muted">Decide which actions the autopilot may take on its own and which need your okay. These settings are saved and applied on every run.</p>
      </div>

      <div class="card">
        <h2>How independent should the autopilot be?</h2>
        <div class="choices cols-1" data-group="alevel">
          <button type="button" class="choice" data-value="conservative">
            <span class="choice-title">🛡️ Conservative</span>
            <span class="choice-sub">Ask before every action — maximum control.</span>
          </button>
          <button type="button" class="choice" data-value="balanced">
            <span class="choice-title">⚖️ Balanced</span>
            <span class="choice-sub">Beneficial &amp; free actions run automatically, the rest asks.</span>
          </button>
          <button type="button" class="choice" data-value="aggressive">
            <span class="choice-title">🤖 Automatic within limits</span>
            <span class="choice-sub">Most actions run automatically; hotels &amp; emails to others still ask.</span>
          </button>
        </div>
      </div>

      <div class="card">
        <h2>Per-action overrides</h2>
        <p class="muted" style="margin-top:0">"Default (by level)" follows the choice above. Pin a specific action to always run or always ask.</p>
        ${toolRow("📲 Notify me", "You are the recipient — always automatic", '<span class="v muted">Always auto</span>')}
        ${toolRow("💶 File compensation claim", "Purely beneficial, money back for you", toolSelect("file_compensation_claim"))}
        ${toolRow("🗓️ Move a tentative appointment", "Reversible calendar change", toolSelect("reschedule_outlook_event_tentative"))}
        ${toolRow("📅 Move a confirmed appointment", "Not freely reversible", toolSelect("reschedule_outlook_event_confirmed"))}
        ${toolRow("🔀 Rebook an alternative train", "Cost depends on the option", toolSelect("book_alternative_connection", true))}
        ${toolRow("🏨 Book a hotel", "Cost + overnight — high commitment", toolSelect("book_hotel"))}
        ${toolRow("✉️ Email participants", "Affects third parties (clients, colleagues)", toolSelect("send_email_to_participants"))}

        <label class="field" style="margin-top:12px">Rebooking cost limit (EUR)
          <span class="hint">Used by "Ask if over limit" — under it rebooks automatically, over it asks</span>
          <input type="number" id="book-threshold" min="0" step="5" value="${thr}">
        </label>

        <button class="btn primary block" id="save-policy" type="button" style="margin-top:14px">Save automation settings</button>
      </div>
    `));

    $("#navbar").hidden = true;
    $("#progress").hidden = true;
    $("#tabbar").hidden = false;
    setActiveTab("profile");

    const box = screen.querySelector('[data-group="alevel"]');
    box.querySelectorAll(".choice").forEach((btn) => {
      if (btn.dataset.value === level) btn.classList.add("selected");
      btn.addEventListener("click", () => {
        box.querySelectorAll(".choice").forEach((b) => b.classList.remove("selected"));
        btn.classList.add("selected");
      });
    });

    $("#save-policy").addEventListener("click", async () => {
      const write_tools = {};
      screen.querySelectorAll("select[data-tool]").forEach((s) => { write_tools[s.dataset.tool] = s.value; });
      try {
        await saveProfile({
          policy: {
            global_autonomy_level: box.querySelector(".choice.selected")?.dataset.value || level,
            book_cost_threshold_eur: Number($("#book-threshold").value) || 0,
            write_tools,
          },
        });
        toast("✓ Automation settings saved");
      } catch (err) {
        toast(`⚠️ ${err.message}`);
      }
    });
  },

  // -- Trip chat: runs the ReAct orchestrator (the scenarios/happy_path.py flow) ------------
  chat() {
    const trip = state.chat.trip;
    screen.replaceChildren(el(`
      <div class="chat-head">
        <button class="chat-back" id="chat-back" type="button" aria-label="Back">‹</button>
        <div class="chat-trip">
          <span class="chat-route">${trip.origin} → ${trip.destination}</span>
          <span class="chat-sub">${trip.train} · ${fmtDate(trip.planned_departure)} · ${fmtTime(trip.planned_departure)}</span>
        </div>
        <span class="chat-live">● live</span>
      </div>
      <div class="chat-log" id="chat-log"></div>
      <form class="chat-input" id="chat-form">
        <input type="text" id="chat-text" placeholder="Ask the autopilot about this trip…" autocomplete="off">
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
    $("#chat-back").addEventListener("click", () => go("dashboard"));
    $("#chat-form").addEventListener("submit", onChatSubmit);
    // Delegated click handler for reroute option cards — one listener on the
    // log survives re-renders. Clicking sends "Take option <id>" as the next
    // user turn and marks the batch as chosen so the cards grey out.
    $("#chat-log").addEventListener("click", onOptionCardClick);
    $("#chat-text").focus();
  },
};

// ---------------------------------------------------------------------------
// Chat: send messages to the orchestrator and render the conversation
// ---------------------------------------------------------------------------

// Chat state (sessionId, trip, messages) is mirrored into sessionStorage on
// every render, so a full page reload within the same browser tab resumes
// the conversation exactly where it left off — same pattern as the "ja_token"
// auth token below.
const CHAT_STORAGE_KEY = "ja_chat";

function persistChat() {
  try {
    if (state.chat) {
      sessionStorage.setItem(CHAT_STORAGE_KEY, JSON.stringify({
        sessionId: state.chat.sessionId,
        trip: state.chat.trip,
        messages: state.chat.messages,
      }));
    } else {
      sessionStorage.removeItem(CHAT_STORAGE_KEY);
    }
  } catch {
    // sessionStorage can be unavailable or quota-limited; chat should still work.
  }
}

// Rehydrates a chat that survived a page reload (called once from boot()).
// Returns true if a chat was restored, so boot() can land on it directly
// instead of the dashboard.
function restoreChatState() {
  const raw = sessionStorage.getItem(CHAT_STORAGE_KEY);
  if (!raw) return false;
  let saved;
  try {
    saved = JSON.parse(raw);
  } catch {
    saved = null;
  }
  if (!saved || !saved.trip || !saved.trip.trip_id || !Array.isArray(saved.messages)) {
    sessionStorage.removeItem(CHAT_STORAGE_KEY);
    return false;
  }
  // Prefer the freshly-fetched trip (from /api/me) over the stored snapshot;
  // fall back to the snapshot if the trip is no longer in the current list.
  const freshTrip = state.trips.find((t) => t.trip_id === saved.trip.trip_id);
  state.chat = {
    sessionId: saved.sessionId || null,
    trip: freshTrip || saved.trip,
    busy: false,
    messages: saved.messages,
  };
  return true;
}

// Reopening the trip you were already chatting about resumes that
// conversation instead of starting over; opening a different trip still
// starts fresh (only one conversation is kept active at a time).
function openChat(trip) {
  if (state.chat && state.chat.trip && state.chat.trip.trip_id === trip.trip_id) {
    go("chat");
    return;
  }
  state.chat = {
    sessionId: null,
    trip,
    busy: false,
    messages: [{
      role: "assistant",
      text: `Hi ${state.account.first_name}! I'm keeping an eye on your ${trip.origin} → ${trip.destination} trip. `
        + `Say "monitor my trip" for a live check. If your appointment is no longer reachable you can ask me to act — `
        + `e.g. "rebook me, move the clashing meeting and let the participants know" — and I'll only do what your `
        + `automation settings allow without asking first.`,
    }],
  };
  persistChat();
  go("chat");
}

function renderTrace(trace) {
  const lines = trace.map((t) => {
    if (t.kind === "call") return `<div class="trace-line"><span class="ag">${escapeHtml(t.author)}</span> → calls <b>${escapeHtml(t.name)}()</b></div>`;
    if (t.kind === "result") return `<div class="trace-line"><span class="ag">${escapeHtml(t.author)}</span> ← result of <b>${escapeHtml(t.name)}</b></div>`;
    return `<div class="trace-line"><span class="ag">${escapeHtml(t.author)}</span>: ${escapeHtml(t.text)}</div>`;
  }).join("");
  return `<details class="chat-trace"><summary>Agent trace (${trace.length})</summary>${lines}</details>`;
}

function renderChatLog() {
  persistChat();
  const log = $("#chat-log");
  if (!log) return;
  const parts = state.chat.messages.map((m) => {
    if (m.role === "user") return `<div class="bubble user">${escapeHtml(m.text)}</div>`;
    if (m.role === "error") return `<div class="bubble error">⚠️ ${escapeHtml(m.text)}</div>`;
    const trace = m.trace && m.trace.length ? renderTrace(m.trace) : "";
    const cards = m.options && m.options.length ? renderOptionCards(m.options, m.optionsSource, m) : "";
    return `<div class="bubble assistant">${escapeHtml(m.text)}${cards}${trace}</div>`;
  });
  if (state.chat.busy) parts.push(`<div class="bubble assistant typing"><i></i><i></i><i></i></div>`);
  log.innerHTML = parts.join("");
  log.scrollTop = log.scrollHeight;
}

// Render reroute option cards below the agent's prose. Clicking a card sends
// "Take option <id>" as the next user turn and disables the batch so the user
// can't pick twice. The per-option `source` (db_service_live / mock_*) decides
// the live/demo badge; optionsSource is the fallback for the whole batch.
// Cards branch on `o.mode` (train / car_sharing / bike_sharing / hotel).
const _MODE_META = {
  car_sharing:  { icon: "🚗", label: "Flinkster",    cls: "car"   },
  bike_sharing: { icon: "🚲", label: "Call-a-Bike",  cls: "bike"  },
  hotel:        { icon: "🏨", label: "Hotel",         cls: "hotel" },
};

function renderOptionCards(options, optionsSource, message) {
  const chosen = message.chosenOption || null;
  const items = options.map((o) => {
    const id = escapeHtml(o.option_id || "?");
    const mode = o.mode || "train";
    const src = o.source || optionsSource || "";
    const liveBadge = src.startsWith("db_service_live")
      ? '<span class="option-source live">● Live DB</span>'
      : src.startsWith("mock_")
        ? '<span class="option-source mock">Demo fallback</span>'
        : "";
    const meta = _MODE_META[mode];
    const modeBadge = meta
      ? `<span class="option-mode-badge option-mode-${meta.cls}">${meta.icon} ${meta.label}</span>`
      : "";
    const picked = chosen === (o.option_id || "");
    const stateCls = picked ? " selected" : chosen ? " disabled" : "";

    let body;
    if (mode === "hotel") {
      const name = escapeHtml(o.name || o.description || "Hotel");
      const dist = o.distance_to_station_km != null ? `${o.distance_to_station_km} km from station` : "";
      const price = o.price_per_night_eur != null ? `${Number(o.price_per_night_eur).toFixed(2)} €/night` : "";
      const nights = o.nights != null ? `${o.nights} night${o.nights === 1 ? "" : "s"}` : "";
      const remarks = (o.remarks || []).slice(0, 1).map((r) => `<span class="option-remark">${escapeHtml(r)}</span>`).join("");
      body = `
        <div class="option-trains">${name}</div>
        <div class="option-meta">
          ${dist ? `<span>${dist}</span>` : ""}${price ? `<span class="option-price">${price}</span>` : ""}${nights ? `<span>${nights}</span>` : ""}
        </div>
        ${remarks}`;
    } else if (mode === "car_sharing" || mode === "bike_sharing") {
      const desc = escapeHtml(o.description || "");
      const pickup = o.pickup ? `<div class="option-times">${escapeHtml(o.pickup)}</div>` : "";
      const dist = o.distance_km != null ? `${o.distance_km} km` : "";
      const dur = o.est_duration_minutes != null ? `~${o.est_duration_minutes} min` : "";
      const arr = o.new_arrival ? `→ ${fmtTime(o.new_arrival)}` : "";
      const price = o.price_eur != null ? `${Number(o.price_eur).toFixed(2)} €` : "";
      const remarks = (o.remarks || []).slice(0, 1).map((r) => `<span class="option-remark">${escapeHtml(r)}</span>`).join("");
      body = `
        <div class="option-trains">${desc}</div>
        ${pickup}
        <div class="option-meta">
          ${dist ? `<span>${dist}</span>` : ""}${dur ? `<span>${dur}</span>` : ""}${arr ? `<span>${arr}</span>` : ""}${price ? `<span class="option-price">${price}</span>` : ""}
        </div>
        ${remarks}`;
    } else {
      // train (default)
      const trains = (o.trains || []).map(escapeHtml).join(" → ") || escapeHtml(o.description || "Connection");
      const dep = o.departure ? fmtTime(o.departure) : "—";
      const arr = o.new_arrival ? fmtTime(o.new_arrival) : "—";
      const transfers = o.transfers != null ? `${o.transfers} change${o.transfers === 1 ? "" : "s"}` : "—";
      const delay = o.added_delay_minutes != null ? `+${o.added_delay_minutes} min` : "";
      const price = o.price_eur != null ? `${Number(o.price_eur).toFixed(2)} €` : "";
      const remarks = (o.remarks || []).slice(0, 1).map((r) => `<span class="option-remark">${escapeHtml(r)}</span>`).join("");
      body = `
        <div class="option-trains">${trains}</div>
        <div class="option-times">${dep} → ${arr}</div>
        <div class="option-meta">
          <span>${transfers}</span>${delay ? `<span class="option-delay">${delay}</span>` : ""}${price ? `<span>${price}</span>` : ""}
        </div>
        ${remarks}`;
    }

    return `
      <button type="button" class="option-card${stateCls}" data-option-id="${id}"${chosen ? " disabled" : ""}>
        <div class="option-head"><span class="option-badge">${id}</span>${modeBadge}${liveBadge}</div>
        ${body}
      </button>`;
  }).join("");
  return `<div class="option-cards" data-chosen="${chosen || ""}">${items}</div>`;
}

function onOptionCardClick(ev) {
  const card = ev.target.closest(".option-card");
  if (!card || card.disabled) return;
  if (state.chat.busy) return;
  const optionId = card.dataset.optionId;
  if (!optionId) return;
  // Mark the originating assistant message so its batch greys out on re-render.
  for (let i = state.chat.messages.length - 1; i >= 0; i--) {
    const m = state.chat.messages[i];
    if (m.options && m.options.some((o) => (o.option_id || "?") === optionId)) {
      m.chosenOption = optionId;
      break;
    }
  }
  const input = $("#chat-text");
  if (input) input.value = `Take option ${optionId}`;
  $("#chat-form").requestSubmit();
}

async function onChatSubmit(ev) {
  ev.preventDefault();
  const input = $("#chat-text");
  const text = input.value.trim();
  if (!text || state.chat.busy) return;

  input.value = "";
  state.chat.messages.push({ role: "user", text });
  state.chat.busy = true;
  $("#chat-send").disabled = true;
  renderChatLog();

  const chat = state.chat; // keep a handle in case the user navigates away
  try {
    const data = await api("/api/chat", {
      method: "POST",
      body: { session_id: chat.sessionId, message: text, trip: chat.trip },
    });
    if (data.session_id) chat.sessionId = data.session_id;
    if (data.error) {
      chat.messages.push({ role: "error", text: data.error });
    } else {
      chat.messages.push({
        role: "assistant",
        text: data.reply,
        trace: data.trace,
        options: data.options || null,
        optionsSource: data.options_source || null,
      });
    }
  } catch (err) {
    chat.messages.push({ role: "error", text: err.message });
  } finally {
    chat.busy = false;
    if (state.chat === chat) {
      if ($("#chat-send")) $("#chat-send").disabled = false;
      renderChatLog();
      if ($("#chat-text")) $("#chat-text").focus();
    }
  }
}

// ---------------------------------------------------------------------------
// Outlook device-code flow: real MS Entra auth in the browser
// ---------------------------------------------------------------------------

async function startOutlookConnect() {
  const btn = $("#outlook-connect");
  if (btn) btn.disabled = true;
  const container = $("#outlook-device-flow");
  if (container) container.innerHTML = '<div class="device-waiting"><span class="spinner"></span>Starting sign-in…</div>';

  try {
    const data = await api("/api/connect/outlook/start", { method: "POST" });
    if (data.mode === "simulated") {
      // No Entra app configured → fall back to the simulated consent dialog
      if (btn) btn.disabled = false;
      if (container) container.innerHTML = "";
      $("#ms-mail").textContent = state.account.email;
      $("#ms-modal").hidden = false;
      return;
    }
    // Real device-code flow — show code + link, then poll for completion
    if (data.pending || !data.user_code) {
      // prompt_callback didn't fire in time — retry
      if (container) container.innerHTML = '<p class="device-error">Could not start sign-in. Please try again.</p>';
      if (btn) btn.disabled = false;
      return;
    }
    renderDeviceCodeScreen(data, container);
    pollOutlookStatus(container);
  } catch (err) {
    if (container) container.innerHTML = `<p class="device-error">⚠️ ${err.message}</p>`;
    if (btn) btn.disabled = false;
  }
}

function renderDeviceCodeScreen(data, container) {
  container.innerHTML = `
    <div class="device-code-box">
      <div class="device-code-label">Enter this code at Microsoft</div>
      <div class="device-code-value">${data.user_code}</div>
      <button class="device-code-copy" id="dc-copy" type="button">Copy code</button>
    </div>
    <a class="device-link" href="${data.verification_uri}" target="_blank" rel="noopener">Open Microsoft sign-in ↗</a>
    <div class="device-waiting"><span class="spinner"></span>Waiting for you to sign in…</div>
  `;
  $("#dc-copy").addEventListener("click", () => {
    navigator.clipboard.writeText(data.user_code).then(() => {
      $("#dc-copy").textContent = "Copied ✓";
      setTimeout(() => { const c = $("#dc-copy"); if (c) c.textContent = "Copy code"; }, 2000);
    }).catch(() => {});
  });
}

let outlookPollTimer = null;

async function pollOutlookStatus(container) {
  clearTimeout(outlookPollTimer);
  const poll = async () => {
    try {
      const data = await api("/api/connect/outlook/status");
      if (data.status === "complete") {
        state.profile = data.profile;
        state.outlookEvents = data.events || [];
        toast(`✓ Outlook connected — ${(data.events || []).length} events detected`);
        renderers[state.step]?.();
        return;
      }
      if (data.status === "expired") {
        container.innerHTML = '<p class="device-error">The code expired. <button class="device-code-copy" id="dc-retry" type="button" style="margin-left:8px">Try again</button></p>';
        const retry = $("#dc-retry");
        if (retry) retry.addEventListener("click", () => startOutlookConnect());
        return;
      }
      if (data.status === "error") {
        container.innerHTML = `<p class="device-error">⚠️ ${data.error}</p>`;
        const btn = $("#outlook-connect");
        if (btn) btn.disabled = false;
        return;
      }
      if (data.status === "none") {
        container.innerHTML = '<p class="device-error">Sign-in session was lost. Please click Sign in with Microsoft again.</p>';
        const btn = $("#outlook-connect");
        if (btn) btn.disabled = false;
        return;
      }
      // pending — keep polling
      outlookPollTimer = setTimeout(poll, 2000);
    } catch (err) {
      container.innerHTML = `<p class="device-error">⚠️ ${err.message}</p>`;
      const btn = $("#outlook-connect");
      if (btn) btn.disabled = false;
    }
  };
  poll();
}

// ---------------------------------------------------------------------------
// Navigation: save the step, then move on
// ---------------------------------------------------------------------------

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

function go(step) {
  state.step = step;
  // The chat is a full-height flex layout (scrolling log + pinned input bar);
  // other screens scroll normally.
  const chatMode = step === "chat";
  document.querySelector(".phone").classList.toggle("chat-active", chatMode);
  screen.classList.toggle("chat-mode", chatMode);
  setProgress(step);
  renderers[step]();
  screen.scrollTop = 0;
  window.scrollTo(0, 0);
}

async function next() {
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

function back() {
  if (state.editReturn) { const ret = state.editReturn; state.editReturn = null; go(ret); return; }
  const idx = STEPS.indexOf(state.step);
  // From the phone step back to the trip overview, not to login
  go(STEPS[Math.max(0, idx - 1)]);
}

// ---------------------------------------------------------------------------
// Events & startup
// ---------------------------------------------------------------------------

$("#btn-next").addEventListener("click", next);
$("#btn-back").addEventListener("click", back);
$("#btn-skip").addEventListener("click", () => {
  if (state.editReturn) { const ret = state.editReturn; state.editReturn = null; go(ret); return; }
  go(STEPS[STEPS.indexOf(state.step) + 1]);
});

$("#ms-cancel").addEventListener("click", () => { $("#ms-modal").hidden = true; });
$("#ms-accept").addEventListener("click", async () => {
  $("#ms-modal").hidden = true;
  try {
    const data = await api("/api/connect/outlook", { method: "POST", body: { consent: true } });
    state.profile = data.profile;
    state.outlookEvents = data.events;
    toast(`✓ Outlook verbunden — ${data.events.length} Termine erkannt`);
    renderers[state.step]?.();
  } catch (err) {
    toast(`⚠️ ${err.message}`);
  }
});

// Tab bar: Trips ↔ Profile navigation
$("#tab-trips").addEventListener("click", () => go("dashboard"));
$("#tab-profile").addEventListener("click", () => go("profile"));

async function boot() {
  if (state.token) {
    try {
      const data = await api("/api/me");
      state.account = data.account;
      state.profile = data.profile;
      state.trips = data.trips;
      updateTopbarAccount();
      // Active session: users who finished onboarding land in the dashboard,
      // everyone else continues after the login step. A chat in progress
      // (this browser tab, same session) takes priority over the dashboard.
      if (state.profile.onboarding_completed) {
        go(restoreChatState() ? "chat" : "dashboard");
      } else {
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

boot();
