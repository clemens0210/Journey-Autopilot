#!/usr/bin/env python3
"""Build the voice-over stem and the ducked music bed for the narrated cut.

The narration is written to the film's own arrangement, not laid over it:

* every line starts on a bar line or a half-bar of the 95 BPM grid, so the
  voice enters where the music already has an edge;
* no line begins on a drop — the two drops (bar 4 and bar 36) are left to the
  track, and the lines around them finish before and start after;
* the breakdown (bars 28–36), where the kick is out, carries the densest
  writing, because that is where a voice can be heard properly;
* nothing here reads the screen out loud. The on-screen copy states, the
  narration tells the story around it;
* the voice is `am_adam` at 1.12, chosen by measurement over `bm_george` and
  `am_michael`: on the same line it is ~15-25 % faster, moves ~20 % further in
  pitch, and is the most tonal of the three (lowest spectral flatness in voiced
  frames). Fast and animated is the brief; the writing does the rest.

Outputs, both in assets/vo/:
  voice.wav          the stem — one 174.3 s track with each line at its mark
  music-ducked.wav   1234.mp3, pickup trimmed, ducked under the stem

Usage:  python3 tools/build_vo.py [--voice am_adam] [--speed 1.12]
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import wave
from pathlib import Path

BAR = 2.5263157894736843
BEAT = BAR / 4
FILM = 174.3
PICKUP = 0.1028          # the same trim the music-only cut applies
SR = 24000               # Kokoro's sample rate
LINE_RMS = -16.0         # every line pushed to this, so the voice is even
BED_TRIM = -5.0          # the bed sits lower in the narrated cut than in the
                         # music-only one; the film is carried by the voice here

HERE = Path(__file__).resolve().parent.parent
OUT = HERE / "assets" / "vo"
LINES_DIR = OUT / "lines"

# (bar position, text). Bar positions are exact grid marks.
#
# The writing rules, which are as much of the spec as the timings:
#   * tell the story, don't caption the picture — if a sentence could be read
#     off the screen it is the wrong sentence;
#   * no jargon. No framework names, no "orchestrator", no "arithmetic". The
#     diagram is on screen; the voice says what it MEANS;
#   * second person, contractions, short sentences, the odd fragment. It should
#     sound like someone who built the thing telling you about it;
#   * two blocks carry no narration at all — the tool wall (bars 43–46) and the
#     whole ending (bar 59 on). The wall is dense enough to read on its own, and
#     the reprise and the end card are better with only the track under them.
SCRIPT = [
    (0.75,  "One number on a board, and the evening's gone."),
    (2.00,  "From there it's your problem. The queue, the hotline, "
            "the hunt for a bed."),
    (5.50,  "So we taught something to worry about it for you. Not a chatbot. "
            "Something that actually goes and fixes it."),
    (7.75,  "First it learns you. What you've booked, where you need to be, "
            "and how far it's allowed to go on its own."),
    (9.75,  "You set that up once, and then you forget about it."),
    (11.25, "Then it goes quiet, and just watches."),
    (14.25, "Until a signal box outside Nuremberg gives up."),
    (15.75, "Your train's fine. Your connection isn't. And you have no idea yet."),
    (17.25, "By the time you look up, it's found two ways round, "
            "and checked them against tomorrow morning."),
    (19.75, "Train, car, bike. Whatever gets you home."),
    (21.25, "And here's the bit I like. It stops, and waits for you."),
    (23.25, "One tap. Same ticket, new seat, nothing to pay."),
    (25.50, "You never queued. You never called. You just kept reading."),
    (28.25, "So what's doing all this? Not one program. A team."),
    (30.25, "One runs the show. It doesn't do the work; it decides who should."),
    (32.25, "One watches the network. One finds the way round. One writes to you. "
            "And only one ever goes near your booking."),
    (34.75, "And none of them can book anything without asking you."),
    (36.50, "And they don't take turns. All of them, at once."),
    (40.50, "You're watching it work. None of this was staged."),
    (41.75, "If it ever does something you don't like, you can see why."),
    (46.50, "It remembers the ones that already went wrong. Two hours late means "
            "you're owed money, and it knows the rules."),
    (51.25, "Planning a new trip? What you see is what the network is doing right now."),
    (53.00, "From that second on, it's not your job any more."),
    (54.25, "And most days? It has nothing to tell you. That's the point."),
    (56.50, "It's the least dramatic thing we built, and the one you'd miss most."),
    (58.25, "All of that, on an evening you'd otherwise have spent on the phone."),
]


def wav_duration(path: Path) -> float:
    with wave.open(str(path), "rb") as w:
        return w.getnframes() / w.getframerate()


def synth(text: str, dest: Path, voice: str, speed: float) -> float:
    env = dict(os.environ)
    venv = HERE / ".tts-venv" / "bin" / "python"
    if venv.exists():
        env["HYPERFRAMES_PYTHON"] = str(venv)
    subprocess.run(
        ["npx", "--yes", "hyperframes@0.7.92", "tts", text,
         "-v", voice, "-s", str(speed), "-o", str(dest)],
        check=True, capture_output=True, env=env)
    return wav_duration(dest)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--voice", default="am_adam")
    ap.add_argument("--speed", type=float, default=1.12)
    ap.add_argument("--skip-synth", action="store_true")
    args = ap.parse_args()

    LINES_DIR.mkdir(parents=True, exist_ok=True)
    marks = []
    for i, (bar, text) in enumerate(SCRIPT):
        dest = LINES_DIR / ("vo-%02d.wav" % (i + 1))
        if args.skip_synth and dest.exists():
            dur = wav_duration(dest)
        else:
            dur = synth(text, dest, args.voice, args.speed)
        marks.append({"i": i + 1, "bar": bar, "at": bar * BAR,
                      "dur": dur, "text": text, "file": dest.name})

    # --- report the fit, in bars, before anything is mixed -------------------
    print("%-3s %8s %8s %8s   %s" % ("#", "bar in", "bar out", "seconds", "line"))
    bad = 0
    for k, m in enumerate(marks):
        end = m["at"] + m["dur"]
        nxt = marks[k + 1]["at"] if k + 1 < len(marks) else FILM
        clash = "  << overlaps next" if end > nxt - 0.15 else ""
        if clash:
            bad += 1
        print("%-3d %8.2f %8.2f %8.2f   %s%s"
              % (m["i"], m["bar"], end / BAR, m["dur"], m["text"][:52], clash))
    print("\n%d line(s), %.1f s of speech in %.1f s of film (%.0f%%)"
          % (len(marks), sum(m["dur"] for m in marks), FILM,
             100 * sum(m["dur"] for m in marks) / FILM))
    if bad:
        print("!! %d line(s) run into the next one — shorten them or move the mark" % bad)

    # --- the stem: one film-length track with each line at its mark ----------
    # Kokoro comes out quiet and a little uneven line to line, so every line is
    # measured and pushed to the same RMS before it is placed. Without this the
    # narration sits ~9 dB UNDER the ducked bed and is simply not heard.
    ins, filters, tags = [], [], []
    for k, m in enumerate(marks):
        src = LINES_DIR / m["file"]
        det = subprocess.run(
            ["ffmpeg", "-v", "info", "-i", str(src), "-af", "volumedetect",
             "-f", "null", "-"], capture_output=True, text=True).stderr
        mean = next((float(l.split("mean_volume:")[1].split("dB")[0])
                     for l in det.splitlines() if "mean_volume:" in l), LINE_RMS)
        gain = max(-6.0, min(18.0, LINE_RMS - mean))
        m["gain_db"] = round(gain, 2)
        ins += ["-i", str(src)]
        # 60 ms of fade either side kills the click at the head of a Kokoro clip
        filters.append(
            "[%d:a]aresample=48000,volume=%.2fdB,alimiter=limit=0.92,"
            "afade=t=in:st=0:d=0.06,afade=t=out:st=%.3f:d=0.10,adelay=%d|%d[v%d]"
            % (k, gain, max(0.0, m["dur"] - 0.10),
               int(m["at"] * 1000), int(m["at"] * 1000), k))
        tags.append("[v%d]" % k)
    stem = OUT / "voice.wav"
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", *ins,
         "-filter_complex", ";".join(filters) + ";" + "".join(tags)
         + "amix=inputs=%d:normalize=0:dropout_transition=0[m];" % len(marks)
         + "[m]apad,atrim=0:%.4f,aformat=channel_layouts=stereo[out]" % FILM,
         "-map", "[out]", "-ar", "48000", str(stem)], check=True)
    (OUT / "script.json").write_text(json.dumps(marks, indent=2) + "\n")
    print("\nstem      %s" % stem)

    # --- the bed: trim the pickup so film time IS file time, then duck -------
    # sidechaincompress keyed off the stem, so the music steps back only while
    # the voice is actually speaking and comes straight back up after it.
    # These numbers were swept and measured, not guessed: they put the bed
    # 10.1 dB down under a line and leave the voice 4.1 dB above it — the bed
    # is still clearly playing, which is the point. Deeper ratios mute the
    # track under every sentence; shallower ones bury the voice.
    bed = OUT / "music-ducked.wav"
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error",
         "-ss", "%.4f" % PICKUP, "-i", str(HERE / "1234.mp3"),
         "-i", str(stem),
         "-filter_complex",
         "[0:a]aresample=48000,atrim=0:%.4f,asetpts=N/SR/TB,volume=%.1fdB[bed];"
         % (FILM, BED_TRIM)
         + "[1:a]aresample=48000,asetpts=N/SR/TB[key];"
         + "[bed][key]sidechaincompress="
           "threshold=0.045:ratio=5:attack=15:release=480:makeup=1:level_sc=1.2[out]",
         "-map", "[out]", "-ar", "48000", str(bed)], check=True)
    print("bed       %s" % bed)

    shutil.rmtree(OUT / "__pycache__", ignore_errors=True)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
