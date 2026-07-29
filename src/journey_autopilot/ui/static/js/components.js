/* HTML fragments shared by more than one screen: trip cards and complaints. */

import { state } from "./state.js";
import { $, SVG, escapeHtml, toast } from "./dom.js";
import { fmtDate, fmtTime, fmtEur } from "./format.js";
import { go } from "./router.js";

// Route grid for a trip card. Single-leg journeys keep the simple origin →
// destination layout; multi-leg journeys (self-added connections, or any trip
// with >1 leg) render the full station chain — origin → each change station →
// final destination — using the existing .route grid markers.
export function routeHTML(t) {
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
export function tripCardHTML(t, { foot, live = false, index = null, deletable = false } = {}) {
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

export const COMPLAINT_STATUS = {
  draft: "Draft — review & submit",
  submitted: "Submitted",
  rejected: "Dismissed",
};

export function draftComplaintsCount() {
  return state.complaints.filter((c) => c.status === "draft").length;
}

export function profileComplaintsNavRow() {
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

export function complaintCardHTML(c) {
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

export function handleComplaintCreated(complaint) {
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

export function wireOpenComplaints() {
  const btn = $("#open-complaints");
  if (btn) btn.addEventListener("click", () => go("complaints"));
}
