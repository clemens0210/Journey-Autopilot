#!/usr/bin/env python3
"""
pptx_extract.py — pull a PowerPoint slide out of a .pptx as structured JSON + media.

A .pptx is a zip of OOXML. Every shape carries its exact position, size, fill, line
and text as data — so instead of eyeballing a screenshot and rebuilding a slide by
hand, we read the real numbers and hand them to the composition.

What it does
  * resolves group transforms (a shape inside a group stores coordinates in the
    group's own child coordinate space — this walks the chain and emits absolute
    page coordinates in px)
  * converts EMU -> px at 96 dpi, so a 13.333in slide lands on a 1280x720 grid
  * turns connectors (cxnSp) into SVG path data, honouring flipH/flipV and the
    curvedConnector3/5 elbow shapes
  * exports the slide's own media (icons, logos) next to the JSON, auto-trimmed

Usage
  python3 pptx_extract.py <file.pptx> <slide-number> [-o outdir] [--scale 1.5]

Output
  <outdir>/slide<N>.json     the shape tree
  <outdir>/media/*.png       every image the slide references (trimmed)

Read the JSON from a build script, or run pptx_to_html.py on it to get a
positioned HTML/CSS scaffold you can drop straight into a HyperFrames scene.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
import zipfile
import xml.etree.ElementTree as ET

NS = {
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    "p": "http://schemas.openxmlformats.org/presentationml/2006/main",
}
R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
SVGNS = "{http://schemas.microsoft.com/office/drawing/2016/SVG/main}"
EMU_PER_INCH = 914400.0
DPI = 96.0


# --------------------------------------------------------------------------- units


def emu(v) -> float:
    return float(v) / EMU_PER_INCH * DPI


# --------------------------------------------------------------------------- colour


def _color(el):
    """Resolve a colour container to '#rrggbb', carrying alpha as '#rrggbb@0.5'."""
    if el is None:
        return None
    srgb = el.find("a:srgbClr", NS)
    node = srgb
    val = None
    if srgb is not None:
        val = "#" + srgb.get("val").lower()
    else:
        sch = el.find("a:schemeClr", NS)
        if sch is not None:
            node = sch
            val = "scheme:" + sch.get("val")
    if val is None:
        return None
    alpha = node.find("a:alpha", NS)
    if alpha is not None:
        val += "@%.3f" % (int(alpha.get("val")) / 100000.0)
    return val


def _fill(spPr):
    if spPr is None:
        return None
    sf = spPr.find("a:solidFill", NS)
    if sf is not None:
        return _color(sf)
    return None


def _line(spPr):
    if spPr is None:
        return None
    ln = spPr.find("a:ln", NS)
    if ln is None or ln.find("a:noFill", NS) is not None:
        return None
    dash = ln.find("a:prstDash", NS)
    head = ln.find("a:headEnd", NS)
    tail = ln.find("a:tailEnd", NS)
    return {
        "w": round(int(ln.get("w", "9525")) / 12700.0, 2),
        "color": _color(ln.find("a:solidFill", NS)),
        "dash": dash.get("val") if dash is not None else None,
        "head": head.get("type") if head is not None else None,
        "tail": tail.get("type") if tail is not None else None,
    }


# --------------------------------------------------------------------------- text


def _text(el):
    tx = el.find("p:txBody", NS)
    if tx is None:
        return None
    paras = []
    for p in tx.findall("a:p", NS):
        runs = []
        for r in p.findall("a:r", NS):
            t = r.find("a:t", NS)
            pr = r.find("a:rPr", NS)
            run = {"t": (t.text or "") if t is not None else ""}
            if pr is not None:
                if pr.get("sz"):
                    # OOXML stores type size in hundredths of a POINT; the rest of
                    # this file works in px at 96 dpi, so convert here or every
                    # label comes out 25% small.
                    run["size"] = round(int(pr.get("sz")) / 100.0 * DPI / 72.0, 2)
                if pr.get("b") == "1":
                    run["bold"] = True
                if pr.get("i") == "1":
                    run["italic"] = True
                if pr.get("spc"):
                    run["tracking"] = int(pr.get("spc")) / 100.0
                c = _color(pr.find("a:solidFill", NS))
                if c:
                    run["color"] = c
                lat = pr.find("a:latin", NS)
                if lat is not None:
                    run["font"] = lat.get("typeface")
            runs.append(run)
        if not runs:
            continue
        pPr = p.find("a:pPr", NS)
        paras.append(
            {
                "align": (pPr.get("algn") if pPr is not None else None) or "l",
                "runs": runs,
                "text": "".join(r["t"] for r in runs),
            }
        )
    if not any(p["text"].strip() for p in paras):
        return None
    body = tx.find("a:bodyPr", NS)
    return {"paras": paras, "anchor": body.get("anchor") if body is not None else None}


# --------------------------------------------------------------------------- transforms


class Frame:
    """Maps a group's child coordinate space onto absolute page px."""

    __slots__ = ("ox", "oy", "sx", "sy", "cx", "cy")

    def __init__(self, ox=0.0, oy=0.0, sx=1.0, sy=1.0, cx=0.0, cy=0.0):
        self.ox, self.oy, self.sx, self.sy, self.cx, self.cy = ox, oy, sx, sy, cx, cy

    def point(self, x, y):
        return self.ox + (x - self.cx) * self.sx, self.oy + (y - self.cy) * self.sy

    def size(self, w, h):
        return w * self.sx, h * self.sy

    def descend(self, xfrm) -> "Frame":
        off, ext = xfrm.find("a:off", NS), xfrm.find("a:ext", NS)
        ax, ay = self.point(emu(off.get("x")), emu(off.get("y")))
        aw, ah = self.size(emu(ext.get("cx")), emu(ext.get("cy")))
        cho, che = xfrm.find("a:chOff", NS), xfrm.find("a:chExt", NS)
        if cho is None or che is None:
            return Frame(ax, ay, self.sx, self.sy)
        cw, ch = emu(che.get("cx")), emu(che.get("cy"))
        return Frame(
            ax,
            ay,
            aw / cw if cw else 1.0,
            ah / ch if ch else 1.0,
            emu(cho.get("x")),
            emu(cho.get("y")),
        )


