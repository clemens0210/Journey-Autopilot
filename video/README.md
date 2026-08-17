# Journey Autopilot — product film

| | |
|---|---|
| `renders/journey-autopilot-v4.mp4` | **the cut** — music, a second track under the middle act, sound design |
| `renders/journey-autopilot-v3-vo.mp4` | the older narrated cut, kept for reference. It carries v3's picture and copy |

2:54, 1920×1080, 30 fps. The full shot list, the beat grid it is cut on, the
two-track music bed and the sound design are in
[`STORYBOARD.md`](STORYBOARD.md). This file is how to run it.

```bash
npm run dev      # Studio preview (long-running — keep it in the background)
npm run check    # lint + runtime + layout + motion + contrast
npm run render -- -o renders/journey-autopilot-v4.mp4      # ~6.5 min
python3 tools/verify_render.py renders/journey-autopilot-v4.mp4
```

Rebuild the media it renders against:

```bash
python3 tools/build_music.py --check   # the two-track bed -> assets/audio/
python3 tools/cut_media.py --list      # screen segments + footage plates
```

`--picture-only` on the verifier skips the audio tests; it exists for the older
narrated cut (`variants/index-vo.html`, `tools/build_vo.py`), which is not part
of v4 and has not been re-timed to it.

Render with the default frame format. `--video-frame-format png` is the
documented choice for UI recordings, but on this project it stalls the capture
(`no frame progress for 60000ms`, around frame 937 of 5229) and leaves no
output. The default is what the shipped cut was made with.

## Layout

```
index.html                 the film: six scene mounts, the act seams, the music
compositions/
  s1-coldopen.html         bars  0–4    the departure board flips to +55
  s2-title.html            bars  4–7    the drop, the name, the promise
  s3-journey.html          bars  7–28   one journey end to end, on one phone
  s4-arch.html             bars 28–40   the whole architecture, then one request through it
  s5-depth.html            bars 40–65   the trace, the tool surface, rights, booking
  s6-outro.html            bars 65–69   three logo builds, then the lockup
assets/
  screens/seg-*.mp4        5 long segments cut from the raw takes in recordings/out
  audio/film-music.mp3     the built bed: two tracks, one grid, three seams
  plates/*.mp4             18 stock clips, normalised to 1920×1080 @ 30 fps
  arch/, logos/            slide 8 and the three marks, extracted from the deck
  sfx/*.mp3                four sounds, from the media-use skill's library
  vo/                      the v3 narration: per-line WAVs, the stem, the ducked bed
  inter-*.woff2            the only typeface used
variants/index-vo.html     the narrated cut — same scenes, different audio bed
                           (a subdirectory, not the root: two root entry points
                            would both be discovered and both beds would play)
tools/verify_render.py     duration, grid lock, which track plays where, and cut
                           alignment — all measured on the encoded MP4
tools/build_music.py       the two-track bed: stretch, align, match, seam, fade
tools/cut_media.py         screen segments (motion-retimed spans) + plates
tools/scan_motion.py       where a raw take is standing still, and for how long
tools/build_vo.py          the v3 narration script, stem and ducked bed
archive/v1-index.html      the previous 60 s cut, kept for reference
recordings/                the harness that produced the raw takes (own README)
lab/                       the animation sandbox the scenes were drawn from
```

## Where the material comes from

Nothing on the phone is a mock-up. Every screen is a frame of a real run,
recorded by `recordings/` driving the actual app in Chromium at the phone
viewport with a synthetic pointer, against real LLM calls and — in the Book tab
— live DB data.

`assets/screens/seg-*.mp4` are **five long segments** cut out of those four
takes, and a segment is a list of *spans* that are RETIMED rather than trimmed.
Between 67% and 88% of every take is frames identical to the one before — an
app being used is mostly waiting — so each span keeps its whole flow, start to
finish, and a speed curve taken from the take's own motion decides how fast the
film moves through it: real time wherever something is happening, gliding
forward wherever the screen is frozen. Nothing inside a span is reordered or
dropped. Only the long agent turns are cut across, and those cuts fall between
spans, so the order, the scrolling and the pointer are the take's own.

