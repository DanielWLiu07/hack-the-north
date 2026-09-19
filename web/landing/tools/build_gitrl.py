#!/usr/bin/env python3
"""Trace a word out of the Katie Roze font itself (GITRL by default).

Katie Roze is a colour font: its `glyf` outlines are empty placeholders and every
letterform is a watercolour PNG in the SVG table, placed in font units by
<image x y width height>. This traces each letter's ink into closed outlines (with
holes, e.g. R's loop) at that exact placement, so the 3D title keeps the font's own
spacing and baseline, and writes the ink density out as a face texture.

    python3 tools/build_gitrl.py [path/to/KatieRoze.otf]          # the GITRL title
    python3 tools/build_gitrl.py --word ENTER --name enter        # any other word

With no arguments it writes title/gitrl.json and title/<letter>.png next to
index.html, exactly as title.js expects. With --word it writes title/<name>.json
(outlines only); add --maps to also write face textures, into title/<name>/ so
they can never overwrite the title's own letter maps.
"""
from __future__ import annotations

import argparse
import base64
import io
import json
import re
from pathlib import Path

import cv2
import numpy as np
from fontTools.ttLib import TTFont
from PIL import Image

DEFAULT_FONT = Path.home() / "Dev/projects/2026/test-run-3d/fonts/KatieRoze.otf"
OUT = Path(__file__).resolve().parent.parent / "title"

UPSAMPLE = 2          # trace at 2x so curves come out smooth, not stair-stepped
INK = 0.16            # alpha above this is ink (keeps the light washes, e.g. T's bar)
BLUR = 2.0            # px at 2x: calms the brush fringe without losing the stroke
SIMPLIFY = 1.1        # approxPolyDP epsilon, px at 2x
MIN_PART = 0.004      # drop ink islands under 0.4% of the letter's main stroke
MIN_HOLE = 0.003      # fill pinholes under 0.3% of their stroke
FACE_INK = 0.15       # face shade = 1 - FACE_INK * ink. Gentle on purpose: the map is
                      # decoded from sRGB, so 0.45 here read as 0.26 LINEAR and dropped the
                      # whole title into the manga pass's ink band. At 0.15 dense brush
                      # areas land in the halftone band (dots) and washes stay paper-white.


def glyph_art(font: TTFont, ch: str):
    gid = font.getGlyphOrder().index(font.getBestCmap()[ord(ch)])
    for d in font["SVG "].docList:
        doc, first, last = (d.data, d.startGlyphID, d.endGlyphID) if hasattr(d, "data") else d
        if first <= gid <= last:
            break
    else:
        raise SystemExit(f"no SVG glyph for {ch!r}")
    svg = doc if isinstance(doc, str) else doc.decode()
    img = re.search(r'<image x="([-\d.]+)" y="([-\d.]+)" width="([\d.]+)" height="([\d.]+)"'
                    r'[^>]*base64,([A-Za-z0-9+/=]+)', svg)
    x, y, w, h = (float(v) for v in img.groups()[:4])
    png = Image.open(io.BytesIO(base64.b64decode(img.group(5)))).convert("RGBA")
    return png, (x, y, w, h)


def trace(alpha: np.ndarray):
    """alpha 0..1 at art resolution -> [{outer, holes}] in art pixels."""
    a = cv2.resize(alpha, None, fx=UPSAMPLE, fy=UPSAMPLE, interpolation=cv2.INTER_CUBIC)
    a = cv2.GaussianBlur(a, (0, 0), BLUR)
    mask = (a > INK).astype(np.uint8)
    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
    mask = cv2.morphologyEx(cv2.morphologyEx(mask, cv2.MORPH_CLOSE, k), cv2.MORPH_OPEN, k)
    contours, hier = cv2.findContours(mask, cv2.RETR_CCOMP, cv2.CHAIN_APPROX_NONE)
    hier = hier[0]
    areas = [cv2.contourArea(c) for c in contours]
    biggest = max(areas[i] for i in range(len(contours)) if hier[i][3] < 0)
    parts = []
    for i, c in enumerate(contours):
        if hier[i][3] >= 0 or areas[i] < MIN_PART * biggest:
            continue
        holes = [contours[j] for j in range(len(contours))
                 if hier[j][3] == i and areas[j] >= MIN_HOLE * areas[i]]
        simp = lambda cc: cv2.approxPolyDP(cc, SIMPLIFY, True)[:, 0, :] / UPSAMPLE
        parts.append({"outer": simp(c), "holes": [simp(hh) for hh in holes]})
    return parts


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("font", nargs="?", type=Path, default=DEFAULT_FONT)
    ap.add_argument("--word", default="GITRL")
    ap.add_argument("--name", help="output basename (default: the word, lower-cased)")
    ap.add_argument("--maps", action="store_true", help="write face textures for a non-title word")
    args = ap.parse_args()
    text, name = args.word, (args.name or args.word.lower())
    is_title = name == "gitrl"
    maps_dir = OUT if is_title else OUT / name          # the title's maps live flat in title/

    font = TTFont(args.font, lazy=True)
    cmap, hmtx = font.getBestCmap(), font["hmtx"]
    OUT.mkdir(exist_ok=True)
    if args.maps:
        maps_dir.mkdir(exist_ok=True)
    letters, pen = [], 0.0
    for ch in text:
        png, (bx, by, bw, bh) = glyph_art(font, ch)
        alpha = np.asarray(png, dtype=np.float32)[..., 3] / 255.0
        W, H = png.size
        # art px -> font units, y up; SVG y is down with the baseline at 0
        to_font = lambda p: [[round(bx + px * bw / W, 1), round(-(by + py * bh / H), 1)]
                             for px, py in p]
        parts = [{"outer": to_font(p["outer"]), "holes": [to_font(h) for h in p["holes"]]}
                 for p in trace(alpha)]
        # face texture: the letters are light metal, so the brush's ink density only
        # mottles them (dense ink -> darker), which the manga pass turns into dots
        if is_title or args.maps:
            shade = 255 * (1 - FACE_INK * alpha)
            Image.fromarray(np.uint8(shade), "L").save(maps_dir / f"{ch}.png", optimize=True)
        letters.append({"ch": ch, "pen": pen, "advance": hmtx[cmap[ord(ch)]][0],
                        "box": {"x": bx, "y": -(by + bh), "w": bw, "h": bh},  # y = bottom
                        "parts": parts})
        pts = sum(len(p["outer"]) + sum(len(h) for h in p["holes"]) for p in parts)
        print(f"{ch}: {len(parts)} part(s), {sum(len(p['holes']) for p in parts)} hole(s), "
              f"{pts} points, art {W}x{H}")
        pen += hmtx[cmap[ord(ch)]][0]
    meta = {"text": text, "unitsPerEm": font["head"].unitsPerEm,
            "capHeight": font["OS/2"].sCapHeight, "advance": pen,
            "source": "Katie Roze (by Lef) — SVG glyph art, traced", "letters": letters}
    dest = OUT / f"{name}.json"
    dest.write_text(json.dumps(meta, separators=(",", ":")))
    print(f"wrote {dest} ({dest.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()