def _xfrm(el):
    xf = el.find("p:spPr/a:xfrm", NS)
    return xf if xf is not None else el.find("p:grpSpPr/a:xfrm", NS)


# --------------------------------------------------------------------------- connectors


def connector_path(rec):
    """SVG path 'd' for a connector, in absolute page px."""
    x, y, w, h = rec["x"], rec["y"], rec["w"], rec["h"]
    # flips decide which diagonal of the bounding box the line runs along
    x1, x2 = (x + w, x) if rec.get("flipH") else (x, x + w)
    y1, y2 = (y + h, y) if rec.get("flipV") else (y, y + h)
    kind = rec.get("geom") or "line"
    if kind == "line" or kind == "straightConnector1":
        return f"M {x1:.1f} {y1:.1f} L {x2:.1f} {y2:.1f}"
    if kind in ("curvedConnector3", "bentConnector3"):
        # leaves horizontally, arrives horizontally, elbow at the midpoint
        mx = (x1 + x2) / 2
        if kind == "bentConnector3":
            return f"M {x1:.1f} {y1:.1f} L {mx:.1f} {y1:.1f} L {mx:.1f} {y2:.1f} L {x2:.1f} {y2:.1f}"
        return f"M {x1:.1f} {y1:.1f} C {mx:.1f} {y1:.1f} {mx:.1f} {y2:.1f} {x2:.1f} {y2:.1f}"
    if kind in ("curvedConnector5", "bentConnector5"):
        # out horizontally, across vertically, back in horizontally (two elbows)
        mx = (x1 + x2) / 2
        my = (y1 + y2) / 2
        return (
            f"M {x1:.1f} {y1:.1f} C {mx:.1f} {y1:.1f} {x1:.1f} {my:.1f} {mx:.1f} {my:.1f} "
            f"C {x2:.1f} {my:.1f} {mx:.1f} {y2:.1f} {x2:.1f} {y2:.1f}"
        )
    if kind in ("curvedConnector2", "bentConnector2"):
        return f"M {x1:.1f} {y1:.1f} Q {x2:.1f} {y1:.1f} {x2:.1f} {y2:.1f}"
    return f"M {x1:.1f} {y1:.1f} L {x2:.1f} {y2:.1f}"


