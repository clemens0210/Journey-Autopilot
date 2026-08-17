#!/usr/bin/env python3
"""Rebuild assets/screens and assets/plates from the raw sources.

`screens` are cut out of the four uncut takes in `recordings/out` — the real
app, real agent turns. `plates` are the stock clips in `assets/footage`,
normalised so the composition never has to think about frame rate or aspect:
everything comes out 1920×1080 at exactly 30 fps, no audio.

RETIMED SPANS, NOT TRIMMED RANGES
---------------------------------
The takes are a person using an app, so they are mostly WAITING: for an LLM, for
a route search, for the pointer to travel, or because the recorder held a beat
so a card could be read. `scan_motion.py` puts numbers on it — between 67% and
88% of every take is frames that are pixel-for-pixel identical to the one
before.

Two earlier cuts both failed on this. The first took a separate three-second
clip per headline and read as a pile of screenshots. The second took longer
ranges, which fixed the pile but carried the dead air in with them: eight
seconds of the "ready to book" card with nothing but the cursor twitching, six
more on the confirmation.

So a screen is now a list of SPANS, and a span is not trimmed — it is RETIMED.
Every span keeps its whole flow, start to finish, and a speed curve derived from
the take's own motion decides how fast the film moves through it: real time
wherever something is happening, gliding forward wherever the screen is frozen.
Nothing is reordered and nothing is dropped from inside a span; the loading
times and the standing still simply go past quickly, which is what a viewer
does with them anyway. Only the long agent turns — 30 s to 95 s of a typing
indicator — are cut across, and those cuts fall between spans.

Five segments carry the whole film:

    seg-setup     take 1   bars  7–12   onboarding, ending on the dashboard
    seg-journey   take 4   bars 14–27   disruption → replan → gate → rebooked
    seg-trace     take 4   bars 40–43   the agent trace
    seg-claim     take 2   bars 47–52   an arrived trip → rights → claim filed
    seg-book      take 3   bars 54–59   booking on live DB data

Each span carries the number of BARS it is on screen for, because the
composition cuts a card on every bar and the screen behind it has to be showing
the thing the card is talking about. The retime hits that duration exactly.

Usage:  python3 tools/cut_media.py [--only screens|plates] [--list] [--qa]
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
BAR = 2.5263157894736843
FPS = 30
DISSOLVE = 4               # frames — visible as a softening, not as a cut

TAKES = {
    1: "01-onboarding",
    2: "02-passenger-claim",
    3: "03-book-trip",
    4: "04-demo-trip-reroute",
}

# ---------------------------------------------------------------------------
# the retime
# ---------------------------------------------------------------------------
# Motion is measured the same way scan_motion.py measures it: the fraction of
# pixels that changed by more than 10/255 between one frame and the next, on a
# half-size greyscale copy. A frozen frame scores an exact zero.
MW, MH = 215, 466
MDELTA = 10
ACTIVE = 0.0004            # above this, something is happening on screen

DWELL = 8                  # frames a frozen screen is held at 1x before moving
GROW = 0.10                # speed gained per further frozen frame after that
SMOOTH = 9                 # frames — so the speed eases rather than steps
EDGE = 9                   # frames at each end of a span kept at real time,
                           #   so a cut always lands on something moving
MINSPEED = 0.85            # never crawl: below this, repeated frames judder
MAXSPEED = 40.0
HOLD_MAX = 1.30            # the longest a finished clip may stand still


def speed_shape(m: np.ndarray) -> np.ndarray:
    """A per-frame speed multiplier from a span's motion signal.

    Real time while the screen is moving and for `DWELL` frames after it stops
    — a held frame is how a viewer is given time to read — and from there the
    speed keeps climbing for as long as nothing changes.

    IT KEEPS CLIMBING, rather than easing up to a ceiling, and that is the
    whole trick. With a ceiling, every dead stretch ends up compressed by the
    same factor, so the take's 7-second wait for an agent stayed 7/n seconds
    long — still the longest thing in the clip, still dead. Growing without
    limit makes the time a frozen screen survives roughly LOGARITHMIC in how
    long it was frozen for, so a 30-second wait and a 2-second beat both come
    out about a second, which is what an editor would have done by hand.
    """
    n = len(m)
    gap = np.zeros(n)
    since = 0.0
    for i in range(n):
        since = 0.0 if m[i] > ACTIVE else since + 1.0
        gap[i] = since
    s = 1.0 + GROW * np.maximum(0.0, gap - DWELL)
    k = np.ones(SMOOTH) / SMOOTH
    s = np.convolve(np.pad(s, SMOOTH, mode="edge"), k, mode="same")[SMOOTH:-SMOOTH]
    s[:EDGE] = 1.0
    s[-EDGE:] = 1.0
    return s


def at_effort(shape: np.ndarray, u: float) -> np.ndarray:
    """The speed curve at one point on a single dial, `u`.

    The dial spends the cheap time first, which is the whole point. A first
    version scaled the shape as a whole, so buying 10% off a span also sped up
    the parts where something was happening while leaving a two-second hold
    only slightly shorter — the worst of both. Here:

        u < 0   nothing to cut: everything runs slower, evenly
        0…1     real motion stays at 1x; dead air alone accelerates
        u > 1   dead air is already flat out, so the whole span speeds up

    The three pieces meet at 1.0 and at `shape`, so speed is continuous and
    strictly increasing in u — which is what makes the bisection below valid.
    """
    if u < 0.0:
        return np.full_like(shape, max(MINSPEED, 1.0 + u))
    if u <= 1.0:
        return np.minimum(1.0 + (shape - 1.0) * u, MAXSPEED)
    return np.minimum(shape * u, MAXSPEED)


def solve_effort(shape: np.ndarray, target: int) -> tuple[float, bool]:
    """The u that lands the span on exactly `target` output frames."""
    def out(u: float) -> float:
        return float((1.0 / at_effort(shape, u)).sum())
    lo, hi = -0.99, 200.0
    if out(hi) > target or out(lo) < target:
        return (hi if out(hi) > target else lo), False
    for _ in range(80):
        mid = (lo + hi) / 2
        if out(mid) > target:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2, True


def pick_frames(m: np.ndarray, target: int) -> tuple[np.ndarray, float, bool]:
    """Which source frame each of `target` output frames comes from."""
    shape = speed_shape(m)
    k, ok = solve_effort(shape, target)
    w = 1.0 / at_effort(shape, k)
    cum = np.concatenate([[0.0], np.cumsum(w)])
    cum *= target / cum[-1]                      # land the last frame exactly
    idx = np.clip(np.searchsorted(cum, np.arange(target) + 0.5) - 1, 0, len(m) - 1)
    return idx, k, ok


# ---------------------------------------------------------------------------
# the edit
# ---------------------------------------------------------------------------
# name, take, pre-roll seconds, [(in, out, bars) …] in take seconds.
#
# `pre` is the half-second a clip window opens BEFORE its block so the <video>
# has presented by the time it is on show (see the note on .jo-shot / .de-shot
# in the compositions). It is added to the first span, which is therefore the
# only one that is partly never seen. The first clip of an act has no earlier
# time to open in — a poster still covers it instead — so those carry pre = 0.
#
# `bars` is how long the span is on screen in the finished film, and it is not
# a free choice: it is set by the cards the composition cuts over the top. The
# comment on each span names the card it has to be underneath.
SEGMENTS = [
    # ---- act 1, bars 7–12 · the wizard ------------------------------------
    ("seg-setup", 1, 0.0, [
        (9.20, 15.60, 1),     # bar 7   "It starts with your DB account."
                              #         signing in, and the three trips landing
        (43.40, 46.40, 1),    # bar 8   "And your personal Outlook calendar."
                              #         the Microsoft consent, read and accepted
        (58.80, 64.80, 1),    # bar 9   ✓ Connected, and the four appointments
                              #         it found, scrolled through
        (72.00, 74.80, 1),    # bar 10  "How you like to travel." — 1st class
        (108.40, 111.60, 1),  # bar 11  "And how far it may go alone."
                              #         the autonomy tile: Approve every action
        (131.60, 135.60, 1),  # bar 12  "Then it takes over." — the dashboard
    ]),

    # ---- act 1, bars 14–27 · the journey ----------------------------------
    # The four spans are the four things that happen; the three cuts between
    # them are the three agent turns, which run 25 s, 7 s and 7 s of a typing
    # indicator. Every span but the first therefore OPENS on that indicator for
    # a beat before its answer lands, so the cut reads as the wait ending.
    ("seg-journey", 4, 0.5, [
        (15.50, 34.50, 3),    # bars 14–16  the trip detail: ICE 528 held, the
                              #             broken transfer, the risk forecast —
                              #             ending on tapping Ask the autopilot
        (88.00, 101.10, 4),   # bars 17–20  the answer landing and being read
                              #             from the top, then choosing R1
        (105.50, 115.90, 3),  # bars 21–23  "Then it asks." — THE VETO GATE,
                              #             read, and the approval typed
        (121.00, 138.50, 4),  # bars 24–27  "Rebooked." — no extra charge, and
                              #             the trace opened on the way out
    ]),

    # ---- act 3, bars 40–43 · the agent trace -------------------------------
    # One span: the trace is the one place the film shows the system talking to
    # itself, and a cut inside it would be a lie about how long that took.
    ("seg-trace", 4, 0.0, [
        (125.00, 140.25, 4),  # opened, then read from the top and back
    ]),

    # ---- act 3, bars 47–52 · after the trip --------------------------------
    ("seg-claim", 2, 0.5, [
        (14.80, 27.60, 2),    # bars 47–48  opening a trip that already ran,
                              #             +128 min, read down the page
        (99.20, 105.60, 1),   # bar 49      "It reads your passenger rights."
        (106.00, 121.60, 2),  # bars 50–51  the complaint it drafted: € 39.95,
                              #             read down and the hand moving to it
        (121.60, 128.40, 1),  # bar 52      Submit complaint → Submitted
    ]),

    # ---- act 3, bars 54–59 · booking on live data --------------------------
    ("seg-book", 3, 0.5, [
        (17.20, 32.80, 2),    # bars 54–55  "Live DB data." — typing a station,
                              #             suggestions straight off the API
        (40.00, 51.20, 2),    # bars 56–57  the connections it actually returned
        (55.60, 70.00, 2),    # bars 58–59  Add trip → and it is being watched
    ]),
]

# name, path under assets/footage, start, duration
PLATES = [
    ("boards",      "03-delay/26575729-station-hall-departure-boards.mp4",   2.0, 11.0),
    ("ice-bridge",  "01-rail-db-ice/37594619-ice-on-city-bridge.mp4",        1.0,  8.0),
    ("laptop",      "02-traveler/7252600-laptop-headphones-window-seat.mp4", 3.0,  8.0),
    ("board-clock", "03-delay/34787776-train-departure-board-clock.mp4",     0.5,  5.5),
    ("clock",       "03-delay/9160920-clock-hands-macro.mp4",               12.0,  6.0),
    ("commuters",   "03-delay/5848336-commuters-motion-blur-tunnel.mp4",     0.4,  5.4),
    ("trails",      "06-city-night/34716032-night-light-trails.mp4",         1.0,  8.0),
    ("trackfan",    "01-rail-db-ice/36898175-hamburg-hbf-track-fan.mp4",     6.0,  9.0),
    ("suitcase",    "02-traveler/6036730-suitcase-glass-corridor.mp4",       2.0,  8.0),
    ("wet-night",   "06-city-night/19023114-munich-wet-night-road.mp4",      0.3,  5.5),
    ("platform",    "02-traveler/12096369-backlit-platform-walk.mp4",        4.0, 10.0),
    ("grid",        "05-tech-transitions/34645139-mono-data-grid.mp4",       2.0, 14.0),
    ("tunnel",      "05-tech-transitions/29167798-color-data-tunnel.mp4",    4.0,  8.0),
    ("db-station",  "01-rail-db-ice/36485168-berlin-hbf-db-logo.mp4",        1.0,  8.0),
    ("seats",       "02-traveler/5717598-empty-seats-table-window.mp4",      2.0,  7.0),
    ("car",         "04-replan/9520980-executive-car-backseat.mp4",          3.0,  8.0),
    ("ubahn",       "01-rail-db-ice/29955387-ubahn-clean-platform.mp4",      0.5,  6.0),
    ("tower",       "06-city-night/38645153-fernsehturm-blue-sky.mp4",       1.0,  8.0),
]

# two clips in the library are DCI 2K (2048×1080), so scale-to-cover then crop
PLATE_VF = "scale=1920:1080:force_original_aspect_ratio=increase,crop=1920:1080,setsar=1,fps=30"


def run(args: list[str]) -> None:
    subprocess.run(args, check=True)


def probe(path: Path) -> tuple[float, int, int]:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "format=duration:stream=width,height",
         "-of", "json", str(path)], capture_output=True, text=True, check=True).stdout
    d = json.loads(out)
    s = d["streams"][0]
    return float(d["format"]["duration"]), s["width"], s["height"]


def read_span(src: Path, a: float, b: float, w: int, h: int) -> list[bytes]:
    """Every frame of [a, b) as raw yuv420p, decoded once."""
    size = w * h * 3 // 2
    p = subprocess.Popen(
        ["ffmpeg", "-v", "error", "-ss", f"{a:.4f}", "-to", f"{b:.4f}", "-i", str(src),
         "-vsync", "cfr", "-r", str(FPS), "-f", "rawvideo", "-pix_fmt", "yuv420p", "-"],
        stdout=subprocess.PIPE)
    frames = []
    while True:
        buf = p.stdout.read(size)
        if len(buf) < size:
            break
        frames.append(buf)
    p.stdout.close()
    p.wait()
    return frames


def motion_of(frames: list[bytes], w: int, h: int) -> np.ndarray:
    """Changed-pixel fraction per frame, off the luma plane at half size."""
    y = np.frombuffer(b"".join(f[: w * h] for f in frames), np.uint8).reshape(-1, h, w)
    small = y[:, : h // 2 * 2 : 2, : w // 2 * 2 : 2].astype(np.int16)
    d = (np.abs(np.diff(small, axis=0)) > MDELTA).mean(axis=(1, 2))
    return np.concatenate([[1.0], d])       # frame 0 counts as active


def blend(a: bytes, b: bytes, t: float) -> bytes:
    x = np.frombuffer(a, np.uint8).astype(np.float32)
    y = np.frombuffer(b, np.uint8).astype(np.float32)
    return (x + (y - x) * t).round().clip(0, 255).astype(np.uint8).tobytes()


def frozen_runs(m: np.ndarray) -> list[tuple[float, float]]:
    """Every run of frames identical to the one before, as (start, seconds)."""
    runs, n = [], 0
    for i, v in enumerate(m):
        if v <= ACTIVE:
            n += 1
        elif n:
            runs.append(((i - n) / FPS, n / FPS))
            n = 0
    if n:
        runs.append(((len(m) - n) / FPS, n / FPS))
    return runs


def cut_segments(verbose: bool = False) -> None:
    src_dir = ROOT / "recordings" / "out"
    out_dir = ROOT / "assets" / "screens"
    out_dir.mkdir(parents=True, exist_ok=True)

    for name, take, pre, spans in SEGMENTS:
        src = src_dir / f"{TAKES[take]}.mp4"
        if not src.exists():
            print(f"  skip {name}: {src} is missing")
            continue
        take_len, w, h = probe(src)
        over = [s for s in spans if s[1] > take_len]
        if over:
            print(f"  !! {name}: {over} runs past the end of the take ({take_len:.1f}s)")
            continue

        total_bars = sum(s[2] for s in spans)
        target = total_bars * BAR + pre
        want = [int(round(s[2] * BAR * FPS)) for s in spans]
        want[0] += int(round(pre * FPS))
        # a chain of n spans joined by n-1 dissolves is sum(lengths) - (n-1)*D
        # long, so every span after the first carries the dissolve it eats
        want[-1] += int(round(target * FPS)) - sum(want)
        lens = [v + (DISSOLVE if i else 0) for i, v in enumerate(want)]

        out = out_dir / f"{name}.mp4"
        enc = subprocess.Popen(
            ["ffmpeg", "-y", "-v", "error", "-f", "rawvideo", "-pix_fmt", "yuv420p",
             "-s", f"{w}x{h}", "-r", str(FPS), "-i", "-",
             "-an", "-c:v", "libx264", "-preset", "slow", "-crf", "20",
             "-pix_fmt", "yuv420p", "-g", "15", "-r", str(FPS),
             "-movflags", "+faststart", str(out)],
            stdin=subprocess.PIPE)

        tail: list[bytes] = []
        rows = []
        for i, ((a, b, bars), L) in enumerate(zip(spans, lens)):
            raw = read_span(src, a, b, w, h)
            if len(raw) < L:
                print(f"  !! {name} span {i}: only {len(raw)} source frames for {L}")
                continue
            idx, k, ok = pick_frames(motion_of(raw, w, h), L)
            got = [raw[j] for j in idx]
            rows.append((a, b, len(raw), L, (b - a) / (L / FPS), k, ok))

            head, body = (got[:DISSOLVE], got[DISSOLVE:]) if i else ([], got)
            for j, f in enumerate(head):
                enc.stdin.write(blend(tail[j], f, (j + 1) / (DISSOLVE + 1)))
            keep = body[-DISSOLVE:] if i < len(spans) - 1 else []
            for f in (body[:-DISSOLVE] if keep else body):
                enc.stdin.write(f)
            tail = keep

        enc.stdin.close()
        enc.wait()

        got_len, _, _ = probe(out)
        flag = "" if abs(got_len - target) < 0.05 else "   << OFF TARGET"
        print(f"  {name:13s} {got_len:6.2f}s  ({total_bars} bars"
              f"{' + %.1fs pre' % pre if pre else ''} = {target:.2f}s)  "
              f"{len(spans)} span(s){flag}")
        if verbose:
            t = 0.0
            for a, b, n, L, ratio, k, ok in rows:
                warn = "" if ok else "   << the speed curve had to be clamped"
                print(f"      {t:6.2f}s  <-  take {take} @ {a:7.2f}–{b:7.2f}"
                      f"   {n:4d}→{L:3d} frames, {ratio:4.2f}x{warn}")
                t += L / FPS - (DISSOLVE / FPS if t else 0)

    # The first clip of an act has no earlier window to open in, so a still of
    # its own first frame sits under it at z-index 0 for the ten frames the
    # <video> needs to present. Generated here so it can never drift from the
    # clip it is standing in for.
    for poster, seg in (("poster-journey", "seg-setup"), ("poster-depth", "seg-trace")):
        clip = out_dir / f"{seg}.mp4"
        if clip.exists():
            run(["ffmpeg", "-y", "-v", "error", "-i", str(clip),
                 "-frames:v", "1", str(out_dir / f"{poster}.png")])
            print(f"  {poster:13s} <- {seg}.mp4 frame 0")

    keep = {f"{n}.mp4" for n, *_ in SEGMENTS}
    stale = sorted(p for p in out_dir.glob("*.mp4") if p.name not in keep)
    for p in stale:
        p.unlink()
    if stale:
        print(f"  removed {len(stale)} stale clip(s) from the previous cut")


def qa_segments() -> int:
    """The test the whole rewrite exists to pass: is anything standing still?

    A hold is how a card is given time to be read, so short frozen runs are
    wanted. What is not wanted is the film parking on a still frame, which is
    what the previous two cuts did.
    """
    out_dir = ROOT / "assets" / "screens"
    bad = 0
    print("screens QA — frozen stretches in the finished clips:")
    for name, *_ in SEGMENTS:
        p = out_dir / f"{name}.mp4"
        if not p.exists():
            continue
        dur, w, h = probe(p)
        m = motion_of(read_span(p, 0, dur + 1, w, h), w, h)
        runs = frozen_runs(m)
        longest = max((r for _, r in runs), default=0.0)
        frozen = sum(r for _, r in runs)
        over = [(t, r) for t, r in runs if r > HOLD_MAX]
        mark = "FAIL" if over else "ok  "
        bad += len(over)
        print(f"  {mark} {name:13s} {dur:5.2f}s   frozen {frozen:5.2f}s"
              f" ({frozen / dur * 100:4.1f}%)   longest hold {longest:.2f}s"
              f"   holds > {HOLD_MAX}s: {len(over)}")
        for t, r in over:
            print(f"         at {t:5.2f}s — {r:.2f}s standing still")
    print("  (a hold under ~1.3s is reading time; longer than that is dead air)")
    return 1 if bad else 0


def cut_plates() -> None:
    src_dir = ROOT / "assets" / "footage"
    out_dir = ROOT / "assets" / "plates"
    out_dir.mkdir(parents=True, exist_ok=True)
    for name, rel, ss, dur in PLATES:
        src = src_dir / rel
        if not src.exists():
            print(f"  skip {name}: {src} is missing")
            continue
        out = out_dir / f"{name}.mp4"
        run(["ffmpeg", "-y", "-v", "error", "-ss", str(ss), "-i", str(src), "-t", str(dur),
             "-an", "-vf", PLATE_VF, "-c:v", "libx264", "-preset", "slow", "-crf", "21",
             "-pix_fmt", "yuv420p", "-g", "15", "-movflags", "+faststart", str(out)])
        print(f"  {name:14s} {probe(out)[0]:5.2f}s")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", choices=["screens", "plates"])
    ap.add_argument("--list", action="store_true", help="print each segment's edit list")
    ap.add_argument("--qa", action="store_true", help="only check the existing clips")
    args = ap.parse_args()
    if args.qa:
        return qa_segments()
    if args.only in (None, "screens"):
        print("screens:")
        cut_segments(args.list)
        print()
        qa_segments()
    if args.only in (None, "plates"):
        print("plates:")
        cut_plates()
    return 0


if __name__ == "__main__":
    sys.exit(main())
