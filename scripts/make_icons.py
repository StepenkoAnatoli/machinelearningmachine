#!/usr/bin/env python3
"""
Rasterise ``static/icons/icon.svg`` into the app's PNG/ICO icons.

Why generate instead of exporting once by hand: the icons have to be binary PNGs
(browsers and OS install dialogs will not take an SVG for an app icon), and a
hand-exported PNG is a file nobody can regenerate, review, or check. So the SVG
is the source of truth, this script turns it into pixels with **nothing but the
standard library** (no Pillow, no cairosvg, no new dependency), and
``tests/test_pwa.py`` fails if the committed images stop matching a fresh render.

Rendering is done by scanline with analytic coverage:

* the rounded square is exact in both axes (per row, the horizontal span of a
  rounded rectangle is a closed form, and a pixel's coverage is its overlap with
  that span),
* the bolt is filled even-odd with fractional endpoints on the same scanlines.

That gives clean anti-aliased edges at every size, deterministically: same SVG,
same pixels, every time and on every machine. The compressed bytes may differ
between zlib builds, which is why the test compares *pixels* - see its docstring.

Usage:
    python3 scripts/make_icons.py            # regenerate the committed icons
    python3 scripts/make_icons.py --out DIR  # render somewhere else
"""

from __future__ import annotations

import argparse
import math
import struct
import sys
import xml.etree.ElementTree as ET
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "machinelearningmachine" / "server" / "static"
ICONS_DIR = STATIC / "icons"
SVG_PATH = ICONS_DIR / "icon.svg"

#: The sizes the manifest and the favicon promise. ``favicon`` is written as an
#: ICO container holding this one PNG (Vista+ ICO is a PNG with a small header).
ICON_SIZES = {"icon-192.png": 192, "icon-512.png": 512, "apple-touch-icon.png": 180}
FAVICON_SIZE = 32

_NS = "{http://www.w3.org/2000/svg}"


# --------------------------------------------------------------------------- #
# reading the source
# --------------------------------------------------------------------------- #
def _tag(element: ET.Element) -> str:
    return element.tag.replace(_NS, "")


def _colour(value: str) -> tuple[int, int, int]:
    """``#rrggbb`` (or ``#rgb``) to an (r, g, b) tuple."""
    text = value.strip().lstrip("#")
    if len(text) == 3:
        text = "".join(char * 2 for char in text)
    if len(text) != 6:
        raise ValueError(f"unsupported colour: {value!r}")
    return tuple(int(text[index : index + 2], 16) for index in (0, 2, 4))  # type: ignore[return-value]


def parse_icon(path: Path = SVG_PATH) -> dict:
    """
    Read the geometry and colours out of the SVG.

    The generator owns *no* colours or coordinates of its own: everything it
    draws comes from this file, so the SVG stays the one place a designer edits.
    """
    # The SVG is this repository's own committed asset, never user input; defusedxml
    # would be a new dependency to parse a file we wrote ourselves.
    root = ET.fromstring(path.read_text(encoding="utf-8"))  # noqa: S314
    view_box = [float(part) for part in root.attrib["viewBox"].split()]
    size = int(view_box[2])
    if view_box[0] != 0 or view_box[1] != 0 or view_box[2] != view_box[3]:
        raise ValueError("the icon generator expects a square viewBox anchored at 0 0")

    stops: list[tuple[float, tuple[int, int, int]]] = []
    gradient = (0.0, 0.0, 1.0, 1.0)
    for element in root.iter():
        if _tag(element) == "linearGradient":
            gradient = (
                float(element.get("x1", "0")),
                float(element.get("y1", "0")),
                float(element.get("x2", "1")),
                float(element.get("y2", "0")),
            )
        elif _tag(element) == "stop":
            stops.append((float(element.get("offset", "0")), _colour(element.get("stop-color", "#000000"))))
    if len(stops) < 2:
        raise ValueError("the icon needs at least two gradient stops")

    radius = 0.0
    for element in root.iter():
        if _tag(element) == "rect":
            radius = float(element.get("rx", "0"))
            break

    bolt: list[tuple[float, float]] = []
    bolt_colour = (255, 255, 255)
    for element in root.iter():
        if _tag(element) == "polygon":
            bolt = [
                (float(pair.split(",")[0]), float(pair.split(",")[1]))
                for pair in element.attrib["points"].replace("\n", " ").split()
            ]
            bolt_colour = _colour(element.get("fill", "#ffffff"))
            break

    return {
        "size": size,
        "radius": radius,
        "stops": stops,
        "gradient": gradient,
        "bolt": bolt,
        "bolt_colour": bolt_colour,
    }