# --------------------------------------------------------------------------- walk


def walk(node, frame, rels, out, path=""):
    for child in node:
        tag = child.tag.split("}")[1]
        if tag not in ("sp", "pic", "grpSp", "cxnSp", "graphicFrame"):
            continue
        nv = child.find(".//p:cNvPr", NS)
        name = nv.get("name") if nv is not None else "?"
        xf = _xfrm(child)

        if tag == "grpSp":
            if xf is not None:
                walk(child, frame.descend(xf), rels, out, f"{path}/{name}")
            continue

        rec = {"kind": tag, "name": name, "group": path}
        if xf is not None:
            off, ext = xf.find("a:off", NS), xf.find("a:ext", NS)
            x, y = frame.point(emu(off.get("x")), emu(off.get("y")))
            w, h = frame.size(emu(ext.get("cx")), emu(ext.get("cy")))
            rec.update(
                x=round(x, 1), y=round(y, 1), w=round(w, 1), h=round(h, 1)
            )
            if xf.get("rot"):
                rec["rot"] = round(int(xf.get("rot")) / 60000.0, 2)
            if xf.get("flipH") == "1":
                rec["flipH"] = True
            if xf.get("flipV") == "1":
                rec["flipV"] = True

        spPr = child.find("p:spPr", NS)
        prst = spPr.find("a:prstGeom", NS) if spPr is not None else None
        if prst is not None:
            rec["geom"] = prst.get("prst")
            adj = [g.get("fmla") for g in prst.findall(".//a:gd", NS) if g.get("fmla")]
            if adj:
                rec["adj"] = adj
        fill = _fill(spPr)
        if fill:
            rec["fill"] = fill
        line = _line(spPr)
        if line:
            rec["line"] = line

        if tag == "pic":
            blip = child.find(".//a:blip", NS)
            if blip is not None:
                rec["image"] = os.path.basename(rels.get(blip.get(R + "embed"), ""))
                svg = child.find(f".//{SVGNS}svgBlip")
                if svg is not None:
                    rec["svg"] = os.path.basename(rels.get(svg.get(R + "embed"), ""))
                # Office icons ship as an SVG with a raster fallback; when only the
                # vector side is wired up, the SVG *is* the image.
                if not rec["image"] and rec.get("svg"):
                    rec["image"] = rec["svg"]
            src = child.find("p:blipFill/a:srcRect", NS)
            if src is not None and src.attrib:
                rec["crop"] = {
                    k: int(v) / 100000.0 for k, v in src.attrib.items()
                }

        if "x" not in rec:
            # placeholder inheriting geometry from the layout — keep the text, drop it
            # from the layout pass; callers decide whether they need it
            rec["placeholder"] = True

        txt = _text(child)
        if txt:
            rec["text"] = txt

        if tag == "cxnSp" and "x" in rec:
            rec["d"] = connector_path(rec)

        out.append(rec)
    return out


# --------------------------------------------------------------------------- media


