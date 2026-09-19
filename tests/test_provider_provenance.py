"""
Tests for output provenance: what was simulated, what was live, what failed.

The audit's product-level complaint was that the simulator wrote "All assertions
should PASS" and "production-ready" about code nothing had ever executed, and
that a provider returning HTTP 500 produced a normal-looking agent message.
These tests pin the opposite behaviour in place.
"""

import asyncio
import json

import pytest

from machinelearningmachine.agents.base import BaseAgent, safe_avatar, safe_color
from machinelearningmachine.agents.copilot import CopilotAgent
from machinelearningmachine.agents.providers import (
    SIMULATION_NOTICE,
    AnthropicProvider,
    MockLLMProvider,
    OpenAIProvider,
    ProviderError,
)
from machinelearningmachine.mesh import AgentMesh
from machinelearningmachine.protocol.message import MessageType


class DeadProvider:
    """A 'live' provider that raises the way a failing HTTP client should."""

    is_simulated = False
    label = "TestLLM"
    fallback_to_mock = True

    def __init__(self, error):
        self._error = error

    async def generate(self, **kwargs):
        raise self._error


class ExplodingProvider(DeadProvider):
    """A provider with a bug in it (not a ProviderError)."""

    is_simulated = False
    label = "TestLLM"

    async def generate(self, **kwargs):
        raise RuntimeError("socket exploded")


# ----------------------------------------------------------- the simulator

def test_mock_provider_labels_every_reply():
    provider = MockLLMProvider()
    out = asyncio.run(provider.generate(
        system_prompt="sys", messages=[{"role": "user", "content": "build a rate limiter"}],
        agent_role="Code Synthesis", agent_name="GitHub Copilot", task_context="build a rate limiter",
    ))
    assert out.startswith(SIMULATION_NOTICE.strip()[:40].split("\n")[0])
    assert "Simulated output" in out
    assert "no model API was called" in out


@pytest.mark.parametrize("name", ["Arena AI", "GitHub Copilot", "Claude", "GPT", "Custom thing"])
def test_simulated_text_never_claims_verification(name):
    out = asyncio.run(MockLLMProvider().generate(
        system_prompt="sys",
        messages=[{"role": "user", "content": "Design an auth middleware with a cache and tests"}],
        agent_role="reviewer", agent_name=name,
        task_context="Design an auth middleware with a cache and tests",
    )).lower()
    forbidden = [
        "all assertions should pass",
        "production-ready implementation",
        "consensus status: ✅ approved",
        "test coverage validates",
        "security and error handling satisfy production criteria",
        "verified constraints",
    ]
    for phrase in forbidden:
        assert phrase not in out, f"the simulator must not claim: {phrase!r}"


def test_simulator_still_produces_useful_content():
    """Honest, but not useless: the draft code and the test names are still there."""
    out = asyncio.run(MockLLMProvider().generate(
        system_prompt="sys", messages=[{"role": "user", "content": "implement a token bucket rate limiter"}],
        agent_role="code", agent_name="GitHub Copilot", task_context="implement a token bucket rate limiter",
    ))
    assert "```python" in out
    assert "TokenBucketRateLimiter" in out
    assert "not yet verified" in out


def test_mock_provider_is_flagged_as_simulated_and_live_providers_are_not():
    assert MockLLMProvider.is_simulated is True
    assert OpenAIProvider.is_simulated is False
    assert AnthropicProvider.is_simulated is False


# ----------------------------------------------------------- provider failures

def test_provider_error_carries_structured_detail():
    err = ProviderError("OpenAI", "the API answered HTTP 500", detail="boom", status_code=500, retryable=True)
    assert err.provider == "OpenAI"
    assert err.status_code == 500
    assert err.retryable is True
    assert "HTTP 500" in str(err)


def test_agent_falls_back_to_the_simulator_and_says_so():
    agent = BaseAgent(
        agent_id="t", name="Test Agent", role="tester", system_prompt="...",
        provider=DeadProvider(ProviderError("TestLLM", "the API answered HTTP 503", status_code=503)),
    )
    msg = asyncio.run(agent.generate_response(prompt="design a cache"))
    assert "TestLLM failed" in msg.content
    assert "not** an answer from TestLLM" in msg.content
    assert "Simulated output" in msg.content          # clearly the simulator now
    assert msg.metadata["simulated"] is True
    assert msg.metadata["provider_error"] == "the API answered HTTP 503"
    assert msg.metadata["provider_status_code"] == 503
    assert agent.status == "degraded"


def test_no_fallback_mode_propagates_the_provider_error():
    agent = BaseAgent(
        agent_id="t", name="Test Agent", role="tester", system_prompt="...",
        provider=DeadProvider(ProviderError("TestLLM", "the API answered HTTP 429", status_code=429)),
    )
    agent.provider.fallback_to_mock = False
    with pytest.raises(ProviderError):
        asyncio.run(agent.generate_response(prompt="design a cache"))
    assert agent.status == "error"


def test_unexpected_provider_bug_is_also_marked_not_hidden():
    agent = BaseAgent(
        agent_id="t", name="Test Agent", role="tester", system_prompt="...",
        provider=ExplodingProvider(RuntimeError("x")),
    )
    msg = asyncio.run(agent.generate_response(prompt="design a cache"))
    assert "failed" in msg.content
    assert msg.metadata["simulated"] is True
    assert "RuntimeError" in msg.metadata["provider_error"]


def test_successful_live_provider_answer_is_not_marked_simulated():
    class OkProvider:
        is_simulated = False
        label = "TestLLM"
        fallback_to_mock = True

        async def generate(self, **kwargs):
            return "A real answer from the configured model."

    agent = BaseAgent(agent_id="t", name="Test Agent", role="r", system_prompt="s", provider=OkProvider())
    msg = asyncio.run(agent.generate_response(prompt="hello there"))
    assert msg.content == "A real answer from the configured model."
    assert msg.metadata["simulated"] is False
    assert "provider_error" not in msg.metadata


