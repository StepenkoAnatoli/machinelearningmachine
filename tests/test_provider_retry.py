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
  rejected credential;
* a ``Retry-After`` from the upstream sets the wait, and a malformed one does
  not set it to zero (D25);
* the total time spent waiting can never outlive the provider timeout (D26);
* ``attempts``, ``reason`` and ``str(exc)`` tell one story (D27), and the wait
  is recorded rather than silent (D28).

No test here sleeps: the ``backoffs`` fixture in ``conftest.py`` replaces the
``_backoff_sleep`` seam with a recorder, so a ``Retry-After: 30`` costs the
suite the assertion and not thirty seconds.
"""


import asyncio

import pytest

from machinelearningmachine.agents.providers import (
    AnthropicProvider,
    OpenAIProvider,
    ProviderError,
    parse_retry_after,
)
from machinelearningmachine.mesh import AgentMesh


def fake_aiohttp(statuses, calls, body=None, exceptions=None, headers=None):
    """
    A stub aiohttp whose responses are scripted per attempt.

    ``statuses`` entries are ints; entries that are Exception *instances* are
    raised instead, which is how connection resets and timeouts are simulated
    without a socket. ``headers`` maps a status to the response headers that
    status should carry, which is how a rate-limit answer says
    ``Retry-After: 3`` without a socket in sight.
    """
    headers = headers or {}

    class FakeResp:
        def __init__(self, status):
            self.status = status
            self.headers = headers.get(status, {})

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


async def test_the_wait_between_attempts_actually_happens(monkeypatch, backoffs):
    """
    A retry without a delay is a busy loop against a service that just said 429.

    The wait is measured through :func:`_backoff_sleep` - the seam the retry
    loop owns - rather than by patching ``asyncio.sleep``: patching the loop's
    sleep would also swallow every other wait in the call stack, so the recorded
    number would not be evidence about *this* policy. The ``backoffs`` fixture
    installs that recorder for every test in the suite, so nothing here sleeps
    for real.
    """
    calls = []
    monkeypatch.setattr(
        "machinelearningmachine.agents.providers._aiohttp",
        lambda: fake_aiohttp([429, 200], calls),
    )
    provider = _provider(retry_backoff=0.5)
    out = await provider.generate(
        system_prompt="s", messages=[{"role": "user", "content": "hi"}],
        agent_role="r", agent_name="GPT",
    )
    assert out == "recovered answer"
    assert len(calls) == 2, "one retry, then success"
    assert len(backoffs) == 1, backoffs
    assert 0.5 <= backoffs[0] < 1.5, "the delay scales with the attempt and stays short"


def test_the_retry_loop_waits_through_its_own_seam_only():
    """
    Pin the seam, not just its use (D25).

    ``_backoff_sleep`` is what makes "the client waited N seconds" a measurable
    claim instead of an inference, and it is what lets the suite assert on a
    ten-minute ``Retry-After`` without spending ten minutes. A second
    ``asyncio.sleep`` appearing inside ``post_for_json`` would be a wait no test
    can see - so this reads the source and refuses one.
    """
    import inspect

    from machinelearningmachine.agents import providers

    assert hasattr(providers, "_backoff_sleep"), "the retry wait needs a named seam"
    source = inspect.getsource(providers.post_for_json)
    assert "asyncio.sleep" not in source, (
        "post_for_json must wait through _backoff_sleep, so a test can measure "
        "the wait instead of sleeping it"
    )
    assert "_backoff_sleep" in source


# ------------------------------------------------------- D25: Retry-After


@pytest.mark.parametrize(
    "value,expected",
    [
        ("0", 0.0),
        ("3", 3.0),
        ("120", 120.0),
        ("  7 ", 7.0),          # header values arrive with whitespace
        ("", None),             # present but empty: no opinion, not zero
        (None, None),
        ("soon", None),         # garbage must not become "retry immediately"
        ("-5", None),           # RFC 9110 delay-seconds is 1*DIGIT
        ("3.5", None),
        ("1e3", None),
        ("Wed, 32 Oct 2026 07:28:00 GMT", None),   # impossible date
    ],
)
def test_parse_retry_after_delta_seconds(value, expected):
    assert parse_retry_after(value) == expected


def test_parse_retry_after_http_date_counts_down_to_the_named_instant():
    from datetime import datetime, timedelta, timezone
    from email.utils import format_datetime

    now = datetime(2026, 9, 19, 12, 0, 0, tzinfo=timezone.utc)
    header = format_datetime(now + timedelta(seconds=30), usegmt=True)
    assert parse_retry_after(header, now=now) == 30.0
    # A date already behind us means "now", not "negative sleep" and not None:
    # None would hand the wait back to the exponential budget and invent a
    # delay the server never asked for.
    past = format_datetime(now - timedelta(seconds=30), usegmt=True)
    assert parse_retry_after(past, now=now) == 0.0


async def test_an_upstream_retry_after_sets_the_wait(monkeypatch, backoffs):
    """
    The server said when to come back; the client used to guess instead.

    Reproduced before the fix: a 429 carrying ``Retry-After: 3`` was retried
    after the jittered ``backoff * attempt`` - measured 0.54 s, 0.79 s, 0.85 s
    and 0.96 s over four runs - i.e. always inside the window the server had
    just named, so the second request was answered with another 429 and the
    turn was lost.
    """
    calls = []
    monkeypatch.setattr(
        "machinelearningmachine.agents.providers._aiohttp",
        lambda: fake_aiohttp([429, 200], calls, headers={429: {"Retry-After": "3"}}),
    )
    provider = _provider(retry_backoff=0.5, timeout=120.0)
    out = await provider.generate(
        system_prompt="s", messages=[{"role": "user", "content": "hi"}],
        agent_role="r", agent_name="GPT",
    )
    assert out == "recovered answer"
    assert len(calls) == 2
    assert backoffs == [3.0], f"the wait must be the 3s the upstream asked for, got {backoffs}"


async def test_a_retry_after_http_date_is_honoured_against_the_clock(monkeypatch, backoffs):
    from datetime import datetime, timedelta, timezone
    from email.utils import format_datetime

    when = format_datetime(datetime.now(timezone.utc) + timedelta(seconds=4), usegmt=True)
    calls = []
    monkeypatch.setattr(
        "machinelearningmachine.agents.providers._aiohttp",
        lambda: fake_aiohttp([429, 200], calls, headers={429: {"Retry-After": when}}),
    )
    provider = _provider(retry_backoff=0.5, timeout=120.0)
    await provider.generate(
        system_prompt="s", messages=[{"role": "user", "content": "hi"}],
        agent_role="r", agent_name="GPT",
    )
    assert len(backoffs) == 1, backoffs
    # Generous on both sides: the header names an instant, and the test must not
    # turn into a clock race. What it must not be is the 0.5-1.5s exponential.
    assert 2.0 < backoffs[0] <= 4.5, backoffs


async def test_a_malformed_retry_after_falls_back_to_the_exponential_budget(monkeypatch, backoffs):
    """Unparseable is "no opinion", never "retry at once"."""
    calls = []
    monkeypatch.setattr(
        "machinelearningmachine.agents.providers._aiohttp",
        lambda: fake_aiohttp([429, 200], calls, headers={429: {"Retry-After": "in a minute"}}),
    )
    provider = _provider(retry_backoff=0.5, timeout=120.0)
    await provider.generate(
        system_prompt="s", messages=[{"role": "user", "content": "hi"}],
        agent_role="r", agent_name="GPT",
    )
    assert len(backoffs) == 1, backoffs
    assert 0.5 <= backoffs[0] < 1.5, f"expected the jittered backoff, got {backoffs}"


async def test_retry_after_is_not_read_off_a_status_that_is_not_retried(monkeypatch, backoffs):
    """A 401 that says "come back in an hour" is still a 401: one request, no wait."""
    calls = []
    monkeypatch.setattr(
        "machinelearningmachine.agents.providers._aiohttp",
        lambda: fake_aiohttp([401, 200], calls, headers={401: {"Retry-After": "3600"}}),
    )
    provider = _provider(retry_backoff=0.5, timeout=120.0)
    with pytest.raises(ProviderError) as exc:
        await provider.generate(
            system_prompt="s", messages=[{"role": "user", "content": "hi"}],
            agent_role="r", agent_name="GPT",
        )
    assert len(calls) == 1
    assert backoffs == [], backoffs
    assert exc.value.attempts == 1
    assert exc.value.retry_after is None, "a header we did not act on is not something we honoured"


async def test_the_error_carries_the_wait_the_upstream_asked_for(monkeypatch, backoffs):
    calls = []
    monkeypatch.setattr(
        "machinelearningmachine.agents.providers._aiohttp",
        lambda: fake_aiohttp([503, 503], calls, headers={503: {"Retry-After": "1"}}),
    )
    provider = _provider(max_attempts=2, retry_backoff=0.5, timeout=120.0)
    with pytest.raises(ProviderError) as exc:
        await provider.generate(
            system_prompt="s", messages=[{"role": "user", "content": "hi"}],
            agent_role="r", agent_name="GPT",
        )
    assert exc.value.retry_after == 1.0
    assert backoffs == [1.0], backoffs


async def test_anthropic_honours_retry_after_too(monkeypatch, backoffs):
    """One policy, two providers: the header cannot be an OpenAI-only feature."""
    calls = []
    monkeypatch.setattr(
        "machinelearningmachine.agents.providers._aiohttp",
        lambda: fake_aiohttp(
            [429, 200], calls,
            body={"content": [{"type": "text", "text": "recovered"}]},
            headers={429: {"Retry-After": "2"}},
        ),
    )
    provider = _provider(AnthropicProvider, retry_backoff=0.5, timeout=120.0)
    out = await provider.generate(
        system_prompt="s", messages=[{"role": "user", "content": "hi"}],
        agent_role="r", agent_name="Claude",
    )
    assert out == "recovered"
    assert backoffs == [2.0], backoffs


async def test_a_real_aiohttp_response_header_is_read_case_insensitively(backoffs):
    """
    The stub double hands back a plain ``dict``; aiohttp hands back a
    ``CIMultiDict``. Only a real socket proves the header is found when the
    server lower-cases it, which plenty do.

    Loopback only, an ephemeral port, no egress - the same shape of double
    ``test_env_key_isolation.py`` uses.
    """
    web = pytest.importorskip("aiohttp.web", reason="the live-path double needs aiohttp")
    import threading

    state = {"n": 0}
    ready = threading.Event()
    port_holder = {}
    stop_holder = {}

    async def handler(request):
        state["n"] += 1
        if state["n"] == 1:
            return web.Response(status=429, text="slow down", headers={"retry-after": "2"})
        return web.json_response({"choices": [{"message": {"content": "recovered answer"}}]})

    async def run():
        app = web.Application()
        app.router.add_post("/v1/chat/completions", handler)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        port_holder["port"] = runner.addresses[0][1]
        loop = asyncio.get_running_loop()
        stop_holder["stop"] = loop.create_future()
        ready.set()
        await stop_holder["stop"]
        await runner.cleanup()

    def thread_main():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(run())
        finally:
            loop.close()

    thread = threading.Thread(target=thread_main, daemon=True)
    thread.start()
    assert ready.wait(10), "the loopback double did not start"
    try:
        provider = OpenAIProvider(
            api_key="sk-" + "k" * 30,
            base_url=f"http://127.0.0.1:{port_holder['port']}/v1",
            retry_backoff=0.5,
            timeout=120.0,
            allow_env_key=False,
        )
        out = await provider.generate(
            system_prompt="s", messages=[{"role": "user", "content": "hi"}],
            agent_role="r", agent_name="GPT",
        )
    finally:
        stop = stop_holder.get("stop")
        if stop is not None and not stop.done():
            stop.set_result(None)
        thread.join(5)

    assert out == "recovered answer"
    assert state["n"] == 2
    assert backoffs == [2.0], f"the lower-cased header was not honoured: {backoffs}"


