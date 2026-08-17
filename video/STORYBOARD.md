# Journey Autopilot — product film v4

2:54, 1920×1080, 30 fps, English, one cut:

| | |
|---|---|
| `renders/journey-autopilot-v4.mp4` | music, a second track under the middle act, and sound design |

(`renders/journey-autopilot-v3-vo.mp4` is the older narrated cut. It is **not**
v4 — it carries v3's picture and v3's copy, and is kept only for reference.)

### What changed from v3

| note | what was done |
|---|---|
| you never see a whole agent message | the recorder got `readReply`, which jumps to the top of the reply the moment it lands and travels down through it at reading pace. `chat.js` pins the log to the bottom on every render, so the old wheel-scroll after a turn was moving nothing at all |
| the app footage is chopped into too many very short clips — use the originals, cut out only the loading and the dead time | **twenty-seven clips became five.** A screen is now a list of *spans* out of one take joined by a four-frame dissolve; the order, the scrolling and the pointer are the take's own. Act 1 runs six bars of onboarding and fourteen of the journey; act 3 runs four, six and six |
| the cut screens still stand still — that is not "only the loading and the dead waiting" | true, and the first fix only moved the problem: longer ranges carried the dead air in with them, eight seconds of the "ready to book" card with nothing but the cursor twitching. Spans are now **retimed rather than trimmed** — see [The screens are retimed, not trimmed](#the-screens-are-retimed-not-trimmed). Longest frozen moment in the finished film: **1.20 s**, down from 8 s |
| show the Outlook connection and the imported appointments | its own two-bar block at bars 8–9: the Microsoft consent dialog, then `✓ Connected — Outlook calendar` and the four detected appointments with their hard deadlines |
| the slogan | **Monitor. Detect. Replan.** — title, reprise and end card |
| `Bahn account` | **`It starts with your DB account.`** |
| avoid "arithmetic" | gone; that headline is `It sees it before you do.` |
| the WhatsApp callout covering the number | removed. Onboarding types a neutral demo number, so the toast the app prints is that one |
| `55 minutes lost. None of them yours.` over night traffic | **`55 minutes lost. Not your issue.`** over a quiet train interior — the old plate read as *driving*, which is the one thing this reroute is not |
| a different track under the middle part | Death of a Bluebird, time-stretched onto the film's own 95 BPM grid for bars 28–40. See below |
| cut the second architecture animation | gone. Bar 36 now simply puts the whole diagram back on screen, undimmed, under the closing title |
| `Not a diagram. A log.` | **`Agent traces are logged.`** |
| the tool wall: drop the two notes, drop the gate, all write tools red | the gate badge and the channel footnote are gone, `send_whatsapp_to_user` is red with the rest, and the caption is just `7 write tools` |
| `rulebook` → passenger rights, drop `Checked, not guessed` | **`It reads your passenger rights.`**, one chip |
| the small line under the claim figure | removed |
| `Submitted` in green | `#24c07a`, matching the app's own submitted banner |
| the section titles arrive half a second early | the lead went from a beat (0.63 s) to **0.13 s** |
| `Live DB data, not a fixture.` | **`Live DB data.`** |
| cut "when nothing is wrong" | the whole block, its two screens and its section label |
| a faster outro, shorter logo builds | the act is a bar shorter and each mark gets three beats instead of four, and finishes building inside the first half of its slot |
| cut `Nothing is booked before you say yes` and the slogan under it | both gone; the reprise is three words and nothing else |
| spend the time won on the recordings | every bar freed went into the phone blocks. Act 3 gained a bar from the outro on top of that |

Two things not on the list were found while checking and fixed anyway:

**The veto gate was not firing.** Onboarding was selecting *Automatic within
limits*, which `policy.py` maps to `aggressive`, under which a free rebooking
resolves to `auto` — the tile even says so. Recorded that way, choosing a
reroute went straight to "you're rebooked" and the beat the film is built
around never happened. Take 1 now selects **Approve every action** and then
pins `book_alternative_connection` to *Always ask* on the product's own
Automation & veto screen, so the gate fires deterministically instead of on the
model's mood. The film's `Nothing is booked before you say so.` is now true of
the run on screen.

**The screencast does not emit frames for a still page**, so the four bars the
film gives the agent trace collapsed to almost nothing in the take. Anything
the film needs to hold on now has to be moving, however slightly —
`readBlock` guarantees it.

---

## The beat grid (this is the spine of the whole film)

`docs/1234.mp3` was re-measured from scratch. **v1 was cut on the wrong tempo** —
it assumed 136 BPM. The track is **95.00 BPM**.

| | |
|---|---|
| quarter | `0.631579 s` |
| **bar** | **`2.526316 s`** |
| downbeat phase in the source file | `0.1028 s` |
| audio mount | `data-media-start="0.1028"` → **video `t=0` is a bar line** |
| bar *n* | `n × 2.526316` |

Verified against four structural edges in the track (kick-in at the drop,
kick-out at the breakdown, kick-in after it, outro): every one lands on a bar
line within +60…+170 ms, and all four residuals are positive — i.e. threshold
lag, not grid error. Independent of phase, the drop→break-out interval is
80.890 s = **exactly 32 bars**.

Everything in this film cuts on a bar, a half-bar or a beat. Nothing cuts
anywhere else.

### The second track

The brief asked for a different track under the middle part. It gets one — but
on the film's grid, not its own, because every cut in that act (the eight trace
steps sit on three-quarter-bar marks) is authored against 95 BPM.
`tools/build_music.py` does the whole job and documents every number in its
docstring; the short version:

| | |
|---|---|
| source | `Rorschach Roy - Death of A Bluebird.mp3`, measured at **160.00 BPM** |
| stretch | `atempo=1.1875` → 190 BPM = **exactly 2 × 95**, so one bar of the track is half a bar of the film and nothing drifts over the twelve bars |
| excerpt | from **0.100 s** — the top of the track, and a downbeat of it: combing the kick band at 3.000 s and at 0.750 s independently puts its downbeats at `t ≡ 0.100` |
| why the top | the track breaks down at 22.8–23.5 s and its next section enters at 24.100 — exactly 24.000 s later, which after the stretch is exactly **eight film bars**. So the break lands in the back half of bar 35, under the riser, and the new section arrives **on bar 36**, on the same frame as the ignite and the closing title |
| bar 28 | a **1.10 s cross-dissolve**: `1234` rides out from the downbeat while Bluebird swells up through its own intro. Both are under a full-frame wipe and a whoosh |
| bar 40 | a 0.25 s ride-out bleeding 0.10 s past the downbeat against a 0.05 s ride-in on it — the returning track owns the bar |
| level | matched by integrated loudness on the parts actually used (+6.2 dB), measured on Bluebird's body rather than its fade-in |

`1234.mp3` is never cut. It plays underneath for the whole film and is muted
across the middle act by a sample-accurate gain envelope, so when it comes back
on bar 40 it is still in phase with its own arrangement — a re-entry, not a
restart.

Verified on the encoded MP4, not on the source: both tracks' kick transients sit
within **0.6 of a frame** of the 95 BPM grid, and a per-bar level comparison
against `1234.mp3` alone shows it present through acts 1 and 3 (mean difference
0.002) and a different track across bars 30–35 (0.356).

### The arrangement, and what each section is for

| bars | video time | music | act |
|---|---|---|---|
| 0–3 | 0.000 – 10.105 | `1234` — quiet intro, riser in bar 3 | **cold open** — the problem |
| **4** | **10.105** | **DROP** | the product lands |
| 4–27 | 10.105 – 70.737 | `1234` — full drive, 24 bars | **Act 1** — the journey, end to end |
| 28–31 | 70.737 – 80.842 | **`Bluebird`** in over a 1.10 s dissolve | **Act 2** — under the hood |
| 32–35 | 80.842 – 90.947 | `Bluebird` — its breakdown, under the trace | the request walks the graph |
| **36** | **90.947** | **`Bluebird`'s next section lands** | the whole system, lit |
| 40 | 101.053 | `1234` back, in phase with itself | **Act 3** — the system at work |
| 40–63 | 101.053 – 164.211 | `1234` — full drive | |
| 64–68 | 161.684 – 174.300 | `1234` — outro | last word, then the **end card** |

