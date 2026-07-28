/* Station autocomplete against /api/stations.
 *
 * Two variants, kept side by side because they differ only in how the caller
 * gets the pick back:
 *
 * - `setupStationAutocomplete` (home-station fields) returns a getter that
 *   falls back to the typed text, so a half-finished entry still saves.
 * - `attachStationAutocomplete` (Book tab) calls `onSelect` on every pick and
 *   clear, and its getter returns only a real pick — the search needs an EVA
 *   id, and free text is resolved separately via `resolveStation`.
 */

import { api } from "./api.js";
import { $, screen } from "./dom.js";

export function setupStationAutocomplete(inputEl, sugBoxEl, initial) {
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

export function setupHomeStationAutocomplete(home) {
  // Thin wrapper that exposes the selected home station via screen._getHomeStation,
  // preserving the contract the preferences/home/profile screens rely on.
  const getStation = setupStationAutocomplete($("#home-station"), $("#station-suggestions"), home.home_station || null);
  if (getStation) screen._getHomeStation = getStation;
}

// Generic station autocomplete on /api/stations (like the home-station field).
// Returns a getter for the selected station; onSelect fires on pick/clear.
export function attachStationAutocomplete(input, sugBox, onSelect) {
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
export async function resolveStation(text) {
  const q = text.trim();
  if (!q) return null;
  const data = await api(`/api/stations?query=${encodeURIComponent(q)}`).catch(() => ({ stations: [] }));
  return data.stations[0] || null;
}
