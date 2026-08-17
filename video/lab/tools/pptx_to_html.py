#!/usr/bin/env python3
"""
pptx_to_html.py — turn pptx_extract.py's JSON into positioned HTML you can animate.

The point: keep the slide's real geometry, but land it as ordinary DOM — absolutely
positioned <div>s for shapes and one <svg> for every connector — so each piece has a
stable id and GSAP can drive it. Shapes come out grouped by the PowerPoint group they
came from, which is usually exactly the unit you want to animate together.

Usage
  python3 pptx_to_html.py assets/arch/slide8.json \\
      --media-prefix assets/arch/media \\
      --prefix arch \\
      -o build/slide8.html          # standalone preview page
  python3 pptx_to_html.py ... --fragment   # just the markup, to paste into a scene

Connectors become <path> with pathLength="1", so a draw-on is a single
strokeDashoffset 1 -> 0 tween with no DOM measurement.
"""
from __future__ import annotations

import argparse
import html
import json
import os
import re

# PowerPoint preset geometries we can express directly in CSS.
ROUNDED = {"roundRect": 0.16, "round2SameRect": 0.16}


def _lum(rgb):
    """WCAG relative luminance."""
    out = []
    for c in rgb:
        c /= 255.0
        out.append(c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4)
    return 0.2126 * out[0] + 0.7152 * out[1] + 0.0722 * out[2]


def darken_to_contrast(hexv, bg="#f7f8f5", ratio=4.5):
    """Deck labels are often set in a grey that is fine on a projector and
    illegible in a 1080p video. Pull any run colour down until it clears the
    ratio against the scene's background, leaving darker colours untouched."""
    try:
        r, g, b = (int(hexv[i : i + 2], 16) for i in (1, 3, 5))
        br, bgc, bb = (int(bg[i : i + 2], 16) for i in (1, 3, 5))
    except (ValueError, IndexError):
        return hexv
    lbg = _lum((br, bgc, bb))
    for _ in range(40):
        lt = _lum((r, g, b))
        hi, lo = max(lbg, lt), min(lbg, lt)
        if (hi + 0.05) / (lo + 0.05) >= ratio:
            break
        r, g, b = int(r * 0.94), int(g * 0.94), int(b * 0.94)
    return "#%02x%02x%02x" % (r, g, b)


def css_color(v, default="transparent"):
    if not v:
        return default
    if v.startswith("scheme:"):
        return default
    if "@" in v:
        hexv, alpha = v.split("@")
        r, g, b = (int(hexv[i : i + 2], 16) for i in (1, 3, 5))
        return f"rgba({r},{g},{b},{float(alpha):.3f})"
    return v


def slug(name, taken):
    s = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "shape"
    n, out = 1, s
    while out in taken:
        n += 1
        out = f"{s}-{n}"
    taken.add(out)
    return out


def shape_style(s):
    css = [
        f"left:{s['x']}px",
        f"top:{s['y']}px",
        f"width:{s['w']}px",
        f"height:{s['h']}px",
    ]
    geom = s.get("geom")
    fill = css_color(s.get("fill"))
    if fill != "transparent":
        css.append(f"background:{fill}")
    if geom == "ellipse":
        css.append("border-radius:50%")
    elif geom in ROUNDED:
        r = min(s["w"], s["h"]) * ROUNDED[geom]
        css.append(f"border-radius:{r:.1f}px")
    elif geom == "diamond":
        css.append("clip-path:polygon(50% 0,100% 50%,50% 100%,0 50%)")
    elif geom == "triangle":
        css.append("clip-path:polygon(50% 0,100% 100%,0 100%)")
    elif geom == "homePlate":
        css.append("clip-path:polygon(0 0,72% 0,100% 50%,72% 100%,0 100%)")
    elif geom == "blockArc":
        # a gauge dial: ring stroke instead of a filled disc, open at the bottom
        w = min(s["w"], s["h"]) * 0.22
        css = [c for c in css if not c.startswith("background")]
        css += [
            "border-radius:50%",
            f"border:{w:.1f}px solid {fill}",
            "border-bottom-color:transparent",
            "background:none",
        ]
    ln = s.get("line")
    if ln and ln.get("color"):
        css.append(f"border:{ln['w']}px solid {css_color(ln['color'])}")
    if s.get("rot"):
        css.append(f"transform:rotate({s['rot']}deg)")
    return ";".join(css)


