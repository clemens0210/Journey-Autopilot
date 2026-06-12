/* Journey Autopilot — Onboarding-Wizard im DB-Navigator-Stil.
 *
 * Ein kleiner zustandsbasierter Wizard ohne Framework: render(step) zeichnet den
 * Screen, die Navbar (Zurück/Überspringen/Weiter) wird pro Schritt konfiguriert.
 * Nach jedem Schritt wird nur der geänderte Profil-Teil als Patch gespeichert —
 * Abbrechen und später Weitermachen ist damit jederzeit möglich.
 */

"use strict";

// ---------------------------------------------------------------------------
// Zustand & API
// ---------------------------------------------------------------------------

const state = {
  token: sessionStorage.getItem("ja_token") || null,
  account: null,
  profile: null,
  trips: [],
  outlookEvents: [],
  step: "welcome",
  editReturn: false, // true = wir kamen vom Dashboard ("Bearbeiten")
  phone: { sent: false, verifiedThisSession: false },
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
  if (!resp.ok) throw new Error(data.detail || `Fehler ${resp.status}`);
  return data;
}

async function saveProfile(patch) {
  const data = await api("/api/profile", { method: "PUT", body: patch });
  state.profile = data.profile;
}

// ---------------------------------------------------------------------------
// DOM-Helfer
// ---------------------------------------------------------------------------

const $ = (sel) => document.querySelector(sel);
const screen = $("#screen");

function el(html) {
  const t = document.createElement("template");
  t.innerHTML = html.trim();
  return t.content;
}

// Inline-SVGs im DB-Navigator-Stil — Marke und Icons der Reise-Karten.
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

