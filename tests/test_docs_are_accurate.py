"""
The repository must not overstate itself.

The audit's project-quality finding was largely about claims: a badge that said
"30 tests passing" with no CI behind it, a README that told people to bind
0.0.0.0, a "MIT License" line with no license file. These tests make the
remaining claims self-enforcing, so a stale number in a document fails the build
instead of living on the front page.
"""

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
README = (ROOT / "README.md").read_text(encoding="utf-8")
SECURITY = (ROOT / "SECURITY.md").read_text(encoding="utf-8")
JS_DIR = ROOT / "tests" / "js"

#: One top-level `test("...")` per browser test, counted the same way in every file.
_JS_TEST = re.compile(r'^test\("', re.M)


def _jsdom_counts():
    return {path.name: len(_JS_TEST.findall(path.read_text(encoding="utf-8"))) for path in sorted(JS_DIR.glob("*.test.mjs"))}


def test_readme_python_test_count_is_true(request):
    collected = request.session.testscollected
    # Only meaningful for a full-suite run: a targeted `pytest tests/x.py`
    # legitimately collects a handful.
    if collected < 100:
        pytest.skip(f"not a full-suite run (collected {collected})")
    claimed = re.search(r"(\d+) tests \((\d+) Python \+ (\d+) jsdom", README)
    assert claimed, "README should state the test counts in one place"
    python_claimed, js_claimed, total_claimed = int(claimed.group(2)), int(claimed.group(3)), int(claimed.group(1))
    assert python_claimed == collected, (
        f"README claims {python_claimed} Python tests but pytest collected {collected}; "
        "update the README (that is what this test is for)"
    )
    assert total_claimed == python_claimed + js_claimed, "the total must be the sum of the parts"


def test_readme_jsdom_test_count_is_true():
    """The jsdom half of the claim, counted from the files CI actually runs."""
    claimed = re.search(r"\(\d+ Python \+ (\d+) jsdom", README)
    assert claimed, "README should state the jsdom test count"
    counts = _jsdom_counts()
    assert counts, f"no jsdom suites found in {JS_DIR}"
    actual = sum(counts.values())
    assert int(claimed.group(1)) == actual, f"README claims {claimed.group(1)} jsdom tests, {counts} sums to {actual}"


def test_ci_runs_every_jsdom_suite():
    """
    A browser suite CI never executes is a suite that rots quietly - which is how
    three of this repo's own examples ended up lint-broken while CI reported green.

    The glob is expanded by the shell, not by node: quoting it would ask node to
    open a file literally named "tests/js/*.test.mjs", which only works on node
    >= 21 and is a hard failure on the node 20 runner this workflow uses.
    """
    command = "node --test tests/js/*.test.mjs"
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    package = (ROOT / "package.json").read_text(encoding="utf-8")
    assert f"{command}\n" in workflow
    assert f'"test:js": "{command}"' in package
    assert len(_jsdom_counts()) >= 2, "expected a sanitizer suite and a client suite"


def test_readme_and_security_document_the_loopback_default():
    # The example that mattered most: telling users to bind a public interface.
    assert "--host 0.0.0.0 --port 8000\n" not in README, "README must not present a public bind as the example"
    assert "127.0.0.1" in README
    for doc in (README, SECURITY):
        assert "--allow-public" in doc and "--auth-token" in doc


def test_licence_file_exists_and_is_declared():
    license_text = (ROOT / "LICENSE").read_text(encoding="utf-8")
    assert "MIT License" in license_text
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert 'license = { file = "LICENSE" }' in pyproject
    assert '"License :: OSI Approved :: MIT License"' in pyproject
    assert "MIT" in README and "[LICENSE](LICENSE)" in README, "the README must point at the file"


def test_version_is_stated_once_and_consistently():
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    declared = re.search(r'^version = "([^"]+)"', pyproject, re.M).group(1)
    init = (ROOT / "machinelearningmachine" / "__init__.py").read_text(encoding="utf-8")
    assert f'__version__ = "{declared}"' in init, "package version and pyproject must agree"
    app = (ROOT / "machinelearningmachine" / "server" / "app.py").read_text(encoding="utf-8")
    assert f'version="{declared}"' in app, "the API's advertised version must match"


def test_security_doc_admits_what_it_does_not_do():
    """A security policy that only lists strengths is marketing."""
    lowered = SECURITY.lower()
    for phrase in ("does not", "residual risk", "known limitations"):
        assert phrase in lowered, f"SECURITY.md should still contain a '{phrase}' section"
    assert "dns-rebinding" in lowered or "dns rebinding" in lowered


