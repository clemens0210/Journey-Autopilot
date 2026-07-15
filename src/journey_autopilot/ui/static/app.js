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
  complaints: [],
  complaintId: null, // active detail view
  outlookEvents: [],
  step: "welcome",
  editReturn: null, // "dashboard" / "profile" = return target after editing
  phone: { sent: false, verifiedThisSession: false },
  chat: null, // { sessionId, trip, messages: [...], busy } when a trip chat is open
  tripDetail: null, // { trip, data, error } when the trip-detail screen is open
  book: null, // { from, to, departure, results, error } for the Book tab
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
  dbLogo: `<img src="/static/db-logo.png" alt="DB Logo" class="db-logo-img">`,
  origin: `<svg class="ic" viewBox="0 0 24 24" fill="none"><circle cx="12" cy="12" r="7" stroke="currentColor" stroke-width="2"/><circle cx="12" cy="12" r="2.6" fill="currentColor"/></svg>`,
  pin: `<svg class="ic" viewBox="0 0 24 24" fill="none"><path d="M12 22s7-6.2 7-12a7 7 0 1 0-14 0c0 5.8 7 12 7 12Z" stroke="currentColor" stroke-width="2" stroke-linejoin="round"/><circle cx="12" cy="10" r="2.4" fill="currentColor"/></svg>`,
  calendar: `<svg class="ic" viewBox="0 0 24 24" fill="none"><rect x="3.5" y="5" width="17" height="16" rx="2.5" stroke="currentColor" stroke-width="2"/><path d="M3.5 9.5h17M8 3v4M16 3v4" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg>`,
  seat: `<svg class="ic" viewBox="0 0 24 24" fill="none"><path d="M7 4v8a2 2 0 0 0 2 2h6M7 20v-4M17 20v-7" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>`,
  bell: `<svg class="ic" viewBox="0 0 24 24" fill="none"><path d="M6 9a6 6 0 1 1 12 0c0 5 2 6 2 6H4s2-1 2-6Z" stroke="currentColor" stroke-width="2" stroke-linejoin="round"/><path d="M10 19a2 2 0 0 0 4 0" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg>`,
  download: `<svg class="ic" viewBox="0 0 24 24" fill="none"><path d="M12 4v10m0 0 4-4m-4 4-4-4M5 20h14" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>`,
  qr: `<svg viewBox="0 0 24 24" fill="#111"><path d="M3 3h7v7H3V3Zm2 2v3h3V5H5Zm9-2h7v7h-7V3Zm2 2v3h3V5h-3ZM3 14h7v7H3v-7Zm2 2v3h3v-3H5Zm11-2h2v2h-2v-2Zm3 0h2v2h-2v-2Zm-3 3h2v2h-2v-2Zm0 3h2v2h-2v-2Zm3-3h2v2h-2v-2Zm0 3h2v2h-2v-2Z"/></svg>`,
  transfer: `<svg class="ic" viewBox="0 0 24 24" fill="none"><rect x="6.5" y="6.5" width="11" height="11" rx="2" fill="currentColor"/></svg>`,
  trash: `<svg class="ic" viewBox="0 0 24 24" fill="none"><path d="M4 7h16M9 7V5a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v2M6 7l1 13h10l1-13" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>`,
};

// Route grid for a trip card. Single-leg journeys keep the simple origin →
// destination layout; multi-leg journeys (self-added connections, or any trip
// with >1 leg) render the full station chain — origin → each change station →
// final destination — using the existing .route grid markers.
function routeHTML(t) {
  const legs = Array.isArray(t.legs) ? t.legs : [];
  if (legs.length > 1) {
    // leg[i].destination === leg[i+1].origin, so taking each leg's destination
    // (after the origin) yields the change chain without duplicates.
    const stops = [legs[0].origin, ...legs.map((leg) => leg.destination)];
    const rows = stops.map((stop, i) => {
      const isLast = i === stops.length - 1;
      const marker = isLast ? SVG.pin : i === 0 ? SVG.origin : SVG.transfer;
      const dots = isLast ? "" : `<span class="dots"><i></i><i></i><i></i></span><span></span>`;
      return `<span class="marker">${marker}</span><span class="station${isLast ? "" : " intermediate"}">${escapeHtml(stop || "")}</span>${dots}`;
    }).join("");
    return `<div class="route multi">${rows}</div>`;
  }
  return `
    <div class="route">
      <span class="marker">${SVG.origin}</span><span class="station">${escapeHtml(t.origin || "")}</span>
      <span class="dots"><i></i><i></i><i></i></span><span></span>
      <span class="marker">${SVG.pin}</span><span class="station">${escapeHtml(t.destination || "")}</span>
    </div>`;
}

