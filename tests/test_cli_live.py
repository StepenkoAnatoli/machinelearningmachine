"""
The CLI's contract with the user's wallet and the user's time.

Two findings live here.

1. README claimed ``OPENAI_API_KEY`` was "Used by `run` mode". It was not - ``
   AgentMesh`` hands every agent the simulator, and nothing in the CLI ever built a
   live provider. So the documented way to get real answers from a terminal did
   nothing, while the *undesigned* fallback in ``OpenAIProvider`` (read the
   environment when no key was passed) meant the same variable silently mattered in
   other paths. A key in your shell must never be able to turn a demo into a paid
   call: ``--live`` is now how, explicitly, and it says what it is about to send and
   where before it does.
2. ``--no-delay`` was honoured by ``p2p`` and ``pipeline`` only. ``debate`` and
   ``hub`` kept sleeping, because the CLI built those topologies by hand and the
   mesh methods had no delay parameter to pass.
"""

import asyncio
from types import SimpleNamespace

import pytest

from machinelearningmachine import cli
from machinelearningmachine.agents.providers import AnthropicProvider, MockLLMProvider, OpenAIProvider
from machinelearningmachine.cli import _apply_live_providers
from machinelearningmachine.mesh import AgentMesh
from machinelearningmachine.protocol.message import Message


def _args(**overrides):
    base = {"live": None, "base_url": None, "provider_timeout": 60.0}
    base.update(overrides)
    return SimpleNamespace(**base)


def _mesh():
    return AgentMesh()


# ------------------------------------------------------- nothing dials out by default

def test_no_flag_means_no_provider_from_the_environment(monkeypatch):
    """The money rule: an ambient key must not cause an outbound call."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-ambient-key-that-must-stay-put")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-ambient")
    mesh = _mesh()
    asyncio.run(_apply_live_providers(mesh, _args()))
    assert all(isinstance(a.provider, MockLLMProvider) for a in mesh.agents.values())


# ------------------------------------------------------------------- --live wiring

def test_live_openai_wires_the_two_coding_agents(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-" + "e" * 30)
    mesh = _mesh()
    asyncio.run(_apply_live_providers(mesh, _args(live="openai")))
    assert isinstance(mesh.gpt.provider, OpenAIProvider)
    assert mesh.copilot.provider is mesh.gpt.provider
    assert mesh.gpt.provider.api_key == "sk-" + "e" * 30
    assert mesh.gpt.provider.allow_env_key is False, "the CLI passes the key explicitly"
    # The architecture's roles stay intact: Claude/Arena are untouched by --live openai.
    assert isinstance(mesh.claude.provider, MockLLMProvider)
    assert isinstance(mesh.arena_ai.provider, MockLLMProvider)


def test_live_both_covers_the_rest(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-" + "e" * 30)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-" + "f" * 30)
    mesh = _mesh()
    asyncio.run(_apply_live_providers(mesh, _args(live="both")))
    assert isinstance(mesh.claude.provider, AnthropicProvider)
    assert isinstance(mesh.arena_ai.provider, AnthropicProvider)
    assert isinstance(mesh.gpt.provider, OpenAIProvider)


def test_live_without_a_key_is_refused_with_an_action(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    mesh = _mesh()
    with pytest.raises(ValueError) as exc:
        asyncio.run(_apply_live_providers(mesh, _args(live="openai")))
    text = str(exc.value)
    assert "OPENAI_API_KEY" in text and "--base-url" in text


def test_live_against_a_local_backend_needs_no_key(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    mesh = _mesh()
    asyncio.run(_apply_live_providers(mesh, _args(live="openai", base_url="http://localhost:11434/v1")))
    assert mesh.gpt.provider.base_url == "http://localhost:11434/v1"


def test_anthropic_live_requires_its_own_key(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-" + "e" * 30)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    mesh = _mesh()
    with pytest.raises(ValueError) as exc:
        asyncio.run(_apply_live_providers(mesh, _args(live="anthropic")))
    assert "ANTHROPIC_API_KEY" in str(exc.value)


@pytest.mark.parametrize("value", [0, -5, 5000, "soon"])
def test_a_nonsense_timeout_is_rejected_instead_of_silently_defaulted(monkeypatch, value):
    """``float(x or default)`` would have turned 0 back into 60."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-" + "e" * 30)
    mesh = _mesh()
    with pytest.raises(ValueError):
        asyncio.run(_apply_live_providers(mesh, _args(live="openai", provider_timeout=value)))


