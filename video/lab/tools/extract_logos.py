#!/usr/bin/env python3
"""
extract_logos.py — pull the three partner logos out of the status deck and cut
each into the pieces it is made of, ready to animate.

The deck already carries all three at print resolution (Uni Köln at 9241×4167),
so nothing has to be re-sourced or redrawn — and the marks in the video are
provably the same files the deck uses.

    python3 tools/extract_logos.py            # rebuilds assets/logos/**

Each logo's parts are written on the same canvas as the whole logo, so stacking
them at inset:0 reassembles it pixel-for-pixel — this script verifies that and
fails loudly if a split loses a pixel.
"""
from __future__ import annotations

import os
import subprocess
import sys
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
PPTX = os.path.join(ROOT, "..", "..", "docs", "Status_Update_BCG_DB.pptx")
OUT = os.path.join(ROOT, "assets", "logos")

# which media file in the deck is which mark, and how to cut it
LOGOS = [
    {
        "image": "ppt/media/image6.png",
        "name": "db",
        # red ring / D / B / white plate / counters, by colour then by blob
        "args": ["--colors", "red:#ec0016,white:#ffffff", "--components", "--min-area", "2000", "--max-width", "1200"],
    },
    {
        "image": "ppt/media/image5.png",
        "name": "bcgp",
        # the prism's five facets, plus the two wordmark lines
        "args": [
            "--parts", "mark:0,0,0.315,1", "bcg:0.315,0,1,0.49", "platinion:0.315,0.49,1,1",
            "--colors", "green:#00ce7c,mint:#7fd9a9,black:#0a0a0a,grey:#5f5f5f,silver:#dcdcdc",
            "--min-area", "300", "--max-width", "1200",
        ],
    },
    {
        "image": "ppt/media/image1.png",
        "name": "unikoeln",
        # the seal, then UNIVERSITÄT and ZU KÖLN as separate lines
        "args": [
            "--parts", "seal:0,0,0.40,1", "line1:0.42,0.20,1,0.51", "line2:0.42,0.51,1,0.80",
            "--min-area", "200", "--max-width", "1400",
        ],
    },
]

FLAT = {"db": "db.png", "bcgp": "bcg-platinion.png", "unikoeln": "uni-koeln.png"}


def verify(folder):
    """Every split must recompose into the original, or the animation would
    quietly reshape the mark."""
    try:
        from PIL import Image
    except ImportError:
        return None
    import json

    manifest = json.load(open(os.path.join(folder, "parts.json")))
    base = Image.open(os.path.join(folder, "full.png")).convert("RGBA")
    comp = Image.new("RGBA", base.size, (0, 0, 0, 0))
    for part in manifest["parts"]:
        comp.alpha_composite(Image.open(os.path.join(folder, part["file"])).convert("RGBA"))
    a, b = base.getchannel("A").tobytes(), comp.getchannel("A").tobytes()
    return sum(1 for x, y in zip(a, b) if abs(x - y) > 40)


def main():
    if not os.path.exists(PPTX):
        sys.exit(f"deck not found: {PPTX}")
    os.makedirs(OUT, exist_ok=True)

    with zipfile.ZipFile(PPTX) as zf:
        for spec in LOGOS:
            flat = os.path.join(OUT, FLAT[spec["name"]])
            with zf.open(spec["image"]) as src, open(flat, "wb") as dst:
                dst.write(src.read())

            folder = os.path.join(OUT, spec["name"])
            subprocess.run(
                [sys.executable, os.path.join(HERE, "logo_split.py"), flat, "-o", folder, *spec["args"]],
                check=True,
            )
            bad = verify(folder)
            if bad:
                sys.exit(f"  !! {spec['name']}: {bad} px lost — the parts no longer rebuild the mark")
            if bad == 0:
                print(f"  verified: {spec['name']} parts rebuild the original exactly\n")

    # the BCG wordmark comes out twice (whole + per line); keep the lines
    stale = os.path.join(OUT, "bcgp", "word-black.png")
    if os.path.exists(stale):
        os.remove(stale)


if __name__ == "__main__":
    main()
