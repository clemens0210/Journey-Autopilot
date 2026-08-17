/**
 * Shared recorder for the Journey Autopilot demo takes.
 *
 * Drives the real app in Chromium at the phone viewport (430x932 — the width the
 * app's only media query, `max-width: 470px`, targets, so the layout is the true
 * mobile one), draws a synthetic pointer, and captures a CDP screencast that is
 * assembled into an uncut MP4. No music, no trimming: what the run did is what
 * the file shows.
 *
 * Every take writes three files into out/:
 *   <name>.mp4        the raw recording
 *   <name>.marks.json timestamped events, so a later edit can find moments
 *   <name>.log.txt    what the agent actually answered, for review
 */
import { chromium } from 'playwright';
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';
import { execFileSync } from 'child_process';

const HERE = path.dirname(fileURLToPath(import.meta.url));
export const OUT_DIR = path.resolve(HERE, '..', 'out');
const FFMPEG = process.env.FFMPEG_BIN || '/opt/homebrew/bin/ffmpeg';
export const APP = process.env.JA_APP_URL || 'http://127.0.0.1:8000';

const CURSOR_JS = `
(() => {
  if (document.getElementById('__cur')) return;
  const c = document.createElement('div');
  c.id = '__cur';
  c.style.cssText = 'position:fixed;left:-100px;top:-100px;width:26px;height:26px;border-radius:50%;' +
    'background:rgba(255,255,255,.92);border:2px solid rgba(0,0,0,.35);' +
    'box-shadow:0 4px 14px rgba(0,0,0,.5);pointer-events:none;z-index:2147483647;' +
    'transform:translate(-50%,-50%);transition:transform .09s ease-out;';
  document.documentElement.appendChild(c);
  window.__moveCur = (x, y) => { c.style.left = x + 'px'; c.style.top = y + 'px'; };
  window.__tapCur = () => {
    c.style.transform = 'translate(-50%,-50%) scale(.62)';
    const r = document.createElement('div');
    r.style.cssText = 'position:fixed;left:' + parseFloat(c.style.left) + 'px;top:' + parseFloat(c.style.top) + 'px;' +
      'width:26px;height:26px;border-radius:50%;border:2px solid rgba(255,255,255,.85);' +
      'pointer-events:none;z-index:2147483646;transform:translate(-50%,-50%);opacity:1;' +
      'transition:transform .45s cubic-bezier(.2,.7,.3,1),opacity .45s ease-out;';
    document.documentElement.appendChild(r);
    requestAnimationFrame(() => { r.style.transform = 'translate(-50%,-50%) scale(3.2)'; r.style.opacity = '0'; });
    setTimeout(() => { c.style.transform = 'translate(-50%,-50%) scale(1)'; r.remove(); }, 460);
  };
  const s = document.createElement('style');
  s.textContent = '::-webkit-scrollbar{width:0!important;height:0!important} *{scrollbar-width:none!important}';
  (document.head || document.documentElement).appendChild(s);
})();
`;

const ease = (t) => (t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2);

