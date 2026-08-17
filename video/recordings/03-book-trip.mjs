/**
 * TAKE 3 — Book a new trip from the Book tab, then let the autopilot look at it.
 *
 * Unlike the other takes this one runs on LIVE data: the station lookup and the
 * connection search both go through the db_service sidecar to the real DB API,
 * so the connections shown are whatever DB is actually offering. The autopilot's
 * verdict is therefore genuinely open — an all-clear is as valid a result as a
 * disruption, and the take records whichever one comes back.
 *
 * Requires an onboarded user and a running sidecar on :3000.
 */
import { startTake, signIn } from './lib/recorder.mjs';

const FROM = 'München Hbf';
const TO = 'Hamburg Hbf';

/** Type into a station field and pick the first live suggestion. */
async function pickStation(r, inputSel, sugSel, value, label) {
  await r.type(inputSel, value, `typed:${label}`, 62);
  const sug = r.page.locator(`${sugSel} button`).first();
  const ok = await sug.waitFor({ state: 'visible', timeout: 12000 }).then(() => true).catch(() => false);
  if (ok) {
    r.log(`${label} suggestions: ` + (await r.page.locator(sugSel).innerText()).replace(/\s+/g, ' ').slice(0, 110));
    await r.tap(sug, `tap:${label}-suggestion`, { after: 900 });
  } else {
    r.log(`!! no suggestions for ${label} — typed value kept`);
  }
  await r.page.locator(`${sugSel} button`).first().waitFor({ state: 'hidden', timeout: 4000 }).catch(() => {});
}

const r = await startTake('03-book-trip');
try {
  await signIn(r, { fresh: false });
  await r.pause(1200);

  // --- Book tab -------------------------------------------------------------
  await r.tap('#tab-book', 'tap:book-tab', { after: 1800 });
  r.mark('book:screen');
  await r.pause(1000);

  await pickStation(r, '#book-from', '#book-from-sug', FROM, 'from');
  await r.pause(500);
  await pickStation(r, '#book-to', '#book-to-sug', TO, 'to');
  await r.pause(900);
  r.log('departure field: ' + (await r.page.locator('#book-depart').inputValue().catch(() => '?')));

  await r.tap('#book-search', 'tap:search-connections', { after: 1200 });
  r.mark('book:searching');

  const results = r.page.locator('#book-results .journey-card, #book-results [class*=card]').first();
  const gotResults = await results.waitFor({ state: 'visible', timeout: 60000 }).then(() => true).catch(() => false);
  if (!gotResults) {
    r.log('!! no connections came back from the live search');
    r.log('book-results: ' + (await r.page.locator('#book-results').innerText().catch(() => '')).slice(0, 400));
    r.mark('END (no connections)');
  } else {
    r.mark('book:results');
    await r.pause(1600);
    r.log('connections: ' + (await r.page.locator('#book-results').innerText()).replace(/\s+/g, ' ').slice(0, 500));
    await r.scroll(280, 1200);
    await r.pause(1600);
    await r.scroll(-280, 800);

    // --- pick one and add it ------------------------------------------------
    await r.tap(results, 'tap:choose-connection', { ms: 700, pos: { x: 150, y: 30 }, after: 1800 });
    r.mark('book:confirm-panel');
    await r.pause(1200);
    await r.type('#book-purpose', 'Partner workshop Hamburg', 'typed:purpose', 45);
    await r.pause(700);
    await r.tap('#book-confirm-btn', 'tap:ADD-TRIP', { after: 3200 });
    r.mark('trip:added');
    await r.pause(2000);

    // --- back to the trip list, open the new trip ---------------------------
    const onTrips = await r.page.locator('.trip-card.clickable').first()
      .waitFor({ state: 'visible', timeout: 20000 }).then(() => true).catch(() => false);
    if (!onTrips) await r.tap('#tab-trips', 'tap:trips-tab', { after: 2200 });
    r.mark('dashboard');
    await r.pause(1400);

    const fresh = r.page.locator('.trip-card.clickable').filter({ hasText: 'Hamburg' }).first();
    const target = (await fresh.count()) ? fresh : r.page.locator('.trip-card.clickable').first();
    await r.tap(target, 'tap:open-new-trip', { ms: 760, pos: { x: 130, y: 30 }, after: 4000 });
    r.mark('trip-detail:new-trip');
    await r.pause(1800);
    await r.scroll(320, 1400);
    await r.pause(1600);
    await r.scroll(-320, 900);

    // --- trigger the autopilot ---------------------------------------------
    await r.tap('#jd-chat', 'tap:ask-the-autopilot', { after: 2200 });
    r.mark('chat:open');
    // readReply, not scroll(): chat.js pins the log to the bottom as it renders,
    // so a plain wheel scroll after a long answer moves nothing and the take only
    // ever showed the last few lines of it.
    const verdict = await r.waitTurn('autopilot-on-new-trip');
    await r.readReply('verdict', { pxPerSec: 130 });
    r.mark('verdict:read');
    r.log(`VERDICT (ok=${verdict.ok}): ${verdict.text.slice(0, 900)}`);

    // one follow-up so the take shows the agent reasoning about this trip, not a canned line
    await r.ask('What is the risk for this specific trip, and would you change anything?', 'follow-up');
    await r.readReply('follow-up', { pxPerSec: 130 });
    r.mark('END');
  }
} finally {
  await r.finish();
}