The music does the structural work for free: the breakdown is where the phone
disappears and the architecture assembles in near-silence, and the second drop
is the moment a request starts moving through it.

---

## Design

Two registers, hard-cut against each other — never dissolved.

| | |
|---|---|
| **night** `#101215` | the world, the phone, the disruption. The app is a dark UI; the film sits in the same room as it. |
| **paper** `#eff0e8` | title, architecture, end card. Every cut to paper is a beat you feel. |
| ink | `#16181a` |
| DB red | `#ec0016` — disruption, the mark, one accent per frame at most |
| violet | `#533cfe` — the system: agents, wires, traces |
| yellow | `#fed508` — used once, on the money |

Type: Inter 400/500/700/800/900, already embedded in `assets/`.
**No letter-spacing. No `·` separators. No grey text.** The only tracking in the
film is negative, on display sizes. Secondary copy is `#eff0e8` at 62–70 %
opacity on night and `#16181a` at 62 % on paper — tone, not a grey hex. The v1
"small spaced kicker over a big headline" pattern is gone entirely; headlines
carry themselves.

Two of those rules had to be enforced against the **deck**, not against my own
CSS: the generated slide-8 markup carries the deck's projector greys
(`#3e4148`, `#686a6d`) *inline on every span*, which beats any class rule, and
writes the orchestrator's sub-label as `Orchestrator · ReAct`. So the scene
remaps both greys to ink by value with `!important`, and normalises the middot
at scene setup. Both fixes survive a re-run of `lab/tools/build_arch.py`, which
a hand-edit of the generated block would not.

**The phone is an iPhone 15 Pro Max**, built to spec: 430×932 screen (which is
exactly what the takes were recorded at, so the screen is pixel-native), 55 px
corner radius, titanium rail, dynamic island, real button geometry, and a
contact shadow. It enters once per act and *stays* — screens cut inside it,
headlines swap beside it. That is also the fix for v1's uneasy scene changes:
most cuts now happen inside a frame that never moves.

**Footage** is never a shot of its own for longer than one bar, is always
graded to one look (crushed blacks, desaturated, red or violet lean, vignette,
grain), and always carries type or a phone. It is texture and rhythm, not
B-roll. That is the whole defence against looking like assembled stock.

---

## Shot list

Times are video time. `‖` marks a hard cut, `→` a velocity-matched transition.

### Cold open — bars 0–3

The whole film is in English. This scene was the last place holding German
copy — the route, the departure abbreviation and the consequence line.

## Frame 1
- `id: s1-coldopen` · `src: compositions/s1-coldopen.html` · bars 0–4 · 0.000–10.105
- Blueprint: adapted from `lab/compositions/01-delay-splitflap.html`
- **Concept.** A platform at night. You are looking at the board, and the board
  is about to take your evening apart. No product, no logo, no promise — just
  a mechanism doing its job.

| at | bar.beat | shot |
|---|---|---|
| 0.000 | 0.1 | Station-hall footage, graded to near-black, slow push. `ICE 528` badge and `MUNICH CENTRAL → BERLIN CENTRAL` type on. |
| 2.526 | 1.1 | `19:10 dep` sets. Footage sinks under it. |
| 5.053 | 2.1 | The split-flap stack starts rolling. Digits blur. |
| 7.579 | 3.1 | Riser. Flaps hit their fastest, then **settle on `+55`**. |
| 8.842 | 3.3 | `Connection in Nuremberg missed` snaps in, DB red. |
| 9.790 | 3.4½ | Frame darkens hard into the drop. |

Transition out: **‖ on the drop**, to paper. The register flip *is* the edit.

### The promise — bars 4–7

## Frame 2
- `id: s2-title` · `src: compositions/s2-title.html` · bars 4–7 · 10.105–17.684
- Rules: kinetic type on the beat, one word per beat.