@pytest.mark.parametrize("argv,env,expected", [
    ([], {}, 180.0),
    ([], {"MACHINELEARNINGMACHINE_RUN_TIMEOUT": "45"}, 45.0),
    ([], {"MODULE_MESH_RUN_TIMEOUT": "45"}, 45.0),
    (["--run-timeout", "30"], {"MACHINELEARNINGMACHINE_RUN_TIMEOUT": "45"}, 30.0),
])
def test_numeric_settings_follow_flag_then_environment_then_default(monkeypatch, argv, env, expected):
    config = _serve_config(monkeypatch, ["serve", *argv], env)
    assert config.run_timeout == expected


@pytest.mark.parametrize("name,value", [
    ("MACHINELEARNINGMACHINE_RUN_TIMEOUT", "5m"),
    ("MACHINELEARNINGMACHINE_SESSION_TTL", "nan"),
    ("MACHINELEARNINGMACHINE_SESSION_TTL", "-3"),
    ("MACHINELEARNINGMACHINE_MAX_SESSIONS", "0"),
    ("MACHINELEARNINGMACHINE_MAX_SESSIONS", "inf"),
])
def test_an_unusable_number_stops_startup_rather_than_ignoring_it(monkeypatch, name, value):
    """
    A typo in a boolean cannot enable anything, so junk there is ignored. A typo in
    a limit would leave a server running with a limit nobody chose, so it is an error.
    """
    with pytest.raises(SystemExit) as exc:
        _serve_config(monkeypatch, ["serve"], {name: value})
    assert exc.value.code == 2


def test_the_rejection_says_which_setting_was_wrong(monkeypatch, capsys):
    with pytest.raises(SystemExit):
        _serve_config(monkeypatch, ["serve"], {"MACHINELEARNINGMACHINE_SESSION_TTL": "overnight"})
    err = capsys.readouterr().err
    assert "SESSION_TTL" in err and "overnight" in err, err


def test_session_ttl_env_reaches_the_registry(monkeypatch):
    config = _serve_config(monkeypatch, ["serve"], {"MACHINELEARNINGMACHINE_SESSION_TTL": "7"})
    assert config.session_idle_ttl == 420.0


def test_the_insecure_provider_url_opt_out_can_come_from_the_environment(monkeypatch):
    assert _serve_config(monkeypatch, ["serve"], {}).allow_insecure_provider_urls is False
    assert _serve_config(
        monkeypatch, ["serve"], {"MACHINELEARNINGMACHINE_ALLOW_INSECURE_PROVIDER_URLS": "1"}
    ).allow_insecure_provider_urls is True
    # Same rule as every other flag here: a value that is not truthy does not enable it.
    assert _serve_config(
        monkeypatch, ["serve"], {"MACHINELEARNINGMACHINE_ALLOW_INSECURE_PROVIDER_URLS": "0"}
    ).allow_insecure_provider_urls is False


def _serve_config(monkeypatch, argv, env):
    """
    Run ``serve`` up to the point where it would bind a socket.

    Returns the config it built, or re-raises the CLI's own SystemExit so a startup
    refusal is asserted as the CLI's behaviour rather than as a missing key here.
    """
    # Start from a known-empty configuration, then apply only what the test asked for.
    for name in ("MACHINELEARNINGMACHINE_AUTH_TOKEN", "MODULE_MESH_AUTH_TOKEN",
                 "MACHINELEARNINGMACHINE_SESSIONS_DIR", "MODULE_MESH_SESSIONS_DIR",
                 "MACHINELEARNINGMACHINE_ALLOW_INSECURE_PROVIDER_URLS",
                 "MODULE_MESH_ALLOW_INSECURE_PROVIDER_URLS",
                 "MACHINELEARNINGMACHINE_RUN_TIMEOUT", "MACHINELEARNINGMACHINE_MAX_SESSIONS",
                 "MACHINELEARNINGMACHINE_SESSION_TTL", "MODULE_MESH_RUN_TIMEOUT",
                 "MODULE_MESH_MAX_SESSIONS", "MODULE_MESH_SESSION_TTL"):
        monkeypatch.delenv(name, raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)

    captured = {}

    def fake_uvicorn_run(app, **kwargs):
        captured["config"] = app.state.config
        captured["serve"] = kwargs
        raise SystemExit(0)

    import uvicorn

    monkeypatch.setattr(uvicorn, "run", fake_uvicorn_run)
    monkeypatch.setattr("sys.argv", ["module-mesh", *argv])
    with pytest.raises(SystemExit) as excinfo:
        cli.main()
    if "config" not in captured:
        raise excinfo.value
    _serve_config.serve_kwargs = captured["serve"]
    return captured["config"]


