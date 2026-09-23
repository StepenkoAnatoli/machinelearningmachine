"""
The dashboard's installable-app assets: icons, manifest, service worker, routes.

Why this file exists: an installable app fails *quietly*. A missing icon, a
manifest with the wrong media type, a service worker the browser silently
refuses (wrong scope, wrong content type, or blocked behind the auth check) all
produce a dashboard that works perfectly in a browser tab and simply never
becomes an app - with no error anywhere a developer would look. So the promises
the manifest makes are checked against the real files, and the routes are
checked the way a browser asks for them.

The icon half of this file is also what makes committing binary PNGs defensible:
``static/icons/*.png`` are generated, never hand-edited, and the generator is
held to the committed bytes.
"""

from __future__ import annotations

import struct
import zlib
from pathlib import Path

import pytest

from scripts import make_icons

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "machinelearningmachine" / "server" / "static"
ICONS = STATIC / "icons"

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


# --------------------------------------------------------------------------- #
# helpers: enough PNG/ICO to read what we shipped, with no new dependencies
# --------------------------------------------------------------------------- #
def _png_header(data: bytes) -> tuple[int, int, int, int]:
    """(width, height, bit depth, colour type) from the IHDR chunk."""
    assert data[:8] == PNG_MAGIC, "not a PNG file"
    length, tag = struct.unpack(">I4s", data[8:16])
    assert tag == b"IHDR", "a PNG must start with IHDR"
    assert length == 13
    width, height, depth, colour, _compression, _filter, _interlace = struct.unpack(">IIBBBBB", data[16:29])
    return width, height, depth, colour


def _png_pixels(data: bytes) -> tuple[int, int, bytes]:
    """
    Decode a PNG to (width, height, RGBA bytes) with the standard library.

    All five filter types are implemented even though our own writer only emits
    filter 0: the point of this decoder is to compare *pixels*, so an image that
    was re-saved by an optimiser (which re-chooses filters) must still compare as
    identical rather than as drift.
    """
    width, height, depth, colour = _png_header(data)
    assert (depth, colour) == (8, 6), f"expected 8-bit RGBA, got depth={depth} colour={colour}"

    # Collect IDAT payloads in file order.
    offset = 8
    idat = bytearray()
    while offset < len(data):
        length, tag = struct.unpack(">I4s", data[offset : offset + 8])
        payload = data[offset + 8 : offset + 8 + length]
        if tag == b"IDAT":
            idat += payload
        offset += 12 + length
        if tag == b"IEND":
            break
    raw = zlib.decompress(bytes(idat))

    stride = width * 4
    out = bytearray()
    previous = bytearray(stride)
    position = 0
    for _row in range(height):
        filter_type = raw[position]
        line = bytearray(raw[position + 1 : position + 1 + stride])
        position += 1 + stride
        if filter_type == 1:  # Sub
            for i in range(4, stride):
                line[i] = (line[i] + line[i - 4]) & 0xFF
        elif filter_type == 2:  # Up
            for i in range(stride):
                line[i] = (line[i] + previous[i]) & 0xFF
        elif filter_type == 3:  # Average
            for i in range(stride):
                left = line[i - 4] if i >= 4 else 0
                line[i] = (line[i] + ((left + previous[i]) >> 1)) & 0xFF
        elif filter_type == 4:  # Paeth
            for i in range(stride):
                left = line[i - 4] if i >= 4 else 0
                up = previous[i]
                up_left = previous[i - 4] if i >= 4 else 0
                estimate = left + up - up_left
                distances = (abs(estimate - left), abs(estimate - up), abs(estimate - up_left))
                predictor = (left, up, up_left)[distances.index(min(distances))]
                line[i] = (line[i] + predictor) & 0xFF
        elif filter_type != 0:
            raise AssertionError(f"unknown PNG filter type {filter_type}")
        out += line
        previous = line
    return width, height, bytes(out)


def _ico_entries(data: bytes) -> list[tuple[int, int, bytes]]:
    """(width, height, embedded PNG bytes) for each image in an ICO file."""
    reserved, kind, count = struct.unpack("<HHH", data[:6])
    assert (reserved, kind) == (0, 1), "not an ICO file"
    entries = []
    for index in range(count):
        entry = data[6 + 16 * index : 22 + 16 * index]
        width, height, _colours, _reserved, _planes, _bpp, size, offset = struct.unpack("<BBBBHHII", entry)
        payload = data[offset : offset + size]
        assert payload[:8] == PNG_MAGIC, "the icon inside the ICO should be a PNG (Vista+ ICO)"
        entries.append((width or 256, height or 256, payload))
    return entries


