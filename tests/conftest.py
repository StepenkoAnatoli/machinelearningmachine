"""
Shared test fixtures - and the reason they exist.

Every test here must be hermetic: no ambient provider key, no ambient operator
configuration, no reading the machine that happens to run the suite. A developer
with ``OPENAI_API_KEY`` exported in their shell used to get a *different* result
than CI for ``test_live_provider_without_key_simulates_loudly`` - which is exactly
the kind of "works on my machine" the pinned constraints file exists to prevent.

Tests that *want* an environment variable set still do it explicitly with
``monkeypatch.setenv``; those calls happen after this fixture, so they win.
"""

from __future__ import annotations

import pytest

#: Anything the code reads from the environment to decide whether to dial out,
#: which provider to use, or where transcripts live.
AMBIENT_VARS = (
    "OPENAI_API_KEY",
    "OPENAI_BASE_URL",
    "ANTHROPIC_API_KEY",
    "MODULE_MESH_SESSIONS_DIR",
    "MACHINELEARNINGMACHINE_SESSIONS_DIR",
    "MACHINELEARNINGMACHINE_AUTH_TOKEN",
    "MODULE_MESH_AUTH_TOKEN",
    "MACHINELEARNINGMACHINE_ENABLE_URL_READER",
    "MODULE_MESH_ENABLE_URL_READER",
    "MACHINELEARNINGMACHINE_ALLOW_ORIGINS",
    "MODULE_MESH_ALLOW_ORIGINS",
    "MACHINELEARNINGMACHINE_URL_ALLOWLIST",
    "MODULE_MESH_URL_ALLOWLIST",
    "MACHINELEARNINGMACHINE_RUN_TIMEOUT",
    "MODULE_MESH_RUN_TIMEOUT",
    "MACHINELEARNINGMACHINE_MAX_SESSIONS",
    "MODULE_MESH_MAX_SESSIONS",
    "MACHINELEARNINGMACHINE_SESSION_TTL",
    "MODULE_MESH_SESSION_TTL",
    "MACHINELEARNINGMACHINE_MAX_QUEUED",
    "MODULE_MESH_MAX_QUEUED",
    "MACHINELEARNINGMACHINE_ALLOW_INSECURE_PROVIDER_URLS",
    "MODULE_MESH_ALLOW_INSECURE_PROVIDER_URLS",
)


@pytest.fixture(autouse=True)
def hermetic_environment(monkeypatch, tmp_path):
    """Strip ambient configuration and point the transcript store at a temp dir."""
    for name in AMBIENT_VARS:
        monkeypatch.delenv(name, raising=False)
    # Never let a test suite write into the developer's real ~/.module_mesh.
    monkeypatch.setenv("MACHINELEARNINGMACHINE_SESSIONS_DIR", str(tmp_path / "sessions"))
    yield