# --------------------------------------------------------------------------- #
# geometry: analytic coverage per scanline
# --------------------------------------------------------------------------- #
def _rounded_rect_span(vertical: float, size: int, radius: float) -> tuple[float, float]:
    """The horizontal extent of the rounded square at the given y."""
    if radius <= 0:
        return 0.0, float(size)
    inset = 0.0
    if vertical < radius:
        inset = radius - math.sqrt(max(0.0, radius * radius - (radius - vertical) ** 2))
    elif vertical > size - radius:
        inset = radius - math.sqrt(max(0.0, radius * radius - (vertical - (size - radius)) ** 2))
    return inset, size - inset


def _polygon_spans(points: list[tuple[float, float]], vertical: float) -> list[tuple[float, float]]:
    """Even-odd scanline crossings of a polygon at the given y, paired into spans."""
    crossings: list[float] = []
    count = len(points)
    for index in range(count):
        x1, y1 = points[index]
        x2, y2 = points[(index + 1) % count]
        if y1 == y2:
            continue
        low, high = (y1, y2) if y1 < y2 else (y2, y1)
        if low <= vertical < high:
            ratio = (vertical - y1) / (y2 - y1)
            crossings.append(x1 + ratio * (x2 - x1))
    crossings.sort()
    return [(crossings[i], crossings[i + 1]) for i in range(0, len(crossings) - 1, 2)]


def _spread(spans: list[tuple[float, float]], size: int) -> list[float]:
    """Per-pixel coverage for one scanline, from fractional span endpoints."""
    coverage = [0.0] * size
    for start, end in spans:
        first = max(0, math.floor(start))
        last = min(size, math.ceil(end))
        for pixel in range(first, last):
            overlap = min(pixel + 1.0, end) - max(float(pixel), start)
            if overlap > 0:
                coverage[pixel] += overlap
    return coverage


def _gradient_rgb(stops: list[tuple[float, tuple[int, int, int]]], position: float) -> tuple[float, float, float]:
    """Piecewise-linear colour lookup, clamped at both ends."""
    if position <= stops[0][0]:
        return tuple(float(channel) for channel in stops[0][1])  # type: ignore[return-value]
    if position >= stops[-1][0]:
        return tuple(float(channel) for channel in stops[-1][1])  # type: ignore[return-value]
    for index in range(len(stops) - 1):
        offset_a, colour_a = stops[index]
        offset_b, colour_b = stops[index + 1]
        if offset_a <= position <= offset_b:
            span = offset_b - offset_a
            ratio = 0.0 if span == 0 else (position - offset_a) / span
            return tuple(
                colour_a[channel] + (colour_b[channel] - colour_a[channel]) * ratio for channel in range(3)
            )  # type: ignore[return-value]
    return tuple(float(channel) for channel in stops[-1][1])  # type: ignore[return-value]


def _byte(value: float) -> int:
    return max(0, min(255, int(value + 0.5)))


