"""
The live offline -> recovery check has to stay runnable, honest and documented.

``scripts/offline_recovery_check.mjs`` is the only place the real page meets a real
server and a real outage: the jsdom suites drive the state machine with doubles, and
the e2e pass never stops the server. That makes this script the sole evidence for the
promise the whole offline feature is built on - the dashboard comes back by itself -
so it is worth the same treatment as every other claim in this repository: pinned, not
trusted.

What is checked here is only what a *test* can check without killing a server in the
middle of a suite: that the script is present, runnable, documented where a reader
would look for it, shipped in the sdist, and still asserting the two things that turn
it from a demo into a check (a port nobody else is listening on, and exactly one live
socket after recovery). The script's own behaviour is verified by running it - the
README says how, and the check's output is quoted in USER_CENTERED_DESIGN.md.
"""

import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "offline_recovery_check.mjs"
README = (ROOT / "README.md").read_text(encoding="utf-8")
SOURCE = SCRIPT.read_text(encoding="utf-8")


def test_the_script_is_present_and_is_javascript():
    assert SCRIPT.is_file(), "scripts/offline_recovery_check.mjs is the live offline check"
    assert SOURCE.startswith("/*"), "it should open by saying what it is for"
    assert "jsdom" in SOURCE, "it drives the real page, so it depends on jsdom"


def test_the_readme_tells_a_reader_how_to_run_it():
    """
    A check nobody can find is a check nobody runs. The README's "Running Tests"
    block is where every other command lives, so this one is there too.
    """
    assert "offline_recovery_check.mjs" in README, "the README must name the script"
    command = next(
        (line for line in README.splitlines() if "offline_recovery_check.mjs" in line),
        "",
    )
    assert command.strip().startswith("node scripts/offline_recovery_check.mjs"), (
        f"the README should show the command to run, got: {command!r}"
    )


def test_the_sdist_ships_it():
    """The launchers' rule: an instruction for a file the sdist does not contain."""
    manifest = (ROOT / "MANIFEST.in").read_text(encoding="utf-8")
    assert "recursive-include scripts *.mjs" in manifest, (
        "scripts/*.mjs must be packaged, or the README's how-to-run line rots for anyone reading an sdist"
    )


def test_the_check_verifies_the_port_is_free_before_it_starts():
    """
    The failure that fooled this check during development: a server left over from
    an earlier experiment held the port, so "the server I killed" was never the one
    answering, and every offline assertion passed against the wrong process. Two
    guards keep that from silently returning.
    """
    assert "portIsFree" in SOURCE, "the check has to be able to ask whether a port is free"
    assert re.search(r"if \(!\(await portIsFree\(port\)\)\)", SOURCE), (
        "and it must refuse to run when the port it picked is taken"
    )
    assert "stopServer" in SOURCE and "still answering" in SOURCE, (
        "a kill that did not take effect must fail the check, not pass the offline phase"
    )


def test_the_check_still_asserts_exactly_one_live_socket():
    """
    The bug this check caught: recovery re-opened the socket by hand while the
    socket's own retry timer was still pending, leaving two live sockets and every
    message delivered twice. The assertion is the reason the script exists.
    """
    assert re.search(r"liveSockets\(\) === 1", SOURCE), (
        "the live-socket assertion is what catches a leaked socket - do not weaken it"
    )
    assert "no socket opens after recovery" in SOURCE, (
        "and it has to keep watching after recovery, since a stale retry fires seconds later"
    )


def test_the_script_parses():
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    result = subprocess.run([node, "--check", str(SCRIPT)], capture_output=True, text=True)  # noqa: S603
    assert result.returncode == 0, f"node --check failed:\n{result.stdout}{result.stderr}"
