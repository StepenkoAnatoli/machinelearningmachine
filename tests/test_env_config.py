"""
Tests for the environment-driven configuration layer.

Two spellings are honoured for every setting - the canonical
``MACHINELEARNINGMACHINE_*`` and the short ``MODULE_MESH_*`` alias that the
launch scripts, older docs and ``sessions.py`` already used. These tests pin
that contract down, because "the env var did nothing" is exactly the kind of
silent misconfiguration that ends in an insecure or broken launch.
"""

import importlib

import pytest

from machinelearningmachine import env, netguard
from machinelearningmachine.server.config import (
    origins_from_env,
    url_reader_enabled_by_env,
    validate_bind_policy,
)

SUFFIX = "TOTALLY_UNRELATED_TEST_SETTING"


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for name in env.names_for(SUFFIX):
        monkeypatch.delenv(name, raising=False)
    for name in ("MACHINELEARNINGMACHINE_AUTH_TOKEN", "MODULE_MESH_AUTH_TOKEN",
                 "MACHINELEARNINGMACHINE_ALLOW_ORIGINS", "MODULE_MESH_ALLOW_ORIGINS",
                 "MACHINELEARNINGMACHINE_ENABLE_URL_READER", "MODULE_MESH_ENABLE_URL_READER"):
        monkeypatch.delenv(name, raising=False)
    yield


def test_names_for_lists_canonical_then_legacy():
    assert env.names_for(SUFFIX) == (
        f"MACHINELEARNINGMACHINE_{SUFFIX}",
        f"MODULE_MESH_{SUFFIX}",
    )
    assert "or MODULE_MESH_" in env.describe(SUFFIX)


@pytest.mark.parametrize("prefix", ["MACHINELEARNINGMACHINE_", "MODULE_MESH_"])
def test_both_prefixes_are_read(monkeypatch, prefix):
    monkeypatch.setenv(prefix + SUFFIX, "value")
    assert env.get(SUFFIX) == "value"


def test_canonical_prefix_wins_over_the_alias(monkeypatch):
    monkeypatch.setenv("MODULE_MESH_" + SUFFIX, "legacy")
    monkeypatch.setenv("MACHINELEARNINGMACHINE_" + SUFFIX, "canonical")
    assert env.get(SUFFIX) == "canonical"


@pytest.mark.parametrize("value", ["  spaced  "])
def test_whitespace_is_trimmed(monkeypatch, value):
    monkeypatch.setenv("MODULE_MESH_" + SUFFIX, value)
    assert env.get(SUFFIX) == "spaced"


def test_empty_and_missing_mean_default(monkeypatch):
    monkeypatch.setenv("MACHINELEARNINGMACHINE_" + SUFFIX, "   ")
    assert env.get(SUFFIX, "fallback") == "fallback"
    assert env.get(SUFFIX) == ""


@pytest.mark.parametrize(
    "raw,expected",
    [("1", True), ("true", True), ("YES", True), ("on", True),
     ("0", False), ("false", False), ("no", False), ("off", False)],
)
def test_flag_values_are_parsed_explicitly(monkeypatch, raw, expected):
    monkeypatch.setenv("MODULE_MESH_" + SUFFIX, raw)
    assert env.flag(SUFFIX, default=not expected) is expected


def test_empty_flag_means_unset_so_the_default_wins(monkeypatch):
    monkeypatch.setenv("MODULE_MESH_" + SUFFIX, "")
    assert env.flag(SUFFIX, default=True) is True
    assert env.flag(SUFFIX, default=False) is False


def test_garbage_flag_does_not_enable_anything(monkeypatch):
    """A typo must not turn a security-relevant feature on."""
    monkeypatch.setenv("MACHINELEARNINGMACHINE_" + SUFFIX, "maybe")
    assert env.flag(SUFFIX, default=False) is False
    assert env.flag(SUFFIX, default=True) is True


def test_string_list_splits_and_dedupes(monkeypatch):
    monkeypatch.setenv("MODULE_MESH_" + SUFFIX, "a, b ,, a   c")
    assert env.string_list(SUFFIX) == ["a", "b", "c"]


# ------------------------------------------------- the settings that use it

def test_auth_token_can_come_from_either_env_name(monkeypatch):
    monkeypatch.setenv("MODULE_MESH_AUTH_TOKEN", "a-sufficiently-long-legacy-token")
    assert validate_bind_policy("0.0.0.0", allow_public=True, auth_token=None) == []
    monkeypatch.setenv("MACHINELEARNINGMACHINE_AUTH_TOKEN", "short")
    errors = validate_bind_policy("0.0.0.0", allow_public=True)
    assert any("too short" in e for e in errors), "the canonical name must take precedence"


def test_public_bind_without_any_token_is_still_refused(monkeypatch):
    assert validate_bind_policy("0.0.0.0", allow_public=True) != []


def test_url_reader_env_flag_opt_in(monkeypatch):
    assert url_reader_enabled_by_env() is False
    monkeypatch.setenv("MODULE_MESH_ENABLE_URL_READER", "1")
    assert url_reader_enabled_by_env() is True
    monkeypatch.setenv("MACHINELEARNINGMACHINE_ENABLE_URL_READER", "false")
    assert url_reader_enabled_by_env() is False, "the canonical name must win"


def test_cors_origins_env_is_normalized(monkeypatch):
    monkeypatch.setenv(
        "MACHINELEARNINGMACHINE_ALLOW_ORIGINS",
        "https://one.example/, *, https://two.example",
    )
    assert origins_from_env() == ("https://one.example", "https://two.example")


def test_sessions_directory_honours_both_names(monkeypatch, tmp_path):
    from machinelearningmachine import sessions

    monkeypatch.setenv("MACHINELEARNINGMACHINE_SESSIONS_DIR", str(tmp_path / "canon"))
    importlib.reload(sessions)
    assert sessions.sessions_dir() == tmp_path / "canon"

    monkeypatch.delenv("MACHINELEARNINGMACHINE_SESSIONS_DIR")
    monkeypatch.setenv("MODULE_MESH_SESSIONS_DIR", str(tmp_path / "legacy"))
    importlib.reload(sessions)
    assert sessions.sessions_dir() == tmp_path / "legacy"
    importlib.reload(sessions)  # leave module state clean for other tests


def test_netguard_allowlist_reads_both_names(monkeypatch):
    from machinelearningmachine import netguard

    monkeypatch.setenv("MODULE_MESH_URL_ALLOWLIST", "docs.example.com, other.example")
    assert netguard.operator_allowlist() == ("docs.example.com", "other.example")
    assert netguard.allowed_by_operator("docs.example.com") is True
    assert netguard.allowed_by_operator("evil.example") is False

    monkeypatch.setenv("MACHINELEARNINGMACHINE_URL_ALLOWLIST", "only.example")
    assert netguard.operator_allowlist() == ("only.example",)


def test_netguard_refuses_a_private_literal_for_the_right_reason():
    """
    The address is the problem, the port is incidental: pointing the reader at
    127.0.0.1:8080 must be refused as SSRF, not as "wrong port".
    """
    with pytest.raises(netguard.UnsafeURL) as exc:
        netguard.check_url("http://127.0.0.1:8080/admin")
    assert "private or local network" in exc.value.reason
    assert "ports" not in exc.value.reason

    # A public host on a bad port still gets the port explanation.
    with pytest.raises(netguard.UnsafeURL) as exc2:
        netguard.check_url("http://example.com:8080/")
    assert "ports" in exc2.value.reason.lower()
