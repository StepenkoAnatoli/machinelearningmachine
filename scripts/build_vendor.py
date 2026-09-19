#!/usr/bin/env python3
"""
Rebuild the vendored browser assets that ship with the dashboard.

Why this exists
---------------
The dashboard used to load Tailwind, FontAwesome, Marked and Highlight.js from
public CDNs with no pinned version and no Subresource Integrity. A CDN outage,
a compromised CDN, or a package swap would then execute arbitrary code inside
the dashboard (which can hold API keys and read saved sessions). Vendoring the
assets removes every third-party origin, which in turn lets the server send a
strict ``Content-Security-Policy: default-src 'self'``.

How to refresh
--------------
1. Bump a version in ``package.json`` (root of the repo).
2. ``npm install`` then ``python3 scripts/build_vendor.py``
3. Review the diff of ``static/vendor/MANIFEST.json`` - it records the exact
   version, byte size and SRI hash of every shipped file.

The script only reads from ``node_modules``; it never downloads anything, so
what lands in the repository is exactly what npm resolved for the pinned
versions (keep ``package-lock.json`` committed).

Requirements: node/npm only for step 2 above; the app itself needs neither.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "machinelearningmachine" / "server" / "static"
VENDOR = STATIC / "vendor"
NODE_MODULES = ROOT / "node_modules"

#: (source inside node_modules, destination inside static/vendor)
ASSETS = [
    ("marked/marked.min.js", "marked/marked.min.js"),
    ("dompurify/dist/purify.min.js", "dompurify/purify.min.js"),
    ("@highlightjs/cdn-assets/highlight.min.js", "highlight.js/highlight.min.js"),
    (
        "@highlightjs/cdn-assets/styles/atom-one-dark.min.css",
        "highlight.js/styles/atom-one-dark.min.css",
    ),
    ("@fortawesome/fontawesome-free/css/all.min.css", "fontawesome/css/all.min.css"),
    (
        "@fortawesome/fontawesome-free/webfonts/fa-solid-900.woff2",
        "fontawesome/webfonts/fa-solid-900.woff2",
    ),
    (
        "@fortawesome/fontawesome-free/webfonts/fa-regular-400.woff2",
        "fontawesome/webfonts/fa-regular-400.woff2",
    ),
]

#: License texts shipped beside the assets that came from them, keyed by the
#: filename they get inside static/vendor/licenses/. Explicit rather than derived
#: from ASSETS on purpose: the first version of this script guessed the name and
#: silently skipped marked, whose file is LICENSE.md, not LICENSE - a missing
#: license is exactly the kind of thing a build must not be quiet about, so a
#: missing source below is a hard error.
LICENSES = {
    "marked.txt": "marked/LICENSE.md",
    "dompurify.txt": "dompurify/LICENSE",
    # DOMPurify is dual-licensed; ship both texts and say so in the note.
    "dompurify-MPL.txt": "dompurify/LICENSE-MPL",
    "highlightjs.txt": "@highlightjs/cdn-assets/LICENSE",
    "fontawesome.txt": "@fortawesome/fontawesome-free/LICENSE.txt",
    # tailwind.css here is *compiled* from this project's markup with the
    # tailwindcss build tool, whose own license must travel with it.
    "tailwindcss.txt": "tailwindcss/LICENSE",
}


def pkg_version(name: str) -> str:
    """Version npm actually installed for ``name`` (from its package.json)."""
    candidates = [NODE_MODULES / name / "package.json"]
    for path in candidates:
        if path.exists():
            try:
                return json.loads(path.read_text(encoding="utf-8")).get("version", "unknown")
            except (OSError, ValueError):
                pass
    # Fall back to what package.json pins.
    root_pkg = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))
    pinned = root_pkg.get("devDependencies", {})
    return str(pinned.get(name, "unknown")).lstrip("^~")


def sri(data: bytes) -> str:
    return "sha384-" + base64.b64encode(hashlib.sha384(data).digest()).decode("ascii")


def build_tailwind(out_dir: Path) -> None:
    """Compile the Tailwind utilities actually used by index.html / app.js."""
    if not (NODE_MODULES / ".bin" / "tailwind").exists() and not (
        NODE_MODULES / ".bin" / "tailwindcss"
    ).exists():
        raise SystemExit("tailwindcss is not installed - run `npm install` first.")

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        config = tmp_path / "tailwind.config.cjs"
        config.write_text(
            "module.exports = {\n"
            '  darkMode: "class",\n'
            "  content: [\n"
            f"    {json.dumps(str(STATIC / 'index.html'))},\n"
            f"    {json.dumps(str(STATIC / 'app.js'))},\n"
            "  ],\n"
            "  theme: {\n"
            "    extend: {\n"
            "      colors: {\n"
            "        brand: {\n"
            '          purple: "#8b5cf6", cyan: "#06b6d4", amber: "#d97706",\n'
            '          emerald: "#10b981", dark: "#0f172a", card: "#1e293b",\n'
            '          border: "#334155",\n'
            "        },\n"
            "      },\n"
            "    },\n"
            "  },\n"
            "  plugins: [],\n"
            "};\n",
            encoding="utf-8",
        )
        entry = tmp_path / "tailwind.input.css"
        entry.write_text(
            "@tailwind base;\n@tailwind components;\n@tailwind utilities;\n",
            encoding="utf-8",
        )
        target = out_dir / "tailwind.css"
        cmd = [
            "npx",
            "--no-install",
            "tailwindcss",
            "-c",
            str(config),
            "-i",
            str(entry),
            "-o",
            str(target),
            "--minify",
        ]
        print("  $ " + " ".join(cmd))
        subprocess.run(cmd, cwd=str(ROOT), check=True, text=True, capture_output=True)

    css = target.read_text(encoding="utf-8")
    used = set()
    for src in (STATIC / "index.html", STATIC / "app.js"):
        text = src.read_text(encoding="utf-8")
        for match in re.finditer(r'class(?:Name)?\s*=\s*"([^"]*)"', text):
            used.update(t for t in match.group(1).split() if t and not t.startswith("$"))
    local = set(re.findall(r"\.([-\w]+)", (STATIC / "style.css").read_text(encoding="utf-8")))
    generated = set(re.findall(r"\.((?:\\.|[-\w])+)", css))
    escaped = lambda c: re.sub(r"([^A-Za-z0-9_-])", lambda m: "\\" + m.group(1), c)  # noqa: E731
    missing = sorted(
        c
        for c in used
        if escaped(c) not in generated
        and c not in local
        and not c.startswith(("fa-", "msg-", "toast", "reader-", "session-", "agent-"))
    )
    if missing:
        print(f"  ! classes not found in the build (usually template-literal noise): {missing[:15]}")


def main() -> int:
    if not NODE_MODULES.exists():
        raise SystemExit("node_modules is missing - run `npm install` first.")

    out = VENDOR
    print("Vendoring browser dependencies into", out.relative_to(ROOT))

    # Stage assets into a temporary directory so a failed build never destroys
    # the existing committed vendor/ folder.
    with tempfile.TemporaryDirectory(dir=STATIC) as tmp:
        stage = Path(tmp) / "vendor"
        stage.mkdir(parents=True, exist_ok=True)
        (stage / "licenses").mkdir(parents=True, exist_ok=True)

        manifest = {
            "generated_by": "scripts/build_vendor.py",
            "note": (
                "Browser assets shipped with the app so the dashboard needs no CDN. "
                "Do not edit by hand - regenerate with `npm install && python3 scripts/build_vendor.py`."
            ),
            "files": {},
        }

        for src_rel, dst_rel in ASSETS:
            src = NODE_MODULES / src_rel
            if not src.exists():
                raise SystemExit(f"missing {src} - run `npm install`.")
            data = src.read_bytes()
            dst = stage / dst_rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_bytes(data)
            pkg_name = src_rel.split("/")[0]
            if pkg_name.startswith("@"):
                pkg_name += "/" + src_rel.split("/")[1]
            manifest["files"][dst_rel] = {
                "package": pkg_name,
                "version": pkg_version(pkg_name),
                "bytes": len(data),
                "sri": sri(data),
            }
            print(f"  {dst_rel:<48} {len(data):>8} bytes")

        # Every vendored package must arrive with its license text, and the build
        # fails rather than quietly omitting one.
        missing = []
        for dst_name, src_rel in sorted(LICENSES.items()):
            src = NODE_MODULES / src_rel
            if not src.exists():
                missing.append(f"{src_rel} (for licenses/{dst_name})")
                continue
            shutil.copyfile(src, stage / "licenses" / dst_name)
        if missing:
            raise SystemExit(
                "vendored licenses missing from node_modules - the assets cannot ship "
                "without them:\n  " + "\n  ".join(missing)
            )
        manifest["licenses"] = {
            "note": (
                "One file per vendored package, copied from the package itself. "
                "DOMPurify is dual-licensed (Apache-2.0 / MPL-2.0), so both texts ship."
            ),
            "files": dict(sorted(LICENSES.items())),
        }
        print(f"  {'licenses/':<48} {len(LICENSES):>8} files")

        build_tailwind(stage)
        tailwind_data = (stage / "tailwind.css").read_bytes()
        manifest["files"]["tailwind.css"] = {
            "package": "tailwindcss",
            "version": pkg_version("tailwindcss"),
            "bytes": len(tailwind_data),
            "sri": sri(tailwind_data),
            "note": "compiled from static/index.html + static/app.js; regenerate, do not edit",
        }
        print(f"  {'tailwind.css':<48} {len(tailwind_data):>8} bytes")

        # Write manifest with explicit LF newlines to prevent Windows CRLF drift
        manifest_data = (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode("utf-8")
        (stage / "MANIFEST.json").write_bytes(manifest_data)

        # Atomic commit: swap staging directory into place
        if out.exists():
            shutil.rmtree(out)
        shutil.move(str(stage), str(out))

    print("Wrote", (out / "MANIFEST.json").relative_to(ROOT))
    print(
        "\nIf the dashboard now renders blank, a class used in a template literal may be\n"
        "invisible to Tailwind's scanner - add a safelist entry in build_tailwind().",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
