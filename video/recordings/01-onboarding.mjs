/**
 * TAKE 1 — Onboarding, start to finish, with a few deliberate changes.
 *
 * Run `python scripts/reset_demo.py` first: this take only makes sense against
 * an empty store, where the wizard actually runs.
 *
 * Changed away from the defaults on the way through (everything else is left
 * as it comes):
 *   preferences  · 2nd class      -> 1st class
 *                · quiet zone     -> on
 *   home         · home station   -> München Hbf (live DB station search)
 *                · hotel stay     -> on
 *   notifications· autonomy       -> "Automatic within limits"
 *
 * The phone field arrives pre-filled from DEMO_TRAVELER_NUMBER in .env, which is
 * a real number — it is replaced with a neutral demo one so no personal data is
 * ever on camera.
 */
import { startTake, signIn } from './lib/recorder.mjs';

const DEMO_PHONE = '+49 151 20000042';

const r = await startTake('01-onboarding');
try {
  // --- welcome, DB login, trip import -------------------------------------
  await signIn(r, { fresh: true });
  await r.scroll(220, 900);
  await r.pause(1400);
  r.mark('trips:reviewed');
  await r.scroll(-220, 700);
  await r.tap('#btn-next', 'tap:next→phone', { after: 1200 });

  // --- phone: real verification round trip, neutral number ----------------
  r.mark('step:phone');
  await r.type('#phone-input', DEMO_PHONE, 'typed:phone', 60);
  await r.pause(400);
  await r.tap('#phone-send', 'tap:send-code', { after: 1600 });

  // the app shows the code as a simulated iOS notification; tapping it fills the field
  const banner = r.page.locator('#sms-banner');
  if (await banner.isVisible().catch(() => false)) {
    const code = (await r.page.locator('#sms-code').innerText().catch(() => '')).trim();
    r.log(`SMS banner code: ${code}`);
    await r.tap(banner, 'tap:sms-banner', { after: 900 });
  } else {
    r.log('!! no SMS banner appeared');
  }
  const confirm = r.page.locator('#screen button:visible').filter({ hasText: /confirm|verify|bestätig/i }).first();
  if (await confirm.count()) await r.tap(confirm, 'tap:confirm-code', { after: 2000 });
  await r.waitFor((_p, t) => /is confirmed|verified/i.test(t), 'phone:confirmed', 30000);
  await r.pause(1000);
  await r.tap('#btn-next', 'tap:next→outlook', { after: 1400 });

  // --- outlook: simulated consent -----------------------------------------
  r.mark('step:outlook');
  await r.pause(900);
  await r.tap('#outlook-connect', 'tap:sign-in-with-microsoft', { after: 1800 });
  const accept = r.page.locator('#ms-accept');
  if (await accept.isVisible().catch(() => false)) {
    await r.pause(1200);
    r.mark('outlook:consent-dialog');
    await r.tap(accept, 'tap:accept-consent', { after: 2500 });
  }
  await r.waitFor((_p, t) => /connected|calendar|meeting/i.test(t), 'outlook:connected', 40000);
  // Hold on the "✓ Connected as …" banner, then walk down the Detected events
  // list the wizard renders under it. This is the one place the film can show
  // that the appointments come out of a real personal calendar rather than a
  // fixture, so it is recorded slowly and given its own marks.
  await r.pause(2200);
  r.mark('outlook:connected-banner');
  const evRows = r.page.locator('.event-row');
  const nEvents = await evRows.count().catch(() => 0);
  r.log(`detected events: ${nEvents}`);
  if (nEvents) {
    await evRows.first().scrollIntoViewIfNeeded().catch(() => {});
    await r.pause(1400);
    r.mark('outlook:events-imported');
    await r.scroll(230, 1700);
    await r.pause(2400);
    await r.scroll(170, 1400);
    await r.pause(2400);
    r.mark('outlook:events-read');
    await r.scroll(-380, 1100);
    await r.pause(1200);
  }
  r.log('calendar preview: ' + (await r.bodyText()).replace(/\s+/g, ' ').slice(0, 400));
  await r.tap('#btn-next', 'tap:next→preferences', { after: 1400 });

  // --- preferences: 1st class + quiet zone --------------------------------
  r.mark('step:preferences');
  await r.pause(800);
  await r.tap(r.page.locator('.choice').filter({ hasText: '1st class' }).first(), 'CHANGED:1st-class', { after: 900 });
  await r.scroll(300, 1000);
  await r.pause(600);
  await r.tap(r.page.locator('#quiet-zone').locator('xpath=ancestor::label[1]'), 'CHANGED:quiet-zone-on', { after: 900 });
  await r.scroll(300, 1000);
  await r.pause(1400);
  r.mark('preferences:reviewed');
  await r.tap('#btn-next', 'tap:next→home', { after: 1400 });

  // --- home: station via live DB search + hotel on -------------------------
  r.mark('step:home');
  await r.pause(700);
  // the list is populated from the live DB station API, so wait for it rather
  // than sampling once — and it must be dismissed or it covers the switches below
  await r.type('#home-station', 'München Hbf', 'typed:home-station', 70);
  const sug = r.page.locator('#station-suggestions button').first();
  const gotSug = await sug.waitFor({ state: 'visible', timeout: 12000 }).then(() => true).catch(() => false);
  if (gotSug) {
    r.log('station suggestions: ' + (await r.page.locator('#station-suggestions').innerText()).replace(/\s+/g, ' ').slice(0, 120));
    await r.tap(sug, 'tap:station-suggestion', { after: 1100 });
  } else {
    r.log('!! no station suggestion list appeared — typed value kept');
    await r.page.locator('#home-station').press('Escape').catch(() => {});
  }
  await r.page.locator('#station-suggestions button').first()
    .waitFor({ state: 'hidden', timeout: 5000 }).catch(() => {});
  await r.scroll(260, 900);
  await r.pause(500);
  await r.tap(r.page.locator('#hotel-ok').locator('xpath=ancestor::label[1]'), 'CHANGED:hotel-ok-on', { after: 900 });
  await r.scroll(300, 1000);
  await r.pause(1300);
  await r.tap('#btn-next', 'tap:next→notifications', { after: 1400 });

  // --- notifications: autonomy up -----------------------------------------
  r.mark('step:notifications');
  await r.pause(800);
  await r.scroll(340, 1100);
  await r.pause(700);
  // "Approve every action", NOT "Automatic within limits".
  //
  // This is a policy setting, not a preference: policy.py maps the wizard's
  // three tiles onto conservative / balanced / aggressive, and under
  // `aggressive` a free rebooking resolves to `auto` — the tile even says so
  // ("Free rebookings happen automatically"). Recorded that way, choosing a
  // reroute goes straight to "you're rebooked" and the veto gate never appears,
  // which is the one beat the film is built around. `approve_each` makes the
  // gate fire deterministically and makes the film's own claim true.
  await r.tap(
    r.page.locator('.choice').filter({ hasText: /Approve every action/i }).first(),
    'CHANGED:autonomy-approve-each',
    { after: 1200 }
  );
  await r.pause(1400);
  await r.tap('#btn-next', 'tap:next→summary', { after: 1600 });

  // --- summary + finish ----------------------------------------------------
  r.mark('step:summary');
  await r.pause(1200);
  await r.scroll(360, 1300);
  await r.pause(1600);
  r.log('summary: ' + (await r.bodyText()).replace(/\s+/g, ' ').slice(0, 600));
  await r.scroll(-360, 900);
  await r.tap('#btn-next', 'tap:finish-onboarding', { after: 3500 });

  await r.waitFor((p) => p.locator('.trip-card').first().isVisible().catch(() => false), 'dashboard:reached', 40000);
  await r.pause(2500);
  await r.scroll(280, 1100);
  await r.pause(2000);
  r.mark('dashboard:reviewed');

  // --- Profile → Automation & veto: pin rebooking to "Always ask" ----------
  //
  // The wizard's autonomy tiles are only a starting point: policy.resolve()
  // reads the per-tool rules underneath them, and the shipped default for
  // `book_alternative_connection` is a €50 cost threshold. The demo reroute
  // costs €0.00, so under BOTH "Approve every action" and "Automatic within
  // limits" it resolves to `auto` and the Executor rebooks without asking —
  // which is exactly what happened on the first two attempts at take 4, and it
  // takes the film's central beat off camera.
  //
  // Pinning the rule here is not a workaround: it is the product's own
  // per-action override screen, it is the honest way to get the behaviour the
  // film claims, and it makes take 4's veto gate deterministic instead of a
  // coin flip on the model's mood.
  await r.scroll(-280, 700);
  await r.tap('#tab-profile', 'tap:profile-tab', { after: 1800 });
  r.mark('step:profile');
  await r.pause(900);
  await r.tap('#edit-policy', 'tap:manage-automation', { after: 1800 });
  r.mark('step:automation-veto');
  await r.pause(1400);
  const sel = r.page.locator('select[data-tool="book_alternative_connection"]');
  await sel.scrollIntoViewIfNeeded().catch(() => {});
  await r.pause(700);
  await sel.selectOption('ask');
  r.mark('CHANGED:rebooking-always-ask');
  await r.pause(2200);
  await r.tap('#save-policy', 'tap:save-automation', { after: 2600 });
  r.log('policy saved: book_alternative_connection = ask');
  await r.pause(1800);
  r.mark('END');
} finally {
  await r.finish();
}
