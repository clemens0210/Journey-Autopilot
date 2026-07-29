/* Book: live journey search via db_service, adds the pick to "Trips".
 *
 * Live-only by design — the backend has no mock fallback for an arbitrary
 * origin/destination search, so a missing sidecar shows an error rather than
 * invented connections.
 */

import { state } from "./state.js";
import { api } from "./api.js";
import { $, el, escapeHtml, screen, setActiveTab, toast } from "./dom.js";
import { fmtDuration, fmtTime, minutesBetween } from "./format.js";
import { attachStationAutocomplete, resolveStation } from "./stations.js";
import { go, registerScreens } from "./router.js";

function nowLocalISO() {
  const d = new Date();
  const pad = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
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

function book() {
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
}

registerScreens({ book });
