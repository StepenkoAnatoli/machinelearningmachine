"""
The install path is code, so it is tested like code.

Two real defects motivated this file, both invisible on the maintainer's machine
and both fatal on a beginner's:

* ``launch-windows.bat`` was committed with mixed LF/CRLF endings (47 CRLF lines,
  the rest bare LF). cmd.exe parses a batch file line by line and is unreliable
  with LF-only files - labels and multi-line ``if (...)`` blocks are the usual
  casualties. It is the *first* file a Windows user double-clicks, and a broken
  first click is a lost user. ``.gitattributes`` pins CRLF for ``*.bat``/``*.cmd``
  and LF for the shell launchers; the assertions below keep it that way.
* All three launchers bound port 8000 unconditionally. A copy of the app left
  running from the previous session - or any other program on 8000 - made the
  window print an error and stop, which reads as "the app is broken". They now ask
  ``scripts/pick_port.py`` for the first free port and open the browser there.

The install guide is checked here too, because it is part of the product for the
audience this project targets: a novice who has never used a command line.
"""

from __future__ import annotations

import re
import socket
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.pick_port import DEFAULT_START, find_free_port

ROOT = Path(__file__).resolve().parents[1]
WINDOWS_LAUNCHER = ROOT / "launch-windows.bat"
SHELL_LAUNCHERS = (ROOT / "launch-linux.sh", ROOT / "launch-macos.command")
LAUNCHERS = (WINDOWS_LAUNCHER, *SHELL_LAUNCHERS)
GUIDE = ROOT / "INSTALL-WINDOWS.md"


def _bytes(path: Path) -> bytes:
    return path.read_bytes()


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


# --------------------------------------------------------------------------
# Line endings: the file a Windows beginner double-clicks first
# --------------------------------------------------------------------------


def test_windows_launcher_is_crlf_end_to_end():
    """Every newline must be CRLF - one bare LF is enough for cmd.exe to complain."""
    data = _bytes(WINDOWS_LAUNCHER)
    assert data.count(b"\r\n") > 40, "the launcher should still be a real script"
    stripped = data.replace(b"\r\n", b"")
    assert b"\n" not in stripped, "launch-windows.bat has an LF-only line (mixed endings)"


def test_shell_launchers_are_lf_end_to_end():
    """The mirror image of the bug: bash treats a trailing CR as part of the command."""
    for launcher in SHELL_LAUNCHERS:
        assert b"\r\n" not in _bytes(launcher), f"{launcher.name} has CRLF line endings"


def test_gitattributes_pins_the_endings_per_platform():
    rules = _text(ROOT / ".gitattributes")
    for expected in ("*.bat text eol=crlf", "*.cmd text eol=crlf", "*.sh text eol=lf", "*.command text eol=lf"):
        assert expected in rules, f".gitattributes should pin {expected!r}"


# --------------------------------------------------------------------------
# What the launchers do before they start the server
# --------------------------------------------------------------------------


def test_windows_launcher_explains_an_old_or_missing_python():
    """A 3.8 interpreter found on PATH used to fail later, with a wall of pip text."""
    text = _text(WINDOWS_LAUNCHER)
    assert "sys.version_info >= (3, 10)" in text, "the launcher must check the Python version"
    assert "https://www.python.org/downloads/" in text
    assert "Add python.exe to PATH" in text, "the installer checkbox is the step people miss"
    assert "Microsoft Store placeholder" in text, "the Store stub deserves the plain-words explanation, not a traceback"


def test_shell_launchers_check_the_python_version_too():
    for launcher in SHELL_LAUNCHERS:
        text = _text(launcher)
        assert "sys.version_info >= (3, 10)" in text, f"{launcher.name} must check the Python version"
        assert "3.10 or newer" in text, f"{launcher.name} should say the requirement in plain words"


def test_launchers_pick_a_free_port_and_print_the_url_they_will_use():
    for launcher in LAUNCHERS:
        text = _text(launcher)
        assert "pick_port.py" in text, f"{launcher.name} must not assume 8000 is free"
        assert '--host 127.0.0.1' in text, f"{launcher.name} must bind loopback explicitly"
        assert "127.0.0.1:$PORT" in text or "127.0.0.1:%PORT%" in text, (
            f"{launcher.name} must tell the user the address it actually chose"
        )


