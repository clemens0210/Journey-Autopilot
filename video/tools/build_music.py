#!/usr/bin/env python3
"""Build the film's music bed: two tracks, one grid, three seams.

The film is cut on a 95.00 BPM bar grid and the brief asked for a different
track under the middle act. So the middle act gets one — but on the film's
grid, not its own, because every cut in that act (the eight trace steps sit on
three-quarter-bar marks) is authored against 95 BPM.

    bars  0–28   1234.mp3          the film's own track
    bars 28–40   Death of a Bluebird, time-stretched onto the grid
    bars 40–69   1234.mp3          back to the track it opened on

WHY THE STRETCH IS EXACTLY 1.1875
---------------------------------
Bluebird measures 160.00 BPM (comb-filter score 1.166 against 1.019 for the
half-time reading, on a 0.25 BPM sweep). 160 × 1.1875 = 190 = 2 × 95, so after
the stretch one bar of the track is exactly half a bar of the film and every
one of its downbeats lands on a film half-bar. No drift accumulates over the
twelve bars because the relationship is exact, not fitted.

WHY THE EXCERPT STARTS AT 0.100 — THE TOP OF THE TRACK
------------------------------------------------------
The track is written in half time: its bar is four 80 BPM beats, 3.000 s of
source, which is exactly one film bar after the stretch. Combing the kick band
(<150 Hz) at both 3.000 s and 0.750 s independently puts its downbeats at
t ≡ 0.100 (mod 3.000), so 0.100 is a bar line of its own arrangement rather
than a beat picked off a finer grid. (The all-band flux argues for a phase half
a film beat away, because the hats and snare are louder than the kick; the kick
is what a cut is felt against, so the kick band decides.)

Starting at the top also hands the act its drop for free. The track breaks down
across 22.8 → 23.5 s and its next section — a whole top layer, the high band
jumps by 75 % — enters on the downbeat at 24.100. That is exactly 24.000 s
after 0.100, which after the stretch is exactly EIGHT film bars: the break
lands in the back half of bar 35, under the riser, and the new section arrives
on bar 36, on the same frame as the ignite and the closing title.

The one cost is that the track fades up from silence over its first 1.3 s, so
the seam into bar 28 is a 1.10 s cross-dissolve rather than a hard cut: 1234
rides out from the downbeat while Bluebird swells in underneath it. Under a
full-frame wipe and a whoosh that is the right move anyway — cutting to a fade
would have left the loudest picture event in the film sitting on the quietest
frame of audio.

So the new track's own structure does the work the old one's second drop used
to do, on the same frame.

THE SEAMS
---------
1234.mp3 is never cut: it plays underneath for the whole film and is muted
across the middle act by a sample-accurate gain envelope, so when it comes back
on bar 40 it is still in phase with its own arrangement — a re-entry, not a
restart. Bar 28 is the 1.10 s dissolve described above; bar 40 is a 0.25 s
ride-out that bleeds 0.10 s PAST the downbeat against a 0.05 s ride-in on it,
so the returning track owns the bar and the outgoing one only keeps its tail.
Both seams sit under a full-frame wipe and a whoosh, so the handover is covered
picture-side too.

Output: assets/audio/film-music.mp3 — 174.3 s, loudness-matched, end fade baked
in (a GSAP volume tween on the <audio> element is NOT honoured by the renderer).

Usage:  python3 tools/build_music.py [--check]
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
OUT = HERE / "assets" / "audio" / "film-music.mp3"

BAR = 2.5263157894736843
FILM = 174.3

TRACK_A = HERE / "1234.mp3"
PICKUP_A = 0.1028               # trims A's pickup so film t=0 IS bar 0

TRACK_B = HERE / "Rorschach Roy - Death of A Bluebird.mp3"
TEMPO_B = 1.1875                # 160.00 -> 190.00 BPM = 2 x the film's 95
START_B = 0.100                 # a downbeat, 24.000 s before the new section

SWAP_IN = 28 * BAR              # 70.736842 — the paper wipe
SWAP_OUT = 40 * BAR             # 101.052632 — the night wipe
RIDE_OUT = 0.25                 # bar 40: the outgoing track's tail, past the downbeat
BLEED = 0.10
RIDE_IN = 0.05
DISSOLVE = 1.10                 # bar 28: 1234 rides out under Bluebird's own swell
BODY_B = 3.100                  # measure B's level on its body, not its fade-in

FADE_AT, FADE_LEN = 172.7, 1.6  # the film's own ending


def loudness(path: Path, ss: float = 0.0, t: float | None = None) -> float:
    """Integrated loudness (LUFS) of a file or a slice of one."""
    args = ["ffmpeg", "-v", "info", "-hide_banner"]
    if ss:
        args += ["-ss", "%.4f" % ss]
    args += ["-i", str(path)]
    if t:
        args += ["-t", "%.4f" % t]
    args += ["-af", "ebur128=framelog=quiet", "-f", "null", "-"]
    err = subprocess.run(args, capture_output=True, text=True).stderr
    m = re.findall(r"I:\s*(-?\d+\.\d+)\s*LUFS", err)
    if not m:
        raise RuntimeError(f"could not measure loudness of {path}")
    return float(m[-1])


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true",
                    help="measure the result and print the seam levels")
    args = ap.parse_args()

    for p in (TRACK_A, TRACK_B):
        if not p.exists():
            print(f"!! missing {p}")
            return 1
    OUT.parent.mkdir(parents=True, exist_ok=True)

    # --- match the two tracks by loudness, measured on the parts actually used
    seg_b = (SWAP_OUT - SWAP_IN) * TEMPO_B                 # 36.000 s of source
    la = loudness(TRACK_A, PICKUP_A, FILM)
    # from BODY_B, so the 1.3 s fade-in at the head does not drag the measured
    # level down and over-boost the whole segment to compensate
    lb = loudness(TRACK_B, BODY_B, seg_b - (BODY_B - START_B))
    gain_b = la - lb
    print("track A  %.1f LUFS" % la)
    print("track B  %.1f LUFS  ->  %+.1f dB" % (lb, gain_b))

    # --- A: continuous, gated across the middle act ------------------------
    # One `volume` expression rather than a cut-and-rejoin, so A never loses
    # phase with its own arrangement and comes back ON its bar 40.
    env = (
        "'if(lt(t,{a}),1,"
        "if(lt(t,{b}),({b}-t)/{r},"
        "if(lt(t,{c}),0,"
        "if(lt(t,{d}),(t-{c})/{i},1))))'"
    ).format(a=SWAP_IN, b=SWAP_IN + DISSOLVE, r=DISSOLVE,
             c=SWAP_OUT, d=SWAP_OUT + RIDE_IN, i=RIDE_IN)

    dur_b = SWAP_OUT - SWAP_IN + BLEED
    fc = (
        "[0:a]aresample=48000,atrim=0:{film},asetpts=N/SR/TB,"
        "volume=eval=frame:volume={env}[a];"
        # no fade-in on B: the track's own 1.3 s swell IS the transition
        "[1:a]aresample=48000,atempo={tempo},atrim=0:{durb},asetpts=N/SR/TB,"
        "volume={gain}dB,afade=t=in:st=0:d=0.02,"
        "afade=t=out:st={fo}:d={rout},adelay={delay}|{delay}[b];"
        "[a][b]amix=inputs=2:normalize=0:dropout_transition=0[m];"
        "[m]afade=t=out:st={fat}:d={falen},atrim=0:{film},"
        "alimiter=limit=0.97,aformat=channel_layouts=stereo[out]"
    ).format(
        film=FILM, env=env, tempo=TEMPO_B, durb=dur_b, gain=round(gain_b, 2),
        rin=RIDE_IN, fo=dur_b - RIDE_OUT, rout=RIDE_OUT,
        delay=round(SWAP_IN * 1000, 3), fat=FADE_AT, falen=FADE_LEN,
    )

    subprocess.run(
        ["ffmpeg", "-y", "-v", "error",
         "-ss", "%.4f" % PICKUP_A, "-i", str(TRACK_A),
         "-ss", "%.4f" % START_B, "-i", str(TRACK_B),
         "-filter_complex", fc, "-map", "[out]",
         "-c:a", "libmp3lame", "-b:a", "320k", "-ar", "48000", str(OUT)],
        check=True)
    print("bed       %s" % OUT)

    if args.check:
        print("\nper-bar loudness across the two seams (bar: LUFS)")
        for n in (26, 27, 28, 29, 31, 32, 35, 36, 38, 39, 40, 41, 42):
            print("  bar %2d  %6.1f" % (n, loudness(OUT, n * BAR, BAR)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
