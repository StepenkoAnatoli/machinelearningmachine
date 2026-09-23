"""
What the built artifact must contain, checked from the source tree.

The failure mode this exists for is a *silent* one: a wheel that builds without
error, installs without error, and then cannot serve the dashboard, because a
directory of Python files was never recognised as a package. ``server/`` shipped
that way for a while - see the test below - and nothing in the suite noticed,
because from a source checkout every import resolves through PEP 420 namespace
packages whether or not an ``__init__.py`` exists.

These checks are deliberately free of build tooling (no ``setuptools``, no
``build``, no network): they inspect the same files the build does, so they run
in the ordinary test job on every Python version rather than only where a wheel
happens to have been built.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "machinelearningmachine"
PYPROJECT = (ROOT / "pyproject.toml").read_text(encoding="utf-8")

#: Directories that hold data, not importable code. ``static/`` is shipped through
#: ``package-data`` below, so it is not supposed to be a package.
_SKIP_DIRS = {"__pycache__"}


def _python_dirs() -> list[Path]:
    """Every directory under the package that contains at least one ``.py`` file."""
    found = {path.parent for path in PACKAGE.rglob("*.py")}
    return sorted(p for p in found if not any(part in _SKIP_DIRS for part in p.parts))


def test_every_directory_of_python_files_is_a_regular_package():
    """
    A directory with modules in it and no ``__init__.py`` is an implicit namespace
    package: importable from a checkout, invisible to ``find_packages``.

    Reproduced before the fix, with ``server/__init__.py`` deleted::

        find_packages(include=["machinelearningmachine*"])
        ['machinelearningmachine', 'machinelearningmachine.agents',
         'machinelearningmachine.protocol', 'machinelearningmachine.topologies']

    ``machinelearningmachine.server`` is simply absent, so ``app.py``, ``config.py``,
    ``state.py`` and ``feed.py`` are never copied into the wheel. It ships today only
    because ``[tool.setuptools.packages.find]`` leaves ``namespaces`` at its default
    of ``true`` - one line changed in ``pyproject.toml``, or one older setuptools,
    and ``module-mesh serve`` dies on a ``ModuleNotFoundError`` in a clean venv while
    every test in a source checkout still passes.
    """
    dirs = _python_dirs()
    assert PACKAGE in dirs, f"no Python files found under {PACKAGE}"
    missing = sorted(
        str(path.relative_to(ROOT)) for path in dirs if not (path / "__init__.py").is_file()
    )
    assert not missing, (
        f"directories holding modules but no __init__.py: {', '.join(missing)}. "
        "They would be dropped from a wheel built with namespaces=false."
    )


def test_declared_package_data_globs_match_real_files():
    """
    ``pyproject.toml`` carries an explicit warning that without its ``package-data``
    entry "the wheel silently ships without the dashboard". A glob that matches
    nothing is exactly that, and it does not fail the build - so match it here.
    """
    section = PYPROJECT.split("[tool.setuptools.package-data]", 1)[1]
    section = section.split("\n[", 1)[0]  # up to the next table, if any
    globs = re.findall(r'"([^"]+)"', section.split("=", 1)[1])
    assert globs, "no package-data globs were parsed - has the section been renamed?"
    # ``next(..., None)`` rather than ``list(...)``: these globs cover the whole
    # vendored asset tree, and one match is all "this pattern is not dead" needs.
    empty = [pattern for pattern in globs if next(PACKAGE.glob(pattern), None) is None]
    assert not empty, f"package-data globs match no files: {', '.join(empty)}"


def test_the_dashboard_files_the_globs_promise_are_present():
    """The specific assets a blank dashboard would be missing, named outright."""
    static = PACKAGE / "server" / "static"
    required = [
        "index.html",
        "app.js",
        "markdown.js",
        "style.css",
        # Install assets: a wheel that serves a dashboard nobody can install is
        # the same class of silent failure as a wheel with no dashboard at all.
        "manifest.webmanifest",
        "favicon.ico",
        "icons/icon.svg",
        "icons/icon-192.png",
        "icons/icon-512.png",
        "icons/apple-touch-icon.png",
        "vendor/MANIFEST.json",
        "vendor/marked/marked.min.js",
        "vendor/dompurify/purify.min.js",
        "vendor/tailwind.css",
    ]
    missing = [name for name in required if not (static / name).is_file()]
    assert not missing, f"dashboard assets missing from static/: {', '.join(missing)}"
