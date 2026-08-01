/* Pure formatting and the trip lifecycle phase — no DOM, no state, no fetch.
 *
 * The phase rules agree on one thing: a trip with unparseable dates never
 * advances, so a bad date can only ever under-claim.
 */

// Display labels for the internally stored profile values
export const LABELS = {
  window: "Window", aisle: "Aisle", any: "No preference",
  open_plan: "Open seating", compartment: "Compartment",
};

export const seatLabel = (pref) =>
  `${LABELS[pref.seat_location] || "No preference"}, ${LABELS[pref.seat_area] || "No preference"}${pref.quiet_zone ? ", quiet zone" : ""}`;

export const fmtDate = (iso) => new Date(iso).toLocaleDateString("de-DE", {
  day: "2-digit", month: "2-digit", year: "numeric",
}).replace(/\./g, "/");

export const fmtTime = (iso) => new Date(iso).toLocaleTimeString("de-DE", {
  hour: "2-digit", minute: "2-digit",
});

export const fmtDuration = (minutes) =>
  minutes >= 60 ? `${Math.floor(minutes / 60)}h ${minutes % 60}min` : `${minutes}min`;

export const fmtEur = (n) => `€${Number(n || 0).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;

export const minutesBetween = (isoA, isoB) => Math.round((new Date(isoB) - new Date(isoA)) / 60000);
export const shiftedTime = (iso, delayMinutes) => new Date(new Date(iso).getTime() + delayMinutes * 60000);

export const tripStartTime = (trip) => {
  const time = new Date(trip?.planned_departure || "").getTime();
  return Number.isFinite(time) ? time : Number.MAX_SAFE_INTEGER;
};

/* --- Trip lifecycle phase --------------------------------------------------
 * One vocabulary, shared verbatim with the backend (trip_status.py) and the
 * agent prompts: pre-trip, en route, arrived. The server attaches `status` to
 * every trip it hands out — schedule-derived on the trip list, refined from
 * live data on the trip-detail response — and scheduleStatus below is the
 * fallback for a trip object that never went through the API.
 */
export const TRIP_STATUS = { PRE_TRIP: "pre_trip", EN_ROUTE: "en_route", ARRIVED: "arrived" };

export const STATUS_LABEL = {
  pre_trip: "Pre-trip",
  en_route: "En route",
  arrived: "Arrived",
};

const CONCLUDED_MARGIN_MS = 3 * 60 * 60 * 1000;

export const scheduleStatus = (trip, now = new Date()) => {
  const departure = new Date(trip?.planned_departure || "").getTime();
  const arrival = new Date(trip?.planned_arrival || "").getTime();
  // Unparseable dates land here too — never claim a trip is running or over.
  if (!Number.isFinite(departure) || departure > now.getTime()) return TRIP_STATUS.PRE_TRIP;
  // No usable arrival means nothing can conclude the trip; it stays en route.
  if (Number.isFinite(arrival) && arrival + CONCLUDED_MARGIN_MS <= now.getTime()) {
    return TRIP_STATUS.ARRIVED;
  }
  return TRIP_STATUS.EN_ROUTE;
};

// The server's phase wins when it sent one we recognise (it may have seen live
// data this side cannot); otherwise fall back to the booked times.
export const tripStatus = (trip, now = new Date()) =>
  (STATUS_LABEL[trip?.status] ? trip.status : scheduleStatus(trip, now));

// Header badge for the trip detail / chat screens. The green dot marks a
// journey that can still change — before departure and while it runs. A trip
// that is over has no live feed to signal, so it goes neutral. Pass `status`
// explicitly to use a phase refined elsewhere (e.g. the details response).
export const tripStatusBadge = (trip, status = tripStatus(trip)) =>
  `<span class="trip-status ${status}">${status === TRIP_STATUS.ARRIVED ? "" : "● "}${STATUS_LABEL[status]}</span>`;

export const sortTripsByDate = (trips) =>
  [...(trips || [])].sort((a, b) => tripStartTime(a) - tripStartTime(b));