`python3 tools/cut_media.py --qa` is the test that matters: no finished clip may
stand still for longer than 1.3 s. `tools/scan_motion.py` reports the same
measure on the raw takes, which is how the spans were chosen.

Take 1 must run first — it onboards the user, and it also pins
`book_alternative_connection` to *Always ask* on the Automation & veto screen,
which is what makes the veto gate in take 4 fire deterministically rather than
resolving to `auto` on a free reroute.

The architecture is not redrawn either. `lab/tools/pptx_extract.py` reads slide
8 out of `docs/Status_Update_BCG_DB.pptx` and emits every shape at the
coordinate the deck puts it at, tagged by region, with connectors as SVG paths
carrying `pathLength="1"`. Change the slide, re-run `lab/tools/build_arch.py`,
and the scene follows. The three partner marks are the print-resolution
originals from the same deck, cut into the pieces they are physically made of.

Tool names, tool counts and the policy gate in `s5-depth.html` are read out of
`src/journey_autopilot/`, not written by hand.

## Rebuilding the media

```bash
# screen segments and footage plates (needs recordings/out and assets/footage)
python3 tools/cut_media.py --list
python3 tools/cut_media.py --qa        # no clip may stand still for over 1.3 s

# the music bed (needs 1234.mp3 and Rorschach Roy - Death of A Bluebird.mp3)
python3 tools/build_music.py --check

# slide 8 + the logos, if the deck changed
cd lab && npm run build:assets
```

## Five things worth knowing before editing

**The beat grid is the spine.** 95.00 BPM, bar = 2.526316 s, and
`data-media-start="0.1028"` on the music trims the pickup so video `t=0` *is* a
bar line. Every scene boundary, every screen cut, every headline swap sits on
`n × 2.526316`, a half of it, or a quarter. If you move something, move it to
another grid position — not to a time that merely looks right.

**Nested media timing differs between preview and render.** A `<video>` inside
a sub-composition whose template root is `data-start="0"`:

- **preview / `snapshot`** reads its `data-start` as absolute film time;
- **`render`** rebases it by the mount offset.

The render is what ships, so every nested clip carries **scene-local**
`data-start`. Because the preview disagrees, which screen is on top is *also*
driven explicitly from each scene's own timeline (`tl.set(sel, { opacity })`)
rather than left to the runtime's clip visibility, and the finished MP4 — not a
Studio scrub — is what gets checked. `tools/verify_render.py` does that
checking against the grid the film was authored on.

**Never reveal a `<video>` — retire the one in front of it.** A `<video>` that
has just been switched on takes about ten frames to present, and until it does
it draws *nothing* — so every screen cut in v2 opened with five to six frames
of empty phone. There is no reveal anywhere in this project now. Each screen
clip's window opens **0.5 s before its slot** (`data-media-start` pulled back by
the same amount so the intended frame still lands on its beat), each screen is
**opaque from the moment its window opens**, and `z-index` **descends with slot
order** so a clip that opened early waits *underneath* the one on show. The
timeline only ever retires the outgoing screen. The first clip of an act has no
earlier time to open in, so a still of its own first frame sits under it at
`z-index: 0`.

**Wipes are driven in pixels, never in xPercent.** A clip is `display: none`
while its window is shut, so a percentage resolves against a zero width and the
panel never moves. Both act wipes were silently doing nothing for exactly that
reason.

**A recording of a still page is not a recording.** The takes are captured as a
CDP screencast, which emits a frame only when the page repaints — so a hold on
an unchanging screen produces no frames and vanishes from the encoded take.
Anything the film needs to sit on has to be moving; `readBlock` in
`recordings/lib/recorder.mjs` guarantees a slow travel even when the block it is
reading already fits on screen.
