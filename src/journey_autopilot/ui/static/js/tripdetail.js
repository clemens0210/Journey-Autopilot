/* Trip detail: DB Navigator-style itinerary with live delay + expected delay. */

import { state } from "./state.js";
import { api } from "./api.js";
import { $, el, escapeHtml, screen } from "./dom.js";
import { fmtDate, fmtDuration, fmtTime, isPastTrip, minutesBetween, shiftedTime, tripLiveBadge } from "./format.js";
import { go, registerScreens } from "./router.js";
import { openChat } from "./chat.js";

export function openTripDetail(trip) {
  const detail = { trip, data: null, error: null };
  state.tripDetail = detail;
  go("tripdetail");
  api(`/api/trips/${encodeURIComponent(trip.trip_id)}/details`)
    .then((data) => { detail.data = data; })
    .catch((err) => { detail.error = err.message; })
    .finally(() => {
      if (state.step === "tripdetail" && state.tripDetail === detail) tripdetail();
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
// On a trip that already arrived the forecast is dropped entirely: the delay is
// final and confirmed, so predicting one is meaningless — and the historical
// band would even contradict the real outcome (e.g. "Expected: +10 min" next to
// a trip that actually ran 95 min late). Only the facts stay: real delay,
// incidents, stops.
function journeyHTML(data, { past = false } = {}) {
  const incidents = (data.incidents || []).map((inc) => `
    <div class="jd-notice"><b>${escapeHtml(inc.type)}</b> (${escapeHtml(inc.location)}): ${escapeHtml(inc.impact)}</div>
  `).join("");

  const parts = data.legs.map((leg, i) => {
    const delay = leg.current_delay_minutes || 0;
    const fc = leg.forecast || {};
    const expected = fc.expected_delay_minutes ?? 0;
    const legMinutes = minutesBetween(leg.origin.planned, leg.destination.planned);

    // Transfer row between the previous leg's arrival and this departure —
    // built from the same delayed times the stop rows show in red, not from
    // the timetable. The timetable gap claims a comfortable transfer to a
    // train that already left, and hides that a delay on both legs often
    // makes the real transfer *longer*, not shorter.
    let transfer = "";
    if (i > 0) {
      const prev = data.legs[i - 1];
      const transferMin = minutesBetween(
        shiftedTime(prev.destination.planned, prev.current_delay_minutes || 0),
        shiftedTime(leg.origin.planned, delay),
      );
      const missed = transferMin < 0;
      transfer = `
      <div class="jd-transfer">
        <div class="jd-legdur">${missed ? "" : fmtDuration(transferMin)}</div>
        <div class="jd-line dotted"></div>
        <div class="jd-transfer-label${missed ? " missed" : ""}">${missed
          ? `↷ Connection missed by ${-transferMin} min`
          : "↷ Transfer"}</div>
      </div>`;
    }

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
            ${past ? "" : `<span class="jd-chip expected ${fc.level || "low"}">Expected: ${expected > 0 ? `+${expected} min` : "on time"}</span>`}
          </div>
          ${!past && fc.factors && fc.factors.length ? `<div class="jd-forecast-note">Autopilot forecast (${Math.round((fc.confidence || 0) * 100)}% confidence): ${escapeHtml(fc.factors[0])}</div>` : ""}
        </div>
      </div>
      ${stopHTML(leg.destination, delay, { arrival: true })}`;
  }).join("");

  return `
    ${incidents}
    ${!past && data.connection_risk ? `<div class="jd-notice">${escapeHtml(data.connection_risk)}</div>` : ""}
    <div class="jd-timeline">${parts}</div>
    ${past ? "" : `<p class="muted" style="margin-top:14px">Expected delay is the autopilot's risk forecast, based on historical DB punctuality data for this route — not a live prediction.</p>`}`;
}

function tripdetail() {
  const { trip, data, error } = state.tripDetail;
  const duration = minutesBetween(trip.planned_departure, trip.planned_arrival);

  let body;
  if (error) {
    body = `<div class="jd-error">${escapeHtml(error)}</div>`;
  } else if (!data) {
    body = `<div class="device-waiting"><span class="spinner"></span>Loading live journey data…</div>`;
  } else {
    body = journeyHTML(data, { past: isPastTrip(trip) });
  }

  screen.replaceChildren(el(`
    <div class="chat-head">
      <button class="chat-back" id="jd-back" type="button" aria-label="Back">‹</button>
      <div class="chat-trip">
        <span class="chat-route">${trip.origin} → ${trip.destination}</span>
        <span class="chat-sub">${fmtDate(trip.planned_departure)} · Duration: ${fmtDuration(duration)}</span>
      </div>
      ${tripLiveBadge(trip)}
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
}

registerScreens({ tripdetail });