def test_launchers_never_claim_they_are_opening_a_public_port():
    """The launchers are the documented "safest" path: loopback, no token needed."""
    for launcher in LAUNCHERS:
        text = _text(launcher)
        for forbidden in ("--allow-public", "0.0.0.0"):
            assert forbidden not in text, f"{launcher.name} must not use {forbidden}"


def test_windows_launcher_points_at_the_guide_when_something_is_missing():
    text = _text(WINDOWS_LAUNCHER)
    assert "INSTALL-WINDOWS.md" in text


# --------------------------------------------------------------------------
# pick_port.py: the helper those launchers rely on
# --------------------------------------------------------------------------


def test_pick_port_skips_a_port_that_is_already_taken():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as busy:
        busy.bind(("127.0.0.1", 0))
        busy.listen(1)
        taken = busy.getsockname()[1]
        chosen = find_free_port(start=taken, tries=3)
        assert chosen != taken, "a port someone else is listening on must be skipped"
        # ...and the port handed back must actually be bindable right now.
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.bind(("127.0.0.1", chosen))


def test_pick_port_falls_back_to_the_os_when_the_whole_range_is_taken():
    """A busy range must not fail the launch: the OS always has a free port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as busy:
        busy.bind(("127.0.0.1", 0))
        busy.listen(1)
        taken = busy.getsockname()[1]
        chosen = find_free_port(start=taken, tries=1)
        assert 1024 <= chosen <= 65535
        assert chosen != taken


def test_pick_port_rejects_a_meaningless_range():
    with pytest.raises(ValueError):
        find_free_port(start=DEFAULT_START, tries=0)


def test_pick_port_prints_one_bare_number_for_the_launchers_to_read():
    """launch-windows.bat reads stdout with `set /p`: anything else becomes a bad URL."""
    # Fixed argv - the interpreter running the tests, no shell, no user input.
    proc = subprocess.run(  # noqa: S603
        [sys.executable, str(ROOT / "scripts" / "pick_port.py")],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode == 0, proc.stderr
    assert re.fullmatch(r"\d+\n?", proc.stdout), f"expected one number, got {proc.stdout!r}"


# --------------------------------------------------------------------------
# The guide itself
# --------------------------------------------------------------------------


def test_the_windows_guide_exists_and_is_reachable_from_the_readme():
    assert GUIDE.is_file(), "the beginner guide must ship with the project"
    readme = _text(ROOT / "README.md")
    assert "INSTALL-WINDOWS.md" in readme, "the README is where people arrive; link the guide there"

    # The guide tells a beginner to double-click a launcher, so the source
    # distribution has to contain both - an sdist that ships the instruction but
    # not the file is the same defect as a README advertising a command that
    # does not exist.
    manifest = _text(ROOT / "MANIFEST.in")
    for packaged in ("INSTALL-WINDOWS.md", ".gitattributes", "launch-windows.bat", "launch-linux.sh", "launch-macos.command"):
        assert packaged in manifest, f"{packaged} must be in the sdist"


def test_the_windows_guide_is_numbered_steps_for_a_complete_beginner():
    guide = _text(GUIDE)
    steps = re.findall(r"^## Step (\d)", guide, re.M)
    assert steps == [str(n) for n in range(1, len(steps) + 1)], f"steps must be numbered in order: {steps}"
    assert len(steps) >= 4, "installing, unpacking, starting, using"
    for expected in (
        "https://www.python.org/downloads/",  # where Python comes from
        "Add python.exe to PATH",  # the checkbox that breaks the install when missed
        "Download ZIP",  # how the project arrives
        "Extract All",  # ...and why running from inside the zip fails
        "launch-windows.bat",  # the one file to double-click
        "More info",  # the SmartScreen dialog, answered
        "Troubleshooting",  # what to do when it does not work
    ):
        assert expected in guide, f"the guide should mention {expected!r}"


def test_the_guide_does_not_promise_more_than_the_app_does():
    """The same rule as tests/test_docs_are_accurate.py, applied to the new guide."""
    guide = _text(GUIDE).lower()
    assert "127.0.0.1" in guide, "the guide should state the loopback default"
    assert "simulated" in guide, "the simulator must not be presented as a real model"
