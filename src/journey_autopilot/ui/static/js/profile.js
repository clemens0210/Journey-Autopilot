/* The Profile tab and the screens reachable from it:
 * profile → complaints → complaint detail, and profile → connections.
 */

import { state } from "./state.js";
import { api, saveProfile } from "./api.js";
import {
  $, el, escapeHtml, hideSmsBanner, screen, setActiveTab, showMainTabBar,
  showSmsBanner, toast, updateTopbarAccount,
} from "./dom.js";
import { fmtDate, fmtEur, fmtTime, seatLabel } from "./format.js";
import {
  COMPLAINT_STATUS, complaintCardHTML, draftComplaintsCount,
  profileComplaintsNavRow, wireOpenComplaints,
} from "./components.js";
import { setupHomeStationAutocomplete } from "./stations.js";
import { go, registerScreens } from "./router.js";
import { startOutlookConnect } from "./outlook.js";
import { LEGACY_CHAT_STORAGE_KEY, chatStorageKey } from "./chat-store.js";
import { POLICY_LEVEL_LABEL, policyOverrideCount } from "./policy.js";

// -- Profile (reachable via the Profile tab in the bottom tab bar) ---------------
function profile() {
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
    // Must run before state.account is cleared — chatStorageKey() reads it.
    sessionStorage.removeItem(chatStorageKey());
    sessionStorage.removeItem(LEGACY_CHAT_STORAGE_KEY);
    Object.assign(state, { token: null, account: null, profile: null, trips: [], complaints: [], complaintId: null, outlookEvents: [], outlookConnectedThisStep: false, editReturn: null, chats: {}, chat: null });
    updateTopbarAccount();
    toast("All data deleted. See you soon!");
    go("welcome");
  });
}

// -- Complaints (Profile → overview of passenger-rights drafts) ----------------
function complaints() {
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
}

// -- Complaint detail (review draft, submit or dismiss) ------------------------
function complaint_detail() {
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

    ${c.legal_context ? `
      <div class="card">
        <h2 style="margin-top:0;font-size:15px">Legal context</h2>
        <p class="muted complaint-context-note">${c.legal_context_translated
          ? "Translated from DB's official passenger-rights pages (German original linked below):"
          : "Quoted from DB's official passenger-rights pages (German):"}</p>
        ${c.legal_context.split("\n\n--- Next Section ---\n").map((chunk) =>
          `<p class="muted" style="white-space:pre-line">${escapeHtml(chunk.trim())}</p>`
        ).join("")}
        ${(c.legal_sources || []).length ? `
          <div class="complaint-sources">
            ${c.legal_sources.map((url) =>
              `<a href="${escapeHtml(url)}" target="_blank" rel="noopener noreferrer">${escapeHtml(url)}</a>`
            ).join("")}
          </div>
        ` : ""}
      </div>
    ` : ""}

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
}

// -- Connections (reachable via "Manage" on the profile/dashboard) ---------------
function connections() {
  const phoneVerified = state.profile?.notifications?.phone_verified;
  const outlookConnected = state.profile?.connections?.outlook;
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
      connections();
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
        connections();
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
      connections();
    });
  } else {
    $("#outlook-connect").addEventListener("click", () => startOutlookConnect());
  }
}

registerScreens({ profile, complaints, complaint_detail, connections });