def text_html(s, contrast_bg=None, ratio=4.5):
    t = s["text"]
    align = {"ctr": "center", "r": "right", "just": "justify"}.get(
        t["paras"][0]["align"], "left"
    )
    anchor = {"ctr": "center", "b": "flex-end"}.get(t.get("anchor"), "flex-start")
    first = t["paras"][0]["runs"][0]
    css = [
        f"text-align:{align}",
        f"justify-content:{anchor}",
        f"font-size:{first.get('size', 12)}px",
    ]
    if first.get("bold"):
        css.append("font-weight:700")
    if first.get("color"):
        col = css_color(first["color"], "inherit")
        if contrast_bg and col.startswith("#"):
            # large type only needs 3:1; the tiny tool captions need the full 4.5
            col = darken_to_contrast(col, contrast_bg, ratio * (3.0 / 4.5) if first.get("size", 12) >= 24 else ratio)
        css.append(f"color:{col}")
    if first.get("tracking"):
        css.append(f"letter-spacing:{first['tracking']}px")
    inner = "".join(
        f"<span>{html.escape(p['text'])}</span>" for p in t["paras"] if p["text"].strip()
    )
    return ";".join(css), inner


def endpoints(s):
    """A connector's (start, end) in page px — same flip logic the path uses."""
    x, y, w, h = s["x"], s["y"], s["w"], s["h"]
    x1, x2 = (x + w, x) if s.get("flipH") else (x, x + w)
    y1, y2 = (y + h, y) if s.get("flipV") else (y, y + h)
    return (x1, y1), (x2, y2)


def region_at(pt, regions, slack=26):
    """Name of the region a point sits in (or just outside — wires stop short
    of the box they point at, so a little slack is what makes this useful)."""
    if not regions:
        return ""
    x, y = pt
    for name, (rx, ry, rw, rh) in regions:
        if rx - slack <= x <= rx + rw + slack and ry - slack <= y <= ry + rh + slack:
            return name
    return ""


def region_of(s, regions):
    """Which named region a shape belongs to, by where its centre falls.

    PowerPoint's own group names ('Gruppieren 33') are useless as animation
    handles, so we re-group by geometry instead: draw boxes over the parts of
    the diagram that should move together and every shape inside gets tagged.
    """
    if not regions:
        return ""
    cx, cy = s["x"] + s.get("w", 0) / 2, s["y"] + s.get("h", 0) / 2
    for name, (x, y, w, h) in regions:
        if x <= cx <= x + w and y <= cy <= y + h:
            return name
    return ""


