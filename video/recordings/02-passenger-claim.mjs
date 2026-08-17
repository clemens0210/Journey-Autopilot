/**
 * TAKE 2 — Passenger-rights claim, all the way to submitting the draft.
 *
 * Uses the already-arrived trip DB-FRA-MUC ("Return from Frankfurt", ICE 521),
 * which lands +128 min late — past the 120-minute threshold, so the 50 %
 * entitlement applies (€ 39.95 of a € 79.90 fare).
 *
 * The automatic monitor turn that fires when the chat opens is what settles the
 * rights lookup and seeds the draft; the chat then renders a "Review complaint →"
 * notice. That link is the product's own path into the draft, so the take
 * follows it rather than detouring through Profile → Complaints (which reads a
 * list only fetched at boot and would show stale data mid-session).
 *
 * Prerequisites: an onboarded user, and NO open draft for this trip — an
 * existing one makes create_draft_complaint a no-op. `npm run reset:claims`
 * clears it.
 */
import { startTake, signIn } from './lib/recorder.mjs';

const r = await startTake('02-passenger-claim');
try {
  await signIn(r, { fresh: false });
  await r.pause(1200);

  // --- open the arrived trip ----------------------------------------------
  const arrived = r.page.locator('.trip-card.clickable').filter({ hasText: 'Return from Frankfurt' }).first();
  await r.tap(arrived, 'tap:trip-return-from-frankfurt', { ms: 760, pos: { x: 130, y: 30 }, after: 3800 });
  r.mark('trip-detail');
  await r.pause(1600);
  await r.scroll(320, 1400);
  await r.pause(1600);
  r.mark('trip-detail:reviewed');
  await r.scroll(-320, 900);

  await r.tap('#jd-chat', 'tap:ask-the-autopilot', { after: 2000 });
  r.mark('chat:open');

  // --- the automatic monitor turn: settles rights, seeds the draft ---------
  // readReply, not scroll(): chat.js pins the log to the bottom as it renders,
  // so a plain wheel scroll after a long answer moves nothing and the take only
  // ever showed the last few lines of it.
  const monitor = await r.waitTurn('auto-monitor');
  await r.readReply('rights-answer', { pxPerSec: 130 });
  r.mark('rights:answer-read');

  // --- into the draft, via the notice the chat just rendered ---------------
  let review = r.page.locator('.notice-link', { hasText: /review complaint/i }).first();
  if (!(await review.count())) {
    // the monitor turn didn't seed it — ask explicitly, which routes the
    // Orchestrator to the Planner and runs get_passenger_rights for real
    r.log('no complaint notice after the monitor turn — asking explicitly');
    await r.ask(
      'This trip is finished. Please run the passenger-rights check and prepare my compensation claim.',
      'rights-explicit'
    );
    await r.readReply('rights-explicit', { pxPerSec: 130 });
    review = r.page.locator('.notice-link', { hasText: /review complaint/i }).first();
  }

  if (!(await review.count())) {
    r.log('!! no complaint draft was created in this run');
    r.log(`monitor reply was: ${monitor.text.slice(0, 500)}`);
    r.mark('END (no draft)');
  } else {
    await r.tap(review, 'tap:review-complaint', { after: 2400 });
    r.mark('complaint:detail');
    await r.pause(1800);
    await r.scroll(360, 1400);
    await r.pause(1800);
    await r.scroll(360, 1400);
    await r.pause(1800);
    r.mark('complaint:read');
    r.log('complaint detail: ' + (await r.bodyText()).replace(/\s+/g, ' ').slice(0, 1000));

    // --- accept the draft --------------------------------------------------
    await r.tap('#complaint-submit', 'tap:SUBMIT-COMPLAINT', { after: 3200 });
    r.mark('complaint:submitted');
    await r.pause(2500);
    r.log('after submit: ' + (await r.bodyText()).replace(/\s+/g, ' ').slice(0, 600));
    await r.scroll(-320, 900);
    await r.pause(2200);
    r.mark('END');
  }
} finally {
  await r.finish();
}
