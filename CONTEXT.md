# Context — Journey Autopilot

The project's ubiquitous language. Terms only: what a word means in this domain,
and what it deliberately does *not* mean. No file paths, no status, no decisions
— those live in `CONTEXT_RECORD.md` and `docs/adr/`.

---

## Journey & trip

**Trip** — one booked door-to-door travel intent belonging to a traveler, from
an origin to a destination on a date. A trip survives rerouting: when a reroute
is booked, the trip is *the same trip* with new legs, not a new trip.

**Leg** — one vehicle segment of a trip. A trip with a transfer has two legs.
Risk is computed per leg; the trip's risk is the worst leg's risk.

**Transfer** — the gap between two legs at a station. Its **buffer** is the
scheduled minutes between arrival and next departure. A buffer consumed by
expected delay is what turns a merely delayed trip into a broken one.

**Trip phase** — where a trip sits in its lifecycle. Exactly three, in order:

- **PRE-TRIP** — the traveler has not departed.
- **EN ROUTE** — departed, not yet confirmed arrived.
- **ARRIVED** — arrival is *confirmed*.

A trip is never ARRIVED merely because its scheduled arrival time passed: a
train ninety minutes late is still EN ROUTE at its planned arrival. The phase
may under-claim progress, never over-claim it — the same verdict decides
whether a compensation claim may be assessed.

**Connection** — a *candidate* itinerary between two points. A connection is
timetable data; a trip is a commitment. Searching returns connections; booking
turns one into the traveler's trip.

## Risk

**Expected delay** — a per-leg forecast in minutes: the leg's current live
delay plus the historical mean delay for that destination and train type. A
number, computed deterministically. Never an LLM estimate.

**Risk band** — the qualitative bucket of expected delay: **low**, **medium**,
**high**. Ordered, comparable, and the thing the reroute gate tests against.
(`NIEDRIG/MITTEL/HOCH` is a stale synonym — the system speaks English bands.)

**Risk score** — a 0–100 figure expressing how delay-prone a route normally is,
independent of today's situation. Distinct from the band: the score is the
long-run character of the route, the band is today's verdict.

**At-risk band** — the configured threshold at or above which a trip warrants
replanning. It is the single knob that decides *when the system acts*, so
prompt and configuration are never allowed to disagree about its value.

**Baseline (punctuality)** — the long-run normal case, from a months-long
punctuality archive. Contrast **today's situation**, from the last few hours of
a station's live arrival board. Risk combines both: the baseline says what this
route usually does, today's situation says whether today is worse.

> Note: "baseline" is overloaded. In evaluation it means the naive comparison
> arm (see below). When both senses are in play, say *punctuality baseline* or
> *evaluation baseline*.

## Constraints & preferences

**Hard constraint** — a condition an option must satisfy to be offered at all;
violating it disqualifies the option. A confirmed on-site meeting time is hard.

**Soft preference** — a condition that ranks options but never excludes them.
Seat and class preferences are soft.

**Profile** — the traveler's persistent preferences and constraints, captured
once at onboarding and read on every run. It is what makes a ranking *theirs*.

## Action & control

**Read** — an operation with no external effect: fetching, scoring, ranking.
Reads run without asking.

**Write** — an operation with an external effect: booking, sending,
rescheduling, filing. Every write is gated.

**Proposal** — a server-issued, identified offer the traveler can accept. Its
purpose is authority: a write executes against a proposal's identifier, never
against figures restated in conversation text. Money, delay, and appointment
firmness are read from the system that owns them, never accepted as an argument
— otherwise the model could talk its way past its own gate.

**Gate / veto** — the pause where a write waits for the traveler. A gated
action reports that it is waiting rather than acting; it fires only once the
traveler has approved.

**Autonomy level** — the traveler's chosen stance, from *notify only* through
*approve each* to *auto within limits*. It shifts every gate at once, which is
what makes autonomy a setting rather than a rewrite.

**Reversibility** — the property that decides a gate's default. Reversible
actions may run automatically; irreversible ones ask. This is why a *tentative*
appointment may be moved automatically and a *confirmed* one may not.

## People

**Traveler** — the person whose trip it is, and the only one who can veto.
Messages to the traveler are not third-party communication.

**Participant** — someone expecting the traveler at the destination: a client,
a colleague. Contacting a participant always requires approval, because the
cost of a wrong message lands on the traveler's reputation.

## Rights & compensation

**Zugbindung** — the ticket's binding to a specific train. A sufficient delay
lifts it, which is what makes a reroute permissible at all. Whether it is
lifted is *looked up* as a read, before any rerouting is treated as covered.

**Compensation claim** — the traveler's statutory delay claim. A claim starts
as a **draft** and becomes filed only on submission. Assessing eligibility
requires the trip to be ARRIVED, since the realized delay is the input.

## Provenance

**Source** — the origin of any piece of data a tool returns: live, archived, or
simulated. Every result carries one. Simulated sources are disclosed to the
traveler rather than presented as fact — the demo is honest about being a demo.

## Evaluation

**Arm** — one of the two systems being compared on the same input: the
**agent** arm (the full orchestrated system) and the **evaluation baseline**
arm (a single model call with the situation described in prose, no tools).

**Run** — one execution of one arm against one scenario. Model output varies
between runs, so a single run is an anecdote; a metric is reported over
repeated runs.

**Scenario** — a fixed, scripted world state a run executes against. Because
the world is scripted, the correct outcome is known by construction — this is
what supplies ground truth in the absence of real labelled data.
