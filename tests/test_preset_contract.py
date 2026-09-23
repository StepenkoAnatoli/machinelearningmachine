"""
Drift lock: the server half of the module-preset contract.

``static/presets.js`` validates presets client-side so the Add Custom Module
form can refuse a preset the server would refuse, before the user ever hits
Register. That promise is only worth something while both sides agree, and
nothing in the codebase enforces agreement by construction — ``presets.js``
restates the server's limits as literals.

So this file (and its JS sibling, ``tests/js/preset-contract.test.mjs``) each
restate the mirror table from the design spec *literally*, the way a reader of
the spec would:

    docs/superpowers/specs/2026-09-23--agent-presets-design.md §5.1

This file asserts the *server* equals that restatement; the JS file asserts
``MLMPresets.LIMITS`` and ``BUILTINS`` do. Change one side without the other and
one of the two files goes red — which is the entire point. Neither file imports
the other's table, deliberately: a shared fixture would move along with a
change instead of failing.

The authoritative source is ``AddAgentRequest`` in ``server/app.py``.
"""

from __future__ import annotations

import re

import pytest
from pydantic import ValidationError

from machinelearningmachine.server.app import (
    AGENT_ID_PATTERN,
    MAX_AGENT_ID_LENGTH,
    MAX_NAME_LENGTH,
    MAX_ROLE_LENGTH,
    MAX_SYSTEM_PROMPT_LENGTH,
    RESERVED_AGENT_IDS,
    AddAgentRequest,
)

# --------------------------------------------------------------------------
# The mirror table, restated literally from spec §5.1. If a test below fails
# because this block changed, the spec changed — update the spec first, then
# both contract files and both implementations, in one commit.
# --------------------------------------------------------------------------

TABLE_AGENT_ID_MIN = 3  # effective: the pattern needs a 3rd char; the field says 2
TABLE_AGENT_ID_MAX = 50
TABLE_AGENT_ID_PATTERN = r"^[a-z0-9][a-z0-9-_]{1,48}[a-z0-9]$"
TABLE_RESERVED = {"system", "broadcast", "all", "*", "api", "admin", "root"}
TABLE_NAME_MIN, TABLE_NAME_MAX = 1, 100
TABLE_ROLE_MIN, TABLE_ROLE_MAX = 1, 200
TABLE_SYSTEM_PROMPT_MIN, TABLE_SYSTEM_PROMPT_MAX = 10, 2000
TABLE_COLOR_PATTERN = r"^#[0-9a-fA-F]{6}$"
TABLE_AVATAR_MAX = 8  # code points, matching Array.from(...).length on the client


def _constraints(field_name: str) -> tuple[int | None, int | None]:
    """(min_length, max_length) recorded on an ``AddAgentRequest`` field."""
    minimum = maximum = None
    for meta in AddAgentRequest.model_fields[field_name].metadata:
        if hasattr(meta, "min_length"):
            minimum = meta.min_length
        if hasattr(meta, "max_length"):
            maximum = meta.max_length
    return minimum, maximum


def _payload(**over: str) -> dict[str, str]:
    payload = {
        "agent_id": "security-auditor",
        "name": "Security Auditor",
        "role": "Application Security Reviewer",
        "system_prompt": "You review code and configs for security issues.",
        "color": "#0ea5e9",
        "avatar": "🛡️",
    }
    payload.update(over)
    return payload


# ---------------------------------------------------------------- constants


def test_server_constants_match_the_table():
    assert MAX_AGENT_ID_LENGTH == TABLE_AGENT_ID_MAX
    assert MAX_NAME_LENGTH == TABLE_NAME_MAX
    assert MAX_ROLE_LENGTH == TABLE_ROLE_MAX
    assert MAX_SYSTEM_PROMPT_LENGTH == TABLE_SYSTEM_PROMPT_MAX
    assert RESERVED_AGENT_IDS == TABLE_RESERVED


def test_request_fields_match_the_table():
    assert _constraints("agent_id") == (2, TABLE_AGENT_ID_MAX), (
        "agent_id's field floor is 2, but the pattern needs 3 — the effective "
        "minimum this table documents is 3 (checked behaviourally below)"
    )
    assert _constraints("name") == (TABLE_NAME_MIN, TABLE_NAME_MAX)
    assert _constraints("role") == (TABLE_ROLE_MIN, TABLE_ROLE_MAX)
    assert _constraints("system_prompt") == (TABLE_SYSTEM_PROMPT_MIN, TABLE_SYSTEM_PROMPT_MAX)
    assert _constraints("avatar") == (None, TABLE_AVATAR_MAX)
    color_meta = [m for m in AddAgentRequest.model_fields["color"].metadata if hasattr(m, "pattern")]
    assert [m.pattern for m in color_meta] == [TABLE_COLOR_PATTERN]