// A trip card in DB Navigator layout: DB logo + train, purpose of travel,
// origin/destination with dot/pin markers, date/time, and a footer status.
// When `index` is given the card becomes clickable (opens the trip chat).
// `deletable` renders a trash button (data-trip-delete-id) in the head; the
// dashboard wires a delegated handler that stops propagation so the card click
// (chat) doesn't fire. The seat/coach/platform row is hidden when none of
// those fields are present (self-added trips have no booking).
function tripCardHTML(t, { foot, live = false, index = null, deletable = false } = {}) {
  const clickable = index !== null;
  const legs = Array.isArray(t.legs) ? t.legs : [];
  const trains = Array.isArray(t.trains) ? t.trains : (t.train ? [t.train] : []);
  const multi = legs.length > 1;
  const trainHead = escapeHtml(t.train || "") + (trains.length > 1 ? ` <span class="train-more">+${trains.length - 1}</span>` : "");
  const hasSeatRow = t.platform || t.coach || t.seat;
  const deleteBtn = deletable
    ? `<button class="trip-delete" type="button" data-trip-delete-id="${escapeHtml(t.trip_id || "")}" aria-label="Delete trip" title="Delete trip">${SVG.trash}</button>`
    : "";
  return `
    <div class="trip-card${clickable ? " clickable" : ""}"${clickable ? ` data-trip-index="${index}"` : ""}>
      <div class="trip-head">
        <span class="db-logo">${SVG.dbLogo}</span>
        <span class="train">${trainHead}</span>
        <span class="trip-head-right">
          ${deleteBtn}
          <span>${t.travel_class}. Kl.</span>
          <span class="qr">${SVG.qr}</span>
        </span>
      </div>
      <div class="trip-fare">${escapeHtml(t.purpose || "")}</div>
      <hr class="trip-divider">
      <div class="trip-body">
        ${routeHTML(t)}
        <div class="trip-meta-row">${SVG.calendar} ${fmtDate(t.planned_departure)} · ${fmtTime(t.planned_departure)} – ${fmtTime(t.planned_arrival)}</div>
        ${multi ? `<div class="trip-meta-row">${SVG.transfer} ${trains.map(escapeHtml).join(" → ")} · ${legs.length - 1} change${legs.length - 1 === 1 ? "" : "s"}</div>` : ""}
        ${hasSeatRow ? `<div class="trip-meta-row">${SVG.seat} ${escapeHtml(t.platform || "")}${t.coach ? ` · ${escapeHtml(t.coach)}` : ""}${t.seat ? `, ${escapeHtml(t.seat)}` : ""}</div>` : ""}
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

const fmtDate = (iso) => new Date(iso).toLocaleDateString("de-DE", {
  day: "2-digit", month: "2-digit", year: "numeric",
}).replace(/\./g, "/");
const fmtTime = (iso) => new Date(iso).toLocaleTimeString("de-DE", {
  hour: "2-digit", minute: "2-digit",
});
const fmtDuration = (minutes) =>
  minutes >= 60 ? `${Math.floor(minutes / 60)}h ${minutes % 60}min` : `${minutes}min`;
const minutesBetween = (isoA, isoB) => Math.round((new Date(isoB) - new Date(isoA)) / 60000);
const shiftedTime = (iso, delayMinutes) => new Date(new Date(iso).getTime() + delayMinutes * 60000);

const tripStartTime = (trip) => {
  const time = new Date(trip?.planned_departure || "").getTime();
  return Number.isFinite(time) ? time : Number.MAX_SAFE_INTEGER;
};
const tripEndTime = (trip) => {
  const time = new Date(trip?.planned_arrival || trip?.planned_departure || "").getTime();
  return Number.isFinite(time) ? time : null;
};
const isPastTrip = (trip, now = new Date()) => {
  const end = tripEndTime(trip);
  return end !== null && end < now.getTime();
};
const isUpcomingTrip = (trip, now = new Date()) => {
  const start = tripStartTime(trip);
  return start !== Number.MAX_SAFE_INTEGER && start >= now.getTime();
};
const sortTripsByDate = (trips) =>
  [...(trips || [])].sort((a, b) => tripStartTime(a) - tripStartTime(b));

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

function setupStationAutocomplete(inputEl, sugBoxEl, initial) {
  if (!inputEl || !sugBoxEl) return null;

  let selected = initial || null;
  let debounce = null;

  inputEl.addEventListener("input", () => {
    selected = null;
    clearTimeout(debounce);
    debounce = setTimeout(async () => {
      const q = inputEl.value.trim();
      sugBoxEl.innerHTML = "";
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
          inputEl.value = s.name;
          sugBoxEl.innerHTML = "";
        });
        list.appendChild(b);
      });
      sugBoxEl.replaceChildren(list);
    }, 250);
  });

  return () => selected || (inputEl.value.trim() ? { id: null, name: inputEl.value.trim() } : null);
}

function setupHomeStationAutocomplete(home) {
  // Thin wrapper that exposes the selected home station via screen._getHomeStation,
  // preserving the contract the preferences/home/profile screens rely on.
  const getStation = setupStationAutocomplete($("#home-station"), $("#station-suggestions"), home.home_station || null);
  if (getStation) screen._getHomeStation = getStation;
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
  const el = tab === "trips" ? $("#tab-trips")
    : tab === "profile" ? $("#tab-profile")
    : tab === "book" ? $("#tab-book") : null;
  if (el) el.classList.add("active");
}

function showMainTabBar(activeTab) {
  $("#navbar").hidden = true;
  $("#progress").hidden = true;
  $("#tabbar").hidden = false;
  setActiveTab(activeTab);
}

const fmtEur = (n) => `€${Number(n || 0).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;

const COMPLAINT_STATUS = {
  draft: "Draft — review & submit",
  submitted: "Submitted",
  rejected: "Dismissed",
};

function draftComplaintsCount() {
  return state.complaints.filter((c) => c.status === "draft").length;
}

function profileComplaintsNavRow() {
  const drafts = draftComplaintsCount();
  return `
    <button type="button" class="profile-nav-row" id="open-complaints">
      <span class="profile-nav-icon">
        <svg class="ic" viewBox="0 0 24 24" fill="none"><path d="M8 4h8l2 4h3a1 1 0 0 1 1 1v11a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1V9a1 1 0 0 1 1-1h3l2-4Z" stroke="currentColor" stroke-width="2" stroke-linejoin="round"/><path d="M12 12v4M12 8h.01" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg>
      </span>
      <span class="profile-nav-body">
        <span class="profile-nav-title">Complaints</span>
        <span class="profile-nav-sub">Review and submit passenger-rights claims</span>
      </span>
      ${drafts ? `<span class="profile-nav-badge">${drafts}</span>` : ""}
      <span class="profile-nav-chevron" aria-hidden="true">›</span>
    </button>`;
}

function complaintCardHTML(c) {
  const statusClass = `complaint-status-${c.status}`;
  const dateLabel = c.travel_date
    ? new Date(`${c.travel_date}T12:00:00`).toLocaleDateString("en-US", {
      weekday: "short", day: "2-digit", month: "short", year: "numeric",
    })
    : "—";
  return `
    <div class="complaint-card clickable" data-complaint-id="${escapeHtml(c.complaint_id)}">
      <div class="complaint-card-head">
        <span class="complaint-route">${escapeHtml(c.origin)} → ${escapeHtml(c.destination)}</span>
        <span class="complaint-badge ${statusClass}">${COMPLAINT_STATUS[c.status] || c.status}</span>
      </div>
      <div class="complaint-meta">${escapeHtml(c.train || "Train")} · ${dateLabel}</div>
      <div class="complaint-meta">Delay ${c.delay_minutes} min · est. ${fmtEur(c.compensation_eur)}</div>
      ${c.status === "draft"
    ? '<div class="complaint-foot">Tap to review and submit</div>'
    : ""}
    </div>`;
}

function handleComplaintCreated(complaint) {
  state.complaints = [
    complaint,
    ...state.complaints.filter((c) => c.complaint_id !== complaint.complaint_id),
  ];
  toast(
    `Draft complaint ready — est. ${fmtEur(complaint.compensation_eur)} compensation. `
    + "Open Profile → Complaints to review and submit.",
    6500,
  );
}

function wireOpenComplaints() {
  const btn = $("#open-complaints");
  if (btn) btn.addEventListener("click", () => go("complaints"));
}

// ---------------------------------------------------------------------------
// Screens
// ---------------------------------------------------------------------------

const renderers = {

  // -- 0: Welcome ---------------------------------------------------------
  welcome() {
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
        state.complaints = data.complaints || [];
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
          <div class="success-banner">✓ Connected${state.profile?.connections?.outlook_email ? ` as ${state.profile.connections.outlook_email}` : " — Outlook calendar"}</div>
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
  },

  // -- Dashboard -------------------------------------------------------------------------
  dashboard() {
    const p = state.profile;
    const pref = p.preferences;
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
        <div class="summary-row"><span class="k">Outlook</span><span class="v">${p.connections.outlook ? (p.connections.outlook_email ? "✓ " + p.connections.outlook_email : "✓ connected") : "not connected"}</span></div>
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
    $("#edit-prefs").addEventListener("click", () => { state.editReturn = "dashboard"; go("preferences"); });
    $("#edit-connections").addEventListener("click", () => { state.editReturn = "dashboard"; go("connections"); });
    $("#edit-policy").addEventListener("click", () => go("policy"));
    $("#delete-profile").addEventListener("click", async () => {
      if (!confirm("Really delete all data? This cannot be undone.")) return;
      await api("/api/profile", { method: "DELETE" });
      sessionStorage.removeItem("ja_token");
      sessionStorage.removeItem(CHAT_STORAGE_KEY);
      Object.assign(state, { token: null, account: null, profile: null, trips: [], complaints: [], complaintId: null, outlookEvents: [], editReturn: null, chat: null });
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

      ${profileComplaintsNavRow()}

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
        <div class="summary-row"><span class="k">Outlook</span><span class="v">${p.connections.outlook ? (p.connections.outlook_email ? "✓ " + p.connections.outlook_email : "✓ connected") : "not connected"}</span></div>
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

    wireOpenComplaints();
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
      Object.assign(state, { token: null, account: null, profile: null, trips: [], complaints: [], complaintId: null, outlookEvents: [], editReturn: null, chat: null });
      updateTopbarAccount();
      toast("All data deleted. See you soon!");
      go("welcome");
    });
  },

  // -- Complaints (Profile → overview of passenger-rights drafts) ----------------
  complaints() {
    const drafts = draftComplaintsCount();
    const cards = state.complaints.map((c) => complaintCardHTML(c)).join("");

    screen.replaceChildren(el(`
      <button type="button" class="screen-back" id="complaints-back">‹ Profile</button>
      <div class="dash-greeting">
        <h1>Complaints</h1>
        <p class="muted">${drafts
    ? `${drafts} draft${drafts === 1 ? "" : "s"} waiting for your review — you submit each claim yourself.`
    : "When the autopilot detects passenger-rights eligibility, a draft appears here for you to submit."}</p>
      </div>
      ${cards || `
        <div class="card">
          <p class="muted" style="margin:0">No complaints yet. Ask the autopilot to monitor a trip in the chat — if compensation applies, you'll get a notification and a draft will show up here.</p>
        </div>`}
    `));

    showMainTabBar("profile");

    $("#complaints-back").addEventListener("click", () => go("profile"));
    screen.querySelectorAll(".complaint-card.clickable").forEach((node) => {
      node.addEventListener("click", () => {
        state.complaintId = node.dataset.complaintId;
        go("complaint_detail");
      });
    });
  },

  // -- Complaint detail (review draft, submit or dismiss) ------------------------
  complaint_detail() {
    const c = state.complaints.find((item) => item.complaint_id === state.complaintId);
    if (!c) {
      go("complaints");
      return;
    }

    const dateLabel = c.travel_date
      ? new Date(`${c.travel_date}T12:00:00`).toLocaleDateString("en-US", {
        weekday: "long", day: "2-digit", month: "long", year: "numeric",
      })
      : "—";

    screen.replaceChildren(el(`
      <button type="button" class="screen-back" id="complaint-back">‹ Complaints</button>
      <div class="dash-greeting">
        <h1>Claim details</h1>
        <span class="complaint-badge complaint-status-${c.status}">${COMPLAINT_STATUS[c.status] || c.status}</span>
      </div>

      <div class="card" style="padding: 12px 16px">
        <div class="summary-row"><span class="k">Route</span><span class="v">${escapeHtml(c.origin)} → ${escapeHtml(c.destination)}</span></div>
        <div class="summary-row"><span class="k">Train</span><span class="v">${escapeHtml(c.train || "—")}</span></div>
        <div class="summary-row"><span class="k">Travel date</span><span class="v">${dateLabel}</span></div>
        <div class="summary-row"><span class="k">Delay</span><span class="v">${c.delay_minutes} min</span></div>
        <div class="summary-row"><span class="k">Est. compensation</span><span class="v">${fmtEur(c.compensation_eur)}</span></div>
      </div>

      <div class="card">
        <h2 style="margin-top:0;font-size:15px">Why this claim applies</h2>
        <p class="muted" style="margin-bottom:0">${escapeHtml(c.reason)}</p>
        ${(c.notes || []).length ? `<ul class="complaint-notes">${c.notes.map((n) => `<li>${escapeHtml(n)}</li>`).join("")}</ul>` : ""}
      </div>

      ${c.status === "draft" ? `
        <p class="muted" style="padding: 0 6px">This is a draft prepared by the autopilot. Nothing is filed until you tap Submit below.</p>
        <button class="btn primary block" id="complaint-submit" type="button">Submit complaint</button>
        <button class="btn ghost block" id="complaint-dismiss" type="button" style="margin-top:8px">Dismiss draft</button>
      ` : c.status === "submitted" ? `
        <div class="success-banner">✓ Complaint submitted${c.submitted_at ? ` on ${fmtDate(c.submitted_at)}` : ""} (simulated)</div>
      ` : `
        <p class="muted" style="padding: 0 6px">This draft was dismissed and will not be submitted.</p>
      `}
    `));

    showMainTabBar("profile");

    $("#complaint-back").addEventListener("click", () => go("complaints"));

    if (c.status === "draft") {
      $("#complaint-submit").addEventListener("click", async () => {
        try {
          const data = await api(`/api/complaints/${encodeURIComponent(c.complaint_id)}`, {
            method: "PATCH",
            body: { status: "submitted" },
          });
          const idx = state.complaints.findIndex((item) => item.complaint_id === c.complaint_id);
          if (idx >= 0) state.complaints[idx] = data.complaint;
          toast("✓ Complaint submitted (simulated)");
          go("complaint_detail");
        } catch (err) {
          toast(`⚠️ ${err.message}`);
        }
      });

      $("#complaint-dismiss").addEventListener("click", async () => {
        if (!confirm("Dismiss this draft? It will not be submitted.")) return;
        try {
          const data = await api(`/api/complaints/${encodeURIComponent(c.complaint_id)}`, {
            method: "PATCH",
            body: { status: "rejected" },
          });
          const idx = state.complaints.findIndex((item) => item.complaint_id === c.complaint_id);
          if (idx >= 0) state.complaints[idx] = data.complaint;
          toast("Draft dismissed");
          go("complaints");
        } catch (err) {
          toast(`⚠️ ${err.message}`);
        }
      });
    }
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
          <div class="success-banner">✓ Connected${state.profile?.connections?.outlook_email ? ` as ${state.profile.connections.outlook_email}` : " — Outlook calendar"}</div>
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
          toast(data.delivery?.sent
            ? `📲 Code sent to ${data.phone} on WhatsApp — code: ${data.demo_code}`
            : `📱 Demo (Twilio off) — your code is ${data.demo_code}`, 10000);
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

  // -- Book: live journey search via db_service, adds the pick to "Trips" ----------
  book() {
    if (!state.book) {
      state.book = { from: null, to: null, departure: nowLocalISO(), results: null, error: null };
    }
    const b = state.book;

    screen.replaceChildren(el(`
      <div class="dash-greeting">
        <h1>Book a trip</h1>
        <p class="muted">Search live connections and add one to your monitored trips — handy for testing the autopilot on a real, current journey.</p>
      </div>
      <div class="card">
        <label class="field">From
          <span class="autocomplete">
            <input type="text" id="book-from" placeholder="e.g. München Hbf" autocomplete="off" value="${b.from?.name || ""}">
            <span id="book-from-sug"></span>
          </span>
        </label>
        <label class="field">To
          <span class="autocomplete">
            <input type="text" id="book-to" placeholder="e.g. Berlin Hbf" autocomplete="off" value="${b.to?.name || ""}">
            <span id="book-to-sug"></span>
          </span>
        </label>
        <label class="field">Departure
          <input type="datetime-local" id="book-depart" value="${b.departure}">
        </label>
        <p class="error" id="book-error"></p>
        <button class="btn primary block" id="book-search" type="button">Search connections</button>
        <div class="demo-hint">🎓 Live data — requires the <code>db_service</code> sidecar to be running.</div>
      </div>
      <div id="book-results"></div>
    `));
    $("#navbar").hidden = true;
    $("#progress").hidden = true;
    $("#tabbar").hidden = false;
    setActiveTab("book");

    const getFrom = attachStationAutocomplete($("#book-from"), $("#book-from-sug"), (s) => { b.from = s; });
    const getTo = attachStationAutocomplete($("#book-to"), $("#book-to-sug"), (s) => { b.to = s; });
    renderBookResults();

    $("#book-depart").addEventListener("change", () => { b.departure = $("#book-depart").value; });
    $("#book-search").addEventListener("click", async () => {
      const errEl = $("#book-error");
      errEl.textContent = "";
      try {
        const from = getFrom() || b.from || await resolveStation($("#book-from").value);
        const to = getTo() || b.to || await resolveStation($("#book-to").value);
        if (!from || !to) throw new Error("Please pick both stations from the suggestions.");
        b.from = from;
        b.to = to;
        b.results = null;
        b.error = null;
        $("#book-results").innerHTML = `<div class="device-waiting"><span class="spinner"></span>Searching connections…</div>`;
        const departure = new Date($("#book-depart").value || Date.now()).toISOString();
        const data = await api(
          `/api/journeys?from_id=${encodeURIComponent(from.id)}&to_id=${encodeURIComponent(to.id)}&departure=${encodeURIComponent(departure)}`
        );
        b.results = data.journeys;
        renderBookResults();
      } catch (err) {
        b.results = null;
        b.error = err.message;
        renderBookResults();
      }
    });
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

  // -- Trip detail: full itinerary with live delay + risk forecast (mock) ----------
  tripdetail() {
    const { trip, data, error } = state.tripDetail;
    const duration = minutesBetween(trip.planned_departure, trip.planned_arrival);

    let body;
    if (error) {
      body = `<div class="jd-error">${escapeHtml(error)}</div>`;
    } else if (!data) {
      body = `<div class="device-waiting"><span class="spinner"></span>Loading live journey data…</div>`;
    } else {
      body = journeyHTML(data);
    }

    screen.replaceChildren(el(`
      <div class="chat-head">
        <button class="chat-back" id="jd-back" type="button" aria-label="Back">‹</button>
        <div class="chat-trip">
          <span class="chat-route">${trip.origin} → ${trip.destination}</span>
          <span class="chat-sub">${fmtDate(trip.planned_departure)} · Duration: ${fmtDuration(duration)}</span>
        </div>
        <span class="chat-live">● live</span>
      </div>
      <div class="jd-body">${body}</div>
      <div class="jd-actions">
        <button class="btn primary block" id="jd-chat" type="button">Ask the autopilot about this trip</button>
      </div>
    `));
    $("#navbar").hidden = true;
    $("#tabbar").hidden = true;
    $("#progress").hidden = true;

    $("#jd-back").addEventListener("click", () => { state.tripDetail = null; go("dashboard"); });
    $("#jd-chat").addEventListener("click", () => openChat(trip));
  },


  // -- Trip chat: runs the ReAct orchestrator (the scenarios/happy_path.py flow) ------------
  chat() {
    const trip = state.chat.trip;
    const headInner = trip
      ? `<div class="chat-trip">
          <span class="chat-route">${escapeHtml(trip.origin || "")} → ${escapeHtml(trip.destination || "")}</span>
          <span class="chat-sub">${escapeHtml(trip.train || "Connection")} · ${escapeHtml(fmtDate(trip.planned_departure))} · ${escapeHtml(fmtTime(trip.planned_departure))}</span>
        </div>
        <span class="chat-live">● live</span>`
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
  },
};

// ---------------------------------------------------------------------------
// Chat: send messages to the orchestrator and render the conversation
// ---------------------------------------------------------------------------

function openChat(trip = null) {
  const tomorrow = new Date(Date.now() + 86400000).toISOString().slice(0, 10);
  const greeting = trip
    ? `Hi ${state.account.first_name}! I'm keeping an eye on your ${trip.origin} → ${trip.destination} trip — `
      + `running a live check for you now. Ask me anything in the meantime.`
    : `Hi ${state.account.first_name}! I'm your monitoring assistant. Describe any trip — e.g. `
      + `"risk for an ICE from Cologne Hbf to Hamburg Hbf on ${tomorrow} at 09:00" — and I'll check the `
      + `delay risk, reroute options, and your calendar deadlines. No booking needed.`;
  if (state.chat && state.chat.trip && trip && state.chat.trip.trip_id === trip.trip_id) {
    go("chat");
    return;
  }
  state.chat = {
    sessionId: null,
    trip,
    busy: false,
    messages: [{ role: "assistant", text: greeting }],
  };
  persistChat();
  go("chat");
  // Trip chats start with an automatic monitoring turn: opening the chat IS
  // the "monitor my trip" intent, so the live status/risk check (and, on a
  // detected risk band, the proactive WhatsApp notice) runs without the user
  // having to type anything. Only once per freshly opened chat — reopening or
  // restoring a conversation never re-triggers it.
  if (trip) {
    runChatTurn(
      "Monitor my trip: check the live status and current disruption risk, and tell me if I need to do anything.",
      { display: { role: "notice", text: "Automatic check — the autopilot is monitoring this trip (live status, risk, calendar)." } },
    );
  }
}

// ---------------------------------------------------------------------------
// Book: station autocomplete + journey search results
// ---------------------------------------------------------------------------

function nowLocalISO() {
  const d = new Date();
  const pad = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

// Generic station autocomplete on /api/stations (like the home-station field).
// Returns a getter for the selected station; onSelect fires on pick/clear.
function attachStationAutocomplete(input, sugBox, onSelect) {
  let selected = null;
  let debounce = null;
  input.addEventListener("input", () => {
    selected = null;
    onSelect(null);
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
        const btn = document.createElement("button");
        btn.type = "button";
        btn.textContent = data.source === "db-live" ? `🟢 ${s.name}` : s.name;
        btn.addEventListener("click", () => {
          selected = s;
          onSelect(s);
          input.value = s.name;
          sugBox.innerHTML = "";
        });
        list.appendChild(btn);
      });
      sugBox.replaceChildren(list);
    }, 250);
  });
  return () => selected;
}

// Resolve free text to the best station hit (when nothing was picked from the list).
async function resolveStation(text) {
  const q = text.trim();
  if (!q) return null;
  const data = await api(`/api/stations?query=${encodeURIComponent(q)}`).catch(() => ({ stations: [] }));
  return data.stations[0] || null;
}

function renderBookResults() {
  const box = $("#book-results");
  if (!box) return;
  const b = state.book;
  if (b.error) {
    box.innerHTML = `<div class="jd-notice">⚠️ ${escapeHtml(b.error)}</div>`;
    return;
  }
  if (!b.results) {
    box.innerHTML = "";
    return;
  }
  if (!b.results.length) {
    box.innerHTML = `<p class="muted" style="padding:0 6px">No connections found for this search.</p>`;
    return;
  }

  const cards = b.results.map((j, i) => {
    const dep = j.planned_departure || j.departure;
    const arr = j.planned_arrival || j.arrival;
    const delay = j.arrival_delay_minutes;
    const transfers = j.transfers === 0 ? "direct" : `${j.transfers} transfer${j.transfers > 1 ? "s" : ""}`;
    return `
      <div class="card journey-option" data-journey-index="${i}">
        <div class="jo-times">
          <b>${fmtTime(dep)}</b> → <b>${fmtTime(arr)}</b>
          <span class="muted">${fmtDuration(minutesBetween(dep, arr))} · ${transfers}</span>
          ${delay ? `<span class="jo-delay">+${delay} min</span>` : ""}
        </div>
        <div class="jo-meta">${escapeHtml(j.description || "")}${j.price_eur ? ` · from ${Number(j.price_eur).toFixed(2)} €` : ""}</div>
        <div class="jo-add">＋ Add to my trips</div>
      </div>`;
  }).join("");

  box.innerHTML = `<h2 style="margin: 18px 4px 10px">Connections</h2>${cards}<div id="book-confirm"></div>`;
  box.querySelectorAll(".journey-option").forEach((cardEl) => {
    cardEl.addEventListener("click", () => showBookConfirm(b.results[Number(cardEl.dataset.journeyIndex)]));
  });
}

function showBookConfirm(journey) {
  const dest = journey.destination || "destination";
  const confirmBox = $("#book-confirm");
  if (!confirmBox) return;
  confirmBox.innerHTML = `
    <div class="card">
      <h2>Name this trip</h2>
      <label class="field">Purpose / subject
        <input type="text" id="book-purpose" value="Trip to ${escapeHtml(dest)}">
      </label>
      <div class="search-confirm-summary">
        ${escapeHtml(journey.origin || "")} → ${escapeHtml(journey.destination || "")}
        · ${escapeHtml(journey.description || "")}
      </div>
      <button class="btn primary block" id="book-confirm-btn" type="button">Add trip</button>
      <button class="btn block" id="book-cancel-btn" type="button" style="margin-top:8px">Back to results</button>
    </div>`;
  confirmBox.scrollIntoView({ behavior: "smooth" });
  $("#book-purpose").focus();
  $("#book-purpose").select();
  $("#book-cancel-btn").addEventListener("click", () => { confirmBox.innerHTML = ""; });
  $("#book-confirm-btn").addEventListener("click", () => {
    const purpose = $("#book-purpose").value.trim() || `Trip to ${dest}`;
    bookJourney(journey, purpose);
  });
}

async function bookJourney(journey, purpose) {
  try {
    const data = await api("/api/trips", { method: "POST", body: { journey, purpose } });
    state.trips = data.trips;
    toast(`✓ ${data.trip.train} ${data.trip.origin} → ${data.trip.destination} added to your trips`);
    go("dashboard");
  } catch (err) {
    toast(`⚠️ ${err.message}`);
  }
}

// ---------------------------------------------------------------------------
// Trip detail: DB Navigator-style itinerary with live delay + expected delay
// ---------------------------------------------------------------------------

function openTripDetail(trip) {
  const detail = { trip, data: null, error: null };
  state.tripDetail = detail;
  go("tripdetail");
  api(`/api/trips/${encodeURIComponent(trip.trip_id)}/details`)
    .then((data) => { detail.data = data; })
    .catch((err) => { detail.error = err.message; })
    .finally(() => {
      if (state.step === "tripdetail" && state.tripDetail === detail) renderers.tripdetail();
    });
}

// One stop row: planned/actual time, station name, platform badge.
function stopHTML(stop, delayMinutes, { arrival = false } = {}) {
  const late = delayMinutes > 0;
  return `
    <div class="jd-stop">
      <div class="jd-times">
        <span class="jd-planned">${fmtTime(stop.planned)}</span>
        <span class="jd-actual ${late ? "late" : "ok"}">${fmtTime(shiftedTime(stop.planned, delayMinutes))}</span>
      </div>
      <div class="jd-node${arrival ? " arr" : " dep"}"><i></i></div>
      <div class="jd-station">${escapeHtml(stop.name)}</div>
      <span class="jd-plat">Pl. ${escapeHtml(stop.platform)}</span>
    </div>`;
}

// The full itinerary: stops and train legs, each leg with its current ("real")
// delay next to the expected delay from the risk forecast (historical DB data).
function journeyHTML(data) {
  const incidents = (data.incidents || []).map((inc) => `
    <div class="jd-notice"><b>${escapeHtml(inc.type)}</b> (${escapeHtml(inc.location)}): ${escapeHtml(inc.impact)}</div>
  `).join("");

  const parts = data.legs.map((leg, i) => {
    const delay = leg.current_delay_minutes || 0;
    const fc = leg.forecast || {};
    const expected = fc.expected_delay_minutes ?? 0;
    const legMinutes = minutesBetween(leg.origin.planned, leg.destination.planned);

    // Transfer row between the previous leg's arrival and this departure.
    const transfer = i === 0 ? "" : `
      <div class="jd-transfer">
        <div class="jd-legdur">${fmtDuration(minutesBetween(data.legs[i - 1].destination.planned, leg.origin.planned))}</div>
        <div class="jd-line dotted"></div>
        <div class="jd-transfer-label">↷ Transfer</div>
      </div>`;

    return `
      ${transfer}
      ${stopHTML(leg.origin, delay)}
      <div class="jd-leg">
        <div class="jd-legdur">${fmtDuration(legMinutes)}</div>
        <div class="jd-line"></div>
        <div class="jd-leginfo">
          <span class="jd-train">${escapeHtml(leg.train)}</span>
          <div class="jd-dir">to ${escapeHtml(leg.direction)}</div>
          <div class="jd-delays">
            <span class="jd-chip real ${delay > 0 ? "late" : "ok"}">${delay > 0 ? `+${delay} min delay` : "On time"}</span>
            <span class="jd-chip expected ${fc.level || "low"}">Expected: ${expected > 0 ? `+${expected} min` : "on time"}</span>
          </div>
          ${fc.factors && fc.factors.length ? `<div class="jd-forecast-note">Autopilot forecast (${Math.round((fc.confidence || 0) * 100)}% confidence): ${escapeHtml(fc.factors[0])}</div>` : ""}
        </div>
      </div>
      ${stopHTML(leg.destination, delay, { arrival: true })}`;
  }).join("");

  return `
    ${incidents}
    ${data.connection_risk ? `<div class="jd-notice">${escapeHtml(data.connection_risk)}</div>` : ""}
    <div class="jd-timeline">${parts}</div>
    <p class="muted" style="margin-top:14px">Expected delay is the autopilot's risk forecast, based on historical DB punctuality data for this route — not a live prediction.</p>`;
}

// ---------------------------------------------------------------------------
// Chat persistence: survive a page reload within the same tab
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
function renderTrace(trace) {
  const lines = trace.map((t) => {
    if (t.kind === "call") return `<div class="trace-line"><span class="ag">${escapeHtml(t.author)}</span> → calls <b>${escapeHtml(t.name)}()</b></div>`;
    if (t.kind === "result") return `<div class="trace-line"><span class="ag">${escapeHtml(t.author)}</span> ← result of <b>${escapeHtml(t.name)}</b></div>`;
    return `<div class="trace-line"><span class="ag">${escapeHtml(t.author)}</span>: ${escapeHtml(t.text)}</div>`;
  }).join("");
  return `<details class="chat-trace"><summary>Agent trace (${trace.length})</summary>${lines}</details>`;
}

// Inline Markdown (bold, italic, inline code, links) for one span of text.
// Escapes first so nothing the model emits can inject HTML, THEN applies the
// token replacements — the escaped `*`, `` ` ``, `[` … survive escaping.
// Only *…* / **…** are treated as emphasis (not `_`), because agent prose is
// full of snake_case identifiers like `mock_hotels` that `_`-italic would mangle.
function renderInlineMd(text) {
  let s = escapeHtml(text);
  s = s.replace(/`([^`]+)`/g, (_, c) => `<code>${c}</code>`);
  s = s.replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g,
    (_, t, url) => `<a href="${url}" target="_blank" rel="noopener noreferrer">${t}</a>`);
  s = s.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
  s = s.replace(/(^|[^*])\*([^*\n]+)\*/g, "$1<em>$2</em>");
  return s;
}

// Minimal, safe Markdown -> HTML for assistant replies. A small line-based block
// grammar (headings, ordered/unordered lists, tables, blockquotes, fenced code,
// rules, paragraphs) wrapping renderInlineMd. Not full CommonMark — just the
// subset the agents actually emit. All raw text passes through renderInlineMd or
// escapeHtml, so no unescaped model output ever reaches innerHTML.
function renderMarkdown(src) {
  const lines = String(src ?? "").replace(/\r\n?/g, "\n").split("\n");
  // A table starts at line idx when it contains pipes and the NEXT line is a
  // |---|---| separator row. Checked by index (not per-line) because both the
  // block dispatcher and the paragraph accumulator must stop there — models
  // often emit "Here are your options:" directly followed by the table, and
  // without this check the whole table was swallowed into the paragraph.
  const isTableStart = (idx) =>
    lines[idx].includes("|") && idx + 1 < lines.length &&
    /^\s*\|?[\s:|-]*-[\s:|-]*$/.test(lines[idx + 1]) && lines[idx + 1].includes("|");
  const isBlockStart = (l) =>
    !l.trim() || /^```/.test(l.trim()) || /^#{1,6}\s/.test(l) || /^\s*>/.test(l) ||
    /^\s*[-*+]\s+/.test(l) || /^\s*\d+[.)]\s+/.test(l);
  const out = [];
  let i = 0;
  while (i < lines.length) {
    const line = lines[i];
    if (!line.trim()) { i++; continue; }

    if (/^```/.test(line.trim())) {
      const buf = [];
      for (i++; i < lines.length && !/^```/.test(lines[i].trim()); i++) buf.push(lines[i]);
      i++;
      out.push(`<pre><code>${escapeHtml(buf.join("\n"))}</code></pre>`);
      continue;
    }
    const h = line.match(/^(#{1,6})\s+(.*)$/);
    if (h) {
      const level = Math.min(h[1].length, 6);
      out.push(`<h${level}>${renderInlineMd(h[2].trim())}</h${level}>`);
      i++; continue;
    }
    if (/^\s*([-*_])(\s*\1){2,}\s*$/.test(line)) { out.push("<hr>"); i++; continue; }

    // Table: a row with pipes followed by a |---|---| separator row.
    if (isTableStart(i)) {
      const cells = (r) => r.trim().replace(/^\|/, "").replace(/\|$/, "").split("|").map((c) => c.trim());
      const head = cells(line);
      i += 2;
      const rows = [];
      for (; i < lines.length && lines[i].includes("|") && lines[i].trim(); i++) rows.push(cells(lines[i]));
      const thead = `<thead><tr>${head.map((c) => `<th>${renderInlineMd(c)}</th>`).join("")}</tr></thead>`;
      const tbody = `<tbody>${rows.map((r) => `<tr>${r.map((c) => `<td>${renderInlineMd(c)}</td>`).join("")}</tr>`).join("")}</tbody>`;
      out.push(`<div class="md-tablewrap"><table class="md-table">${thead}${tbody}</table></div>`);
      continue;
    }
    if (/^\s*>\s?/.test(line)) {
      const buf = [];
      for (; i < lines.length && /^\s*>\s?/.test(lines[i]); i++) buf.push(lines[i].replace(/^\s*>\s?/, ""));
      out.push(`<blockquote>${renderInlineMd(buf.join(" "))}</blockquote>`);
      continue;
    }
    if (/^\s*[-*+]\s+/.test(line)) {
      const buf = [];
      for (; i < lines.length && /^\s*[-*+]\s+/.test(lines[i]); i++) buf.push(lines[i].replace(/^\s*[-*+]\s+/, ""));
      out.push(`<ul>${buf.map((it) => `<li>${renderInlineMd(it)}</li>`).join("")}</ul>`);
      continue;
    }
    if (/^\s*\d+[.)]\s+/.test(line)) {
      const buf = [];
      for (; i < lines.length && /^\s*\d+[.)]\s+/.test(lines[i]); i++) buf.push(lines[i].replace(/^\s*\d+[.)]\s+/, ""));
      out.push(`<ol>${buf.map((it) => `<li>${renderInlineMd(it)}</li>`).join("")}</ol>`);
      continue;
    }
    const buf = [];
    for (; i < lines.length && lines[i].trim() && !isBlockStart(lines[i]) && !isTableStart(i); i++) buf.push(lines[i]);
    out.push(`<p>${renderInlineMd(buf.join("\n")).replace(/\n/g, "<br>")}</p>`);
  }
  return out.join("");
}

