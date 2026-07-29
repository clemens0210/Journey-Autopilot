/* Connecting Outlook, from the browser side.
 *
 * Two paths reach the same end state, and BOTH must set
 * `state.outlookConnectedThisStep` — the onboarding step renders from that flag,
 * not from the profile, so a path that forgets it leaves the wizard showing
 * "Sign in with Microsoft" after a successful connect:
 *
 * 1. Real MS Entra device code — start, show the code, poll until complete.
 * 2. Simulated consent modal — the fallback when no Entra app is configured.
 *
 * They live in one module so that shared post-connect contract stays visible.
 * Two screens use them (the onboarding step and Profile → Connections), which
 * is why the flow talks to `rerender()` rather than naming a screen.
 */

import { state } from "./state.js";
import { api } from "./api.js";
import { $, toast } from "./dom.js";
import { rerender } from "./router.js";

export async function startOutlookConnect() {
  const btn = $("#outlook-connect");
  if (btn) btn.disabled = true;
  const container = $("#outlook-device-flow");
  if (container) container.innerHTML = '<div class="device-waiting"><span class="spinner"></span>Starting sign-in…</div>';

  try {
    const data = await api("/api/connect/outlook/start", { method: "POST" });
    if (data.mode === "cached") {
      // A cached Microsoft login was reused — no device code needed. The
      // status poll completes immediately with the account + event preview.
      if (container) container.innerHTML = '<div class="device-waiting"><span class="spinner"></span>Reusing your Microsoft sign-in…</div>';
      pollOutlookStatus(container);
      return;
    }
    if (data.mode === "simulated") {
      // No Entra app configured → fall back to the simulated consent dialog
      if (btn) btn.disabled = false;
      if (container) container.innerHTML = "";
      $("#ms-mail").textContent = state.account.email;
      $("#ms-modal").hidden = false;
      return;
    }
    // Real device-code flow — show code + link, then poll for completion
    if (data.pending || !data.user_code) {
      // prompt_callback didn't fire in time — retry
      if (container) container.innerHTML = '<p class="device-error">Could not start sign-in. Please try again.</p>';
      if (btn) btn.disabled = false;
      return;
    }
    renderDeviceCodeScreen(data, container);
    pollOutlookStatus(container);
  } catch (err) {
    if (container) container.innerHTML = `<p class="device-error">⚠️ ${err.message}</p>`;
    if (btn) btn.disabled = false;
  }
}

function renderDeviceCodeScreen(data, container) {
  container.innerHTML = `
    <div class="device-code-box">
      <div class="device-code-label">Enter this code at Microsoft</div>
      <div class="device-code-value">${data.user_code}</div>
      <button class="device-code-copy" id="dc-copy" type="button">Copy code</button>
    </div>
    <a class="device-link" href="${data.verification_uri}" target="_blank" rel="noopener">Open Microsoft sign-in ↗</a>
    <div class="device-waiting"><span class="spinner"></span>Waiting for you to sign in…</div>
  `;
  $("#dc-copy").addEventListener("click", () => {
    navigator.clipboard.writeText(data.user_code).then(() => {
      $("#dc-copy").textContent = "Copied ✓";
      setTimeout(() => { const c = $("#dc-copy"); if (c) c.textContent = "Copy code"; }, 2000);
    }).catch(() => {});
  });
}

let outlookPollTimer = null;

async function pollOutlookStatus(container) {
  clearTimeout(outlookPollTimer);
  const poll = async () => {
    try {
      const data = await api("/api/connect/outlook/status");
      if (data.status === "complete") {
        state.profile = data.profile;
        state.outlookEvents = data.events || [];
        state.outlookConnectedThisStep = true;
        toast(`✓ Outlook connected — ${(data.events || []).length} events detected`);
        rerender();
        return;
      }
      if (data.status === "expired") {
        container.innerHTML = '<p class="device-error">The code expired. <button class="device-code-copy" id="dc-retry" type="button" style="margin-left:8px">Try again</button></p>';
        const retry = $("#dc-retry");
        if (retry) retry.addEventListener("click", () => startOutlookConnect());
        return;
      }
      if (data.status === "error") {
        container.innerHTML = `<p class="device-error">⚠️ ${data.error}</p>`;
        const btn = $("#outlook-connect");
        if (btn) btn.disabled = false;
        return;
      }
      if (data.status === "none") {
        container.innerHTML = '<p class="device-error">Sign-in session was lost. Please click Sign in with Microsoft again.</p>';
        const btn = $("#outlook-connect");
        if (btn) btn.disabled = false;
        return;
      }
      // pending — keep polling
      outlookPollTimer = setTimeout(poll, 2000);
    } catch (err) {
      container.innerHTML = `<p class="device-error">⚠️ ${err.message}</p>`;
      const btn = $("#outlook-connect");
      if (btn) btn.disabled = false;
    }
  };
  poll();
}

// The simulated Microsoft consent dialog in index.html. Wired once at startup;
// `startOutlookConnect` only unhides it when the server reports no Entra app.
export function wireSimulatedConsentModal() {
  $("#ms-cancel").addEventListener("click", () => { $("#ms-modal").hidden = true; });
  $("#ms-accept").addEventListener("click", async () => {
    $("#ms-modal").hidden = true;
    try {
      const data = await api("/api/connect/outlook", { method: "POST", body: { consent: true } });
      state.profile = data.profile;
      state.outlookEvents = data.events;
      // Same flag the device-code path sets above — see the module header.
      state.outlookConnectedThisStep = true;
      toast(`✓ Outlook connected — ${data.events.length} events detected`);
      rerender();
    } catch (err) {
      toast(`⚠️ ${err.message}`);
    }
  });
}