def test_claims_about_vendoring_are_backed_by_files():
    vendor = ROOT / "machinelearningmachine" / "server" / "static" / "vendor"
    assert (vendor / "MANIFEST.json").is_file()
    manifest = (vendor / "MANIFEST.json").read_text(encoding="utf-8")
    for package in ("marked", "dompurify", "tailwindcss"):
        assert f'"{package}"' in manifest, f"{package} is claimed as vendored but is not in the manifest"
    assert (vendor / "licenses").is_dir(), "vendored third-party licenses must ship with them"
    assert "no third-party origins" in README.lower() or "vendored" in README.lower()


def test_hardening_doc_agrees_runs_queue_instead_of_refusing():
    # D24: decision B was rewritten (lock + bounded FIFO), but F2, the F2
    # evidence row, section 7 and the N1/N2/N4 paragraph still said refusal.
    prod = (ROOT / "PRODUCTION_HARDENING.md").read_text(encoding="utf-8")
    flat = re.sub(r"\s+", " ", prod)  # prose wraps; claims should not depend on where
    for stale in (
        "a second concurrent run is refused",
        "loser gets 409",
        "Deliberately refused (decision B)",
        "a queue would need persistence to be honest about it",
        "rather than a queue",
    ):
        assert stale not in flat, f"stale no-queue claim still in PRODUCTION_HARDENING.md: {stale!r}"
    for current in (
        "waits in a bounded queue",  # F2
        "202-with-position",  # section 4 F2 row
        "| F15 |",  # section 4 gained the queue requirement's evidence row
        "queueing, not refusal",  # section 7
        "an in-memory list as the queue",  # N1/N2/N4 paragraph
        "`--run-timeout`/`--max-sessions`/`--session-ttl`/`--max-queued`",  # F9 row
    ):
        assert current in flat, f"PRODUCTION_HARDENING.md should say: {current!r}"


def test_user_centered_design_agrees_runs_queue():
    # D24: the "what it is now" column still promised a 409 for a second run.
    ucd = (ROOT / "USER_CENTERED_DESIGN.md").read_text(encoding="utf-8")
    assert "A second request is refused with a `409`" not in ucd
    assert "waits its turn in a bounded per-session queue" in ucd


def test_readme_test_listing_counts_are_true():
    # D24: the tests/ tree said "9 tests" for a 17-test suite, omitted the
    # run-control suites, and the jsdom how-to line said 24 for 32.
    counts = _jsdom_counts()
    for name, actual in counts.items():
        line = next(line for line in README.splitlines() if name in line and "test" in line)
        claimed = re.search(r"(\d+)", line)
        assert claimed, f"README tree should state a count for {name}"
        assert int(claimed.group(1)) == actual, (
            f"README tree claims {claimed.group(1)} tests for {name}, file has {actual}"
        )
    howto = next(line for line in README.splitlines() if "jsdom browser tests" in line)
    claimed_total = re.search(r"(\d+) jsdom browser tests", howto)
    assert claimed_total, "README should state the jsdom total where it shows the command"
    assert int(claimed_total.group(1)) == sum(counts.values())
    for suite in ("test_run_cancellation.py", "test_run_queue.py", "test_queued_run_validation.py"):
        assert suite in README, f"README tests/ tree omits {suite}"


def test_readme_running_tests_block_states_todays_count(request):
    """
    D29: the headline count is pinned by the test above, and the copyable block
    400 lines below it was not.

    It said ``pytest -q  # 357 tests, all offline`` and showed a sample run
    ending ``357 passed in 21.3s`` while the suite collected 432 - two numbers in
    one document, one of them enforced. A reader who copies the command and gets
    a different count than the document promised stops trusting the rest of it,
    which is the same failure as a badge that says "30 tests passing" with no CI
    behind it. The duration is not pinned and is marked as the machine's own: a
    wall clock is not a claim this repo can keep.
    """
    collected = request.session.testscollected
    if collected < 100:
        pytest.skip(f"not a full-suite run (collected {collected})")
    start = README.index("## 🧪 Running Tests")
    block = README[start:README.index("Coverage by area", start)]
    counts = [int(n) for n in re.findall(r"(\d+)\s+(?:tests\b|passed\b)", block)]
    assert counts, f"the Running Tests block should state a pytest count: {block!r}"
    assert all(n == collected for n in counts), (
        f"the Running Tests block claims {counts} but pytest collected {collected}"
    )
