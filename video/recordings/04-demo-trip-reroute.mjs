/**
 * TAKE 4 — The canonical demo trip: disruption → reroute → the veto gate.
 *
 * Munich → Berlin (ICE 528) is held before Nuremberg at +55 min, which breaks
 * the booked Nuremberg transfer. The take follows the whole write path: the
 * Planner's options, picking one, the Executor's veto gate, the approval, and
 * the agent trace that shows which agent did what.
 *
 * WHY THIS IS NOT THE CALENDAR-CLASH TAKE
 * ---------------------------------------
 * The fixture does hold a clashing appointment ("Client meeting Berlin",
 * confirmed + hard_constraint, 23:38, organiser Anna Client), and
 * classify_window_conflicts detects it correctly when handed the events. But no
 * events ever reach it without a real Microsoft login:
 *
 *   MS_ENTRA_CLIENT_ID unset -> is_calendar_connected() is false, so the
 *       Planner's calendar steps are dropped from its prompt entirely.
 *   MS_ENTRA_CLIENT_ID set   -> get_user_calendar queries Graph for real and,
 *       with no token, returns {"events": [], "source": "outlook"}.
 *
 * The fixture calendar is only served on the first path — the one where the
 * agents are told to ignore appointments — so the two never combine. The
 * Communicator's email (orchestrator.py §6: offer -> draft -> send) is gated on
 * the Planner reporting a clash, so it cannot fire either. Recording that
 * scenario needs `python scripts/check_outlook.py --login` once, or a mock
 * fallback in tools/read/calendar.py.
 */
import { startTake, signIn } from './lib/recorder.mjs';

const r = await startTake('04-demo-trip-reroute');
try {
  await signIn(r, { fresh: false });
  await r.pause(1200);

  // --- open the disrupted trip ---------------------------------------------
  const trip = r.page.locator('.trip-card.clickable').filter({ hasText: 'Client meeting Berlin' }).first();
  await r.tap(trip, 'tap:trip-client-meeting-berlin', { ms: 760, pos: { x: 130, y: 30 }, after: 4000 });
  r.mark('trip-detail');
  await r.pause(1800);
  await r.scroll(340, 1500);
  await r.pause(1800);
  r.mark('trip-detail:missed-connection');
  await r.scroll(340, 1400);
  await r.pause(1600);
  await r.scroll(-680, 1200);

  await r.tap('#jd-chat', 'tap:ask-the-autopilot', { after: 2200 });
  r.mark('chat:open');

  // --- the monitor turn: risk, reroute options, passenger rights ------------
  // readReply, not scroll(): chat.js pins the log to the bottom as it renders,
  // so a plain wheel scroll after a long answer moves nothing and the take only
  // ever showed the last few lines of it. This starts at the top of the bubble
  // and travels down through the whole answer at reading pace.
  const monitor = await r.waitTurn('auto-monitor');
  await r.readReply('monitor', { pxPerSec: 130 });
  r.mark('monitor:read');
  await r.pause(1200);
  r.mark('options:seen');

  // --- pick the recommended reroute ----------------------------------------
  const r1 = r.page.locator('.option-card').filter({ hasText: 'R1' }).first();
  if (await r1.count()) {
    await r.tap(r1, 'tap:choose-R1', { ms: 800, pos: { x: 150, y: 40 }, after: 1600 });
    const gate = await r.waitTurn('veto-gate');
    await r.readReply('veto-gate', { pxPerSec: 130 });
    r.mark('veto-gate:read');
    r.log(`VETO GATE (ok=${gate.ok}): ${gate.text.slice(0, 1200)}`);

    // --- approve: the one place a write is actually released ---------------
    // The gate can take more than one turn to clear: some model tiers restate
    // the plan and ask for a final "yes" before calling the tool. The take
    // follows through rather than stopping on the first approval, because the
    // beat the film needs is the CONFIRMATION — a gate that is asked and never
    // answered is only half the story.
    let reply = await r.ask('Yes, go ahead and rebook me onto R1.', 'approve-rebooking');
    await r.readReply('rebooking', { pxPerSec: 130 });
    // Key on COMPLETION, not on the shape of the question. Two attempts at
    // matching "is it still asking?" both failed on real replies — first
    // "is now confirmed" matched a bare /confirm/, then "Do you want me to
    // proceed with booking option R1?" matched none of the question patterns.
    // What the film needs is unambiguous: keep confirming until the reply says
    // the reroute actually happened.
    const done = /rebooked|is now confirmed|reroute (is )?confirmed|has (already )?been (executed|performed|confirmed|booked)|you'?re all set|booking (is )?(now )?(complete|confirmed)/i;
    for (let n = 0; n < 3 && !done.test(reply.text); n++) {
      r.log(`gate still open after approval ${n + 1} — confirming again`);
      reply = await r.ask('Yes. Approved — please execute the rebooking now.', `confirm-${n + 1}`);
      await r.readReply(`confirm-${n + 1}`, { pxPerSec: 130 });
    }
    if (!done.test(reply.text)) r.log('!! the reroute was never confirmed on this run');
    r.mark('rebooking:result');
    r.log(`AFTER APPROVAL (ok=${reply.ok}): ${reply.text.slice(0, 1200)}`);
  } else {
    r.log('!! no option cards were offered this run');
    r.log(`monitor reply: ${monitor.text.slice(0, 800)}`);
  }

  // --- the agent trace: which agent actually did what ----------------------
  const trace = r.page.locator('summary, [class*=trace]').filter({ hasText: /agent trace/i }).last();
  if (await trace.count()) {
    await r.tap(trace, 'tap:agent-trace', { after: 1800 });
    r.mark('trace:open');
    // readBlock on the OPENED details, not readReply: the trace hangs off an
    // earlier bubble than the last one, and expanding it re-renders the log
    // straight to the bottom — so both a wheel scroll and a read of the last
    // bubble moved nothing, and four bars of the film sat on a still frame that
    // the screencast then did not even emit frames for.
    await r.readBlock('details.chat-trace[open]', 'trace',
      { pxPerSec: 60, lead: 1600, tail: 2200, min: 6000 });
    r.mark('trace:read');
    await r.pause(600);
    await r.scroll(-260, 1800);
    await r.pause(900);
    await r.scroll(300, 2000);
    r.mark('trace:reviewed');
  }
  await r.pause(1500);
  r.mark('END');
} finally {
  await r.finish();
}
