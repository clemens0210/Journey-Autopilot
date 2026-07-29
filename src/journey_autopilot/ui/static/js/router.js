/* Screen registry and navigation.
 *
 * Screen modules call `registerScreens({ name: fn })` at import time; `go(step)`
 * looks the renderer up by name. A registry rather than one big `renderers`
 * object literal, because that object was the reason every screen had to live
 * in the same file — and a registry breaks the import cycle a static map would
 * create (router needs the screens, every screen needs `go`).
 *
 * `go(step)` deliberately does NOT use optional chaining: navigating to a step
 * nobody registered is a bug that should be loud. `rerender()` does, because it
 * fires from async callbacks that may outlive the screen they started on.
 */

import { state } from "./state.js";
import { screen, hideSmsBanner, setProgress } from "./dom.js";

const renderers = {};

export function registerScreens(map) {
  Object.assign(renderers, map);
}

export function go(step) {
  state.step = step;
  hideSmsBanner(); // a pending verification code belongs to the previous screen
  // Chat and trip detail are full-height flex layouts (scrolling body with a
  // pinned header/footer); other screens scroll normally.
  const chatMode = step === "chat" || step === "tripdetail";
  document.querySelector(".phone").classList.toggle("chat-active", chatMode);
  screen.classList.toggle("chat-mode", chatMode);
  setProgress(step);
  renderers[step]();
  screen.scrollTop = 0;
  window.scrollTo(0, 0);
}

// Redraw whatever screen is currently open, without re-running navigation.
// Used by async flows (the Outlook poll, the simulated consent modal) that
// finish while an arbitrary screen is on display.
export function rerender() {
  renderers[state.step]?.();
}
