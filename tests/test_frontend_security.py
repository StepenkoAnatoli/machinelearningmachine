"""
Tests for how the dashboard ships and serves its browser-side code.

Covers the audit's fourth finding (remote JavaScript with no integrity
protection) plus the headers that make the vendored setup meaningful. They read
the real files from the package, so a regression in the build is caught here
rather than in a browser.
"""

import base64
import hashlib
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from machinelearningmachine.server.app import create_app
from machinelearningmachine.server.config import ServerConfig

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "machinelearningmachine" / "server" / "static"
VENDOR = STATIC / "vendor"

EXTERNAL_URL = re.compile(r"""(?:src|href)\s*=\s*["']\s*(?:https?:)?//([^/"']+)""", re.I)
ALLOWED_REMOTE_HOSTS: set[str] = set()  # the dashboard must load nothing remotely


@pytest.fixture(scope="module")
def client():
    return TestClient(create_app(ServerConfig(host="127.0.0.1")))


def test_index_html_references_no_third_party_origins():
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    hosts = {h.lower() for h in EXTERNAL_URL.findall(html)}
    assert hosts == ALLOWED_REMOTE_HOSTS, f"index.html loads assets from {sorted(hosts)}"


def test_no_cdn_hostnames_anywhere_in_the_frontend():
    for name in ("index.html", "app.js", "markdown.js", "style.css"):
        text = (STATIC / name).read_text(encoding="utf-8")
        for cdn in ("cdn.tailwindcss.com", "cdnjs.cloudflare.com", "cdn.jsdelivr.net", "unpkg.com"):
            assert cdn not in text, f"{name} still points at {cdn}"


def test_every_referenced_local_asset_exists():
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    refs = re.findall(r"""(?:src|href)\s*=\s*["'](\/static\/[^"']+)["']""", html)
    assert len(refs) >= 8, "expected the vendored scripts and styles to be referenced"
    for ref in refs:
        path = STATIC / ref.replace("/static/", "")
        assert path.is_file(), f"index.html references a missing file: {ref}"
        assert path.stat().st_size > 0, f"{ref} is empty"


def test_vendor_manifest_matches_what_is_on_disk():
    manifest = json.loads((VENDOR / "MANIFEST.json").read_text(encoding="utf-8"))
    assert manifest["files"], "manifest lists no files"
    for rel, info in manifest["files"].items():
        path = VENDOR / rel
        assert path.is_file(), f"manifest lists {rel} but it is not vendored"
        data = path.read_bytes()
        assert len(data) == info["bytes"], f"{rel} size differs from the manifest"
        digest = base64.b64encode(hashlib.sha384(data).digest()).decode()
        assert info["sri"] == f"sha384-{digest}", f"{rel} does not match its recorded SRI hash"
        assert re.match(r"^\d+\.\d+\.\d+$", str(info["version"])), f"{rel} is not version-pinned"


def test_all_vendored_packages_are_version_pinned():
    manifest = json.loads((VENDOR / "MANIFEST.json").read_text(encoding="utf-8"))
    versions = {info["package"]: info["version"] for info in manifest["files"].values()}
    for package in ("marked", "dompurify", "@highlightjs/cdn-assets", "tailwindcss", "@fortawesome/fontawesome-free"):
        assert package in versions, f"{package} is not in the vendor manifest"
        assert re.match(r"^\d+\.\d+\.\d+$", versions[package]), f"{package} is not pinned: {versions[package]}"


def test_sanitizer_is_loaded_before_the_app_script():
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    order = [m.group(1) for m in re.finditer(r"""<script[^>]*src="[^"]*?/([^/"]+\.js)\"""", html)]
    assert "purify.min.js" in order and "marked.min.js" in order and "app.js" in order
    assert order.index("purify.min.js") < order.index("app.js")
    assert order.index("markdown.js") < order.index("app.js"), "the sanitizer module must load first"


def test_no_inline_scripts_or_event_handlers_in_html():
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    assert not re.search(r"<script(?![^>]*\bsrc=)[^>]*>", html, re.I), "inline <script> is not allowed"
    assert not re.search(r"""\son[a-z]+\s*=\s*["']""", html, re.I), "inline on* handlers are not allowed"
    app = (STATIC / "app.js").read_text(encoding="utf-8")
    # app.js may set innerHTML only with its own literal markup, never with data
    assert not re.search(r"""innerHTML\s*=\s*[^;\n]*\$\{\s*(?:ag|msg|data|s)\.""", app), (
        "user-controlled values must not be interpolated into innerHTML"
    )


def test_security_headers_are_sent_for_pages_and_assets(client):
    for path in ("/", "/static/app.js", "/static/vendor/marked/marked.min.js"):
        resp = client.get(path)
        assert resp.status_code == 200, path
        csp = resp.headers.get("content-security-policy", "")
        assert "default-src 'self'" in csp, f"{path} has no CSP"
        assert "unsafe-eval" not in csp and "*" not in csp
        assert resp.headers.get("x-content-type-options") == "nosniff"
        assert resp.headers.get("x-frame-options") == "DENY"
        assert resp.headers.get("referrer-policy") == "no-referrer"


