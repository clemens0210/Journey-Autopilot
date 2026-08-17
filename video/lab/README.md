# Animation Lab

A sandbox for **Journey Autopilot** video ideas. Nothing in here is wired into the
real film at `video/index.html` — it is a place to try motion, look at it, and
decide what earns a spot.

Each idea is a self-contained HyperFrames sub-composition in `compositions/`.
`index.html` is a **reel** that plays all of them back to back with a chapter tag,
so you can watch the whole set in one go, or open any single one in Studio.

```bash
cd video/lab
npm run dev              # Studio: pick a single composition, scrub it
npm run check            # lint + runtime + layout + motion + contrast
npm run snap -- --at 12,49,80    # PNG frames at given seconds
npm run render -- -o renders/animation-lab-reel.mp4
```

Rendered reel: `renders/animation-lab-reel.mp4`

---

## The ideas

| #      | File                            | Idea                                                                                                                                                                              |
| ------ | ------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **01** | `01-delay-splitflap.html`       | The delay count-up you already like, given a **mechanism**: a DB departure board (Fallblattanzeige) physically flipping to +55. The ones flap rolls fast enough to blur, then settles. |
| **02** | `02-delay-timeline.html`        | The same 55 minutes as a **consequence** — the ICE bar grows until it runs past the moment the connection leaves, and the transfer window closes underneath it.                     |
| **03** | `03-logo-db.html`               | The DB logo **builds itself**: the red keeper frame sweeps closed, the white plate fills, D and B slam in from opposite sides.                                                     |
| **04** | `04-logo-bcgp.html`             | BCG Platinion's mark is a folded prism, so it **folds** — five real colour facets fly to their own face of the solid, then the wordmark wipes in line by line.                     |
| **05** | `05-logo-koeln.html`            | The Universität zu Köln seal **stamps**: a ring scribes the edge, the seal drops into it rotating, then UNIVERSITÄT / ZU KÖLN wipe out of it.                                      |
| **06** | `06-logo-lockup.html`           | The three-way credit as one move — the marks converge onto a single optical line and the dividers draw between them. This is the end-card version.                                |
| **07** | `07-arch-build.html`            | **Slide 8, assembled.** The real architecture, animated in the order the system runs: trigger → orchestrator → model + memory → tool bus → each agent with its tools → the outputs. |
| **08** | `08-arch-trace.html`            | **Slide 8, traced.** The diagram sits dimmed and one request travels it — a live packet rides the deck's own connector curves, the camera flies with it, each agent lights only while it holds the request. |
| **09** | `09-reroute-map.html`           | The reroute on the network: the booked route draws, the train runs it, the onward leg **retracts**, and the replan grows out of the same junction.                                 |
| **10** | `10-veto-gate.html`             | The veto moment. An oversized cursor walks in from off-frame and clicks "Yes, rebook" — and the click is what ignites the confirmation.                                            |

03–06 all sit on cream (`#eff0e8`), 01/02/09/10 on near-black (`#101215`), 07/08 on
the deck's own near-white. That is deliberate: it lets you see which register each
idea wants before committing the film to one.

---

## Getting slide 8 out of the deck, exactly

This was the interesting problem: the current prototype re-drew the architecture
slide by hand and it shows. It doesn't have to be re-drawn — a `.pptx` is a zip of
OOXML, and **every shape in it carries its exact position, size, fill, line and
text as data**. So the pipeline reads the real numbers:

```
docs/Status_Update_BCG_DB.pptx
   │
   │  tools/pptx_extract.py         resolve group transforms → absolute px,
   │                                 EMU → px, connectors → SVG path data,
   │                                 export the slide's own icons
   ▼
assets/arch/slide8.json  +  assets/arch/media/*.png
   │
   │  tools/pptx_to_html.py         positioned <div>s + one <svg> of wires,
   │                                 tagged by region so it is animatable
   ▼
compositions/07-arch-build.html     ← injected between the
compositions/08-arch-trace.html        <!-- pptx:slide8 --> markers
```

One command rebuilds all of it:

```bash
npm run build:assets     # extract slide 8 + the logos, re-inject the markup
npm run preview:slide    # open build/slide8.html — the raw reconstruction, no motion
```

Change the slide, re-run that, and both architecture scenes follow. **Nothing about
the architecture is retyped by hand, so it cannot drift away from the deck.**

### What makes the output animatable

A faithful copy is not enough — it also has to be addressable. Two build-time tags
do that work, and no scene code ever mentions a PowerPoint shape name:

- `data-part="planner"` — which block of the diagram a shape belongs to. Assigned
  **by geometry**, from the boxes in `tools/arch-regions.json`. (PowerPoint's own
  group names are "Gruppieren 33"; useless. Redrawing the grouping over the layout
  is both easier and more honest.) Check the map any time with:
  ```bash
  python3 tools/pptx_to_html.py assets/arch/slide8.json --prefix arch \
      --media-prefix ../assets/arch/media --regions tools/arch-regions.json \
      --debug-regions -o build/slide8-regions.html
  ```
