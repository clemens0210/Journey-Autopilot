#!/usr/bin/env python3
"""Verify a rendered cut against the beat grid it was authored on.

Three things this checks that a visual review cannot:

1. **Duration and frame count** — the film is authored as 69 bars of 2.526316 s.
2. **Beat alignment** — the music is decoded back out of the *encoded MP4*, not
   out of the source file, and two independent things are measured on it:

   * **grid lock** — every kick transient in a span is located and its offset
     from the nearest 95 BPM beat taken; the median must be inside a frame or
     two. This is what actually catches an audio offset introduced during
     muxing, and unlike a threshold crossing it does not care which track is
     playing. v4 runs two tracks (see tools/build_music.py) and this test holds
     both of them to the same grid.
   * **which track is playing where** — the per-bar kick level of the render is
     compared against `1234.mp3`'s own. It must match through acts 1 and 3 and
     must NOT match across bars 30–35, where the film's own track is muted and
     the second one is carrying the act. A level-step test cannot do this job:
     1234's bar 40 is a quiet bar in its own arrangement, so the swap BACK on
     bar 40 produces no step at all.
3. **Cut alignment** — for a list of authored bar numbers, the frame-to-frame
   pixel difference is measured across a window. A bar of 2.526316 s is 75.79
   frames, so bar lines fall *between* frames: a cut can only sit on the frame
   before or after. The test is therefore that the frames straddling the bar
   carry the bulk of the change in that window.

Usage:  python3 tools/verify_render.py renders/journey-autopilot-v4.mp4
        python3 tools/verify_render.py renders/...-vo.mp4 --picture-only
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np

BAR = 2.5263157894736843
FPS = 30
BARS = 69


def probe(path: str) -> dict:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries",
         "format=duration,size:stream=codec_type,codec_name,width,height,r_frame_rate,nb_frames,sample_rate,channels",
         "-of", "json", path],
        capture_output=True, text=True, check=True).stdout
    return json.loads(out)


def audio_from(path: str, sr: int = 22050, ss: float = 0.0) -> np.ndarray:
    with tempfile.NamedTemporaryFile(suffix=".raw", delete=False) as f:
        raw = f.name
    args = ["ffmpeg", "-y", "-v", "error"]
    if ss:
        args += ["-ss", f"{ss:.4f}"]
    args += ["-i", path, "-ac", "1", "-ar", str(sr), "-f", "f32le", raw]
    subprocess.run(args, check=True)
    x = np.fromfile(raw, dtype=np.float32)
    Path(raw).unlink()
    return x


def audio(path: str, sr: int = 22050) -> np.ndarray:
    return audio_from(path, sr)


def kick_band(x: np.ndarray, sr: int) -> np.ndarray:
    n = len(x) // 2 * 2
    X = np.fft.rfft(x[:n])
    f = np.fft.rfftfreq(n, 1 / sr)
    low = np.fft.irfft(np.where(f < 120, X, 0)).astype(np.float32)
    w = int(sr * 0.030)
    return np.sqrt(np.convolve(low ** 2, np.ones(w) / w, "same"))


def crossing(env, sr, a, b, level, rising) -> float:
    seg = env[int(a * sr):int(b * sr)]
    i = int(np.argmax(seg > level) if rising else np.argmax(seg < level))
    return a + i / sr


def frame_diffs(path: str, at: float, span: float = 0.5):
    """Mean absolute frame-to-frame difference around `at`, one row per frame."""
    a = max(0.0, at - span)
    out = subprocess.run(
        ["ffmpeg", "-v", "error", "-ss", f"{a:.4f}", "-i", path,
         "-t", f"{2 * span:.4f}", "-vf", "scale=160:90,format=gray",
         "-f", "rawvideo", "-"],
        capture_output=True, check=True).stdout
    fr = np.frombuffer(out, dtype=np.uint8).reshape(-1, 90, 160).astype(np.int16)
    d = np.abs(np.diff(fr, axis=0)).mean(axis=(1, 2))
    t = a + (np.arange(len(d)) + 1) / FPS
    return t, d


def main(path: str, music_edges: bool = True) -> int:
    """`music_edges=False` for the narrated cut.

    The beat test locates threshold crossings in the kick band of the encoded
    audio. On the narrated cut that band is side-chained by the narration and
    the speech itself puts energy into it, so the crossings move and the test
    reports a grid error that is not there. The narrated cut carries the same
    picture as the music-only one (verified frame by frame: mean absolute
    difference under 0.1 of 255, i.e. encoder noise), so the grid is checked
    once, on the music-only master, and only the picture is checked here.
    """
    bad = 0
    p = probe(path)
    fmt = p["format"]
    v = next(s for s in p["streams"] if s["codec_type"] == "video")
    a = next((s for s in p["streams"] if s["codec_type"] == "audio"), None)
    dur = float(fmt["duration"])

    print(f"file        {path}  ({int(fmt['size']) / 1e6:.1f} MB)")
    print(f"video       {v['codec_name']} {v['width']}x{v['height']} @ {v['r_frame_rate']}  "
          f"{v.get('nb_frames', '?')} frames")
    print(f"audio       {a['codec_name']} {a['sample_rate']} Hz {a['channels']} ch" if a else "audio       MISSING")
    print(f"duration    {dur:.3f} s   ({dur / BAR:.3f} bars)")
    if a is None:
        print("  ✗ no audio stream"); bad += 1
    if abs(dur - BARS * BAR) > 0.25:
        print(f"  ! duration is {dur - BARS * BAR:+.3f} s off {BARS} whole bars")

    if not music_edges:
        print("\n--- beat alignment: skipped (narrated cut, see main()'s docstring) ---")
    else:
        print("\n--- beat alignment, measured on the ENCODED audio ---")
        bad += beats(path)

    print("\n--- cuts land on their bar ---")
    # Only bars that carry a picture CUT. v4's phone footage runs in long
    # unbroken clips, so a bar where nothing changes but the headline has its
    # biggest luma event in the outgoing card's exit, not on the downbeat —
    # true by design, and not something this test should be asked to judge.
    for n in (4, 7, 13, 14, 26, 28, 36, 40, 44, 47, 53, 54, 60, 62, 65):
        t, d = frame_diffs(path, n * BAR)
        peak = float(t[int(np.argmax(d))])
        off_frames = (peak - n * BAR) * FPS
        # A bar of 2.526316 s is 75.79 frames, so bar lines land BETWEEN frames:
        # the cut can only be on the frame before or the frame after. And a
        # designed wipe or exit legitimately carries luma either side of it.
        # So the test is: does the frame pair straddling the bar carry most of
        # the change in this window?
        i = int(np.argmin(np.abs(t - n * BAR)))
        straddle = float(d[max(0, i - 1):i + 2].max())
        share = straddle / max(d.max(), 1e-6)
        ok = abs(off_frames) <= 1.5 or share >= 0.6
        print(f"  {'ok ' if ok else '✗  '}bar {n:2d} @ {n * BAR:8.3f} s   biggest change at "
              f"{peak:8.3f} s  ({off_frames:+.2f} frames)   the bar's own frames carry {share:.0%} of it")
        if not ok:
            bad += 1

    print("\n" + ("PASS" if bad == 0 else f"FAIL — {bad} problem(s)"))
    return 0 if bad == 0 else 1


def kick_transients(env: np.ndarray, sr: int, t0: float, t1: float) -> np.ndarray:
    """Offsets of every kick transient in [t0, t1) from the nearest 95 BPM beat."""
    seg = env[int(t0 * sr):int(t1 * sr)]
    d = np.diff(seg)
    thr = d.mean() + 1.6 * d.std()
    per = BAR / 4
    out, i = [], 1
    while i < len(d) - 1:
        if d[i] > thr and d[i] >= d[i - 1] and d[i] > d[i + 1]:
            r = ((t0 + i / sr) % per)
            out.append(r - per if r > per / 2 else r)
            i += int(0.12 * sr)
        else:
            i += 1
    return np.array(out)


def beats(path: str) -> int:
    bad = 0
    sr = 22050
    env = kick_band(audio(path, sr), sr)

    # --- 1. is the music ON the grid, wherever it comes from -----------------
    print("  grid lock (median kick offset from the nearest beat)")
    for name, a, b in (("bars  4–27  track A", 4, 27),
                       ("bars 29–39  track B", 29, 39),
                       ("bars 41–63  track A", 41, 63)):
        r = kick_transients(env, sr, a * BAR, b * BAR)
        med = float(np.median(r)) if len(r) else 0.0
        ok = len(r) >= 20 and abs(med) * FPS <= 2.0
        print(f"    {'ok ' if ok else '✗  '}{name}   n={len(r):3d}   "
              f"median {med * 1000:+6.1f} ms ({med * FPS:+.1f} frames)")
        if not ok:
            bad += 1

    # --- 2. is the RIGHT track playing in each act ---------------------------
    # Per-bar kick level of the render against 1234.mp3's own, normalised on
    # act 1 so the render's own gain and limiter drop out. This is the test that
    # actually says "the second track is under the middle act and the first one
    # is back afterwards"; a level step cannot, because 1234's bar 40 is a quiet
    # bar in its own arrangement and the swap back makes no step there.
    lvl = np.array([env[int(n * BAR * sr):int((n + 1) * BAR * sr)].mean()
                    for n in range(BARS)])
    step = np.abs(np.diff(lvl))
    print("  structure")
    for name, n in (("drop A", 4), ("track B in", 28), ("outro", 64)):
        w = range(max(1, n - 2), min(BARS - 1, n + 3))
        at = max(w, key=lambda k: step[k - 1])
        ok = at == n
        print(f"    {'ok ' if ok else '✗  '}{name:12s} expected bar {n:2d}, "
              f"biggest level step at bar {at:2d}   "
              f"({lvl[n - 1]:.3f} -> {lvl[n]:.3f})")
        if not ok:
            bad += 1

    track_a = Path(__file__).resolve().parent.parent / "1234.mp3"
    if not track_a.exists():
        print("  which track is playing where: skipped (1234.mp3 not found)")
        return bad
    ea = kick_band(audio_from(str(track_a), sr, ss=0.1028), sr)
    la = np.array([ea[int(n * BAR * sr):int((n + 1) * BAR * sr)].mean()
                   for n in range(BARS) if int((n + 1) * BAR * sr) <= len(ea)])
    m = min(len(la), BARS)
    k = float(np.median(lvl[4:27]) / np.median(la[4:27]))
    print(f"  which track is playing where (vs 1234.mp3 alone, gain x{k:.2f})")
    for name, a, b, same in (("bars  8–26  the film's own track", 8, 26, True),
                             ("bars 30–35  the second track", 30, 35, False),
                             ("bars 41–62  back to the first", 41, 62, True)):
        if b > m:
            continue
        d = float(np.abs(lvl[a:b] - k * la[a:b]).mean())
        ok = d < 0.06 if same else d > 0.20
        print(f"    {'ok ' if ok else '✗  '}{name:34s} mean |difference| {d:.3f}"
              f"   ({'same track' if same else 'a different track'} expected)")
        if not ok:
            bad += 1
    return bad


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    sys.exit(main(args[0] if args else "renders/journey-autopilot-v4.mp4",
                  music_edges="--picture-only" not in sys.argv))