def test_openai_provider_raises_instead_of_returning_an_error_string(monkeypatch):
    """The old behaviour returned '[Error calling OpenAI API: HTTP 500]' as if it were an answer."""

    class FakeResp:
        status = 500

        async def text(self):
            return "upstream exploded"

    class FakeCtx:
        async def __aenter__(self):
            return FakeResp()

        async def __aexit__(self, *a):
            return False

    class FakeSession:
        def __init__(self, *a, **kw):
            pass

        def post(self, *a, **kw):
            return FakeCtx()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

    class FakeAiohttp:
        ClientSession = FakeSession
        ClientTimeout = staticmethod(lambda **kw: None)
        ClientError = Exception

    monkeypatch.setattr("machinelearningmachine.agents.providers._aiohttp", lambda: FakeAiohttp)
    provider = OpenAIProvider(api_key="sk-test-key-value")
    with pytest.raises(ProviderError) as exc:
        asyncio.run(provider.generate(system_prompt="s", messages=[{"role": "user", "content": "hi"}],
                                       agent_role="r", agent_name="GPT"))
    assert "HTTP 500" in exc.value.reason
    assert exc.value.status_code == 500


def test_anthropic_provider_raises_on_unreachable_api(monkeypatch):
    class FakeSession:
        def __init__(self, *a, **kw):
            pass

        def post(self, *a, **kw):
            raise FakeAiohttp.ClientError("connection refused")

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

    class FakeAiohttp:
        ClientSession = FakeSession
        ClientTimeout = staticmethod(lambda **kw: None)
        ClientError = Exception

    monkeypatch.setattr("machinelearningmachine.agents.providers._aiohttp", lambda: FakeAiohttp)
    provider = AnthropicProvider(api_key="sk-ant-test-value")
    with pytest.raises(ProviderError) as exc:
        asyncio.run(provider.generate(system_prompt="s", messages=[{"role": "user", "content": "hi"}],
                                       agent_role="r", agent_name="Claude"))
    assert "could not reach" in exc.value.reason
    assert exc.value.retryable is True


def test_live_provider_without_key_simulates_loudly():
    """Zero-config mode still works, but announces that it is not OpenAI."""
    provider = OpenAIProvider(api_key=None, base_url="https://api.openai.com/v1")
    out = asyncio.run(provider.generate(system_prompt="s", messages=[{"role": "user", "content": "hi"}],
                                        agent_role="r", agent_name="GPT"))
    assert "not an OpenAI answer" in out
    assert "Simulated output" in out
    strict = OpenAIProvider(api_key=None, base_url="https://api.openai.com/v1", fallback_to_mock=False)
    with pytest.raises(ProviderError):
        asyncio.run(strict.generate(system_prompt="s", messages=[{"role": "user", "content": "hi"}],
                                     agent_role="r", agent_name="GPT"))


# --------------------------------------------------------------- the mesh

def test_mesh_messages_carry_provider_metadata():
    mesh = AgentMesh()
    msgs = asyncio.run(mesh.talk_p2p("arena-ai", "copilot", "Design a rate limiter in Python", turns=2))
    assert msgs
    for m in msgs:
        assert m.metadata["provider"] == "MockLLMProvider"
        assert m.metadata["simulated"] is True
    # and it survives serialization (what the UI and saved sessions see)
    dumped = msgs[0].to_dict()
    assert dumped["metadata"]["simulated"] is True
    assert json.loads(mesh.export_json())[0]["metadata"]["simulated"] is True


def test_agents_report_their_provider_kind():
    mesh = AgentMesh()
    for agent in mesh.list_agents():
        assert agent["provider_kind"] == "simulated"
    mesh.gpt.provider = OpenAIProvider(api_key="sk-some-long-enough-key")
    assert mesh.get_agent("gpt").to_dict()["provider_kind"] == "live"
    assert mesh.get_agent("gpt").to_dict()["model"] == "gpt-4o"


def test_markdown_export_records_provenance():
    from machinelearningmachine.protocol.bus import MessageBus

    agent = BaseAgent(agent_id="t", name="Test Agent", role="r", system_prompt="s", bus=MessageBus())
    asyncio.run(agent.generate_response(prompt="build a cache"))
    text = agent.bus.export_markdown()
    assert "Simulated output" in text


# ------------------------------------------------- display-safety validators

@pytest.mark.parametrize("bad", ['#fff" onmouseover="x', "", None, "javascript:alert(1)", "#GGGGGG", "red"])
def test_unsafe_colors_fall_back_to_the_default(bad):
    assert safe_color(bad, "#123456") == "#123456"


@pytest.mark.parametrize("good", ["#06b6d4", "#FFFFFF"])
def test_hex_colors_survive(good):
    assert safe_color(good) == good


@pytest.mark.parametrize("bad", ['<img src=x>', '🤖" onload="alert(1)', "", "   ", None, 12])
def test_avatars_are_short_plain_text(bad):
    cleaned = safe_avatar(bad, "🤖")
    assert cleaned == "🤖"
    assert "<" not in cleaned and '"' not in cleaned


def test_agent_sanitises_markup_supplied_at_construction():
    agent = CopilotAgent(
        agent_id="x", name="X", role="r", system_prompt="s",
        color='#effefe" onmouseover="alert(1)', avatar='<img src=x onerror=alert(1)>',
    )
    assert agent.color == "#6366f1"
    assert "<" not in agent.avatar and '"' not in agent.avatar


def test_message_type_enum_unchanged_for_clients():
    assert MessageType.PROPOSAL.value == "proposal"
