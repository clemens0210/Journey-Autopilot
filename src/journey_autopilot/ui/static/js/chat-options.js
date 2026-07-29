/* Reroute option cards — the selectable alternatives under an agent reply.
 *
 * Purely presentational: builds HTML from the option payload and nothing else.
 * The click handling lives in chat.js, because picking a card starts a chat
 * turn and this module deliberately knows nothing about turns.
 */

import { escapeHtml } from "./dom.js";
import { fmtTime, minutesBetween } from "./format.js";

// Shared inner body for a train journey/reroute option. The helper speaks one
// vocabulary — the arrival field is always `arrival` — so each caller maps its
// source field at the call site: a reroute option passes `new_arrival`, a live
// search result passes `planned_arrival || arrival`. Cost labels distinguish a
// reroute's added cost from a quoted fare so the two values are never conflated.
export function journeyBodyHTML(j) {
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

// Render reroute option cards below the agent's prose. Clicking a card sends
// "Take option <id>" as the next user turn and disables the batch so the user
// can't pick twice. The per-option `source` (db_service_live / mock_*) decides
// the live/demo badge; optionsSource is the fallback for the whole batch.
export function renderOptionCards(options, optionsSource, message, { fallback = false, messageIndex } = {}) {
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