// Eine Reise-Karte im DB-Navigator-Layout: DB-Logo + Zug, Reisegrund,
// Start/Ziel mit Punkt-/Pin-Markern, Datum/Zeit und ein Fußzeilen-Status.
function tripCardHTML(t, { foot, live = false } = {}) {
  return `
    <div class="trip-card">
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
        <div class="trip-meta-row">${SVG.calendar} ${fmtDate(t.planned_departure)} · ${fmtTime(t.planned_departure)} – ${fmtTime(t.planned_arrival)} Uhr</div>
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

// Anzeige-Labels für die intern gespeicherten Profilwerte
const LABELS = {
  fenster: "Fenster", gang: "Gang", egal: "egal",
  grossraum: "Großraum", abteil: "Abteil",
};
const seatLabel = (pref) =>
  `${LABELS[pref.seat_location]}, ${LABELS[pref.seat_area]}${pref.quiet_zone ? ", Ruhebereich" : ""}`;

const fmtDate = (iso) => new Date(iso).toLocaleDateString("de-DE", {
  weekday: "short", day: "2-digit", month: "2-digit", year: "numeric",
});
const fmtTime = (iso) => new Date(iso).toLocaleTimeString("de-DE", {
  hour: "2-digit", minute: "2-digit",
});

function setNav({ back = true, next = "Weiter", skip = null, nextEnabled = true } = {}) {
  $("#tabbar").hidden = true; // Tableiste nur im Dashboard (siehe renderers.dashboard)
  $("#navbar").hidden = false;
  $("#btn-back").style.visibility = back ? "visible" : "hidden";
  $("#btn-next").textContent = next;
  $("#btn-next").disabled = !nextEnabled;
  $("#btn-skip").hidden = !skip;
  if (skip) $("#btn-skip").textContent = skip;
}

function setProgress(step) {
  const idx = STEPS.indexOf(step);
  const wizard = idx > 0; // Welcome & Dashboard ohne Fortschrittsbalken
  $("#progress").hidden = !wizard || step === "dashboard";
  if (wizard) {
    $("#progress-fill").style.width = `${(idx / (STEPS.length - 1)) * 100}%`;
    $("#progress-label").textContent = `Schritt ${idx} von ${STEPS.length - 1}`;
  }
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

// ---------------------------------------------------------------------------
// Screens
// ---------------------------------------------------------------------------

const renderers = {

  // -- 0: Willkommen ---------------------------------------------------------
  welcome() {
    screen.replaceChildren(el(`
      <div class="card hero">
        <svg class="hero-logo" viewBox="0 0 64 44"><rect width="64" height="44" rx="9" fill="#EC0016"/><rect x="5" y="5" width="54" height="34" rx="5" fill="#fff"/><text x="32" y="33" font-size="27" font-weight="900" fill="#EC0016" text-anchor="middle" font-family="'Arial Black',Arial,sans-serif">DB</text></svg>
        <h1>Dein Journey Autopilot</h1>
        <p class="muted">Reist mit. Denkt mit. Plant um, bevor du es musst.</p>
        <ul class="feature-list">
          <li><span class="feature-icon">📡</span><span><b>Störungen früh erkennen</b> — Risiko-Vorhersage Stunden im Voraus, nicht erst am Bahnsteig.</span></li>
          <li><span class="feature-icon">🔀</span><span><b>Automatisch umplanen</b> — Alternativen, die zu deinen Terminen und Vorlieben passen.</span></li>
          <li><span class="feature-icon">💶</span><span><b>Fahrgastrechte automatisch</b> — Entschädigungen werden erkannt und vorbereitet.</span></li>
          <li><span class="feature-icon">✋</span><span><b>Du behältst das Veto</b> — keine Buchung, keine Nachricht ohne deine Freigabe.</span></li>
        </ul>
      </div>
      <p class="muted" style="padding: 0 6px">Für die Einrichtung brauchen wir dein DB-Konto (Pflicht) sowie optional Mobilnummer und Kalender. Alle Daten bleiben lokal, du kannst dein Profil jederzeit einsehen, ändern oder löschen (DSGVO).</p>
    `));
    setNav({ back: false, next: "Los geht's" });
  },

  // -- 1: DB-Konto-Login -------------------------------------------------------
  login() {
    screen.replaceChildren(el(`
      <div class="card">
        <h2>Mit DB-Konto anmelden <span class="badge required">Pflicht</span></h2>
        <p class="muted">Melde dich mit deinem bahn.de-Konto an. Wir importieren deine gebuchten Reisen und dein BahnBonus-Profil — du musst nichts abtippen.</p>
        <form id="login-form">
          <label class="field">E-Mail-Adresse
            <input type="email" id="login-email" autocomplete="username" required value="lucas.wild@example.com">
          </label>
          <label class="field">Passwort
            <input type="password" id="login-password" autocomplete="current-password" required value="demo123">
          </label>
          <p class="error" id="login-error"></p>
          <button class="btn primary block" type="submit">Anmelden &amp; Reisen importieren</button>
        </form>
        <div class="demo-hint">🎓 <b>Demo-Modus:</b> Der DB-Login ist simuliert (keine offizielle DB-API). Zugang: <code>lucas.wild@example.com</code> / <code>demo123</code></div>
      </div>
    `));
    setNav({ back: true, next: "Weiter", nextEnabled: false });

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
        toast(`Willkommen, ${data.account.first_name}! ${data.trips.length} Reisen importiert.`);
        // Wer das Onboarding schon abgeschlossen hat, landet direkt im Dashboard.
        go(data.profile.onboarding_completed ? "dashboard" : "trips");
      } catch (err) {
        $("#login-error").textContent = err.message;
      }
    });
  },

  // -- 2: Importierte Reisen ------------------------------------------------------
  trips() {
    const cards = state.trips
      .map((t) => tripCardHTML(t, { foot: `Auftrag ${t.order_number} · aus DB-Konto importiert` }))
      .join("");

    screen.replaceChildren(el(`
      <div class="success-banner">✓ DB-Konto verbunden — ${state.trips.length} bevorstehende Reisen importiert</div>
      <div class="card" style="padding: 12px 16px">
        <div class="summary-row"><span class="k">Konto</span><span class="v">${state.account.display_name}</span></div>
        <div class="summary-row"><span class="k">BahnCard</span><span class="v">${state.account.bahncard}</span></div>
        <div class="summary-row"><span class="k">BahnBonus</span><span class="v">${state.account.bahnbonus_status} · ${state.account.bahnbonus_points.toLocaleString("de-DE")} Punkte</span></div>
      </div>
      <h2 style="margin: 16px 4px 10px">Deine nächsten Reisen</h2>
      ${cards || '<p class="muted">Keine bevorstehenden Reisen gefunden.</p>'}
      <p class="muted" style="padding: 0 6px">Diese Reisen überwacht der Autopilot ab sofort automatisch.</p>
    `));
    setNav({ back: false, next: "Weiter" });
  },

  // -- 3: Mobilnummer ---------------------------------------------------------------
  phone() {
    const verified = state.profile?.notifications?.phone_verified;
    screen.replaceChildren(el(`
      <div class="card">
        <h2>Mobilnummer bestätigen <span class="badge optional">Optional</span></h2>
        <p class="muted">Bei Störungen zählt jede Minute: Über deine bestätigte Nummer erreichen dich Warnungen und Umplanungs-Vorschläge per SMS/WhatsApp — auch wenn die App zu ist.</p>
        ${verified ? `
          <div class="success-banner">✓ ${state.profile.notifications.phone} ist bestätigt</div>
        ` : `
          <label class="field">Mobilnummer
            <input type="tel" id="phone-input" placeholder="+49 151 12345678" autocomplete="tel" value="${state.profile?.notifications?.phone || ""}">
          </label>
          <button class="btn primary block" id="phone-send" type="button">Code senden</button>
          <div id="phone-confirm-area" hidden>
            <label class="field" style="margin-top:16px">Bestätigungscode
              <input type="text" id="phone-code" class="code-input" inputmode="numeric" maxlength="4" placeholder="····">
            </label>
            <button class="btn primary block" id="phone-verify" type="button">Bestätigen</button>
          </div>
          <p class="error" id="phone-error"></p>
          <div class="demo-hint">🎓 <b>Demo-Modus:</b> Es wird keine echte SMS verschickt — der Code erscheint als Einblendung.</div>
        `}
      </div>
    `));
    setNav({ back: true, next: "Weiter", skip: verified ? null : "Überspringen", nextEnabled: !!verified });
    if (verified) return;

    $("#phone-send").addEventListener("click", async () => {
      $("#phone-error").textContent = "";
      try {
        const data = await api("/api/verify/phone/start", {
          method: "POST", body: { phone: $("#phone-input").value },
        });
        $("#phone-confirm-area").hidden = false;
        $("#phone-code").focus();
        toast(`📱 SMS an ${data.phone} (Demo): Dein Code ist ${data.demo_code}`, 10000);
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
        toast("✓ Nummer bestätigt");
        renderers.phone(); // Screen mit Erfolgs-Status neu zeichnen
      } catch (err) {
        $("#phone-error").textContent = err.message;
      }
    });
  },

  // -- 4: Outlook-Kalender -----------------------------------------------------------
  outlook() {
    const connected = state.profile?.connections?.outlook;
    const events = state.outlookEvents.map((e) => `
      <div class="event-row">
        <span class="event-when">${fmtDate(e.start).slice(0, 10)}<br>${fmtTime(e.start)} Uhr</span>
        <span><span class="event-title">${e.title}</span>
          <span class="event-loc">${e.location}</span>
          ${e.hard_constraint ? '<span class="event-hard">Harter Termin</span>' : ""}
        </span>
      </div>
    `).join("");

    screen.replaceChildren(el(`
      <div class="card">
        <h2>Outlook-Kalender verbinden <span class="badge optional">Optional</span></h2>
        <p class="muted">Der Autopilot liest deine Termine, um harte Deadlines (z. B. Kundentermine vor Ort) bei jeder Umplanung zu schützen — und trägt neue Verbindungen direkt in deinen Kalender ein.</p>
        ${connected ? `
          <div class="success-banner">✓ Outlook-Kalender verbunden</div>
          ${events ? `<h2 style="font-size:14px">Erkannte Termine</h2>${events}` : ""}
          <button class="btn danger block" id="outlook-disconnect" type="button" style="margin-top:12px">Verbindung trennen</button>
        ` : `
          <button class="btn primary block" id="outlook-connect" type="button">Mit Microsoft anmelden</button>
          <div class="demo-hint">🎓 <b>Demo-Modus:</b> Der Microsoft-Login ist simuliert — es werden Beispiel-Termine geladen.</div>
        `}
      </div>
    `));
    setNav({ back: true, next: "Weiter", skip: connected ? null : "Überspringen" });

    if (connected) {
      $("#outlook-disconnect").addEventListener("click", async () => {
        const data = await api("/api/connect/outlook", { method: "DELETE" });
        state.profile = data.profile;
        state.outlookEvents = [];
        renderers.outlook();
      });
    } else {
      $("#outlook-connect").addEventListener("click", () => {
        $("#ms-mail").textContent = state.account.email;
        $("#ms-modal").hidden = false;
      });
    }
  },

  // -- 5: Reisepräferenzen ---------------------------------------------------------------
  preferences() {
    const p = state.profile.preferences;
    screen.replaceChildren(el(`
      <div class="card">
        <h2>Deine Reisepräferenzen</h2>
        <p class="muted">Danach richten sich alle Umplanungs-Vorschläge. Du kannst alles später im Profil ändern.</p>

        <label class="field">Klasse</label>
        <div class="choices" data-group="travel_class">
          <button type="button" class="choice" data-value="2"><span class="choice-title">2. Klasse</span><span class="choice-sub">Standard</span></button>
          <button type="button" class="choice" data-value="1"><span class="choice-title">1. Klasse</span><span class="choice-sub">Mehr Ruhe &amp; Platz</span></button>
        </div>

        <label class="field" style="margin-top:16px">Sitzplatz</label>
        <div class="choices cols-3" data-group="seat_location">
          <button type="button" class="choice" data-value="fenster"><span class="choice-title">Fenster</span></button>
          <button type="button" class="choice" data-value="gang"><span class="choice-title">Gang</span></button>
          <button type="button" class="choice" data-value="egal"><span class="choice-title">Egal</span></button>
        </div>
        <div class="choices cols-3" style="margin-top:9px" data-group="seat_area">
          <button type="button" class="choice" data-value="grossraum"><span class="choice-title">Großraum</span></button>
          <button type="button" class="choice" data-value="abteil"><span class="choice-title">Abteil</span></button>
          <button type="button" class="choice" data-value="egal"><span class="choice-title">Egal</span></button>
        </div>

        <div class="switch-row" style="margin-top:8px">
          <span>Ruhebereich bevorzugen<span class="sub">Möglichst im Ruhewagen reservieren</span></span>
          <label class="switch"><input type="checkbox" id="quiet-zone" ${p.quiet_zone ? "checked" : ""}><span class="track"></span></label>
        </div>
      </div>

      <div class="card">
        <h2>Schnell oder bequem?</h2>
        <p class="muted">Wie soll der Autopilot bei einer Störung abwägen?</p>
        <div class="slider-row">
          <span class="end">🛋️ Maximaler Komfort</span>
          <input type="range" id="speed-comfort" min="0" max="100" step="5" value="${p.speed_vs_comfort}">
          <span class="end right">⚡ Schnellste Ankunft</span>
        </div>
        <div class="slider-value" id="speed-comfort-label"></div>

        <label class="field" style="margin-top:14px">Maximale Umstiege bei Umplanung</label>
        <div class="choices cols-3" data-group="max_transfers">
          <button type="button" class="choice" data-value="0"><span class="choice-title">Direkt</span></button>
          <button type="button" class="choice" data-value="2"><span class="choice-title">Bis 2</span></button>
          <button type="button" class="choice" data-value="9"><span class="choice-title">Egal</span></button>
        </div>
      </div>
    `));
    setNav({ back: true, next: state.editReturn ? "Speichern" : "Weiter" });

    // Kachel-Gruppen initialisieren
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
        v < 25 ? "Komfort geht vor — lieber später, aber entspannt"
        : v < 50 ? "Eher Komfort, Tempo zählt aber"
        : v < 75 ? "Eher Tempo, Komfort zählt aber"
        : "Tempo geht vor — Hauptsache schnellstmöglich ankommen";
    };
    $("#speed-comfort").addEventListener("input", sliderLabel);
    sliderLabel();
  },

  // -- 6: Zuhause & Constraints --------------------------------------------------------------
  home() {
    const h = state.profile.home;
    screen.replaceChildren(el(`
      <div class="card">
        <h2>Zuhause &amp; harte Grenzen</h2>
        <p class="muted">Damit weiß der Autopilot, wie weit eine Umleitung gehen darf — und wann ein Hotel die bessere Option ist als eine Nacht im Zug.</p>

        <label class="field">Heimatbahnhof
          <span class="hint">Suche nutzt Live-DB-Daten, sobald der db_service läuft</span>
          <span class="autocomplete">
            <input type="text" id="home-station" placeholder="z. B. München Hbf" autocomplete="off" value="${h.home_station?.name || ""}">
            <span id="station-suggestions"></span>
          </span>
        </label>

        <label class="field">Späteste Ankunft zuhause
          <span class="hint">Danach schlägt der Autopilot lieber ein Hotel vor</span>
          <input type="time" id="latest-arrival" value="${h.latest_arrival_home}">
        </label>

        <div class="switch-row">
          <span>Hotel-Übernachtung okay<span class="sub">Bei Strandung darf ein Hotel vorgeschlagen werden</span></span>
          <label class="switch"><input type="checkbox" id="hotel-ok" ${h.hotel_ok ? "checked" : ""}><span class="track"></span></label>
        </div>
        <div class="switch-row">
          <span>Taxi für die letzte Meile okay<span class="sub">Wenn der letzte Anschluss wegfällt</span></span>
          <label class="switch"><input type="checkbox" id="taxi-ok" ${h.taxi_ok ? "checked" : ""}><span class="track"></span></label>
        </div>
      </div>
    `));
    setNav({ back: true, next: state.editReturn ? "Speichern" : "Weiter" });

    // Autocomplete gegen /api/stations (Live-Sidecar mit Fallback)
    const input = $("#home-station");
    const sugBox = $("#station-suggestions");
    let selected = h.home_station || null;
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

    // Auswahl für den Weiter-Klick merken
    screen._getHomeStation = () => selected || (input.value.trim() ? { id: null, name: input.value.trim() } : null);
  },

  // -- 7: Benachrichtigungen & Autonomie ----------------------------------------------------------
  notifications() {
    const n = state.profile.notifications;
    const channels = new Set(n.channels);
    screen.replaceChildren(el(`
      <div class="card">
        <h2>Benachrichtigungen</h2>
        <div class="switch-row">
          <span>Push-Mitteilungen<span class="sub">In der App, immer aktuell</span></span>
          <label class="switch"><input type="checkbox" data-channel="push" ${channels.has("push") ? "checked" : ""}><span class="track"></span></label>
        </div>
        <div class="switch-row">
          <span>WhatsApp / SMS<span class="sub">${n.phone_verified ? `An ${n.phone}` : "Erfordert bestätigte Mobilnummer"}</span></span>
          <label class="switch"><input type="checkbox" data-channel="whatsapp" ${channels.has("whatsapp") ? "checked" : ""} ${n.phone_verified ? "" : "disabled"}><span class="track"></span></label>
        </div>
        <div class="switch-row">
          <span>E-Mail<span class="sub">Zusammenfassungen &amp; Belege</span></span>
          <label class="switch"><input type="checkbox" data-channel="email" ${channels.has("email") ? "checked" : ""}><span class="track"></span></label>
        </div>
        <label class="field" style="margin-top:12px">Ruhezeiten <span class="hint">Keine Benachrichtigungen außer Notfällen</span></label>
        <div style="display:flex; gap:10px; align-items:center">
          <input type="time" id="quiet-from" value="${n.quiet_hours.from}"> <span class="muted">bis</span>
          <input type="time" id="quiet-to" value="${n.quiet_hours.to}">
        </div>
      </div>

      <div class="card">
        <h2>Wie selbstständig darf der Autopilot sein?</h2>
        <div class="choices cols-1" data-group="autonomy">
          <button type="button" class="choice" data-value="notify_only">
            <span class="choice-title">🔔 Nur informieren</span>
            <span class="choice-sub">Der Autopilot warnt und schlägt vor — du machst alles selbst.</span>
          </button>
          <button type="button" class="choice" data-value="approve_each">
            <span class="choice-title">✋ Jede Aktion freigeben <i>(empfohlen)</i></span>
            <span class="choice-sub">Umbuchungen, Nachrichten &amp; Anträge erst nach deinem Okay.</span>
          </button>
          <button type="button" class="choice" data-value="auto_within_limits">
            <span class="choice-title">🤖 Automatisch in Grenzen</span>
            <span class="choice-sub">Kostenfreie Umbuchungen automatisch, alles andere mit Freigabe.</span>
          </button>
        </div>
      </div>
    `));
    setNav({ back: true, next: state.editReturn ? "Speichern" : "Weiter" });

    const box = screen.querySelector('[data-group="autonomy"]');
    box.querySelectorAll(".choice").forEach((btn) => {
      if (btn.dataset.value === state.profile.autonomy) btn.classList.add("selected");
      btn.addEventListener("click", () => {
        box.querySelectorAll(".choice").forEach((b) => b.classList.remove("selected"));
        btn.classList.add("selected");
      });
    });
  },

  // -- 8: Zusammenfassung ------------------------------------------------------------------------
  summary() {
    const p = state.profile;
    const pref = p.preferences;
    const autonomyLabel = {
      notify_only: "Nur informieren",
      approve_each: "Jede Aktion freigeben",
      auto_within_limits: "Automatisch in Grenzen",
    }[p.autonomy];

    screen.replaceChildren(el(`
      <div class="card">
        <h2>Alles startklar? 🚦</h2>
        <div class="summary-row"><span class="k">DB-Konto</span><span class="v">✓ ${state.account.display_name}</span></div>
        <div class="summary-row"><span class="k">Importierte Reisen</span><span class="v">${state.trips.length}</span></div>
        <div class="summary-row"><span class="k">Mobilnummer</span><span class="v">${p.notifications.phone_verified ? "✓ " + p.notifications.phone : "— übersprungen"}</span></div>
        <div class="summary-row"><span class="k">Outlook-Kalender</span><span class="v">${p.connections.outlook ? "✓ verbunden" : "— übersprungen"}</span></div>
        <div class="summary-row"><span class="k">Klasse / Sitzplatz</span><span class="v">${pref.travel_class}. Klasse · ${seatLabel(pref)}</span></div>
        <div class="summary-row"><span class="k">Tempo vs. Komfort</span><span class="v">${pref.speed_vs_comfort} / 100</span></div>
        <div class="summary-row"><span class="k">Max. Umstiege</span><span class="v">${pref.max_transfers >= 9 ? "egal" : pref.max_transfers}</span></div>
        <div class="summary-row"><span class="k">Heimatbahnhof</span><span class="v">${p.home.home_station?.name || "—"}</span></div>
        <div class="summary-row"><span class="k">Späteste Heimkehr</span><span class="v">${p.home.latest_arrival_home} Uhr</span></div>
        <div class="summary-row"><span class="k">Autonomie</span><span class="v">${autonomyLabel}</span></div>
      </div>
      <p class="muted" style="padding: 0 6px">Mit dem Abschluss beginnt der Autopilot, deine importierten Reisen zu überwachen. Jede Einstellung ist später im Profil änderbar.</p>
    `));
    setNav({ back: true, next: "Onboarding abschließen 🚀" });
  },

  // -- Dashboard -------------------------------------------------------------------------------
  dashboard() {
    const p = state.profile;
    const pref = p.preferences;
    const nextTrip = state.trips[0];
    const cards = state.trips
      .map((t) => tripCardHTML(t, { foot: "Wird vom Autopilot überwacht", live: true }))
      .join("");

    screen.replaceChildren(el(`
      <div class="dash-greeting">
        <h1>Hallo ${state.account.first_name} 👋</h1>
        <p class="muted">${nextTrip
          ? `Deine nächste Reise startet ${fmtDate(nextTrip.planned_departure)} um ${fmtTime(nextTrip.planned_departure)} Uhr — der Autopilot wacht.`
          : "Keine bevorstehenden Reisen — der Autopilot ist bereit."}</p>
      </div>

      <div class="section-title"><h2>Überwachte Reisen</h2></div>
      ${cards || '<div class="card"><p class="muted">Keine Reisen importiert.</p></div>'}

      <div class="section-title"><h2>Dein Profil</h2><button id="edit-prefs" type="button">Bearbeiten</button></div>
      <div class="card" style="padding: 12px 16px">
        <div class="summary-row"><span class="k">Klasse / Sitzplatz</span><span class="v">${pref.travel_class}. Klasse · ${seatLabel(pref)}</span></div>
        <div class="summary-row"><span class="k">Tempo vs. Komfort</span><span class="v">${pref.speed_vs_comfort} / 100</span></div>
        <div class="summary-row"><span class="k">Heimatbahnhof</span><span class="v">${p.home.home_station?.name || "—"}</span></div>
        <div class="summary-row"><span class="k">Autonomie</span><span class="v">${{ notify_only: "Nur informieren", approve_each: "Jede Aktion freigeben", auto_within_limits: "Automatisch in Grenzen" }[p.autonomy]}</span></div>
      </div>

      <div class="section-title"><h2>Verbindungen</h2><button id="edit-connections" type="button">Verwalten</button></div>
      <div class="card" style="padding: 12px 16px">
        <div class="summary-row"><span class="k">DB-Konto</span><span class="v">✓ ${state.account.email}</span></div>
        <div class="summary-row"><span class="k">Mobilnummer</span><span class="v">${p.notifications.phone_verified ? "✓ " + p.notifications.phone : "nicht bestätigt"}</span></div>
        <div class="summary-row"><span class="k">Outlook</span><span class="v">${p.connections.outlook ? "✓ verbunden" : "nicht verbunden"}</span></div>
      </div>

      <div class="card">
        <p class="muted" style="margin-top:0">Deine Daten gehören dir: Mit einem Klick löschst du Profil, Verbindungen und importierte Reisen unwiderruflich (DSGVO Art. 17).</p>
        <button class="btn danger block" id="delete-profile" type="button">Profil &amp; Daten löschen</button>
      </div>
    `));
    setNav({ back: false, next: "Weiter" });
    $("#navbar").hidden = true;
    $("#progress").hidden = true;
    $("#tabbar").hidden = false; // Mock-Tableiste des DB Navigators

    $("#edit-prefs").addEventListener("click", () => { state.editReturn = true; go("preferences"); });
    $("#edit-connections").addEventListener("click", () => { state.editReturn = true; go("phone"); });
    $("#delete-profile").addEventListener("click", async () => {
      if (!confirm("Wirklich alle Daten löschen? Das kann nicht rückgängig gemacht werden.")) return;
      await api("/api/profile", { method: "DELETE" });
      sessionStorage.removeItem("ja_token");
      Object.assign(state, { token: null, account: null, profile: null, trips: [], outlookEvents: [], editReturn: false });
      updateTopbarAccount();
      toast("Alle Daten gelöscht. Bis bald!");
      go("welcome");
    });
  },
};

// ---------------------------------------------------------------------------
// Navigation: Schritt speichern, dann weiter
// ---------------------------------------------------------------------------

async function persistCurrentStep() {
  switch (state.step) {
    case "preferences": {
      const groupVal = (g) => screen.querySelector(`[data-group="${g}"] .choice.selected`)?.dataset.value;
      await saveProfile({
        preferences: {
          travel_class: Number(groupVal("travel_class")),
          seat_location: groupVal("seat_location"),
          seat_area: groupVal("seat_area"),
          quiet_zone: $("#quiet-zone").checked,
          speed_vs_comfort: Number($("#speed-comfort").value),
          max_transfers: Number(groupVal("max_transfers")),
        },
      });
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
      });
      break;
    case "notifications": {
      const channels = [...screen.querySelectorAll("[data-channel]")]
        .filter((c) => c.checked).map((c) => c.dataset.channel);
      await saveProfile({
        notifications: {
          channels,
          quiet_hours: { from: $("#quiet-from").value, to: $("#quiet-to").value },
        },
        autonomy: screen.querySelector('[data-group="autonomy"] .choice.selected')?.dataset.value,
      });
      break;
    }
  }
}

function go(step) {
  state.step = step;
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
    toast("🎉 Onboarding abgeschlossen — gute Reise!");
    go("dashboard");
    return;
  }
  if (state.editReturn) {
    state.editReturn = false;
    toast("✓ Gespeichert");
    go("dashboard");
    return;
  }
  go(STEPS[STEPS.indexOf(state.step) + 1]);
}

function back() {
  if (state.editReturn) { state.editReturn = false; go("dashboard"); return; }
  const idx = STEPS.indexOf(state.step);
  // Vom Telefon-Schritt zurück zur Reise-Übersicht, nicht zum Login
  go(STEPS[Math.max(0, idx - 1)]);
}

// ---------------------------------------------------------------------------
// Events & Start
// ---------------------------------------------------------------------------

$("#btn-next").addEventListener("click", next);
$("#btn-back").addEventListener("click", back);
$("#btn-skip").addEventListener("click", () => {
  if (state.editReturn) { state.editReturn = false; go("dashboard"); return; }
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
    renderers.outlook();
  } catch (err) {
    toast(`⚠️ ${err.message}`);
  }
});

async function boot() {
  if (state.token) {
    try {
      const data = await api("/api/me");
      state.account = data.account;
      state.profile = data.profile;
      state.trips = data.trips;
      updateTopbarAccount();
      // Laufende Session: fertige Nutzer landen im Dashboard, alle anderen
      // machen nach dem Login-Schritt weiter.
      go(state.profile.onboarding_completed ? "dashboard" : "trips");
      return;
    } catch {
      sessionStorage.removeItem("ja_token");
      state.token = null;
    }
  }
  go("welcome");
}

boot();
