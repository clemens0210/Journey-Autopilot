/* Automation & veto — per-write-tool auto/ask plus the global autonomy level.
 *
 * What is saved here is read server-side by journey_autopilot.policy.resolve(),
 * which every write tool calls before acting. The labels and the
 * onboarding→policy mapping live here too, because this screen defines what
 * those levels mean; the wizard's notifications step only seeds them.
 */

import { state } from "./state.js";
import { saveProfile } from "./api.js";
import { $, el, screen, toast, setActiveTab } from "./dom.js";
import { registerScreens } from "./router.js";

export const POLICY_LEVEL_LABEL = {
  conservative: "Conservative — asks before everything",
  balanced: "Balanced",
  aggressive: "Automatic within limits",
};

export const AUTONOMY_TO_LEVEL = {
  notify_only: "conservative",
  approve_each: "balanced",
  auto_within_limits: "aggressive",
};

export function policyOverrideCount(p) {
  const wt = (p.policy && p.policy.write_tools) || {};
  return Object.values(wt).filter((v) => v && v !== "default").length;
}

function policy() {
  const pol = state.profile.policy || { global_autonomy_level: "balanced", book_cost_threshold_eur: 50, write_tools: {} };
  const wt = pol.write_tools || {};
  const level = pol.global_autonomy_level || "balanced";
  const thr = pol.book_cost_threshold_eur ?? 50;

  const opt = (value, label, current) =>
    `<option value="${value}" ${(current || "default") === value ? "selected" : ""}>${label}</option>`;
  const toolSelect = (key, withThreshold = false) => `
    <select data-tool="${key}" class="policy-select">
      ${opt("default", "Default (by level)", wt[key])}
      ${opt("auto", "Always auto", wt[key])}
      ${opt("ask", "Always ask", wt[key])}
      ${withThreshold ? opt("ask_over_threshold", "Ask if over limit", wt[key]) : ""}
    </select>`;
  const toolRow = (label, sub, control) => `
    <div class="switch-row">
      <span>${label}<span class="sub">${sub}</span></span>
      ${control}
    </div>`;

  screen.replaceChildren(el(`
    <div class="dash-greeting">
      <h1>Automation &amp; veto</h1>
      <p class="muted">Decide which actions the autopilot may take on its own and which need your okay. These settings are saved and applied on every run.</p>
    </div>

    <div class="card">
      <h2>How independent should the autopilot be?</h2>
      <div class="choices cols-1" data-group="alevel">
        <button type="button" class="choice" data-value="conservative">
          <span class="choice-title">🛡️ Conservative</span>
          <span class="choice-sub">Ask before every action — maximum control.</span>
        </button>
        <button type="button" class="choice" data-value="balanced">
          <span class="choice-title">⚖️ Balanced</span>
          <span class="choice-sub">Beneficial &amp; free actions run automatically, the rest asks.</span>
        </button>
        <button type="button" class="choice" data-value="aggressive">
          <span class="choice-title">🤖 Automatic within limits</span>
          <span class="choice-sub">Most actions run automatically; hotels &amp; emails to others still ask.</span>
        </button>
      </div>
    </div>

    <div class="card">
      <h2>Per-action overrides</h2>
      <p class="muted" style="margin-top:0">"Default (by level)" follows the choice above. Pin a specific action to always run or always ask.</p>
      ${toolRow("📲 Notify me", "You are the recipient — always automatic", '<span class="v muted">Always auto</span>')}
      ${toolRow("💶 File compensation claim", "Purely beneficial, money back for you", toolSelect("file_compensation_claim"))}
      ${toolRow("🗓️ Move a tentative appointment", "Reversible calendar change", toolSelect("reschedule_outlook_event_tentative"))}
      ${toolRow("📅 Move a confirmed appointment", "Not freely reversible", toolSelect("reschedule_outlook_event_confirmed"))}
      ${toolRow("🔀 Rebook an alternative train", "Cost depends on the option", toolSelect("book_alternative_connection", true))}
      ${toolRow("🏨 Book a hotel", "Cost + overnight — high commitment", toolSelect("book_hotel"))}
      ${toolRow("✉️ Email participants", "Affects third parties (clients, colleagues)", toolSelect("send_email_to_participants"))}

      <label class="field" style="margin-top:12px">Rebooking cost limit (EUR)
        <span class="hint">Used by "Ask if over limit" — under it rebooks automatically, over it asks</span>
        <input type="number" id="book-threshold" min="0" step="5" value="${thr}">
      </label>

      <button class="btn primary block" id="save-policy" type="button" style="margin-top:14px">Save automation settings</button>
    </div>
  `));

  $("#navbar").hidden = true;
  $("#progress").hidden = true;
  $("#tabbar").hidden = false;
  setActiveTab("profile");

  const box = screen.querySelector('[data-group="alevel"]');
  box.querySelectorAll(".choice").forEach((btn) => {
    if (btn.dataset.value === level) btn.classList.add("selected");
    btn.addEventListener("click", () => {
      box.querySelectorAll(".choice").forEach((b) => b.classList.remove("selected"));
      btn.classList.add("selected");
    });
  });

  $("#save-policy").addEventListener("click", async () => {
    const write_tools = {};
    screen.querySelectorAll("select[data-tool]").forEach((s) => { write_tools[s.dataset.tool] = s.value; });
    try {
      await saveProfile({
        policy: {
          global_autonomy_level: box.querySelector(".choice.selected")?.dataset.value || level,
          book_cost_threshold_eur: Number($("#book-threshold").value) || 0,
          write_tools,
        },
      });
      toast("✓ Automation settings saved");
    } catch (err) {
      toast(`⚠️ ${err.message}`);
    }
  });
}

registerScreens({ policy });