def test_live_is_announced_before_it_runs(monkeypatch, capsys):
    """A call that costs money and leaves the machine says so, once, up front."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-" + "e" * 30)
    mesh = _mesh()
    asyncio.run(_apply_live_providers(mesh, _args(live="openai")))
    printed = capsys.readouterr().out
    assert "--live openai" in printed
    assert "leaves this machine" in printed
    assert "sk-" not in printed, "the key is used, never echoed"


# ------------------------------------------------------------------ --no-delay

@pytest.mark.parametrize("topology,argument", [
    ("debate", "inter_turn_delay"),
    ("hub", "inter_step_delay"),
    ("pipeline", "inter_step_delay"),
    ("p2p", "inter_turn_delay"),
])
def test_no_delay_reaches_every_topology(monkeypatch, topology, argument):
    """
    ``--no-delay`` used to be honoured for two of the four, because the CLI built
    those topologies by hand. The delay is now an argument of the mesh methods, so
    there is one path for both the CLI and the API.
    """
    seen = {}

    async def capture(self, *args, **kwargs):
        seen.update(kwargs)
        return [Message(sender_id="copilot", sender_name="GitHub Copilot", content="ok")]

    method = {
        "debate": "run_debate",
        "hub": "run_hub_and_spoke",
        "pipeline": "run_pipeline",
        "p2p": "talk_p2p",
    }[topology]
    monkeypatch.setattr(AgentMesh, method, capture)

    args = SimpleNamespace(
        topology=topology, agent_a="arena-ai", agent_b="copilot", agent_ids=None,
        turns=2, prompt="build a rate limiter", no_delay=True, export=None, speak=False,
        live=None, base_url=None, provider_timeout=60.0,
    )
    asyncio.run(cli.execute_cli_run(args))
    assert seen[argument] == 0.0, seen


def test_delay_is_left_to_the_topology_when_not_disabled(monkeypatch):
    seen = {}

    async def capture(self, *args, **kwargs):
        seen.update(kwargs)
        return []

    monkeypatch.setattr(AgentMesh, "run_debate", capture)
    args = SimpleNamespace(
        topology="debate", agent_a="arena-ai", agent_b="copilot", agent_ids=None,
        turns=2, prompt="build a rate limiter", no_delay=False, export=None, speak=False,
        live=None, base_url=None, provider_timeout=60.0,
    )
    asyncio.run(cli.execute_cli_run(args))
    assert seen["inter_turn_delay"] is None, "None means 'use the topology's own default'"


# --------------------------------------------------------------------- argparse

def test_serve_flags_reach_the_server_config(monkeypatch):
    config = _serve_config(monkeypatch, [
        "serve", "--host", "127.0.0.1", "--port", "8123",
        "--run-timeout", "45", "--max-sessions", "8", "--session-ttl", "30",
        "--allow-insecure-provider-urls",
    ], {})
    assert (config.run_timeout, config.max_sessions, config.session_idle_ttl) == (45.0, 8, 1800.0)
    assert config.allow_insecure_provider_urls is True
    assert _serve_config.serve_kwargs["port"] == 8123


def test_a_zero_run_timeout_is_refused_with_an_action(monkeypatch, capsys):
    """Clamping 0 up to 5 silently overrules the operator; saying so does not."""
    with pytest.raises(SystemExit) as exc:
        _serve_config(monkeypatch, ["serve", "--run-timeout", "0"], {})
    assert exc.value.code == 2
    assert "--run-timeout must be at least 5" in capsys.readouterr().err


@pytest.mark.parametrize("command,flags", [
    ("run", ("--live", "--base-url", "--provider-timeout", "--no-delay", "--speak", "--export")),
    ("serve", ("--run-timeout", "--allow-insecure-provider-urls", "--max-sessions",
               "--session-ttl", "--strict-provider-errors", "--enable-url-reader")),
])
def test_documented_flags_exist(monkeypatch, capsys, command, flags):
    """
    A flag that is not in the parser is a flag that silently does nothing - which
    is how --no-delay ended up ignored by two of the four topologies.
    """
    monkeypatch.setattr("sys.argv", ["module-mesh", command, "--help"])
    with pytest.raises(SystemExit):
        cli.main()
    text = capsys.readouterr().out
    for flag in flags:
        assert flag in text, f"{flag} missing from `{command} --help`"