ICON_FILES = {
    "icon-192.png": 192,
    "icon-512.png": 512,
    "apple-touch-icon.png": 180,
}


# --------------------------------------------------------------------------- #
# the committed images
# --------------------------------------------------------------------------- #
def test_the_icon_files_are_committed_and_are_real_images():
    missing = [name for name in ICON_FILES if not (ICONS / name).is_file()]
    assert not missing, f"icons missing from static/icons/: {', '.join(missing)}"
    for name in ICON_FILES:
        assert (ICONS / name).read_bytes()[:8] == PNG_MAGIC, f"{name} is not a PNG"
    assert (STATIC / "favicon.ico").is_file(), "/favicon.ico has a PUBLIC_PATHS entry and must serve a real file"


def test_the_pngs_are_the_sizes_the_manifest_will_promise():
    for name, expected in ICON_FILES.items():
        width, height, _depth, _colour = _png_header((ICONS / name).read_bytes())
        assert (width, height) == (expected, expected), f"{name} is {width}x{height}, expected {expected}x{expected}"


def test_the_favicon_holds_the_same_32px_icon():
    entries = _ico_entries((STATIC / "favicon.ico").read_bytes())
    assert entries, "the ICO must contain at least one image"
    width, height, png = entries[0]
    assert (width, height) == (32, 32)
    # The embedded image must be a real render, not an empty placeholder.
    _w, _h, pixels = _png_pixels(png)
    assert len(set(pixels[3::4])) > 1, "the icon's alpha channel is uniform - it was never drawn"


def test_the_icons_are_transparent_outside_the_rounded_square():
    """A square icon on a light background looks like a grey box; the corners must be clear."""
    _w, _h, pixels = _png_pixels((ICONS / "icon-192.png").read_bytes())

    def alpha_at(x: int, y: int) -> int:
        return pixels[(y * 192 + x) * 4 + 3]

    corners = [alpha_at(0, 0), alpha_at(191, 0), alpha_at(0, 191), alpha_at(191, 191)]
    assert corners == [0, 0, 0, 0], f"corners should be fully transparent, got {corners}"
    # ... and the middle must be fully *opaque*. Coverage is computed as 0..1 and
    # written to an 0..255 channel; getting that scale wrong renders an icon that
    # is technically drawn and practically invisible, which is how this bug shipped
    # into the first version of these tests.
    assert alpha_at(96, 96) == 255, f"the icon's body should be opaque, got alpha={alpha_at(96, 96)}"


