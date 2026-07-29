/* DOM helpers and the static chrome around the screen: navbar, progress bar,
 * tab bar, toast, and the simulated SMS banner.
 *
 * These touch elements that live in index.html and outlive every screen, which
 * is why they sit here rather than in a screen module.
 */

import { state, STEPS } from "./state.js";

export const $ = (sel) => document.querySelector(sel);
export const screen = $("#screen");

export function el(html) {
  const t = document.createElement("template");
  t.innerHTML = html.trim();
  return t.content;
}

// Escape user/agent text before injecting it into chat bubbles.
export function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

// Inline SVGs in DB Navigator style — brand mark and icons for the trip cards.
export const SVG = {
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

// Simulated incoming SMS: shows the verification code as a notification
// inside the phone frame (no need to leave the UI). Tapping it fills the
// code input. Re-showing restarts the slide-down animation.
export function showSmsBanner(code) {
  const banner = $("#sms-banner");
  if (!banner) return;
  $("#sms-code").textContent = code;
  banner.hidden = true;
  void banner.offsetWidth; // restart the drop animation on resend
  banner.hidden = false;
}

export function hideSmsBanner() {
  const banner = $("#sms-banner");
  if (banner) banner.hidden = true;
}

let toastTimer = null;

export function toast(msg, ms = 4200) {
  const node = $("#toast");
  node.textContent = msg;
  node.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { node.hidden = true; }, ms);
}

export function setNav({ back = true, next = "Next", skip = null, nextEnabled = true } = {}) {
  $("#tabbar").hidden = true; // tab bar only in the dashboard (see dashboard.js)
  $("#navbar").hidden = false;
  $("#btn-back").style.visibility = back ? "visible" : "hidden";
  $("#btn-next").textContent = next;
  $("#btn-next").disabled = !nextEnabled;
  $("#btn-skip").hidden = !skip;
  if (skip) $("#btn-skip").textContent = skip;
}

export function setProgress(step) {
  const idx = STEPS.indexOf(step);
  const wizard = idx > 0; // Welcome & dashboard have no progress bar
  $("#progress").hidden = !wizard || step === "dashboard";
  if (wizard) {
    $("#progress-fill").style.width = `${(idx / (STEPS.length - 1)) * 100}%`;
    $("#progress-label").textContent = `Step ${idx} of ${STEPS.length - 1}`;
  }
}

export function updateTopbarAccount() {
  const node = $("#topbar-account");
  if (state.account) {
    node.textContent = state.account.first_name;
    node.hidden = false;
  } else {
    node.hidden = true;
  }
}

export function setActiveTab(tab) {
  document.querySelectorAll(".tab").forEach((t) => t.classList.remove("active"));
  const el = tab === "trips" ? $("#tab-trips")
    : tab === "profile" ? $("#tab-profile")
    : tab === "book" ? $("#tab-book") : null;
  if (el) el.classList.add("active");
}

export function showMainTabBar(activeTab) {
  $("#navbar").hidden = true;
  $("#progress").hidden = true;
  $("#tabbar").hidden = false;
  setActiveTab(activeTab);
}
