/* Pure formatting and trip-time predicates — no DOM, no state, no fetch.
 *
 * The trip predicates all agree on one rule: a trip with unparseable dates is
 * never treated as past, so a bad date can only ever under-claim.
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

export const tripEndTime = (trip) => {
  const time = new Date(trip?.planned_arrival || trip?.planned_departure || "").getTime();
  return Number.isFinite(time) ? time : null;
};

export const isPastTrip = (trip, now = new Date()) => {
  const end = tripEndTime(trip);
  return end !== null && end < now.getTime();
};

export const isUpcomingTrip = (trip, now = new Date()) => {
  const start = tripStartTime(trip);
  return start !== Number.MAX_SAFE_INTEGER && start >= now.getTime();
};

// Header badge for the trip detail / chat screens. The green "● live" dot only
// makes sense while the journey can still change — on a trip that already
// arrived it claims a live feed that isn't running. Finished trips get a
// neutral label instead, matching the dashboard card's "Past trip" footer.
// A trip with unparseable dates is not treated as past, so it keeps "● live".
export const tripLiveBadge = (trip) =>
  isPastTrip(trip)
    ? `<span class="chat-live past">Past trip</span>`
    : `<span class="chat-live">● live</span>`;

export const sortTripsByDate = (trips) =>
  [...(trips || [])].sort((a, b) => tripStartTime(a) - tripStartTime(b));