def export_media(zf, rels, shapes, outdir, trim=True):
    wanted = {s[k] for s in shapes for k in ("image", "svg") if s.get(k)}
    media_dir = os.path.join(outdir, "media")
    os.makedirs(media_dir, exist_ok=True)
    written = []
    for name in sorted(wanted):
        src = f"ppt/media/{name}"
        if src not in zf.namelist():
            continue
        dest = os.path.join(media_dir, name)
        with zf.open(src) as fh, open(dest, "wb") as out:
            shutil.copyfileobj(fh, out)
        written.append(name)
    if trim:
        _trim_pngs(media_dir, written)
    return written


def _trim_pngs(media_dir, names):
    """Crop fully-transparent margins so a logo's box == the logo."""
    try:
        from PIL import Image
    except ImportError:
        return
    for name in names:
        if not name.lower().endswith(".png"):
            continue
        path = os.path.join(media_dir, name)
        im = Image.open(path)
        if im.mode != "RGBA":
            continue
        box = im.getchannel("A").getbbox()
        if box and box != (0, 0, im.width, im.height):
            im.crop(box).save(path)


# --------------------------------------------------------------------------- main


def extract(pptx_path, slide_no, outdir, scale=1.0, trim=True):
    with zipfile.ZipFile(pptx_path) as zf:
        pres = zf.read("ppt/presentation.xml").decode("utf-8")
        m = re.search(r'<p:sldSz cx="(\d+)" cy="(\d+)"', pres)
        page_w, page_h = emu(m.group(1)), emu(m.group(2))

        slide_xml = f"ppt/slides/slide{slide_no}.xml"
        if slide_xml not in zf.namelist():
            sys.exit(f"no such slide: {slide_xml}")
        rels_xml = f"ppt/slides/_rels/slide{slide_no}.xml.rels"
        rels = dict(
            re.findall(
                r'Id="(rId\d+)"[^>]*Target="([^"]+)"', zf.read(rels_xml).decode("utf-8")
            )
        )
        tree = ET.fromstring(zf.read(slide_xml))
        sp_tree = tree.find(".//p:cSld/p:spTree", NS)
        shapes = walk(sp_tree, Frame(), rels, [])

        os.makedirs(outdir, exist_ok=True)
        media = export_media(zf, rels, shapes, outdir, trim=trim)

    if scale != 1.0:
        for s in shapes:
            for k in ("x", "y", "w", "h"):
                if k in s:
                    s[k] = round(s[k] * scale, 1)
            if "d" in s:
                s["d"] = connector_path(s)
            if "line" in s and s["line"].get("w"):
                s["line"]["w"] = round(s["line"]["w"] * scale, 2)
            if "text" in s:
                for p in s["text"]["paras"]:
                    for r in p["runs"]:
                        if "size" in r:
                            r["size"] = round(r["size"] * scale, 2)
        page_w, page_h = page_w * scale, page_h * scale

    doc = {
        "source": os.path.basename(pptx_path),
        "slide": slide_no,
        "scale": scale,
        "width": round(page_w, 1),
        "height": round(page_h, 1),
        "media": media,
        "shapes": shapes,
    }
    dest = os.path.join(outdir, f"slide{slide_no}.json")
    with open(dest, "w") as fh:
        json.dump(doc, fh, indent=1, ensure_ascii=False)
    return dest, doc


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("pptx")
    ap.add_argument("slide", type=int)
    ap.add_argument("-o", "--out", default="out")
    ap.add_argument("--scale", type=float, default=1.0, help="e.g. 1.5 maps a 1280x720 slide onto a 1920x1080 canvas")
    ap.add_argument("--no-trim", action="store_true")
    args = ap.parse_args()

    dest, doc = extract(args.pptx, args.slide, args.out, args.scale, trim=not args.no_trim)
    kinds = {}
    for s in doc["shapes"]:
        kinds[s["kind"]] = kinds.get(s["kind"], 0) + 1
    print(f"{dest}  {doc['width']:.0f}x{doc['height']:.0f}  {len(doc['shapes'])} shapes {kinds}")
    print(f"  media: {len(doc['media'])} files -> {os.path.join(args.out, 'media')}")


if __name__ == "__main__":
    main()