def test_the_icons_render_the_brand_gradient_and_a_white_bolt():
    """
    Pixel-level proof that the SVG's colours survive the trip to PNG.

    Deliberately structural (quadrant means, the bolt's centroid, an opacity
    count) rather than "the pixel at (450, 60) is #ec4899": a coordinate-guessed
    assertion breaks when the drawing is nudged, and would say nothing about
    whether the *rendering* is right.
    """
    width, height, pixels = _png_pixels((ICONS / "icon-512.png").read_bytes())

    def rgb_at(x: int, y: int) -> tuple[int, int, int]:
        offset = (y * width + x) * 4
        return tuple(pixels[offset : offset + 3])  # type: ignore[return-value]

    white = [
        (x, y)
        for y in range(height)
        for x in range(width)
        if rgb_at(x, y) == (255, 255, 255) and pixels[(y * width + x) * 4 + 3] == 255
    ]
    area = width * height
    assert 0.02 * area < len(white) < 0.15 * area, f"the bolt should cover a few percent of the icon, got {len(white)}px"
    centroid_x = sum(x for x, _y in white) / len(white)
    centroid_y = sum(y for _x, y in white) / len(white)
    assert abs(centroid_x - width / 2) < width * 0.1 and abs(centroid_y - height / 2) < height * 0.1, (
        f"the bolt should sit in the middle, its centroid is ({centroid_x:.0f}, {centroid_y:.0f})"
    )

    def quadrant_mean(corners: list[tuple[int, int]]) -> tuple[float, float, float]:
        samples = [rgb_at(x, y) for x, y in corners if pixels[(y * width + x) * 4 + 3] == 255]
        assert samples, "the quadrant should contain opaque pixels"
        return tuple(sum(sample[channel] for sample in samples) / len(samples) for channel in range(3))  # type: ignore[return-value]

    step = 4
    bottom_left = quadrant_mean(
        [(x, y) for x in range(0, width // 2, step) for y in range(height // 2, height, step)]
    )
    top_right = quadrant_mean(
        [(x, y) for x in range(width // 2, width, step) for y in range(0, height // 2, step)]
    )
    # The SVG's gradient runs indigo (bottom-left) -> purple -> pink (top-right).
    assert bottom_left[2] > bottom_left[0], f"bottom-left should lean blue/violet, got {bottom_left}"
    assert top_right[0] > top_right[2], f"top-right should lean pink, got {top_right}"
    drift = sum((bottom_left[channel] - top_right[channel]) ** 2 for channel in range(3)) ** 0.5
    assert drift > 40, f"the two corners are too similar to be a gradient: {bottom_left} vs {top_right}"


# --------------------------------------------------------------------------- #
# the source they come from
# --------------------------------------------------------------------------- #
def test_the_svg_source_declares_everything_the_generator_reads():
    """The generator reads these attributes; deleting one must not silently change the icons."""
    text = (ICONS / "icon.svg").read_text(encoding="utf-8")
    parsed = make_icons.parse_icon(ICONS / "icon.svg")
    assert parsed["size"] > 0 and 'viewBox=' in text
    assert parsed["radius"] > 0 and "rx=" in text
    assert len(parsed["stops"]) >= 2 and "linearGradient" in text
    assert len(parsed["bolt"]) >= 6 and "<polygon" in text
    # The brand gradient is the dashboard's own header mark (#6366f1 -> #a855f7 -> #ec4899).
    assert [stop[1] for stop in parsed["stops"]][:3] == [(99, 102, 241), (168, 85, 247), (236, 72, 153)]


def test_regenerating_the_icons_reproduces_the_committed_pixels(tmp_path):
    """
    The drift lock that makes binary assets reviewable.

    Compared as *pixels*, not as bytes: the compressed stream depends on the zlib
    build (CI runs several Python versions on several images), and an icon that
    differs only in deflate output is not drift. Pixel equality is the promise
    that matters - the committed image is exactly what the SVG source renders.
    """
    written = make_icons.generate(tmp_path)
    assert sorted(p.name for p in written if p.suffix == ".png") == sorted(ICON_FILES), (
        "the generator must write exactly the icons this test checks"
    )
    for name in ICON_FILES:
        committed = _png_pixels((ICONS / name).read_bytes())
        regenerated = _png_pixels((tmp_path / name).read_bytes())
        assert committed == regenerated, f"{name} differs from a fresh render of icon.svg - regenerate and commit it"

    committed_ico = _ico_entries((STATIC / "favicon.ico").read_bytes())
    fresh_ico = _ico_entries((tmp_path / "favicon.ico").read_bytes())
    assert [entry[:2] for entry in committed_ico] == [entry[:2] for entry in fresh_ico]
    assert _png_pixels(committed_ico[0][2]) == _png_pixels(fresh_ico[0][2]), "favicon.ico is stale"


def test_the_generator_is_hermetic(tmp_path, monkeypatch):
    """
    Generating the icons must not depend on - or touch - the working tree.

    A generator that writes into ``static/`` when called from a test would make
    a failing drift test quietly "fix" the repository it is checking.
    """
    before = {path.name: path.read_bytes() for path in ICONS.iterdir() if path.is_file()}
    before["favicon.ico"] = (STATIC / "favicon.ico").read_bytes()

    make_icons.generate(tmp_path)

    after = {path.name: path.read_bytes() for path in ICONS.iterdir() if path.is_file()}
    after["favicon.ico"] = (STATIC / "favicon.ico").read_bytes()
    assert before == after, "generate() must only write into the directory it is given"


@pytest.mark.parametrize("size", [32, 180, 192, 512])
def test_rendering_is_deterministic(size):
    """Same input, same pixels - otherwise the drift lock above is a coin toss."""
    assert make_icons.render(size) == make_icons.render(size)


# --------------------------------------------------------------------------- #
# the manifest a browser reads before it will offer to install the app
# --------------------------------------------------------------------------- #
from fastapi.testclient import TestClient  # noqa: E402  - see the section comment above

from machinelearningmachine.server.app import PUBLIC_PATHS, create_app  # noqa: E402
from machinelearningmachine.server.config import ServerConfig  # noqa: E402

TOKEN = "a-sufficiently-long-random-token-value"
MANIFEST_PATH = STATIC / "manifest.webmanifest"


def _manifest() -> dict:
    import json

    assert MANIFEST_PATH.is_file(), "the dashboard must ship a web app manifest to be installable"
    return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))


def test_the_manifest_declares_an_installable_app():
    manifest = _manifest()
    assert manifest["name"], "the install dialog needs a name"
    assert manifest["short_name"], "and a short one for the home screen"
    assert manifest["start_url"] == "/", "launching the app must open the dashboard, not a subpath"
    assert manifest["display"] == "standalone", "an installed app should not open with browser chrome"
    assert manifest["theme_color"].startswith("#") and manifest["background_color"].startswith("#")
    assert manifest["description"], "the install prompt shows this"


def test_the_manifest_icons_exist_at_the_sizes_it_promises():
    """The classic silent failure: a manifest listing sizes nothing actually has."""
    sizes: list[tuple[int, int]] = []
    for icon in _manifest()["icons"]:
        path = STATIC / icon["src"].replace("/static/", "")
        assert path.is_file(), f"manifest references a missing icon: {icon['src']}"
        width, height, _depth, _colour = _png_header(path.read_bytes())
        declared = icon["sizes"].lower().replace(" ", "")
        assert declared == f"{width}x{height}", f"{icon['src']} is {width}x{height} but the manifest says {declared}"
        assert icon["type"] == "image/png"
        sizes.append((width, height))
    assert (192, 192) in sizes and (512, 512) in sizes, "Chrome/Edge want at least 192 and 512"


def test_the_manifest_is_valid_json_with_no_comments():
    """A manifest is read by the browser's JSON parser - a trailing comma makes it inert."""
    import json

    raw = MANIFEST_PATH.read_text(encoding="utf-8")
    json.loads(raw)
    assert "/*" not in raw and "//" not in raw, "keep the manifest comment-free: browsers parse it as strict JSON"


# --------------------------------------------------------------------------- #
# the routes, asked for the way a browser asks
# --------------------------------------------------------------------------- #
def test_the_manifest_is_served_with_its_own_media_type():
    client = TestClient(create_app(ServerConfig(host="127.0.0.1")))
    response = client.get("/manifest.webmanifest")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/manifest+json"), (
        "a manifest served as text/plain is ignored by the browser"
    )
    assert response.json()["start_url"] == "/"


def test_the_favicon_route_serves_the_real_file():
    """/favicon.ico has been in PUBLIC_PATHS all along while nothing served it."""
    client = TestClient(create_app(ServerConfig(host="127.0.0.1")))
    response = client.get("/favicon.ico")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("image/")
    assert response.content[:4] == b"\x00\x00\x01\x00", "an ICO container"


def test_the_install_assets_are_reachable_before_signing_in():
    """
    The shell is public by design: a browser fetches the manifest and the icons
    without credentials, and so does a service worker during install. If these
    need the token, installing breaks exactly when an operator sets one.
    """
    client = TestClient(create_app(ServerConfig(auth_token=TOKEN, host="0.0.0.0", allow_public=True)))
    for path in ("/manifest.webmanifest", "/favicon.ico"):
        assert client.get(path).status_code == 200, f"{path} must not require the token"
    assert "/manifest.webmanifest" in PUBLIC_PATHS


def test_the_api_is_still_guarded():
    """Widening the public set must not have widened anything else."""
    client = TestClient(create_app(ServerConfig(auth_token=TOKEN, host="0.0.0.0", allow_public=True)))
    assert client.get("/api/status").status_code in (401, 403)
    assert client.get("/api/agents").status_code in (401, 403)
    assert PUBLIC_PATHS == {"/", "/health", "/favicon.ico", "/manifest.webmanifest"}, (
        "PUBLIC_PATHS grows deliberately, one entry at a time"
    )


def test_the_icons_are_served_from_the_static_mount():
    client = TestClient(create_app(ServerConfig(host="127.0.0.1")))
    for name in ("icon-192.png", "icon-512.png", "apple-touch-icon.png"):
        response = client.get(f"/static/icons/{name}")
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("image/png")