def build(doc, prefix, media_prefix, regions=None, contrast_bg=None, contrast_ratio=4.5):
    taken, blocks, paths = set(), [], []
    for s in doc["shapes"]:
        if s.get("placeholder") or "x" not in s:
            continue
        sid = f"{prefix}-{slug(s['name'], taken)}"
        part = region_of(s, regions)
        data_group = f' data-part="{html.escape(part)}"' if part else ""

        if s["kind"] == "cxnSp":
            ln = s.get("line") or {}
            a, b = endpoints(s)
            wire = f"{region_at(a, regions) or '?'}>{region_at(b, regions) or '?'}"
            attrs = [
                f'id="{sid}"',
                f'd="{s["d"]}"',
                'pathLength="1"',
                f'data-wire="{wire}"',
                *([data_group.strip()] if data_group else []),
                f'stroke="{css_color(ln.get("color"), "#a9adb5")}"',
                f'stroke-width="{ln.get("w", 1.4)}"',
                'fill="none"',
                'stroke-linecap="round"',
            ]
            if ln.get("dash") == "dash":
                attrs.append('class="dashed"')
            if ln.get("tail") == "triangle":
                attrs.append('marker-end="url(#%s-arrow)"' % prefix)
            paths.append("      <path " + " ".join(attrs) + " />")
            continue

        if s["kind"] == "pic" and s.get("image"):
            src = os.path.join(media_prefix, s["image"])
            blocks.append(
                f'    <img id="{sid}" class="pp-img"{data_group} src="{src}" alt="" '
                f'style="{shape_style(s)}" />'
            )
            continue

        style = shape_style(s)
        if "text" in s:
            tcss, inner = text_html(s, contrast_bg, contrast_ratio)
            blocks.append(
                f'    <div id="{sid}" class="pp-text"{data_group} style="{style};{tcss}">{inner}</div>'
            )
        else:
            blocks.append(f'    <div id="{sid}" class="pp-shape"{data_group} style="{style}"></div>')

    svg = [
        f'    <svg id="{prefix}-wires" class="pp-wires" viewBox="0 0 {doc["width"]:.0f} {doc["height"]:.0f}">',
        "      <defs>",
        f'        <marker id="{prefix}-arrow" viewBox="0 0 10 10" refX="9" refY="5" '
        'markerWidth="5" markerHeight="5" orient="auto-start-reverse">',
        '          <path d="M 0 0 L 10 5 L 0 10 z" fill="#a9adb5" />',
        "        </marker>",
        "      </defs>",
        *paths,
        "    </svg>",
    ]
    return "\n".join(svg + blocks)


CSS = """
    .pp-stage { position:relative; width:%(w).0fpx; height:%(h).0fpx; overflow:hidden;
                font-family:"Albert Sans","InterLocal",system-ui,sans-serif; color:#3e4148; }
    .pp-wires { position:absolute; inset:0; width:100%%; height:100%%; overflow:visible; }
    .pp-wires .dashed { stroke-dasharray:4 4; }
    .pp-shape, .pp-text, .pp-img { position:absolute; }
    .pp-text { display:flex; flex-direction:column; line-height:1.15; }
    .pp-img { object-fit:contain; }
"""

PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>%(title)s</title>
<style>
  * { margin:0; padding:0; box-sizing:border-box; }
  body { background:#fff; }
%(css)s
</style>
</head>
<body>
  <div class="pp-stage">
%(body)s
  </div>
</body>
</html>
"""


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("json")
    ap.add_argument("-o", "--out")
    ap.add_argument("--prefix", default="pp")
    ap.add_argument("--media-prefix", default="media")
    ap.add_argument("--fragment", action="store_true", help="markup only, no page chrome")
    ap.add_argument("--regions", help="JSON file: {name: [x,y,w,h], …} -> data-part on every shape inside")
    ap.add_argument("--debug-regions", action="store_true", help="outline the regions so you can check the map")
    ap.add_argument("--contrast-bg", help="scene background (e.g. #f7f8f5): darken deck text that would fail WCAG on it")
    args = ap.parse_args()

    doc = json.load(open(args.json))
    regions = None
    if args.regions:
        regions = list(json.load(open(args.regions)).items())
    body = build(doc, args.prefix, args.media_prefix, regions, args.contrast_bg)
    if args.debug_regions and regions:
        body += "\n" + "\n".join(
            f'    <div style="position:absolute;left:{x}px;top:{y}px;width:{w}px;height:{h}px;'
            f'border:2px dashed #533cfe;color:#533cfe;font:600 15px sans-serif;padding:2px 5px;'
            f'z-index:99">{name}</div>'
            for name, (x, y, w, h) in regions
        )
    dims = {"w": doc["width"], "h": doc["height"]}
    out = (
        body
        if args.fragment
        else PAGE % {"title": f"slide {doc['slide']}", "css": CSS % dims, "body": body}
    )
    if args.out:
        os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
        open(args.out, "w").write(out)
        print(f"{args.out}  ({len(body.splitlines())} elements)")
    else:
        print(out)


if __name__ == "__main__":
    main()