def test_the_server_pattern_is_the_table_pattern_plus_one_unreachable_alternative():
    """
    ``AGENT_ID_PATTERN`` carries ``|^[a-z0-9]$``: a 1-char alternative that the
    field's ``min_length=2`` makes unreachable (documented drift, out of scope
    to fix here). Everything the field lets through must therefore match the
    table pattern exactly — checked on probes rather than by string equality,
    so tidying the server regex does not fail this file spuriously.
    """
    table_re = re.compile(TABLE_AGENT_ID_PATTERN)
    probes = [
        "a", "ab", "abc", "a-b", "a_b", "9ab", "ab9", "a9b",
        "-abc", "abc-", "_abc", "abc_", "my agent", "ABC", "aBc",
        "a" * 49 + "b", "a" * 50 + "b", "abé", "a--b", "a__b", "a-_-b",
    ]
    for probe in probes:
        if len(probe) < 2:  # the field floor: below it the server rejects first
            continue
        assert bool(AGENT_ID_PATTERN.match(probe)) == bool(table_re.match(probe)), (
            f"server and table disagree about agent_id {probe!r}"
        )


# ---------------------------------------------------------------- behaviour


def test_agent_id_boundaries_bite():
    assert AddAgentRequest(**_payload(agent_id="abc")).agent_id == "abc"  # 3 is the effective floor
    assert AddAgentRequest(**_payload(agent_id="a" + "b" * 48 + "c")).agent_id  # 50
    with pytest.raises(ValidationError):
        AddAgentRequest(**_payload(agent_id="a"))  # field min_length=2
    with pytest.raises(ValidationError):
        AddAgentRequest(**_payload(agent_id="ab"))  # 2 chars: rejected by the pattern
    with pytest.raises(ValidationError):
        AddAgentRequest(**_payload(agent_id="a" + "b" * 49 + "c"))  # 51


def test_agent_id_is_normalised_then_checked_against_the_reserved_set():
    assert AddAgentRequest(**_payload(agent_id="  Security-Auditor  ")).agent_id == "security-auditor"
    for reserved in sorted(TABLE_RESERVED):
        with pytest.raises(ValidationError):
            AddAgentRequest(**_payload(agent_id=reserved))
    with pytest.raises(ValidationError):
        AddAgentRequest(**_payload(agent_id="  System "))


def test_name_role_and_prompt_reject_whitespace_only_and_respect_lengths():
    for field in ("name", "role", "system_prompt"):
        with pytest.raises(ValidationError):
            AddAgentRequest(**_payload(**{field: "   "}))
    assert AddAgentRequest(**_payload(name="n" * TABLE_NAME_MAX)).name
    with pytest.raises(ValidationError):
        AddAgentRequest(**_payload(name="n" * (TABLE_NAME_MAX + 1)))
    assert AddAgentRequest(**_payload(role="r" * TABLE_ROLE_MAX)).role
    with pytest.raises(ValidationError):
        AddAgentRequest(**_payload(role="r" * (TABLE_ROLE_MAX + 1)))
    assert AddAgentRequest(**_payload(system_prompt="p" * TABLE_SYSTEM_PROMPT_MIN)).system_prompt
    with pytest.raises(ValidationError):
        AddAgentRequest(**_payload(system_prompt="p" * (TABLE_SYSTEM_PROMPT_MIN - 1)))
    assert AddAgentRequest(**_payload(system_prompt="p" * TABLE_SYSTEM_PROMPT_MAX)).system_prompt
    with pytest.raises(ValidationError):
        AddAgentRequest(**_payload(system_prompt="p" * (TABLE_SYSTEM_PROMPT_MAX + 1)))


def test_color_and_avatar_gates_bite():
    assert AddAgentRequest(**_payload(color="#0EA5E9")).color == "#0EA5E9"  # case-insensitive
    for bad in ("red", "#0ea5e", "#0ea5e9a", "0ea5e9", "#0ea5e9 "):
        with pytest.raises(ValidationError):
            AddAgentRequest(**_payload(color=bad))
    # Code points, not bytes — the same count Array.from().length gives: "🛡️"
    # is a shield plus a variation selector.
    assert len("🛡️") == 2
    assert AddAgentRequest(**_payload(avatar="🛡️")).avatar == "🛡️"
    assert AddAgentRequest(**_payload(avatar="🧩" * 8)).avatar
    with pytest.raises(ValidationError):
        AddAgentRequest(**_payload(avatar="🧩" * 9))
