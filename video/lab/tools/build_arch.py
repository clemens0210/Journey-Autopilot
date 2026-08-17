#!/usr/bin/env python3
"""
build_arch.py — inject the real slide-8 markup into the scenes that animate it.

Any composition can mark a slot:

    <!-- pptx:slide8 begin -->
      …generated markup, do not hand-edit…
    <!-- pptx:slide8 end -->

and this refills it from assets/arch/slide8.json. That keeps the diagram
*derived* from the deck: change the slide, re-run

    python3 tools/pptx_extract.py ../../docs/Status_Update_BCG_DB.pptx 8 -o assets/arch --scale 1.5
    python3 tools/build_arch.py

and every scene picks the new geometry up. Nothing about the architecture is
retyped by hand, so it cannot drift away from what the deck actually says.
"""
from __future__ import annotations

import glob
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

from pptx_to_html import build  # noqa: E402

BEGIN = "<!-- pptx:slide8 begin -->"
END = "<!-- pptx:slide8 end -->"


def main():
    doc = json.load(open(os.path.join(ROOT, "assets/arch/slide8.json")))
    regions = list(json.load(open(os.path.join(HERE, "arch-regions.json"))).items())
    # both arch scenes sit on the same near-white ground; deck greys that
    # would fail WCAG on it are pulled down to legibility
    markup = build(doc, "arch", "assets/arch/media", regions, contrast_bg="#f7f8f5", contrast_ratio=5.0)

    touched = 0
    for path in sorted(glob.glob(os.path.join(ROOT, "compositions", "*.html"))):
        src = open(path).read()
        if BEGIN not in src:
            continue
        out = re.sub(
            re.escape(BEGIN) + r".*?" + re.escape(END),
            BEGIN + "\n" + markup + "\n      " + END,
            src,
            flags=re.S,
        )
        if out != src:
            open(path, "w").write(out)
        touched += 1
        print(f"  injected -> {os.path.relpath(path, ROOT)}")

    if not touched:
        print("no composition carries the pptx:slide8 markers")
    else:
        print(f"{len(doc['shapes'])} shapes from slide {doc['slide']} into {touched} scene(s)")


if __name__ == "__main__":
    main()