def test_csp_does_not_allow_remote_sources(client):
    csp = client.get("/").headers["content-security-policy"]
    for directive in csp.split(";"):
        directive = directive.strip()
        if not directive or directive.startswith("default-src"):
            continue
        sources = directive.split()[1:]
        for source in sources:
            assert not source.startswith("http"), f"{directive} allows a remote origin"
            assert source not in ("*", "https:"), f"{directive} allows anything"


def test_websocket_is_same_origin_only(client):
    app_js = (STATIC / "app.js").read_text(encoding="utf-8")
    assert "window.location.host" in app_js, "the socket must connect to its own origin"
    assert not re.search(r"""new WebSocket\(\s*[\"']ws""", app_js), "hard-coded remote WebSocket URL"


def test_transcript_rendering_goes_through_the_sanitizer_module():
    app = (STATIC / "app.js").read_text(encoding="utf-8")
    assert "MeshRender.render" in app or "renderMarkdownFragment" in app
    assert "DOMPurify" not in app, "sanitizing belongs in markdown.js, where it is tested"
    markdown_js = (STATIC / "markdown.js").read_text(encoding="utf-8")
    assert "DOMPurify" in markdown_js and "RETURN_DOM_FRAGMENT" in markdown_js


def test_jsdom_browser_suites_pass():
    """
    Run the jsdom suites from the Python side too, so `pytest` alone proves the
    browser behaviour as well - skipped when node or jsdom is absent.

    The file list is expanded here rather than passed as a glob: naming one file is
    how a second suite would sit unexecuted forever, and asking node to expand a
    quoted pattern needs node >= 21, which the CI runner does not have.
    """
    if shutil.which("node") is None:
        pytest.skip("node is not installed")
    root = Path(__file__).resolve().parents[1]
    if not (root / "node_modules" / "jsdom").exists():
        pytest.skip("jsdom is not installed (run `npm ci`)")
    suites = sorted(str(path.relative_to(root)) for path in (root / "tests" / "js").glob("*.test.mjs"))
    assert suites, "no jsdom suites found in tests/js"
    proc = subprocess.run(  # noqa: S603  - argv is node plus paths globbed from this repo's own tests/js
        ["node", "--test", *suites],
        cwd=str(root), capture_output=True, text=True, timeout=300,
    )
    assert proc.returncode == 0, proc.stdout[-4000:] + proc.stderr[-2000:]
    assert "fail 0" in proc.stdout, proc.stdout[-2000:]


def test_static_directory_has_no_source_maps_or_sourcemaps_referenced():
    # Minified vendor files must not point at a remote source map (they would be
    # fetched with the page's origin and can leak/redirect debugging).
    for path in [p for p in VENDOR.rglob("*.js") if p.is_file()]:
        tail = path.read_text(encoding="utf-8", errors="replace")[-400:]
        assert "sourceMappingURL=http" not in tail, f"{path.name} fetches a remote source map"


LICENSE_MARKERS = (
    "mit license", "permission is hereby granted", "apache license",
    "mozilla public license", "bsd", "creative commons", "sil open font",
    "license information",
)


def test_every_vendored_package_ships_its_license_text():
    """
    Vendoring without the license texts would be a compliance bug dressed up as a
    security fix, so the presence and shape of each text is checked here.
    """
    manifest = json.loads((VENDOR / "MANIFEST.json").read_text(encoding="utf-8"))
    lic_dir = VENDOR / "licenses"
    assert lic_dir.is_dir(), "no licenses directory - run scripts/build_vendor.py"
    files = sorted(p.name for p in lic_dir.glob("*.txt"))
    packages = {info["package"] for info in manifest["files"].values()}
    assert len(files) >= len(packages), (
        f"{len(files)} license files cannot cover {len(packages)} vendored packages: {sorted(packages)}"
    )
    for path in lic_dir.glob("*.txt"):
        text = path.read_text(encoding="utf-8", errors="replace")
        assert len(text) > 200, f"{path.name} looks like a stub, not a license"
        assert any(marker in text.lower() for marker in LICENSE_MARKERS), (
            f"{path.name} does not read like a license text"
        )
    # The manifest lists them, so an upstream rename is a loud build failure
    # rather than a missing file in the shipped wheel.
    assert set(manifest["licenses"]["files"]) == {p.name for p in lic_dir.glob("*.txt")}


def test_vendored_files_do_not_match_the_build_script_by_accident():
    """ASSETS/LICENSES in scripts/build_vendor.py stay in step with the tree."""
    script = (ROOT / "scripts" / "build_vendor.py").read_text(encoding="utf-8")
    manifest = json.loads((VENDOR / "MANIFEST.json").read_text(encoding="utf-8"))
    for rel in manifest["files"]:
        if rel == "tailwind.css":
            continue  # built, not copied
        assert Path(rel).name in script or rel.split("/")[-1] in script, (
            f"{rel} is in the manifest but not named by the build script"
        )