function renderChatLog() {
  persistChat();
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

// Shared inner body for a train journey/reroute option. The helper speaks one
// vocabulary — the arrival field is always `arrival` — so each caller maps its
// source field at the call site: a reroute option passes `new_arrival`, a live
// search result passes `planned_arrival || arrival`. Cost labels distinguish a
// reroute's added cost from a quoted fare so the two values are never conflated.
function journeyBodyHTML(j) {
  const trains = (j.trains || []).map(escapeHtml).join(" → ") || escapeHtml(j.description || "Connection");
  const dep = j.departure ? fmtTime(j.departure) : "—";
  const arr = j.arrival ? fmtTime(j.arrival) : "—";
  const transfers = j.transfers != null ? `${j.transfers} change${j.transfers === 1 ? "" : "s"}` : "—";
  const delay = j.added_delay_minutes != null ? `<span class="option-delay">+${j.added_delay_minutes} min</span>` : "";
  let cost = "";
  if (j.cost_status === "unknown") {
    cost = '<span class="option-price unknown">Added cost unknown</span>';
  } else if (j.cost_status === "estimate" && j.added_cost_eur != null) {
    cost = `<span class="option-price">~${Number(j.added_cost_eur).toFixed(2)} € added</span>`;
  } else if (j.added_cost_eur != null) {
    const addedCost = Number(j.added_cost_eur);
    cost = addedCost === 0
      ? '<span class="option-price">No added cost</span>'
      : `<span class="option-price">+${addedCost.toFixed(2)} € added</span>`;
  } else if (j.price_eur != null) {
    cost = `<span class="option-price">Fare ${Number(j.price_eur).toFixed(2)} €</span>`;
  }
  const remarks = (j.remarks || []).slice(0, 1).map((r) => `<span class="option-remark">${escapeHtml(r)}</span>`).join("");
  return `
    <div class="option-trains">${trains}</div>
    <div class="option-times">${dep} → ${arr}</div>
    <div class="option-meta"><span>${transfers}</span>${delay}${cost}</div>
    ${remarks}`;
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

// Itinerary for a train option: every stop with its time, the train of each
// leg, and the connection (transfer) time at every change station.
function optionStopsHTML(legs) {
  if (!Array.isArray(legs) || legs.length === 0) return "";
  const rows = [];
  legs.forEach((leg, i) => {
    if (i === 0) {
      rows.push(`<div class="os-row"><span class="os-time">${leg.departure ? fmtTime(leg.departure) : "—"}</span><span class="os-station">${escapeHtml(leg.origin || "")}</span></div>`);
    }
    rows.push(`<div class="os-leg">${escapeHtml(leg.train || "")}</div>`);
    const next = legs[i + 1];
    if (next) {
      const transferMin = leg.arrival && next.departure ? minutesBetween(leg.arrival, next.departure) : null;
      const transfer = transferMin != null
        ? `<span class="os-transfer">${transferMin} min transfer · dep ${fmtTime(next.departure)}</span>`
        : "";
      rows.push(`<div class="os-row"><span class="os-time">${leg.arrival ? fmtTime(leg.arrival) : "—"}</span><span class="os-station">${escapeHtml(leg.destination || "")}</span>${transfer}</div>`);
    } else {
      rows.push(`<div class="os-row"><span class="os-time">${leg.arrival ? fmtTime(leg.arrival) : "—"}</span><span class="os-station">${escapeHtml(leg.destination || "")}</span></div>`);
    }
  });
  return `<div class="option-stops">${rows.join("")}</div>`;
}

function renderOptionCards(options, optionsSource, message, { fallback = false, messageIndex } = {}) {
  const chosen = message.chosenOption || null;
  const proposalExpired = message.proposalExpiresAt
    ? Date.parse(message.proposalExpiresAt) <= Date.now()
    : false;
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
    const selectable = !fallback && !proposalExpired && o.selectable !== false && o.eligible !== false;
    const stateCls = picked ? " selected" : chosen || !selectable ? " disabled" : "";
    const recommended = o.recommended
      ? '<span class="option-recommended">Recommended</span>'
      : "";
    const violations = [
      ...(o.constraint_violations || []),
      ...(proposalExpired ? ["proposal_expired_refresh_required"] : []),
    ].map((reason) =>
      escapeHtml(String(reason).replaceAll("_", " "))
    ).join(", ");

    let body;
    if (mode === "hotel") {
      const name = escapeHtml(o.name || o.description || "Hotel");
      const dist = o.distance_to_station_km != null ? `${o.distance_to_station_km} km from station` : "";
      const nights = o.nights != null ? `${o.nights} night${o.nights === 1 ? "" : "s"}` : "";
      const remarks = (o.remarks || []).slice(0, 1).map((r) => `<span class="option-remark">${escapeHtml(r)}</span>`).join("");
      body = `
        <div class="option-trains">${name}</div>
        <div class="option-meta">
          ${dist ? `<span>${dist}</span>` : ""}${nights ? `<span>${nights}</span>` : ""}
        </div>
        ${remarks}`;
    } else if (mode === "car_sharing" || mode === "bike_sharing") {
      const desc = escapeHtml(o.description || "");
      const pickup = o.pickup ? `<div class="option-times">${escapeHtml(o.pickup)}</div>` : "";
      const dist = o.distance_km != null ? `${o.distance_km} km` : "";
      const dur = o.est_duration_minutes != null ? `~${o.est_duration_minutes} min` : "";
      const arr = o.new_arrival ? `→ ${fmtTime(o.new_arrival)}` : "";
      const price = o.price_eur != null ? `Price ${Number(o.price_eur).toFixed(2)} €` : "";
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
      body = journeyBodyHTML({
        trains: o.trains, description: o.description,
        departure: o.departure,
        arrival: o.new_arrival,            // reroute-specific field
        transfers: o.transfers, added_delay_minutes: o.added_delay_minutes,
        added_cost_eur: o.added_cost_eur, price_eur: o.price_eur,
        cost_status: o.cost_status,
        remarks: o.remarks,
      });
      // Full itinerary (stops, per-leg trains, transfer times) when available.
      body += optionStopsHTML(o.legs);
    }

    return `
      <button type="button" class="option-card${stateCls}" data-option-id="${id}" data-message-index="${messageIndex}"${chosen || !selectable ? " disabled" : ""}>
        <div class="option-head"><span class="option-badge">${id}</span>${modeBadge}${recommended}${liveBadge}</div>
        ${body}
        ${violations ? `<span class="option-violation">Not selectable: ${violations}</span>` : ""}
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
async function runChatTurn(text, { display = null, selection = null } = {}) {
  if (state.chat.busy) return;
  state.chat.messages.push(display || { role: "user", text });
  state.chat.busy = true;
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
      chat.messages.push({
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
      });
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
  // Chat and trip detail are full-height flex layouts (scrolling body with a
  // pinned header/footer); other screens scroll normally.
  const chatMode = step === "chat" || step === "tripdetail";
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
    // If the deleted trip was the one open in the chat, drop the chat too.
    if (state.chat && state.chat.trip && state.chat.trip.trip_id === tripId) {
      state.chat = null;
      persistChat();
    }
    toast("✓ Trip deleted");
    // Trash buttons only appear on the dashboard (trips page); stay there.
    go("dashboard");
  } catch (err) {
    toast(`⚠️ ${err.message}`);
  }
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
