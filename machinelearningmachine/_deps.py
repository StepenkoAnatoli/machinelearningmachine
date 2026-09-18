"""
Dependency diagnostics for MachineLearningMachine.

Turns raw ``ModuleNotFoundError`` tracebacks into actionable install hints.
Nothing in this module imports third-party packages, so it is always safe to
import — even in an environment where nothing has been installed yet.
"""

from __future__ import annotations

import importlib.util
import sys
from typing import Iterable, List

#: Distribution -> import name that provides it (they differ for a few packages,
#: e.g. the ``websockets`` project imports as ``websockets``, but PyYAML would
#: import as ``yaml``). Keep in sync with pyproject.toml / requirements.txt.
KNOWN_DEPENDENCIES = {
    "pydantic": "pydantic>=2.6.0",
    "fastapi": "fastapi>=0.110.0",
    "uvicorn": "uvicorn>=0.28.0",
    "aiohttp": "aiohttp>=3.9.0",
    "requests": "requests>=2.31.0",
    "websockets": "websockets>=12.0",
}

_LINE = "=" * 70


def root_module(module_name: str | None) -> str | None:
    """Return the top-level package name of an import, e.g. ``pydantic.v1`` -> ``pydantic``."""
    if not module_name:
        return None
    return module_name.split(".")[0]


def is_known_dependency(module_name: str | None) -> bool:
    """True when ``module_name`` is one of the third-party packages we depend on."""
    return root_module(module_name) in KNOWN_DEPENDENCIES


def missing(modules: Iterable[str]) -> List[str]:
    """Return the subset of ``modules`` that cannot be imported in this interpreter."""
    missing_modules = []
    for module in modules:
        try:
            found = importlib.util.find_spec(module) is not None
        except (ImportError, ValueError):
            found = False
        if not found:
            missing_modules.append(module)
    return missing_modules


def install_hint(missing_modules: Iterable[str] | str) -> str:
    """Build a friendly, copy-pasteable 'this dependency is missing' message."""
    if isinstance(missing_modules, str):
        missing_modules = [missing_modules]
    packages = sorted({root_module(m) or m for m in missing_modules})
    listed = ", ".join(packages)
    pip_cmd = "py -m pip" if sys.platform.startswith("win") else "python -m pip"

    return (
        f"\n{_LINE}\n"
        f"MachineLearningMachine cannot start: missing dependency '{listed}'.\n"
        f"\n"
        f"Install the project together with its dependencies — run this from the\n"
        f"folder that contains 'pyproject.toml':\n"
        f"\n"
        f"    {pip_cmd} install -e .\n"
        f"\n"
        f"Equivalent alternative:\n"
        f"\n"
        f"    {pip_cmd} install -r requirements.txt\n"
        f"\n"
        f"If it still fails, you are probably running a different Python\n"
        f"interpreter/virtual environment than the one you installed into. Check:\n"
        f"\n"
        f"    {pip_cmd} --version\n"
        f"    python -c \"import sys; print(sys.executable)\"\n"
        f"{_LINE}\n"
    )


def require(modules: Iterable[str]) -> None:
    """
    Raise ``ImportError`` with install instructions when any of ``modules`` is
    unavailable. Safe to call from import time, before third-party imports run.
    """
    missing_modules = missing(modules)
    if missing_modules:
        raise ImportError(install_hint(missing_modules))
