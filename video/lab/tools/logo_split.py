#!/usr/bin/env python3
"""
logo_split.py — cut a flat logo PNG into the pieces it is *made of*, so it can
build itself on screen instead of just fading in.

Two cutting modes, combinable:
  colors=<name:hex,...>   nearest-colour segmentation (a mark's facets, DB's red vs white)
  parts=<name:box,...>    rectangular slices in 0..1 of the trimmed box (mark vs wordmark)
  --components            after cutting, split each piece into its connected blobs
                          (this is what separates DB's frame from its D and its B)

Every part is written on the SAME canvas as the whole logo, so stacking them all
at inset:0 reassembles the original pixel-for-pixel — animation is then purely
transform + opacity per layer, with nothing to re-register.

parts.json records each piece's own bounding box (px and %), which is what you
feed to transform-origin so a shard rotates about itself rather than the canvas.

Usage
  python3 logo_split.py logo.png -o out/db --colors red:#ec0016,white:#ffffff --components
  python3 logo_split.py logo.png -o out/bcgp --parts mark:0,0,.28,1 word:.28,0,1,1 \\
      --colors green:#00ce7c,mint:#7fd9a9,black:#000000,grey:#5f5f5f,silver:#d8d8d8
"""
from __future__ import annotations

import argparse
import json
import os
import sys

try:
    from PIL import Image
except ImportError:
    sys.exit("needs Pillow:  pip install pillow")


def parse_hex(h):
    h = h.lstrip("#")
    return tuple(int(h[i : i + 2], 16) for i in (0, 2, 4))


def nearest(px, palette):
    r, g, b = px[:3]
    best, bd = None, 1e9
    for name, (pr, pg, pb) in palette:
        d = (r - pr) ** 2 + (g - pg) ** 2 + (b - pb) ** 2
        if d < bd:
            best, bd = name, d
    return best


def blank(size):
    return Image.new("RGBA", size, (0, 0, 0, 0))


def color_masks(im, palette, alpha_min=40):
    """Split opaque pixels into one full-canvas layer per palette entry."""
    w, h = im.size
    src = im.load()
    layers = {name: blank(im.size) for name, _ in palette}
    dst = {name: layers[name].load() for name in layers}
    for y in range(h):
        for x in range(w):
            px = src[x, y]
            if px[3] < alpha_min:
                continue
            dst[nearest(px, palette)][x, y] = px
    return layers


def components(layer, min_area=64, alpha_min=40):
    """Flood-fill connected opaque blobs; returns full-canvas layers, biggest first."""
    w, h = layer.size
    src = layer.load()
    seen = bytearray(w * h)
    out = []
    for sy in range(h):
        base = sy * w
        for sx in range(w):
            if seen[base + sx] or src[sx, sy][3] < alpha_min:
                continue
            stack, pixels = [(sx, sy)], []
            seen[base + sx] = 1
            while stack:
                x, y = stack.pop()
                pixels.append((x, y))
                for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                    nx, ny = x + dx, y + dy
                    if 0 <= nx < w and 0 <= ny < h and not seen[ny * w + nx]:
                        if src[nx, ny][3] >= alpha_min:
                            seen[ny * w + nx] = 1
                            stack.append((nx, ny))
            if len(pixels) < min_area:
                continue
            part = blank(layer.size)
            pd = part.load()
            for x, y in pixels:
                pd[x, y] = src[x, y]
            out.append((len(pixels), part))
    out.sort(key=lambda t: -t[0])
    return [p for _, p in out]


def box_slice(im, box):
    """Full-canvas layer holding only the pixels inside a fractional box."""
    w, h = im.size
    x0, y0, x1, y1 = (int(box[0] * w), int(box[1] * h), int(box[2] * w), int(box[3] * h))
    part = blank(im.size)
    part.paste(im.crop((x0, y0, x1, y1)), (x0, y0))
    return part


def describe(name, layer, canvas):
    bb = layer.getchannel("A").getbbox()
    if bb is None:
        return None
    w, h = canvas
    return {
        "name": name,
        "file": f"{name}.png",
        "box": [bb[0], bb[1], bb[2] - bb[0], bb[3] - bb[1]],
        # transform-origin for "this shard rotates about itself"
        "origin_pct": [
            round((bb[0] + bb[2]) / 2 / w * 100, 2),
            round((bb[1] + bb[3]) / 2 / h * 100, 2),
        ],
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("png")
    ap.add_argument("-o", "--out", required=True)
    ap.add_argument("--colors", help="name:#hex,name:#hex,…")
    ap.add_argument("--parts", help="name:x0,y0,x1,y1 (fractions of the trimmed box), space separated", nargs="*")
    ap.add_argument("--components", action="store_true", help="also split each layer into connected blobs")
    ap.add_argument("--min-area", type=int, default=400)
    ap.add_argument("--max-width", type=int, default=1600, help="downscale the working canvas")
    args = ap.parse_args()

    im = Image.open(args.png).convert("RGBA")
    bb = im.getchannel("A").getbbox()
    if bb:
        im = im.crop(bb)
    if im.width > args.max_width:
        im = im.resize((args.max_width, round(im.height * args.max_width / im.width)), Image.LANCZOS)

    os.makedirs(args.out, exist_ok=True)
    im.save(os.path.join(args.out, "full.png"))

    # stage 1 — rectangular regions (default: the whole thing as one region)
    regions = {"": im}
    if args.parts:
        regions = {}
        for spec in args.parts:
            name, nums = spec.split(":")
            regions[name] = box_slice(im, [float(v) for v in nums.split(",")])

    # stage 2 — colour segmentation inside each region
    palette = None
    if args.colors:
        palette = [(n, parse_hex(h)) for n, h in (c.split(":") for c in args.colors.split(","))]

    layers = []
    for rname, region in regions.items():
        subs = color_masks(region, palette).items() if palette else [("", region)]
        for cname, layer in subs:
            name = "-".join(p for p in (rname, cname) if p) or "logo"
            if args.components:
                blobs = components(layer, min_area=args.min_area)
                if len(blobs) > 1:
                    for i, b in enumerate(blobs, 1):
                        layers.append((f"{name}-{i}", b))
                    continue
                if blobs:
                    layers.append((name, blobs[0]))
                continue
            if layer.getchannel("A").getbbox():
                layers.append((name, layer))

    manifest = {"source": os.path.basename(args.png), "canvas": list(im.size), "parts": []}
    for name, layer in layers:
        info = describe(name, layer, im.size)
        if not info:
            continue
        layer.save(os.path.join(args.out, f"{name}.png"))
        manifest["parts"].append(info)

    with open(os.path.join(args.out, "parts.json"), "w") as fh:
        json.dump(manifest, fh, indent=1)
    print(f"{args.out}  canvas {im.size[0]}x{im.size[1]}  {len(manifest['parts'])} parts")
    for p in manifest["parts"]:
        print(f"   {p['name']:22s} box={p['box']}  origin={p['origin_pct']}")


if __name__ == "__main__":
    main()