| at | bar.beat | shot |
|---|---|---|
| 10.105 | 4.1 | **DROP.** Paper. `Journey Autopilot` SLAMS in, 190 px, ink. Rule draws under it in violet. |
| 11.368 | 4.3 | Sub-line wipes: `A multi-agent assistant for Deutsche Bahn journeys.` |
| 12.632 | 5.1 | `Monitor.` |
| 13.263 | 5.2 | `Detect.` |
| 13.895 | 5.3 | `Replan.` — each word lands on a beat, the previous one knocked out by the next. |
| 15.158 | 6.1 | ‖ ICE on the city bridge, one bar, graded, moving left to right. Over it: `Every trip you book, watched end to end.` |

Transition out: whip on the direction the train is travelling → the phone
arrives from the same side. Velocity-matched.

### Act 1 — the journey — bars 7–28

**Section labels.** `Setup` (bar 7), `Disruption` (14), `Replanning` (17),
`Your call` (21), `Rebooked` (25). Each lands **0.13 s** before its block — four
frames, not a beat. A beat's lead was the "section titles come half a second too
early" note: it put the label up while the previous block was still on screen.

## Frame 3
- `id: s3-journey` · `src: compositions/s3-journey.html` · bars 7–28 · 17.684–70.737
- One iPhone, on screen for 21 bars. **Two clips, not twenty**: six bars of the
  onboarding take and fourteen of the disruption take, each continuous, retimed
  so the loading and the standing still glide past rather than being cut out
  (see [The screens are retimed, not trimmed](#the-screens-are-retimed-not-trimmed)).
  The headline beside the phone still swaps on the bar; the screen inside it no
  longer does. The journey clip is four spans and three cuts, and each of those
  cuts is an agent turn — 25 s, 7 s and 7 s of a typing indicator.
- Two footage bars (13, 26) break the rhythm so the middle never reads as a
  spec list. The four-flash recap that used to close the act is gone.

| at | bar | screen (source) | headline |
|---|---|---|---|
| 17.684 | 7 | take 1 — DB account connected, 3 trips imported | **It starts with your DB account.** chips: `BahnCard 50` `3 trips imported` |
| 20.211 | 8–9 | take 1 — the Microsoft consent dialog read and accepted, then `✓ Connected — Outlook calendar` and four detected appointments | **And your personal Outlook calendar.** chips: `4 appointments` `2 hard deadlines` |
| 25.263 | 10 | take 1 — 1st class, quiet zone, window | **How you like to travel.** |
| 27.789 | 11 | take 1 — autonomy: *Approve every action* | **And how far it may go alone.** chip: `Approve every action` |
| 30.316 | 12 | take 1 — dashboard, trips monitored live | **Then it takes over.** |
| 32.842 | 13 | ‖ footage: departure board + clock macro, then commuters, half-bar cuts, red frame on 14 | — |
| 35.368 | 14 | take 4 — *Signal box malfunction (Nuremberg area)* | **Something breaks.** |
| 37.895 | 15 | take 4 — +55 min, the transfer broken | **It sees it before you do.** chips: `+55 min` `43 min short` |
| 40.421 | 16 | take 4 — the risk forecast | **On DB's own punctuality record.** chip: `85 % confidence` |
| 42.947 | 17 | take 4 — the turn still in flight, then the monitor answer landing and read from the top | **Two ways out.** |
| 45.474 | 18 | take 4 — the same answer, further down | **Both priced. Both timed.** chips: `+86 min` `+107 min` `No added cost` |
| 48.000 | 19 | take 4 — no calendar conflicts | **Checked against your meetings.** |
| 50.526 | 20 | take 4 — the R1/R2 option cards, then `Take option R1` | **Trains. Cars. Bikes.** |
| 53.053 | 21–22 | take 4 — **the veto gate**: *the executor is asking for a final approval before it can confirm the reroute* | **Then it asks.** |
| 58.105 | 23–24 | take 4 — the approval typed and sent, and the answer coming back | **Nothing is booked before you say so.** |
| 63.158 | 25 | take 4 — *your reroute onto R1 is now confirmed, no extra charge* | **Rebooked.** chips: `€ 0.00 extra` `Ticket still valid` |
| 65.684 | 26 | ‖ footage: a quiet train interior | `55 minutes lost.` `Not your issue.` |
| 68.211 | 27 | the phone steps back, small, on the agent trace being opened | **That was one trip.** |

Transition out: everything plays right up to **bar 28**, where the host's paper
wipe covers the frame. Nothing is emptied out beforehand — that left three black
frames, which is what made this seam read as broken in v2.

### Act 2 — under the hood — bars 28–40

## Frame 4
- `id: s4-arch` · `src: compositions/s4-arch.html` · bars 28–40 · 70.737–101.053
- **There is no build any more.** Twice the note on this act was that the
  diagram takes too long to assemble, so it now simply arrives — the whole
  system inside one beat, region by region in reading order, every wire drawn
  in one gesture. The seven bars that used to be spent constructing it are
  spent on the thing worth watching.

| at | bar.beat | shot |
|---|---|---|
| 70.737 | 28.1 | ‖ The paper wipe uncovers the finished diagram from the left. `Five agents, one orchestrator.` |
| 73.263 | 29.1 | The stage dims to 34 %. The packet is placed **on the head of the first wire**, not near it. Eight trace steps follow, **three quarters of a bar each** — ~1.9 s per caption instead of v2's 1.2 s. |
| 73.263 | 29 | `01 trigger` — A trip is opened. |
| 75.158 | 29.75 | `02 orchestrator` — The ReAct loop picks it up. |
| 77.053 | 30.5 | `03 monitoring` — It reads the live board. |
| 78.947 | 31.25 | `04 the gate` — Risk high enough to act on? |
| 80.842 | 32 | `05 planner` — **True.** The dot takes the deck's own *true* arrow out of the top port. |
| 82.737 | 32.75 | `06 communicator` — It drafts the heads-up. |
| 84.632 | 33.5 | `07 executor` — Nothing is booked until you say yes. |
| 86.526 | 34.25 | `08 you` — Then it books, and tells the people waiting. |
| 88.421 | 35.1 | Swell: camera back to the whole board, everything coming up together into the drop. |
| **90.947** | **36.1** | **The second track's new section lands.** One violet frame, then the **whole graph back at full brightness** under `One loop. Every trip.` — and it simply holds there, on one slow push, until the night wipe takes it on bar 40. v3 ran four dots round the diagram again here; the note was that it read as a second animation of something already understood, so there is no second animation. |

Four things in the trace were wrong in v2 and are fixed here:

- **the dot started in the wrong place** — it was parked at a hand-typed
  coordinate and jumped onto the graph when the first ride took over. It is now
  placed with `getPointAtLength(0)` on the wire it is about to ride.
- **the dot jumped between agents** — consecutive wires do not share endpoints.
  Every handover is now an authored eased glide, and a step that has to come
  back to the orchestrator rides the wire it came in on, backwards.
- **the camera drifted** — `#ar-cam` had the default `transform-origin: 50% 50%`,
  which cancels the `x = CX − s·px` framing formula's `px` out entirely. Origin
  is now `0 0`, the maths lands where it says, and each move is a decisive
  half-second on the step followed by stillness.
- **the gate's arrows were wrong** — the dot rode `branch>planner`, which in the
  deck is a 35 px stub between two tool icons; the actual *true* arrow
  (`branch>branch`, top port → planner) was never drawn at all. The dot now
  takes the true arrow, and both labels are on screen while it does.

### Act 3 — the system at work — bars 40–65

## Frame 5
- `id: s5-depth` · `src: compositions/s5-depth.html` · bars 40–65 · 101.053–164.211
- The same phone returns, but now the machinery is visible next to it.
- **Three clips for twenty-five bars.** Same rule as Act 1: each block runs one
  continuous, retimed cut of a real take, four to six bars long. The trace is a
  single unbroken span — a cut inside it would be a lie about how long the
  system took to say what it did.
- **Every block is announced**, 0.13 s ahead of itself. The tool wall is the one
  exception — its own headline occupies the label's band and is a stronger cue
  than a kicker would be, so the label clears out of its way instead of
  fighting it.
- This act is a bar longer than v3: the outro gave one back, and cutting the
  all-clear block and the eight-flash recap freed four more. All five went into
  the phone blocks.

| at | bar | shot | headline |
|---|---|---|---|
| 101.053 | 40–44 | `Under the hood` · the app's **real** agent trace, pulled out at a size you can read — six lines, then held. **Four bars.** | **Agent traces are logged.** / **Every call, on the record.** |
| 111.158 | 44–47 | twelve read tools across bar 44, seven write tools across 45, the wall standing through 46. No gate badge, no footnote, every write tool red. | **Nineteen tools. Read and write, kept apart.** |
| 118.737 | 47–53 | `After the trip` · a trip that has **already happened** — arrived, 128 min late — then the passenger-rights answer, then `€ 39.95` counting up in yellow with nothing under it, then the claim filed | **A trip that is already over.** → **It reads your passenger rights.** → **And files the claim for you.** chip: `Submitted` in green |
| 133.895 | 53 | ‖ footage: Hamburg track fan, one bar | **A new trip?** |
| 136.421 | 54–60 | `Booking on live data` · live station autocomplete, the connections the DB API actually returned, the trip added, the autopilot's verdict on it | **Live DB data.** → **Real trains. Real fares.** (`8 h 41 min` `Direct` `from € 71.99`) → **Watched from the moment you book.** |
| 151.579 | 60–62 | ‖ footage: platform, two bars | **From the booking to the platform, it is the one watching.** |
| 156.632 | 62 | slam to paper | **Monitor.** |
| 159.158 | 63 | | **Detect.** |
| 161.684 | 64 | the track's own outro opens up under the last word | **Replan.** |

Cut from v3 on the note: the whole **`When nothing is wrong`** block, and the
eight-flash recap that closed the act. The line `Nothing is booked before you
say yes.` and the slogan under it are gone with them — the reprise is three
words and nothing else.

### End card — bars 65–69

## Frame 6
- `id: s6-outro` · `src: compositions/s6-outro.html` · bars 65–69 · 164.211–174.300
- Three logo builds as three beats of one move, not three separate cards —
  **three quarters of a bar each**, which is still exactly three beats of the
  grid. Every build finishes inside the first half of its slot, so each mark
  stands *complete* for longer than it spends assembling; in the first v4 pass
  the Köln wordmark was still drawing itself into its own cut.

| at | bar | shot |
|---|---|---|
| 164.211 | 65 | DB: keeper frame sweeps closed, plate fills, D and B slam in |
| 166.105 | 65.75 | BCG Platinion: five facets fold onto the prism |
| 168.000 | 66.5 | Universität zu Köln: the seal drops and stamps |
| 169.895 | 67.25 | The lockup: `Journey Autopilot`, the rule, `Monitor. Detect. Replan.`, then the three marks converging onto one line with dividers between them |
| 174.300 | 69 | out, on the track's own fade |

No captions under the three marks and no line under the lockup.

## The screens are retimed, not trimmed

Every phone screen in the film comes out of four uncut takes of the real app,
and the hard part is that **an app being used is mostly waiting**. `tools/
scan_motion.py` counts the frames that are pixel-for-pixel identical to the one
before, and the answer is 67% of take 1, 80% of take 4, 88% of take 2 — waiting
for an LLM, for a route search, for the pointer to travel, or because the
recorder held a beat so a card could be read.

Two earlier cuts both lost to that. The first took a separate three-second clip
per headline and read as a pile of screenshots. The second took longer ranges,
which fixed the pile but carried the dead air in with them: eight seconds of the
"ready to book" card with nothing moving but the cursor, six more on the
confirmation.

So a span is no longer trimmed, it is **retimed**. It keeps its whole flow, and
a speed curve derived from the take's own motion decides how fast the film moves
through it:

- while the screen is moving, and for **8 frames after it stops**, real time —
  a held frame is how a viewer is given time to read;
- from there the speed **keeps climbing** for as long as nothing changes, with
  no ceiling. That is the whole trick: with a ceiling every dead stretch is
  compressed by the same factor, so the take's 30-second wait stays the longest
  thing in the clip. Growing without limit makes the time a frozen screen
  survives roughly *logarithmic* in how long it was frozen — a 30-second wait
  and a 2-second beat both come out about a second;
- one dial then scales that curve to land the span on its exact bar count, and
  the dial **spends the cheap time first**: dead air accelerates alone until it
  is flat out, and only then does anything that is moving speed up.

Nothing inside a span is reordered or dropped. Only the long agent turns are cut
across, and those cuts fall between spans — so every span but the first *opens*
on the typing indicator for a beat before its answer lands, and the cut reads as
the wait ending rather than as a jump.

`python3 tools/cut_media.py --qa` is the acceptance test: **no finished clip may
stand still for longer than 1.3 s.**

| clip | on screen | frozen | longest hold |
|---|---|---|---|
| `seg-setup` | 15.17 s | 35.6% | 0.80 s |
| `seg-journey` | 35.87 s | 48.6% | 1.20 s |
| `seg-trace` | 10.10 s | 32.0% | 0.70 s |
| `seg-claim` | 15.67 s | 61.1% | 0.97 s |
| `seg-book` | 15.67 s | 41.9% | 0.67 s |

The frozen percentages are still high, and they have to be: across the region of
take 4 the film draws on there are 25.5 s of motion for 46 s of screen time. The
number that matters is the last column — the film now holds to let you read and
then moves, instead of parking.

## Feature coverage

The brief asked for breadth rather than one hero, so this is the checklist the
cut is measured against.

| feature | where |
|---|---|
| DB account import (BahnCard, trips) | bar 7 |
| **Outlook calendar connection + imported appointments, hard deadlines** | bars 8–10 |
| Travel preferences | bar 10 |
| Autonomy levels | bar 11 |
| Live monitoring | bars 12, 58 |
| Disruption detection with real cause | bar 14 |
| Missed-connection maths | bar 15 |
| Risk forecast on historical punctuality | bar 16 |
| Reroute options, priced and timed | bars 17–19 |
| Calendar-clash check | bar 19 |
| Multimodal alternatives | bar 20 |
| **The approval gate, asked and answered** | bars 21–25 |
| Proactive WhatsApp notice | in the take's own toast, and `send_whatsapp_to_user` on the wall at bar 45 |
| Multi-agent architecture | bars 28–40 |
| ReAct loop, agents as tools | bars 29–35, held whole at 36–40 |
| The five agents named | bar 28, against the diagram |
| The app's own agent trace | bars 40–44 |
| Read/write tool separation | bars 44–47 |
| Passenger rights, EU compensation maths | bars 47–51 |
| Claim filing | bar 52 |
| Booking on live DB data | bars 54–60 |

## Sound design

Four sounds, used sparingly, all mixed well under the track. Nothing here is
decorative: each one marks a moment the picture already has.

| at | bar | sound | why |
|---|---|---|---|
| 7.579 | 3 | soft tick | the split-flap board settles on +55 |
| 9.755 | 4 | whoosh | into the white flash and the first drop |
| 70.437 | 28 | whoosh | the paper wipe — and the handover between the two tracks |
| 88.421 | 35–36 | riser | its own climax lands on the ignite, over the second track's own break |
| 100.753 | 40 | whoosh | the night wipe, and the handover back |
| 131.368 | 52 | confirm | the compensation claim goes in |
| ×8 | — | quiet tick | one under each section label, on the label's own frame |

Sources are the bundled library in the `media-use` skill; the files live in
`assets/sfx/`. Every SFX clip has its own track index (21–34) so nothing
collides on a track.

## The narrated cut

Not part of v4. `renders/journey-autopilot-v3-vo.mp4` and
`variants/index-vo.html` are the v3 narration, kept for reference; the script
and the mix are documented in `tools/build_vo.py`. Rebuilding it against v4
would need the script re-timed to the new act boundaries, so it has
deliberately been left alone rather than shipped out of sync with the picture.

## Constraints held

- **No real phone number reaches a frame.** Onboarding clears the field that
  arrives pre-filled from `DEMO_TRAVELER_NUMBER` and types `+49 151 20000042`
  instead, so every later WhatsApp toast in takes 2–4 carries that synthetic
  number. Confirmed by grepping all four take logs: one number appears, and it
  is the synthetic one. The designed callout that used to cover it in v3 has
  been removed, as asked.
- **No real Microsoft identity reaches a frame.** The takes run with
  `MS_ENTRA_CLIENT_ID` unset, which is what makes the wizard use its simulated
  consent dialog and serve the fixture calendar. On the cached-login path it
  would instead print the signed-in account's real address under
  `✓ Connected as …`, and return zero events.
- **Slide 8 names a specific model; the recordings did not run on it.** The
  architecture scene labels that block `LLM`, which is true on every
  configuration.
- **The gate on screen is the real one.** Take 1 sets *Approve every action* and
  pins `book_alternative_connection` to *Always ask*, so `policy.resolve`
  returns `ask` and the Executor genuinely stops. Nothing about the approval
  beat is staged.

---

## What the build changed, and the framework notes

**The tempo.** v1 was cut at 136 BPM. Re-measuring from the audio gives
95.00 BPM — the earlier figure was a harmonic artefact. Everything above is on
the corrected grid.

**The act seams did nothing at all in v2.** Both wipes were driven with GSAP's
`xPercent`. A clip is `display: none` while its window is shut, so the
percentage resolves against a **zero width** and the panel never left its start
position. Wipes are driven in **pixels**, and each carries a violet leading
edge: because each panel lands on a scene of its own colour, the edge is the
only part of the wipe you ever see.

**Nested media timing is inconsistent between preview and render.** A
`<video>` inside a sub-composition whose template root is `data-start="0"`:

- **preview / `snapshot`** treats its `data-start` as *absolute* film time;
- **`render`** rebases it by the mount offset — the documented behaviour.

The render is what ships, so every nested clip carries **scene-local**
`data-start`, and which screen is on top is owned by the scene's own timeline.
The consequence when reviewing: a Studio scrub shows the wrong screen on the
phone through most of both acts, and that is expected. Only the encoded MP4 is
evidence.

**Never reveal a `<video>` — retire the one in front of it.** A `<video>` that
has just been switched on takes about ten frames to present, and until it does
it draws nothing at all. So there is no reveal anywhere in this project: each
clip's window opens **0.5 s before its slot** (`data-media-start` pulled back by
the same amount), each screen is **opaque from the moment its window opens**,
`z-index` **descends with slot order**, and the timeline only ever retires the
outgoing screen. The first clip of an act has no earlier time to open in, so a
still of its own first frame sits under it at `z-index: 0` —
`tools/cut_media.py` generates those posters from the clips themselves so the
two can never drift apart.

v4 makes this much easier to hold: there are five screen clips in the whole
film instead of twenty-seven, so there are four retirements instead of forty.

**A CDP screencast emits frames only when the page repaints.** A still page
produces no frames and simply collapses out of the encoded take — which is how
four bars of agent trace turned into a fraction of a second. `readBlock` in the
recorder therefore always moves the log a little, even when the block it is
reading already fits on screen.

**Verification, end to end.** What was actually run against the shipped file:

```
npm run check                                        → passed, 43/43 WCAG AA
python3 tools/verify_render.py renders/journey-autopilot-v4.mp4   → PASS
```

`verify_render.py` measures the **encoded MP4**, never a preview or a source
file. It checks duration and frame count; that every kick transient in each act
sits within a frame or two of the 95 BPM grid (which holds both tracks to the
same spine); that the arrangement steps where the film says it does; that
`1234.mp3` is the track playing in acts 1 and 3 and *not* the one under bars
30–35; and that the film's structural cuts land on their bar.
