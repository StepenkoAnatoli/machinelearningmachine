"""
Tests for the dependency diagnostics that turn a raw ModuleNotFoundError into
copy-pasteable install instructions (see machinelearningmachine/_deps.py).
"""

import sys

from machinelearningmachine import _deps


def test_install_hint_names_package_and_commands():
    hint = _deps.install_hint("pydantic")
    assert "pydantic" in hint
    assert "install -e ." in hint
    assert "install -r requirements.txt" in hint
    # The hint must reference the interpreter actually in use.
    assert ("py -m pip" if sys.platform.startswith("win") else "python -m pip") in hint


def test_install_hint_uses_import_root_for_submodules():
    # `import pydantic.v1` failing should still be reported as `pydantic`.
    assert "pydantic" in _deps.install_hint("pydantic.v1")
    assert "pydantic.v1" not in _deps.install_hint("pydantic.v1")


def test_install_hint_deduplicates_and_sorts_packages():
    hint = _deps.install_hint(["uvicorn", "fastapi", "uvicorn.main"])
    assert "fastapi, uvicorn" in hint


def test_missing_detects_absent_module():
    absent = "definitely_not_a_real_module_xyz"
    assert _deps.missing([absent]) == [absent]
    assert _deps.missing(["json", "sys"]) == []


def test_known_dependency_classification():
    assert _deps.is_known_dependency("pydantic")
    assert _deps.is_known_dependency("pydantic.fields")
    assert not _deps.is_known_dependency("machinelearningmachine")
    assert not _deps.is_known_dependency(None)


def test_require_raises_importerror_for_missing_dependency():
    try:
        _deps.require(["definitely_not_a_real_module_xyz"])
    except ImportError as exc:
        assert "definitely_not_a_real_module_xyz" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("require() should have raised ImportError")


def test_require_passes_when_dependencies_present():
    _deps.require(["json", "sys"])
