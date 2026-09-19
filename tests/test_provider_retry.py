"""
Transient provider failures: tried again, and honest about how many times.

``ProviderError.retryable`` used to be computed from the HTTP status and then
ignored, so one 429 from a rate-limited endpoint cost the turn (or, with
``--strict-provider-errors``, the whole run) - while the same endpoint a second
later would have answered. The retry policy now lives in one place
(:func:`machinelearningmachine.agents.providers.post_for_json`) and both live
providers share it, so it cannot drift between them.

What these tests pin:
* only retryable statuses are retried, and at most ``max_attempts`` times;
* the retry is what makes the answer arrive, not what hides the failure;
* a failure that persists says how hard it tried;
* a non-retryable failure (401) costs exactly one request - no hammering a
  rejected credential.
"""


import time

import pytest

from machinelearningmachine.agents.providers import (
    AnthropicProvider,
    OpenAIProvider,
    ProviderError,
)
from machinelearningmachine.mesh import AgentMesh


def fake_aiohttp(statuses, calls, body=None, exceptions=None):
    """
    A stub aiohttp whose responses are scripted per attempt.

    ``statuses`` entries are ints; entries that are Exception *instances* are
    raised instead, which is how connection resets and timeouts are simulated
    without a socket.
    """

    class FakeResp:
        def __init__(self, status):
            self.status = status

        async def text(self):
            return "upstream said: too many requests"

        async def json(self):
            if isinstance(body, Exception):
                raise body
            return body or {"choices": [{"message": {"content": "recovered answer"}}]}

    class FakeCtx:
        def __init__(self, status):
            self.status = status

        async def __aenter__(self):
            return FakeResp(self.status)

        async def __aexit__(self, *a):
            return False

    class FakeSession:
        def __init__(self, *a, **kw):
            pass

        def post(self, url, **kwargs):
            index = len(calls)
            calls.append({"url": url, "headers": kwargs.get("headers") or {}})
            outcome = statuses[min(index, len(statuses) - 1)]
            if isinstance(outcome, Exception):
                raise outcome
            return FakeCtx(outcome)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

    class FakeAiohttp:
        ClientSession = FakeSession
        ClientTimeout = staticmethod(lambda **kw: None)
        ClientError = ConnectionError

    return FakeAiohttp


def _provider(cls=OpenAIProvider, **kwargs):
    kwargs.setdefault("api_key", "sk-" + "k" * 30)
    kwargs.setdefault("retry_backoff", 0.0)      # a test must not sleep to prove a sleep
    return cls(**kwargs)


async def test_a_retryable_status_is_tried_again_and_the_answer_arrives(monkeypatch):
    calls = []
    monkeypatch.setattr(
        "machinelearningmachine.agents.providers._aiohttp",
        lambda: fake_aiohttp([429, 200], calls),
    )
    provider = _provider()
    out = await provider.generate(
        system_prompt="s", messages=[{"role": "user", "content": "hi"}],
        agent_role="r", agent_name="GPT",
    )
    assert out == "recovered answer"
    assert len(calls) == 2, "one retry, then success"


async def test_attempts_are_reported_on_the_error_that_gives_up(monkeypatch):
    calls = []
    monkeypatch.setattr(
        "machinelearningmachine.agents.providers._aiohttp",
        lambda: fake_aiohttp([503, 503, 503], calls),
    )
    provider = _provider(max_attempts=2)
    with pytest.raises(ProviderError) as exc:
        await provider.generate(
            system_prompt="s", messages=[{"role": "user", "content": "hi"}],
            agent_role="r", agent_name="GPT",
        )
    err = exc.value
    assert err.attempts == 2
    assert "after 2 attempts" in err.reason
    # ``str(exc)`` is what reaches the toast; ``reason`` is what reaches the transcript.
    assert err.reason in str(err), f"{str(err)!r} does not carry {err.reason!r}"
    assert len(calls) == 2, "max_attempts is a total, not a per-failure budget"