- `data-wire="orchestrator>communicator"` — which two blocks a connector joins,
  from its resolved endpoints. So `draw('[data-wire="branch>planner"]')` reads
  like the diagram instead of like an index.

Every connector also gets `pathLength="1"`, which makes a draw-on exactly one
`strokeDashoffset: 1 → 0` tween with no DOM measuring — and a **retract** the same
tween run backwards (that's how scene 09 kills the dead route).

### Two fixes the extractor applies on purpose

- **Point sizes.** OOXML stores type size in points; the DOM wants px. Straight
  through, every label lands 25 % too small. It converts at 96 dpi.
- **Deck greys.** Labels like "Chat Model" are set in a grey that is fine on a
  projector and mushy at 1080p. `--contrast-bg` walks any run colour down until it
  clears WCAG against the scene's background, and leaves everything else alone.

---

## The logos

All three partner marks were already in the deck at print resolution — DB at
3840×2688, Universität zu Köln at 9241×4167 — so nothing was re-sourced or traced.
`tools/extract_logos.py` lifts them out and `tools/logo_split.py` cuts each into the
pieces it is physically made of:

| Mark               | Parts                                                                  | Cut by                          |
| ------------------ | ---------------------------------------------------------------------- | ------------------------------- |
| **DB**             | red keeper frame · D · B · white plate · three letter counters         | colour, then connected blobs    |
| **BCG Platinion**  | five prism facets (emerald / mint / black / graphite / silver) · BCG · PLATINION | colour, inside rectangular regions |
| **Universität zu Köln** | seal · UNIVERSITÄT · ZU KÖLN                                      | rectangular regions             |

The trick that makes them safe to animate: **every part is written on the same
canvas as the whole logo.** Stack them all at `inset: 0` and you get the original
back pixel-for-pixel — so animation is pure transform + opacity, and the mark can
never end up subtly reshaped. `extract_logos.py` verifies that recomposition on
every run and fails if a single pixel is lost.

`parts.json` next to each set records every piece's own bounding box and centre as
a percentage, which is what the scenes feed to `transform-origin` so a shard pivots
about itself rather than about the canvas.

---

## Known findings

`npm run check` is clean on lint errors, runtime, motion and contrast. Three
categories of finding survive on purpose:

- **`content_overlap` × 7–8 inside idea 01**, all `div.sf-glyph inside div.sf-glyph`.
  A false positive: a split-flap unit really is four stacked glyph layers (static
  top, static bottom, and the two faces of the falling leaf) and only one faces the
  camera at a time. The layout inspector measures DOM boxes and cannot see the 3D
  flip. Idea 01 is the only source of these — drop it and `check` goes green.
- **`duplicate_media_discovery_risk` on 07 / 08.** The slide legitimately uses the
  same icon several times (the Claude mark on four agents, the DB logo on three
  tools). Ids are unique, and the snapshots confirm every instance renders.
- **`composition_file_too_large` on 07 / 08.** Those files carry 188 generated
  shapes. Splitting them would split generated markup, which is the opposite of
  what you want — the whole point is that the block is regenerated, not edited.

---

## Ideas not built yet

Worth trying if the reel needs more:

- **Agent chatter** — the five agents' tool calls scrolling as a live log beside the
  architecture, so "agents as tools" stops being an abstraction.
- **The clock that doesn't stop** — a single wall-clock running the whole film,
  with the agents' work compressed against it: 55 minutes of human phone calls vs.
  9 seconds of autopilot.
- **Before / after split-screen** — the same disruption on the left with nothing
  watching and on the right with the autopilot, run on one shared timeline.
- **Risk score as a live dial** — the Monitoring agent's gauge (the deck already
  draws one) sweeping up through the threshold and tripping the branch.
- **WhatsApp thread as the hero** — the whole story told only as messages arriving
  on a phone, the architecture never shown.
- **Slide 8 → slide 9 morph** — extract two slides and FLIP-morph the shared shapes
  between them; the extractor already gives matching ids across slides.
- **Type-only opener** — kinetic "Detects. Replans. Asks first." with the three
  words cut on a shared vector, as an alternative cold open to the delay counter.

---

## Layout

```
lab/
├── index.html              the reel — mounts every idea back to back
├── compositions/           one file per idea (each previewable on its own)
├── tools/
│   ├── pptx_extract.py     pptx slide → JSON + media (group transforms resolved)
│   ├── pptx_to_html.py     JSON → positioned, tagged, animatable HTML
│   ├── build_arch.py       inject that markup into the scenes that use it
│   ├── arch-regions.json   the geometry → data-part map for slide 8
│   ├── logo_split.py       flat logo PNG → the parts it is made of
│   └── extract_logos.py    pull all three marks from the deck and split them
├── assets/
│   ├── arch/               slide8.json + the slide's own icons
│   ├── logos/{db,bcgp,unikoeln}/   full.png, per-part PNGs, parts.json
│   └── inter-*.woff2
├── build/                  scratch reconstructions (safe to delete)
└── renders/                MP4 output
```