# --------------------------------------------------------------------------- #
# rendering
# --------------------------------------------------------------------------- #
def render(size: int, icon: dict | None = None) -> bytes:
    """
    Render one square icon and return raw filtered scanlines (PNG's row bytes).

    Straight (non-premultiplied) RGBA, filter 0 on every row: simple, and the
    decoder in the test reads all five filter types anyway.
    """
    icon = icon or parse_icon()
    scale = size / icon["size"]
    radius = icon["radius"] * scale
    stops = icon["stops"]
    bolt = [(x * scale, y * scale) for x, y in icon["bolt"]]
    bolt_r, bolt_g, bolt_b = (float(channel) for channel in icon["bolt_colour"])

    gx0, gy0, gx1, gy1 = icon["gradient"]
    origin_x, origin_y = gx0 * size, gy0 * size
    axis_x, axis_y = gx1 * size - origin_x, gy1 * size - origin_y
    axis_length_squared = axis_x * axis_x + axis_y * axis_y

    raw = bytearray()
    for row in range(size):
        vertical = row + 0.5
        left, right = _rounded_rect_span(vertical, size, radius)
        rect_coverage = _spread([(left, right)], size)
        bolt_coverage = _spread(_polygon_spans(bolt, vertical), size) if bolt else [0.0] * size

        line = bytearray(b"\x00")  # PNG filter: none
        for column in range(size):
            alpha = rect_coverage[column]
            if alpha <= 0:
                line += b"\x00\x00\x00\x00"
                continue
            progress = (
                (column + 0.5 - origin_x) * axis_x + (vertical - origin_y) * axis_y
            ) / axis_length_squared
            red, green, blue = _gradient_rgb(stops, progress)
            bolt_alpha = bolt_coverage[column]
            if bolt_alpha > 0:
                red += (bolt_r - red) * bolt_alpha
                green += (bolt_g - green) * bolt_alpha
                blue += (bolt_b - blue) * bolt_alpha
            # Alpha is a 0..255 channel while coverage is 0..1 - scaling it here (not
            # only in the colour channels) is what keeps the icon opaque.
            line += bytes((_byte(red), _byte(green), _byte(blue), _byte(alpha * 255)))
        raw += line
    return bytes(raw)


def png_bytes(size: int) -> bytes:
    """A complete PNG file for one icon size."""
    return _png(size, size, render(size))


def _chunk(tag: bytes, payload: bytes) -> bytes:
    return struct.pack(">I", len(payload)) + tag + payload + struct.pack(">I", zlib.crc32(tag + payload) & 0xFFFFFFFF)


def _png(width: int, height: int, raw: bytes) -> bytes:
    """8-bit RGBA, no interlacing. ``raw`` is the filtered scanline stream."""
    return (
        b"\x89PNG\r\n\x1a\n"
        + _chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
        + _chunk(b"IDAT", zlib.compress(raw, 9))
        + _chunk(b"IEND", b"")
    )


def ico_bytes(size: int = FAVICON_SIZE) -> bytes:
    """
    A one-image ICO holding a PNG - what every browser since Vista reads.

    The directory entry stores 0 for a 256px image, which is why ``size`` or 256
    is written back out by the reader in the tests.
    """
    image = png_bytes(size)
    header = struct.pack("<HHH", 0, 1, 1)
    entry = struct.pack(
        "<BBBBHHII",
        size % 256,
        size % 256,
        0,
        0,
        1,
        32,
        len(image),
        len(header) + 16,
    )
    return header + entry + image


# --------------------------------------------------------------------------- #
# entry points
# --------------------------------------------------------------------------- #
def generate(out_dir: Path, favicon_dir: Path | None = None) -> list[Path]:
    """
    Write every icon into ``out_dir`` (and the favicon into ``favicon_dir``).

    ``generate()`` only ever writes where it is told: the drift test calls it with
    a temp directory, and a generator that quietly wrote into the repository would
    make that test "fix" the tree it is supposed to be checking.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    icon = parse_icon()
    written: list[Path] = []
    for name, size in ICON_SIZES.items():
        destination = out_dir / name
        destination.write_bytes(_png(size, size, render(size, icon)))
        written.append(destination)
    ico_dir = favicon_dir or out_dir
    ico_dir.mkdir(parents=True, exist_ok=True)
    favicon = ico_dir / "favicon.ico"
    favicon.write_bytes(ico_bytes())
    written.append(favicon)
    return written


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    parser.add_argument("--out", type=Path, default=None, help="write the icons here instead of into static/")
    args = parser.parse_args(argv)

    if args.out is None:
        written = generate(ICONS_DIR, favicon_dir=STATIC)
    else:
        written = generate(args.out)
    for path in written:
        print(f"wrote {path.relative_to(ROOT) if path.is_relative_to(ROOT) else path} ({path.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