export async function startTake(name) {
  fs.mkdirSync(OUT_DIR, { recursive: true });
  const framesDir = path.join(OUT_DIR, `.frames-${name}`);
  fs.rmSync(framesDir, { recursive: true, force: true });
  fs.mkdirSync(framesDir, { recursive: true });

  const browser = await chromium.launch({ args: ['--force-color-profile=srgb'] });
  const ctx = await browser.newContext({
    viewport: { width: 430, height: 932 },
    deviceScaleFactor: 2,
    colorScheme: 'dark',
  });
  const page = await ctx.newPage();

  const logLines = [];
  const marks = [];
  let t0 = 0;
  const now = () => (Date.now() - t0) / 1000;

  page.on('pageerror', (e) => logLines.push(`[pageerror] ${String(e).slice(0, 300)}`));

  await page.goto(APP, { waitUntil: 'networkidle' });
  await page.waitForTimeout(500);
  await page.evaluate(CURSOR_JS);
  if (!(await page.evaluate(() => typeof window.__moveCur === 'function'))) {
    throw new Error('cursor injection failed — the take would have no pointer');
  }

  let cx = 215, cy = 700;
  await page.evaluate(([x, y]) => window.__moveCur(x, y), [cx, cy]);

  const client = await ctx.newCDPSession(page);
  const index = [];
  let n = 0;
  client.on('Page.screencastFrame', async ({ data, sessionId }) => {
    const f = `f${String(n++).padStart(5, '0')}.jpg`;
    fs.writeFileSync(path.join(framesDir, f), Buffer.from(data, 'base64'));
    index.push({ f, t: Date.now() });
    try { await client.send('Page.screencastFrameAck', { sessionId }); } catch {}
  });
  await client.send('Page.startScreencast', { format: 'jpeg', quality: 92, everyNthFrame: 1 });
  t0 = Date.now();

  const api = {
    page,
    mark(label) {
      const t = now();
      marks.push({ t: +t.toFixed(2), label });
      console.log(`  ⏱ ${t.toFixed(2).padStart(7)}s  ${label}`);
      return t;
    },
    log(line) {
      logLines.push(line);
      console.log(`     ${String(line).slice(0, 160)}`);
    },
    async glide(x, y, ms = 600) {
      const steps = Math.max(12, Math.round(ms / 16));
      const sx = cx, sy = cy;
      const ax = (sx + x) / 2 + (y - sy) * 0.12;
      const ay = (sy + y) / 2 - (x - sx) * 0.12;
      for (let i = 1; i <= steps; i++) {
        const u = ease(i / steps), v = 1 - u;
        const px = v * v * sx + 2 * v * u * ax + u * u * x;
        const py = v * v * sy + 2 * v * u * ay + u * u * y;
        await page.mouse.move(px, py);
        await page.evaluate(([X, Y]) => window.__moveCur && window.__moveCur(X, Y), [px, py]);
        await page.waitForTimeout(16);
      }
      cx = x; cy = y;
    },
    /** Glide to an element, tap it, and click it for real. */
    async tap(selectorOrLocator, label, opts = {}) {
      const el = typeof selectorOrLocator === 'string'
        ? page.locator(selectorOrLocator).first()
        : selectorOrLocator;
      await el.waitFor({ state: 'visible', timeout: opts.timeout || 60000 });
      await el.scrollIntoViewIfNeeded().catch(() => {});
      await page.waitForTimeout(220);
      const box = await el.boundingBox();
      if (!box) throw new Error(`no bounding box for ${label || selectorOrLocator}`);
      await api.glide(
        box.x + Math.min(box.width / 2, opts.maxX || 1e6) + (opts.dx || 0),
        box.y + Math.min(box.height / 2, 40) + (opts.dy || 0),
        opts.ms || 600
      );
      await page.waitForTimeout(150);
      await page.evaluate(() => window.__tapCur());
      await page.waitForTimeout(90);
      await el.click({ position: opts.pos, force: opts.force });
      if (label) api.mark(label);
      await page.waitForTimeout(opts.after ?? 300);
    },
    /** Clear first — several fields in this app arrive pre-filled. */
    async type(selector, text, label, delay = 55) {
      const el = page.locator(selector).first();
      await el.waitFor({ state: 'visible', timeout: 30000 });
      await el.fill('');
      const box = await el.boundingBox();
      await api.glide(box.x + Math.min(box.width * 0.4, 150), box.y + box.height / 2, 480);
      await page.evaluate(() => window.__tapCur());
      await el.click();
      await el.pressSequentially(text, { delay });
      if (label) api.mark(label);
    },
    async scroll(px, ms = 1300) {
      const steps = Math.round(ms / 16);
      for (let i = 1; i <= steps; i++) {
        const u = ease(i / steps), up = ease((i - 1) / steps);
        await page.mouse.wheel(0, px * (u - up));
        await page.waitForTimeout(16);
      }
    },
    async pause(ms) { await page.waitForTimeout(ms); },
    async bodyText() { return (await page.locator('body').innerText().catch(() => '')) || ''; },
    /**
     * Read the newest assistant reply the way a person actually would.
     *
     * chat.js pins the log to the bottom on every render (`log.scrollTop =
     * log.scrollHeight`), so a long answer lands with only its LAST few lines
     * on screen — which is why the takes never showed a whole message. This
     * jumps back to the top of the bubble the moment it arrives, holds a beat,
     * then scrolls down through it at reading pace and holds again at the end.
     *
     * The tween runs inside the page on rAF rather than as a stream of CDP
     * scrollTop writes: the screencast captures what the page paints, so an
     * in-page tween is both smoother and cheaper.
     */
    async readReply(label, opts = {}) {
      return api.readBlock('.bubble.assistant:not(.typing)', label, opts);
    },
    /**
     * The same travel, over whatever block you name — the last element in the
     * chat log matching `sel`. Used for the expanded agent trace, which hangs
     * off an OLDER bubble than the last one, so readReply would scroll the
     * wrong thing (and, being three lines long, scroll it by nothing).
     *
     * It always moves at least a little, even when the block already fits: the
     * CDP screencast only emits a frame when the page repaints, so a static
     * hold produces no frames at all and simply collapses out of the encoded
     * take. Anything the film needs to sit on has to be moving.
     */
    async readBlock(sel, label, opts = {}) {
      // Reading pace, not scrolling pace: the film holds these clips for four
      // to six bars, so the answer has to still be travelling at the end of the
      // block rather than having flicked past in a second and a half.
      const { pxPerSec = 125, lead = 900, tail = 1400, min = 2600 } = opts;
      const box = await page.evaluate((q) => {
        const log = document.querySelector('#chat-log');
        if (!log) return null;
        const list = log.querySelectorAll(q);
        if (!list.length) return null;
        const el = list[list.length - 1];
        const top = el.getBoundingClientRect().top - log.getBoundingClientRect().top + log.scrollTop;
        return {
          from: Math.max(0, top - 16),
          max: Math.max(0, log.scrollHeight - log.clientHeight),
          height: el.offsetHeight,
          view: log.clientHeight,
        };
      }, sel);
      if (!box) { await page.waitForTimeout(min); return; }
      const to = Math.min(box.max, box.from + Math.max(0, box.height - box.view + 40));
      await page.evaluate((y) => { document.querySelector('#chat-log').scrollTop = y; }, box.from);
      if (label) api.mark(`read:${label}`);
      await page.waitForTimeout(lead);
      // never less than a slow crawl — see the note above about repaints
      const dist = Math.max(to - box.from, Math.min(120, box.max - box.from));
      if (dist > 8) {
        const ms = Math.max(min, Math.round((dist / pxPerSec) * 1000));
        await page.evaluate(async ({ from, to, ms }) => {
          const log = document.querySelector('#chat-log');
          const t0 = performance.now();
          await new Promise((res) => {
            const step = () => {
              const u = Math.min(1, (performance.now() - t0) / ms);
              // slow in, cruise, slow out — a hand on a scroll wheel, not a jump
              const e = u < 0.5 ? 2 * u * u : 1 - Math.pow(-2 * u + 2, 2) / 2;
              log.scrollTop = from + (to - from) * e;
              if (u < 1) requestAnimationFrame(step); else res();
            };
            requestAnimationFrame(step);
          });
        }, { from: box.from, to: box.from + dist, ms });
      }
      await page.waitForTimeout(tail);
    },
    /**
     * Wait for an agent turn. These call a real LLM and routinely take 60-120 s,
     * so this polls the DOM rather than guessing a timeout.
     */
    async waitFor(predicate, label, timeoutMs = 240000) {
      const deadline = Date.now() + timeoutMs;
      while (Date.now() < deadline) {
        await page.waitForTimeout(1000);
        let hit = false;
        try { hit = await predicate(page, await api.bodyText()); } catch {}
        if (hit) { api.mark(label); return true; }
      }
      api.mark(`${label} — TIMEOUT after ${timeoutMs / 1000}s`);
      api.log(`!! ${label} did not appear within ${timeoutMs / 1000}s`);
      return false;
    },
    /**
     * Wait out one agent turn. chat.js renders `.bubble.assistant.typing` while a
     * turn is in flight and removes it when the answer lands, so the DOM says
     * exactly when the turn is over — no guessed timeout. These turns hit a real
     * LLM and routinely run 60-240 s. A failed turn renders `.bubble.error`,
     * which is surfaced loudly rather than silently passing.
     */
    async waitTurn(label, timeoutMs = 300000) {
      const typing = page.locator('.bubble.assistant.typing');
      await typing.waitFor({ state: 'visible', timeout: 25000 }).catch(() => {});
      const ok = await typing.waitFor({ state: 'detached', timeout: timeoutMs })
        .then(() => true).catch(() => false);
      const t = api.mark(ok ? `replied:${label}` : `TIMEOUT:${label}`);

      const errors = await page.locator('.bubble.error').allInnerTexts().catch(() => []);
      if (errors.length) {
        api.log(`!! AGENT ERROR during "${label}": ${errors[errors.length - 1].slice(0, 300)}`);
      }
      const bubbles = await page.locator('.bubble.assistant:not(.typing)').allInnerTexts().catch(() => []);
      const last = bubbles.length ? bubbles[bubbles.length - 1] : '(no assistant bubble)';
      api.log(`--- ${label} @${t.toFixed(1)}s ---\n${last.slice(0, 2200)}\n`);
      return { ok: ok && !errors.length, text: last, errors };
    },
    /** Type a question into the trip chat, send it, and wait for the answer. */
    async ask(text, label, timeoutMs = 300000) {
      await api.type('#chat-text', text, `typed:${label}`, 32);
      await api.pause(300);
      await api.tap('#chat-send', `send:${label}`, { after: 700 });
      return api.waitTurn(label, timeoutMs);
    },
    async finish() {
      await client.send('Page.stopScreencast').catch(() => {});
      await page.waitForTimeout(400);
      const total = now();
      await browser.close();

      if (index.length < 2) throw new Error('no frames captured');
      const base = index[0].t;
      const lines = [];
      for (let i = 0; i < index.length; i++) {
        const dur = i + 1 < index.length ? (index[i + 1].t - index[i].t) / 1000 : 0.05;
        lines.push(`file '${path.join(framesDir, index[i].f)}'`);
        lines.push(`duration ${Math.max(0.008, Math.min(dur, 5)).toFixed(4)}`);
      }
      lines.push(`file '${path.join(framesDir, index[index.length - 1].f)}'`);
      const concat = path.join(framesDir, 'concat.txt');
      fs.writeFileSync(concat, lines.join('\n') + '\n');

      const mp4 = path.join(OUT_DIR, `${name}.mp4`);
      execFileSync(FFMPEG, [
        '-y', '-v', 'error', '-f', 'concat', '-safe', '0', '-i', concat,
        '-vsync', 'cfr', '-r', '30', '-c:v', 'libx264', '-preset', 'medium',
        '-crf', '16', '-pix_fmt', 'yuv420p', mp4,
      ]);
      fs.rmSync(framesDir, { recursive: true, force: true });

      fs.writeFileSync(path.join(OUT_DIR, `${name}.marks.json`),
        JSON.stringify({ take: name, durationSeconds: +total.toFixed(2), marks }, null, 2));
      fs.writeFileSync(path.join(OUT_DIR, `${name}.log.txt`), logLines.join('\n') + '\n');

      const size = (fs.statSync(mp4).size / 1e6).toFixed(1);
      console.log(`\n✓ ${name}.mp4 — ${total.toFixed(1)}s, ${index.length} frames, ${size} MB`);
      console.log(`  marks: ${marks.length}   → ${OUT_DIR}`);
      return mp4;
    },
  };
  api.mark('START');
  return api;
}

/** Sign in as the demo user. Credentials arrive pre-filled, so clear before typing. */
export async function signIn(r, { fresh }) {
  await r.pause(1100);
  r.mark('welcome-screen');
  await r.tap('#btn-next', 'tap:lets-go', { after: 900 });
  await r.type('#login-email', 'lucas.wild@example.com', 'typed:email');
  await r.pause(250);
  await r.type('#login-password', 'demo123', 'typed:password', 75);
  await r.pause(350);
  await r.tap(r.page.getByRole('button', { name: /sign in & import/i }), 'tap:sign-in', { after: 0 });
  if (fresh) {
    await r.page.locator('.success-banner, #screen').first().waitFor({ state: 'visible', timeout: 60000 });
  } else {
    await r.page.locator('.trip-card.clickable').first().waitFor({ state: 'visible', timeout: 60000 });
  }
  await r.pause(1200);
  r.mark(fresh ? 'onboarding:trips-imported' : 'dashboard');
}
