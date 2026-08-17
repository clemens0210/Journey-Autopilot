#!/usr/bin/env python3
"""Measure how much is actually HAPPENING at every frame of a raw take.

The cut list lives or dies on this. A take is a screen recording of an app that
spends most of its length waiting — for an LLM, for a route search, or for the
pointer to travel — and those stretches are identical frame to frame. Time in
the film is expensive, so the edit needs to know, per frame, whether the screen
is moving or standing still.

WHY A PIXEL COUNT AND NOT A MEAN DIFFERENCE
-------------------------------------------
The first version averaged the absolute difference over the frame, which is
dominated by AREA: a scroll moves everything and scores high, but a line of
text being typed moves a few hundred pixels out of 100k and scored the same as
a frozen screen. Typing is not dead air. So the measure is the FRACTION OF
PIXELS that changed by more than 10/255, on a half-size greyscale copy — it
answers "did anything on screen change" rather than "how much of the screen
changed", and a frozen frame scores an exact zero.

On this footage:

    0.00000     frozen — the pointer is parked, nothing is animating
    ~0.0005     a caret blink, one character typed
    ~0.003      the pointer gliding, a row highlighting
    >0.02       a scroll, a screen change, a dialog opening

Usage:
    python3 tools/scan_motion.py 04-demo-trip-reroute            # dead spans
    python3 tools/scan_motion.py 04-demo-trip-reroute 90 132     # a window
    python3 tools/scan_motion.py --all
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "recordings" / "out"
FPS = 30
W, H = 215, 466         # half of the 430x932 take — small enough to be quick,
                        # big enough that one typed character still registers
DELTA = 10              # a pixel counts as changed at 10/255

STILL = 0.0004          # below this, nothing the eye can see is happening
MIN_DEAD = 0.50         # shorter than this is a beat, not dead air


def motion(take: str, ss: float = 0.0, to: float | None = None) -> np.ndarray:
    """Per-frame changed-pixel fraction, indexed from `ss` (m[0] = 0)."""
    src = OUT / f"{take}.mp4"
    cmd = ["ffmpeg", "-v", "error"]
    if ss:
        cmd += ["-ss", f"{ss:.4f}"]
    if to is not None:
        cmd += ["-to", f"{to:.4f}"]
    cmd += ["-i", str(src), "-vf", f"scale={W}:{H},format=gray",
            "-f", "rawvideo", "-"]
    raw = subprocess.run(cmd, capture_output=True, check=True).stdout
    f = np.frombuffer(raw, np.uint8).reshape(-1, H, W).astype(np.int16)
    d = (np.abs(np.diff(f, axis=0)) > DELTA).mean(axis=(1, 2))
    return np.concatenate([[0.0], d])


def dead_spans(m: np.ndarray, off: float = 0.0):
    """Contiguous runs of frozen frames, as (start, end, seconds)."""
    still = m < STILL
    spans, run = [], None
    for i, s in enumerate(still):
        if s and run is None:
            run = i
        elif not s and run is not None:
            spans.append((run, i))
            run = None
    if run is not None:
        spans.append((run, len(still)))
    return [(off + s / FPS, off + e / FPS, (e - s) / FPS)
            for s, e in spans if (e - s) / FPS >= MIN_DEAD]


def report(take: str, lo: float = 0.0, hi: float | None = None) -> None:
    m = motion(take, lo, hi)
    dead = dead_spans(m, lo)
    total = sum(d[2] for d in dead)
    span = len(m) / FPS
    print(f"\n{take}   {lo:.1f} – {lo + span:.1f}s")
    print(f"  frozen {(m < STILL).mean() * 100:4.1f}% of frames"
          f"   dead spans {len(dead)} = {total:.1f}s of {span:.1f}s")
    for s, e, sec in dead:
        print(f"    {s:7.2f} – {e:7.2f}   {sec:5.2f}s")


def main() -> int:
    args = sys.argv[1:]
    if not args or args[0] == "--all":
        for p in sorted(OUT.glob("*.mp4")):
            report(p.stem)
        return 0
    lo = float(args[1]) if len(args) > 1 else 0.0
    hi = float(args[2]) if len(args) > 2 else None
    report(args[0], lo, hi)
    return 0


if __name__ == "__main__":
    sys.exit(main())
