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


def test_hardening_doc_breaks_down_the_jsdom_count_per_file():
    """`15 sanitizer + 9 client lifecycle` has to be each file's real count.

    The aggregate is checked against README above; this checks the split, so neither
    half of it can quietly stop matching the suite it names.
    """
    hardening = (ROOT / "PRODUCTION_HARDENING.md").read_text(encoding="utf-8")
    claimed = re.search(
        r"\*\*(\d+) pass\*\* \((\d+) sanitizer \+ (\d+) client lifecycle\)", hardening
    )
    assert claimed, "PRODUCTION_HARDENING.md should break the jsdom count down by file"

    counts = _jsdom_counts()
    assert sum(counts.values()) == int(claimed.group(1)), (
        f"claims {claimed.group(1)} jsdom tests, counted {sum(counts.values())}"
    )
    assert counts.get("sanitize.test.mjs") == int(claimed.group(2)), "sanitizer count drifted"
    assert counts.get("client-lifecycle.test.mjs") == int(claimed.group(3)), (
        "client-lifecycle count drifted"
    )


#: The one `node --test` argument that runs every suite on every node we support.
#: `tests/js/` is not a valid entry point ("Cannot find module"), and
#: `"tests/js/*.test.mjs"` relies on node itself expanding the pattern, which needs >= 21.
_JS_ARG = "tests/js/*.test.mjs"
_JS_CMD = f"node --test {_JS_ARG}"
_NODE_TEST = re.compile(r"node --test (\S+)")

#: What a reader forms their habits from, plus the two places commands really run.
_COMMAND_DOCS = (
    "README.md", "SECURITY.md", "PRODUCTION_HARDENING.md", "USER_CENTERED_DESIGN.md",
    "package.json", ".github/workflows/ci.yml",
)


def _backticked_spans(line):
    """Character ranges inside `...` - inline code, i.e. prose *about* a command."""
    spans, start = [], -1
    for index, char in enumerate(line):
        if char != "`":
            continue
        if start < 0:
            start = index
        else:
            spans.append((start, index))
            start = -1
    if start >= 0:
        spans.append((start, len(line)))
    return spans


def _command_lines(path):
    """Yield `(line, is_copyable)`; copyable = offered as a command to run.

    Copyable means a fenced code block or a `Run:` line. Everything else is prose,
    and prose is allowed to quote the broken form - that is how a defect gets written
    down. Editing such a quote away to satisfy a lint would make the document less
    true, not more.
    """
    in_fence = False
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line.startswith("```"):
            in_fence = not in_fence
            continue
        copyable = in_fence or line.lstrip("* ").startswith("Run:")
        yield line, copyable


def _jsdom_instructions():
    """Every `node --test` mention in a document or test-file header, with its context."""
    files = [ROOT / name for name in _COMMAND_DOCS]
    files += sorted((ROOT / "tests" / "js").glob("*.mjs"))
    for path in files:
        for line, copyable in _command_lines(path):
            for match in _NODE_TEST.finditer(line):
                quoted = any(a <= match.start() < b for a, b in _backticked_spans(line))
                yield path, line, match.group(1).rstrip("`,).'\""), copyable, quoted


def test_the_documented_way_to_run_the_browser_tests_works():
    """A command offered for copying has to be a command that runs.

    D17 generalised: what broke was the *form* of a command in a place nobody re-reads,
    not the prose. So the form is checked wherever it is presented as an instruction -
    including the header comments of the suites themselves, one of which told people to
    run `node --test tests/js/`, a command that has never once worked. A guard that
    matches nothing looks exactly like a passing one, so the same test also requires
    that instructions were found, and that every document which mentions node's test
    runner states the working form somewhere.
    """
    instructions = list(_jsdom_instructions())
    offenders = [
        f"{path.relative_to(ROOT)}: {line[:100]}"
        for path, line, arg, is_copyable, quoted in instructions
        if not quoted and arg != _JS_ARG
    ]
    assert not offenders, "a jsdom command is offered as runnable but is not: " + "; ".join(offenders)

    copyable = sum(1 for _, _, _, is_copyable, quoted in instructions if is_copyable and not quoted)
    assert copyable >= 3, (
        f"only {copyable} copyable `node --test` instructions found across the docs - "
        "a guard that passes because nothing was written down guards nothing"
    )

    for name in ("README.md", "SECURITY.md", "PRODUCTION_HARDENING.md", "USER_CENTERED_DESIGN.md"):
        text = (ROOT / name).read_text(encoding="utf-8")
        if "node --test" in text:
            assert _JS_CMD in text, f"{name} mentions node's test runner but never gives the form that works"


def test_ci_runs_every_jsdom_suite():
    """
    A browser test file CI never executes is a file that rots quietly - which is how
    three of this repo's own examples ended up lint-broken while CI reported green.
    """
    counts = _jsdom_counts()
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    package = (ROOT / "package.json").read_text(encoding="utf-8")
    assert "node --test tests/js/*.test.mjs\n" in workflow, (
        "CI should let the shell glob the jsdom directory, so a new suite is picked up "
        "automatically. Quoting the pattern instead (node --test \"tests/js/*.test.mjs\") "
        "asks node to open a file with that literal name: fine on node >= 21, a hard "
        "failure on the node 20 runner this workflow uses."
    )
    assert '"test:js": "node --test tests/js/*.test.mjs"' in package, "npm run test:js must run the same set"
    assert "node --test tests/js/sanitize.test.mjs" not in workflow, (
        "naming one file silently excludes the others"
    )
    assert len(counts) >= 2, f"expected a sanitizer suite and a client suite, found {counts}"


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