async def test_a_rejected_credential_is_not_retried(monkeypatch):
    """401 is permanent: retrying it only makes a bad key more visible."""
    calls = []
    monkeypatch.setattr(
        "machinelearningmachine.agents.providers._aiohttp",
        lambda: fake_aiohttp([401, 200], calls),
    )
    provider = _provider()
    with pytest.raises(ProviderError) as exc:
        await provider.generate(
            system_prompt="s", messages=[{"role": "user", "content": "hi"}],
            agent_role="r", agent_name="GPT",
        )
    assert exc.value.retryable is False
    assert exc.value.attempts == 1
    assert "attempts" not in exc.value.reason
    assert len(calls) == 1, "a 401 is not a reason to knock twice"


async def test_connection_resets_are_treated_as_transient(monkeypatch):
    calls = []
    monkeypatch.setattr(
        "machinelearningmachine.agents.providers._aiohttp",
        lambda: fake_aiohttp([ConnectionError("reset by peer"), 200], calls),
    )
    provider = _provider()
    out = await provider.generate(
        system_prompt="s", messages=[{"role": "user", "content": "hi"}],
        agent_role="r", agent_name="GPT",
    )
    assert out == "recovered answer"
    assert len(calls) == 2


async def test_retrying_is_disabled_by_max_attempts_one(monkeypatch):
    calls = []
    monkeypatch.setattr(
        "machinelearningmachine.agents.providers._aiohttp",
        lambda: fake_aiohttp([500, 200], calls),
    )
    provider = _provider(max_attempts=1)
    with pytest.raises(ProviderError):
        await provider.generate(
            system_prompt="s", messages=[{"role": "user", "content": "hi"}],
            agent_role="r", agent_name="GPT",
        )
    assert len(calls) == 1


async def test_an_unparseable_success_body_is_not_retried(monkeypatch):
    """The request worked; replaying it would double-bill a token-limited call."""
    calls = []
    monkeypatch.setattr(
        "machinelearningmachine.agents.providers._aiohttp",
        lambda: fake_aiohttp([200], calls, body=ValueError("not json")),
    )
    provider = _provider()
    with pytest.raises(ProviderError) as exc:
        await provider.generate(
            system_prompt="s", messages=[{"role": "user", "content": "hi"}],
            agent_role="r", agent_name="GPT",
        )
    assert "not JSON" in exc.value.reason
    assert len(calls) == 1


async def test_anthropic_shares_the_policy(monkeypatch):
    calls = []
    monkeypatch.setattr(
        "machinelearningmachine.agents.providers._aiohttp",
        lambda: fake_aiohttp([429, 200], calls, body={"content": [{"type": "text", "text": "recovered"}]}),
    )
    provider = _provider(AnthropicProvider)
    out = await provider.generate(
        system_prompt="s", messages=[{"role": "user", "content": "hi"}],
        agent_role="r", agent_name="Claude",
    )
    assert out == "recovered"
    assert len(calls) == 2


async def test_the_degraded_message_says_the_provider_was_retried(monkeypatch):
    """
    Honesty rule: a fallback message must not hide that the server tried again.
    ``metadata.provider_attempts`` is what makes "we did retry" part of the record
    rather than a log line nobody reads.
    """
    calls = []
    monkeypatch.setattr(
        "machinelearningmachine.agents.providers._aiohttp",
        lambda: fake_aiohttp([502, 502, 502], calls),
    )
    mesh = AgentMesh()
    for agent in mesh.agents.values():
        agent.provider = _provider(max_attempts=3)
    transcript = await mesh.talk_p2p("arena-ai", "copilot", "build a rate limiter", turns=2)
    meta = transcript[0].metadata
    assert meta["simulated"] is True and meta["provider_error"]
    assert meta["provider_attempts"] == 3
    assert "after 3 attempts" in meta["provider_error"]


async def test_the_wait_between_attempts_actually_happens(monkeypatch):
    """A retry without a delay is a busy loop against a service that just said 429."""
    calls = []
    monkeypatch.setattr(
        "machinelearningmachine.agents.providers._aiohttp",
        lambda: fake_aiohttp([429, 200], calls),
    )
    provider = _provider(retry_backoff=0.2)
    started = time.monotonic()
    out = await provider.generate(
        system_prompt="s", messages=[{"role": "user", "content": "hi"}],
        agent_role="r", agent_name="GPT",
    )
    assert out == "recovered answer"
    assert len(calls) == 2
    assert time.monotonic() - started >= 0.2, "the retry went out immediately"

