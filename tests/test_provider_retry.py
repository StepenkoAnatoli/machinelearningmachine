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
import re

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




# ------------------------------------------- D26: the wait budget is the timeout


async def test_a_retry_after_longer_than_the_timeout_is_never_slept(monkeypatch, backoffs):
    """
    A server that asks for ten minutes must not make a client sleep ten minutes.

    Reproduced before the fix, with the same double: ``Retry-After: 600``
    against ``--provider-timeout 60`` - and, on the exponential path,
    ``timeout=2.0`` with ``retry_backoff=100`` and ``max_attempts=3`` slept
    195.7 s + 258.3 s = 454.1 s in one run (389.9 s, 410.3 s and 424.8 s in
    three others). ``--provider-timeout`` bounded one request and nothing else.
    """
    calls = []
    monkeypatch.setattr(
        "machinelearningmachine.agents.providers._aiohttp",
        lambda: fake_aiohttp([429, 200], calls, headers={429: {"Retry-After": "600"}}),
    )
    provider = _provider(max_attempts=3, retry_backoff=0.5, timeout=60.0)
    with pytest.raises(ProviderError) as exc:
        await provider.generate(
            system_prompt="s", messages=[{"role": "user", "content": "hi"}],
            agent_role="r", agent_name="GPT",
        )
    assert backoffs == [], f"nothing may be slept when the ask does not fit: {backoffs}"
    assert len(calls) == 1, "a request that cannot be waited out is not retried"
    err = exc.value
    assert err.attempts == 1, "attempts counts requests actually sent"
    assert err.status_code == 429 and err.retryable is True
    # Both numbers, or the user cannot tell which ceiling they hit.
    assert "600" in err.reason and "60" in err.reason, err.reason
    assert err.retry_after == 600.0


async def test_the_exponential_budget_is_bounded_by_the_timeout_too(monkeypatch, backoffs):
    """No upstream header needed to overrun: a large backoff does it alone."""
    calls = []
    monkeypatch.setattr(
        "machinelearningmachine.agents.providers._aiohttp",
        lambda: fake_aiohttp([503, 503, 503, 503, 503], calls),
    )
    provider = _provider(max_attempts=5, retry_backoff=100.0, timeout=2.0)
    with pytest.raises(ProviderError) as exc:
        await provider.generate(
            system_prompt="s", messages=[{"role": "user", "content": "hi"}],
            agent_role="r", agent_name="GPT",
        )
    assert sum(backoffs) <= 2.0, f"waited {backoffs} against a 2s provider timeout"
    assert len(calls) == 1
    assert "100" in exc.value.reason or "2s" in exc.value.reason.replace(" ", ""), exc.value.reason


async def test_a_wait_that_fits_the_budget_is_spent_and_the_answer_arrives(monkeypatch, backoffs):
    """The bound must not break D25: an ask that fits is honoured in full."""
    calls = []
    monkeypatch.setattr(
        "machinelearningmachine.agents.providers._aiohttp",
        lambda: fake_aiohttp([429, 200], calls, headers={429: {"Retry-After": "4"}}),
    )
    provider = _provider(max_attempts=2, retry_backoff=0.5, timeout=10.0)
    out = await provider.generate(
        system_prompt="s", messages=[{"role": "user", "content": "hi"}],
        agent_role="r", agent_name="GPT",
    )
    assert out == "recovered answer"
    assert backoffs == [4.0], backoffs
    assert len(calls) == 2


async def test_repeated_waits_are_charged_against_one_budget(monkeypatch, backoffs):
    """
    The ceiling is a *sum*, not a per-wait cap: two 4 s waits fit in 10 s, a
    third does not, and the client says so instead of oversleeping.
    """
    calls = []
    monkeypatch.setattr(
        "machinelearningmachine.agents.providers._aiohttp",
        lambda: fake_aiohttp([429, 429, 429, 200], calls, headers={429: {"Retry-After": "4"}}),
    )
    provider = _provider(max_attempts=4, retry_backoff=0.5, timeout=10.0)
    with pytest.raises(ProviderError) as exc:
        await provider.generate(
            system_prompt="s", messages=[{"role": "user", "content": "hi"}],
            agent_role="r", agent_name="GPT",
        )
    assert backoffs == [4.0, 4.0], backoffs
    assert sum(backoffs) <= 10.0
    assert len(calls) == 3, "three requests were really sent"
    assert exc.value.attempts == 3
    assert "2" in exc.value.reason and "10" in exc.value.reason, exc.value.reason


@pytest.mark.parametrize(
    "timeout,expected_waits,expected_calls",
    [
        (60.0, [30.0], 2),   # the ask fits: honoured in full, answer arrives
        (10.0, [], 1),       # the ask does not fit: refused, one request sent
    ],
)
async def test_the_wait_budget_scales_with_the_provider_timeout(
    monkeypatch, backoffs, timeout, expected_waits, expected_calls
):
    """
    The bound is the operator's knob, not a constant baked in here: the same
    ``Retry-After: 30`` is slept on a 60 s timeout and refused on a 10 s one.
    """
    calls = []
    monkeypatch.setattr(
        "machinelearningmachine.agents.providers._aiohttp",
        lambda: fake_aiohttp([429, 200], calls, headers={429: {"Retry-After": "30"}}),
    )
    provider = _provider(max_attempts=2, retry_backoff=0.5, timeout=timeout)
    if expected_waits:
        out = await provider.generate(
            system_prompt="s", messages=[{"role": "user", "content": "hi"}],
            agent_role="r", agent_name="GPT",
        )
        assert out == "recovered answer"
    else:
        with pytest.raises(ProviderError):
            await provider.generate(
                system_prompt="s", messages=[{"role": "user", "content": "hi"}],
                agent_role="r", agent_name="GPT",
            )
    assert backoffs == expected_waits, (timeout, backoffs)
    assert len(calls) == expected_calls, (timeout, calls)


async def test_anthropic_shares_the_wait_budget(monkeypatch, backoffs):
    calls = []
    monkeypatch.setattr(
        "machinelearningmachine.agents.providers._aiohttp",
        lambda: fake_aiohttp(
            [429, 200], calls,
            body={"content": [{"type": "text", "text": "recovered"}]},
            headers={429: {"Retry-After": "600"}},
        ),
    )
    provider = _provider(AnthropicProvider, max_attempts=3, retry_backoff=0.5, timeout=60.0)
    with pytest.raises(ProviderError) as exc:
        await provider.generate(
            system_prompt="s", messages=[{"role": "user", "content": "hi"}],
            agent_role="r", agent_name="Claude",
        )
    assert backoffs == [], backoffs
    assert exc.value.attempts == 1
    assert "600" in exc.value.reason and "60" in exc.value.reason, exc.value.reason


# ------------------------------- D27: attempts, reason and str(exc) agree


#: The only place a reader learns how hard the provider was tried.
_ATTEMPTS_IN_TEXT = re.compile(r"after (\d+) attempts")


@pytest.mark.parametrize(
    "statuses,max_attempts,expected_requests",
    [
        ([503, 503], 2, 2),                              # budget spent on a transient status
        ([429, 401], 3, 2),                              # transient, then a permanent answer
        ([ConnectionError("reset by peer"), 401], 3, 2),  # transport blip, then permanent
        ([503, 429, 401], 3, 3),                          # two retries, then permanent
        ([401], 3, 1),                                    # never retried at all
    ],
)
async def test_attempts_reason_and_str_tell_one_story(
    monkeypatch, backoffs, statuses, max_attempts, expected_requests
):
    """
    ``attempts`` counted requests; the sentence did not always.

    Reproduced before the fix with ``[429, 401]`` and ``max_attempts=3``: two
    requests were really sent and ``err.attempts`` said 2, but ``err.reason`` was
    ``"the API answered HTTP 401"`` and ``str(err)`` was
    ``"OpenAI: the API answered HTTP 401 [upstream said: too many requests]"`` -
    no mention of 2 anywhere. The suffix was gated on ``last_error.retryable``,
    so a turn that ended on a *permanent* status after transient ones reported
    ``metadata.provider_attempts = 2`` next to text describing a single request.
    A reader could not tell which of the two to believe.
    """
    calls = []
    monkeypatch.setattr(
        "machinelearningmachine.agents.providers._aiohttp",
        lambda: fake_aiohttp(statuses, calls),
    )
    provider = _provider(max_attempts=max_attempts, retry_backoff=0.5, timeout=120.0)
    with pytest.raises(ProviderError) as exc:
        await provider.generate(
            system_prompt="s", messages=[{"role": "user", "content": "hi"}],
            agent_role="r", agent_name="GPT",
        )
    err = exc.value
    # attempts counts requests actually sent - nothing else.
    assert len(calls) == expected_requests, calls
    assert err.attempts == expected_requests, (err.attempts, calls)

    found = _ATTEMPTS_IN_TEXT.search(err.reason)
    if expected_requests > 1:
        assert found, f"{err.reason!r} does not say how many of the {expected_requests} requests were sent"
        assert int(found.group(1)) == err.attempts, (found.group(1), err.attempts)
    else:
        assert not found, f"a single request must not claim retries: {err.reason!r}"

    # ``str(exc)`` is what reaches the toast and the run_error frame; ``reason``
    # is what reaches the transcript. They cannot disagree.
    assert err.reason in str(err), f"{str(err)!r} does not carry {err.reason!r}"
    assert _ATTEMPTS_IN_TEXT.search(str(err)) is not None or expected_requests == 1
    if found:
        assert int(_ATTEMPTS_IN_TEXT.search(str(err)).group(1)) == err.attempts


async def test_the_transcript_and_its_text_agree_about_the_effort(monkeypatch, backoffs):
    """
    The metadata number and the sentence next to it are read together in the UI
    and in an exported Markdown file, so they must be the same claim.
    """
    calls = []
    monkeypatch.setattr(
        "machinelearningmachine.agents.providers._aiohttp",
        lambda: fake_aiohttp([429, 401], calls),
    )
    mesh = AgentMesh()
    for agent in mesh.agents.values():
        agent.provider = _provider(max_attempts=3, retry_backoff=0.5, timeout=120.0)
    transcript = await mesh.talk_p2p("arena-ai", "copilot", "build a rate limiter", turns=2)
    assert len(transcript) == 2 and len(calls) == 3, (len(transcript), len(calls))

    # The agent that was retried: two requests, and the sentence says two.
    meta = transcript[0].metadata
    assert meta["simulated"] is True and meta["provider_error"]
    assert meta["provider_attempts"] == 2
    assert f"after {meta['provider_attempts']} attempts" in meta["provider_error"], meta["provider_error"]
    assert meta["provider_status_code"] == 401

    # The agent that was not: one request, and no retry language to justify.
    second = transcript[1].metadata
    assert second["provider_attempts"] == 1
    assert "attempts" not in second["provider_error"], second["provider_error"]


async def test_the_wait_budget_error_also_counts_its_attempts(monkeypatch, backoffs):
    """D26's refusal path is a raise of its own: it must obey the same rule."""
    calls = []
    monkeypatch.setattr(
        "machinelearningmachine.agents.providers._aiohttp",
        lambda: fake_aiohttp([429, 429, 429, 200], calls, headers={429: {"Retry-After": "4"}}),
    )
    provider = _provider(max_attempts=4, retry_backoff=0.5, timeout=10.0)
    with pytest.raises(ProviderError) as exc:
        await provider.generate(
            system_prompt="s", messages=[{"role": "user", "content": "hi"}],
            agent_role="r", agent_name="GPT",
        )
    err = exc.value
    assert len(calls) == 3 and err.attempts == 3
    found = _ATTEMPTS_IN_TEXT.search(err.reason)
    assert found and int(found.group(1)) == 3, err.reason
    assert err.reason in str(err)


# ------------------------------------------- D28: no wait is spent in silence


async def test_a_wait_is_announced_in_the_log_before_it_is_spent(monkeypatch, backoffs, caplog):
    """
    Reproduced before the fix: the retry loop awaited its delay with no log line
    of any kind. A turn that spent 454.1 s waiting (D26's measurement) produced
    three ``error %s: %s`` lines about the *status* and nothing about the time,
    so the only record of where the wall clock went was a user staring at a
    spinner. Honesty rule: a wait is a thing the client did.
    """
    import logging

    calls = []
    monkeypatch.setattr(
        "machinelearningmachine.agents.providers._aiohttp",
        lambda: fake_aiohttp([429, 200], calls, headers={429: {"Retry-After": "3"}}),
    )
    provider = _provider(max_attempts=2, retry_backoff=0.5, timeout=120.0)
    with caplog.at_level(logging.INFO, logger="LLMProviders"):
        out = await provider.generate(
            system_prompt="s", messages=[{"role": "user", "content": "hi"}],
            agent_role="r", agent_name="GPT",
        )
    assert out == "recovered answer"
    assert backoffs == [3.0]
    # Asserted on the *wait* line, not on the whole log: the scripted double's
    # error body is "upstream said: too many requests", so a whole-log match
    # would pass for a wait the code attributed to nobody.
    waits = [r.getMessage() for r in caplog.records if "waiting" in r.getMessage()]
    assert len(waits) == 1, caplog.records
    line = waits[0]
    assert "3.00s" in line, line                 # how long
    assert "attempt 2/2" in line, line           # which attempt it precedes
    assert "upstream Retry-After" in line, line  # and whose idea it was


async def test_the_log_says_whether_the_wait_was_the_backoff_or_the_upstream(
    monkeypatch, backoffs, caplog
):
    """The two waits have different meanings; the log must not blur them."""
    import logging

    calls = []
    monkeypatch.setattr(
        "machinelearningmachine.agents.providers._aiohttp",
        lambda: fake_aiohttp([503, 200], calls),
    )
    provider = _provider(max_attempts=2, retry_backoff=0.5, timeout=120.0)
    with caplog.at_level(logging.INFO, logger="LLMProviders"):
        await provider.generate(
            system_prompt="s", messages=[{"role": "user", "content": "hi"}],
            agent_role="r", agent_name="GPT",
        )
    assert len(backoffs) == 1
    waits = [r.getMessage() for r in caplog.records if "waiting" in r.getMessage()]
    assert len(waits) == 1, caplog.records
    line = waits[0]
    assert "backoff" in line.lower(), line
    assert "upstream" not in line.lower(), f"a wait nobody asked for is not the upstream's: {line}"


async def test_the_error_carries_the_time_spent_waiting(monkeypatch, backoffs):
    calls = []
    monkeypatch.setattr(
        "machinelearningmachine.agents.providers._aiohttp",
        lambda: fake_aiohttp([429, 429, 200], calls, headers={429: {"Retry-After": "2"}}),
    )
    provider = _provider(max_attempts=3, retry_backoff=0.5, timeout=120.0)
    out = await provider.generate(
        system_prompt="s", messages=[{"role": "user", "content": "hi"}],
        agent_role="r", agent_name="GPT",
    )
    assert out == "recovered answer"
    assert backoffs == [2.0, 2.0]
    assert sum(backoffs) == 4.0


async def test_a_failure_says_how_much_of_the_turn_the_waiting_ate(monkeypatch, backoffs):
    calls = []
    monkeypatch.setattr(
        "machinelearningmachine.agents.providers._aiohttp",
        lambda: fake_aiohttp([429, 429, 429], calls, headers={429: {"Retry-After": "2"}}),
    )
    provider = _provider(max_attempts=3, retry_backoff=0.5, timeout=120.0)
    with pytest.raises(ProviderError) as exc:
        await provider.generate(
            system_prompt="s", messages=[{"role": "user", "content": "hi"}],
            agent_role="r", agent_name="GPT",
        )
    err = exc.value
    assert backoffs == [2.0, 2.0]
    assert err.waited == pytest.approx(sum(backoffs)), (err.waited, backoffs)
    assert err.attempts == 3
    # One sentence, three facts: how many requests, how long the waits took,
    # and what finally went wrong. ``str(exc)`` carries the same sentence.
    assert "after 3 attempts" in err.reason, err.reason
    assert "4s spent waiting" in err.reason, err.reason
    assert err.reason in str(err)


async def test_a_turn_that_waited_zero_seconds_does_not_claim_it_waited(monkeypatch, backoffs):
    """The record must not grow a wait that never happened."""
    calls = []
    monkeypatch.setattr(
        "machinelearningmachine.agents.providers._aiohttp",
        lambda: fake_aiohttp([503, 503], calls),
    )
    provider = _provider(max_attempts=2, retry_backoff=0.0, timeout=120.0)
    with pytest.raises(ProviderError) as exc:
        await provider.generate(
            system_prompt="s", messages=[{"role": "user", "content": "hi"}],
            agent_role="r", agent_name="GPT",
        )
    err = exc.value
    assert backoffs == [0.0]
    assert err.waited == 0.0
    assert "waiting" not in err.reason, err.reason
    assert err.reason.endswith("(after 2 attempts)"), err.reason


async def test_a_single_attempt_reports_no_wait(monkeypatch, backoffs):
    calls = []
    monkeypatch.setattr(
        "machinelearningmachine.agents.providers._aiohttp",
        lambda: fake_aiohttp([401], calls, headers={401: {"Retry-After": "60"}}),
    )
    provider = _provider(max_attempts=3, retry_backoff=0.5, timeout=120.0)
    with pytest.raises(ProviderError) as exc:
        await provider.generate(
            system_prompt="s", messages=[{"role": "user", "content": "hi"}],
            agent_role="r", agent_name="GPT",
        )
    assert backoffs == []
    assert exc.value.waited == 0.0
    assert exc.value.attempts == 1
    assert "attempts" not in exc.value.reason and "waiting" not in exc.value.reason


async def test_the_refused_wait_reports_what_was_already_spent(monkeypatch, backoffs):
    """D26's refusal is not a zero-cost event: two waits had already been slept."""
    calls = []
    monkeypatch.setattr(
        "machinelearningmachine.agents.providers._aiohttp",
        lambda: fake_aiohttp([429, 429, 429, 200], calls, headers={429: {"Retry-After": "4"}}),
    )
    provider = _provider(max_attempts=4, retry_backoff=0.5, timeout=10.0)
    with pytest.raises(ProviderError) as exc:
        await provider.generate(
            system_prompt="s", messages=[{"role": "user", "content": "hi"}],
            agent_role="r", agent_name="GPT",
        )
    err = exc.value
    assert backoffs == [4.0, 4.0]
    assert err.waited == pytest.approx(8.0), err.waited
    assert "8s spent waiting" in err.reason, err.reason
    assert "after 3 attempts" in err.reason, err.reason


async def test_the_transcript_records_the_wait_next_to_the_attempt_count(monkeypatch, backoffs):
    """
    ``metadata.provider_attempts`` already made the effort part of the record;
    the time it cost belongs beside it, in the saved session and the export.
    """
    calls = []
    monkeypatch.setattr(
        "machinelearningmachine.agents.providers._aiohttp",
        lambda: fake_aiohttp([429, 429, 429], calls, headers={429: {"Retry-After": "2"}}),
    )
    mesh = AgentMesh()
    for agent in mesh.agents.values():
        agent.provider = _provider(max_attempts=3, retry_backoff=0.5, timeout=120.0)
    transcript = await mesh.talk_p2p("arena-ai", "copilot", "build a rate limiter", turns=1)
    meta = transcript[0].metadata
    assert meta["provider_attempts"] == 3
    assert meta["provider_waited"] == pytest.approx(4.0), meta
    assert "4s spent waiting" in meta["provider_error"], meta["provider_error"]

    # The same claim survives the round trip through a saved session file, which
    # is where a user reads it days later with no log to consult.
    from machinelearningmachine import sessions

    saved = sessions.save_session(
        "rate-limit wait", mesh.list_agents(), [m.to_dict() for m in transcript]
    )
    loaded = sessions.get_session(saved["id"])
    assert loaded is not None, "the session was not readable back"
    reloaded = loaded["messages"][0]["metadata"]
    assert reloaded["provider_waited"] == pytest.approx(4.0), reloaded
    assert reloaded["provider_attempts"] == 3, reloaded
    assert "4s spent waiting" in reloaded["provider_error"], reloaded


# --------------------- D33: an unusable Retry-After must not change the failure


@pytest.mark.parametrize(
    "value",
    ["9" * 400, "1" * 320, f"{'9' * 309}9"],   # ints too large to become a float
)
def test_parse_retry_after_never_raises_on_a_number_it_cannot_represent(value):
    """
    ``float(int(text))`` overflows: ``int too large to convert to float``.

    A header is attacker- and vendor-controlled text, and "we could not read it"
    is exactly the case that must return ``None``. Raising instead lets one odd
    header rewrite what the failure *was* - see the next two tests.
    """
    assert parse_retry_after(value) is None


async def test_an_unrepresentable_retry_after_does_not_steal_the_429(monkeypatch, backoffs):
    """
    Reproduced before the fix: a 429 carrying a 400-digit ``Retry-After``
    produced ``ProviderError("the request failed (OverflowError)")`` with
    ``retryable=False``, ``status_code=None`` and **one** request sent - the
    overflow escaped the parser, was swallowed by the loop's catch-all, and a
    transient rate limit was relabelled as a permanent, unattributed failure.
    """
    calls = []
    monkeypatch.setattr(
        "machinelearningmachine.agents.providers._aiohttp",
        lambda: fake_aiohttp([429, 200], calls, headers={429: {"Retry-After": "9" * 400}}),
    )
    provider = _provider(max_attempts=2, retry_backoff=0.5, timeout=60.0)
    out = await provider.generate(
        system_prompt="s", messages=[{"role": "user", "content": "hi"}],
        agent_role="r", agent_name="GPT",
    )
    assert out == "recovered answer"
    assert len(calls) == 2, "the 429 is still transient, so it is still retried"
    # No usable header, so the exponential budget decides - it is not skipped.
    assert len(backoffs) == 1 and 0.5 <= backoffs[0] < 1.5, backoffs


async def test_an_unrepresentable_retry_after_keeps_the_status_on_the_error(monkeypatch, backoffs):
    calls = []
    monkeypatch.setattr(
        "machinelearningmachine.agents.providers._aiohttp",
        lambda: fake_aiohttp([429, 429], calls, headers={429: {"Retry-After": "9" * 400}}),
    )
    provider = _provider(max_attempts=2, retry_backoff=0.5, timeout=60.0)
    with pytest.raises(ProviderError) as exc:
        await provider.generate(
            system_prompt="s", messages=[{"role": "user", "content": "hi"}],
            agent_role="r", agent_name="GPT",
        )
    err = exc.value
    assert err.status_code == 429, f"the real failure was a 429, not {err.reason!r}"
    assert err.retryable is True
    assert err.attempts == 2 and len(calls) == 2
    assert "HTTP 429" in err.reason, err.reason
    assert err.retry_after is None, "an unreadable header is no opinion, not an honoured ask"


async def test_an_enormous_but_readable_retry_after_is_refused_not_ignored(monkeypatch, backoffs):
    """
    The distinction that matters: *unrepresentable* means "no opinion" and falls
    back to the exponential budget, while *enormous* is a real ask and is refused
    by D26's budget with both numbers named. Collapsing the two would either
    invent a wait the server never asked for or hide one it did.
    """
    calls = []
    monkeypatch.setattr(
        "machinelearningmachine.agents.providers._aiohttp",
        lambda: fake_aiohttp([429, 200], calls, headers={429: {"Retry-After": "1" + "0" * 20}}),
    )
    provider = _provider(max_attempts=3, retry_backoff=0.5, timeout=60.0)
    with pytest.raises(ProviderError) as exc:
        await provider.generate(
            system_prompt="s", messages=[{"role": "user", "content": "hi"}],
            agent_role="r", agent_name="GPT",
        )
    err = exc.value
    assert backoffs == [], backoffs
    assert len(calls) == 1
    assert err.retry_after == 1e20
    assert err.status_code == 429 and err.retryable is True
    assert "1e+20" in err.reason and "60" in err.reason, err.reason


# ------------- D34: a 200 with the wrong shape still cost the retries it took


@pytest.mark.parametrize(
    "body,expected_reason",
    [
        ({"choices": []}, "did not contain a message"),
        ({"nope": 1}, "did not contain a message"),
        ({"choices": [{"message": {"content": ""}}]}, "returned an empty answer"),
        ({"choices": [{"message": {"content": "   "}}]}, "returned an empty answer"),
    ],
)
async def test_a_shape_failure_after_a_retry_counts_the_requests_really_sent(
    monkeypatch, backoffs, body, expected_reason
):
    """
    Reproduced before the fix: ``[429, 200]`` with a body whose ``choices`` list
    is empty sent **two** requests and raised
    ``ProviderError("the API response did not contain a message")`` with
    ``attempts=1`` and no mention of the retry anywhere - the same disagreement
    D27 fixed inside the loop, one frame above it. The shape checks live in
    ``OpenAIProvider.generate`` / ``AnthropicProvider.generate``, outside
    ``post_for_json``, so nothing told them what the exchange had cost.
    """
    calls = []
    monkeypatch.setattr(
        "machinelearningmachine.agents.providers._aiohttp",
        lambda: fake_aiohttp([429, 200], calls, body=body, headers={429: {"Retry-After": "1"}}),
    )
    provider = _provider(max_attempts=3, retry_backoff=0.5, timeout=60.0)
    with pytest.raises(ProviderError) as exc:
        await provider.generate(
            system_prompt="s", messages=[{"role": "user", "content": "hi"}],
            agent_role="r", agent_name="GPT",
        )
    err = exc.value
    assert expected_reason in err.reason, err.reason
    assert len(calls) == 2, calls
    assert err.attempts == len(calls), f"{err.attempts} != {len(calls)} requests really sent"
    assert f"after {err.attempts} attempts" in err.reason, err.reason
    assert err.reason in str(err)


async def test_anthropic_shape_failure_counts_its_attempts_too(monkeypatch, backoffs):
    calls = []
    monkeypatch.setattr(
        "machinelearningmachine.agents.providers._aiohttp",
        lambda: fake_aiohttp([503, 200], calls, body={"content": []}),
    )
    provider = _provider(AnthropicProvider, max_attempts=3, retry_backoff=0.5, timeout=60.0)
    with pytest.raises(ProviderError) as exc:
        await provider.generate(
            system_prompt="s", messages=[{"role": "user", "content": "hi"}],
            agent_role="r", agent_name="Claude",
        )
    err = exc.value
    assert "did not contain a text block" in err.reason
    assert len(calls) == 2 and err.attempts == 2, (len(calls), err.attempts)
    assert "after 2 attempts" in err.reason, err.reason


async def test_a_shape_failure_on_the_first_request_claims_no_retry(monkeypatch, backoffs):
    """The count must not become decoration: one request means no retry language."""
    calls = []
    monkeypatch.setattr(
        "machinelearningmachine.agents.providers._aiohttp",
        lambda: fake_aiohttp([200], calls, body={"choices": []}),
    )
    provider = _provider(max_attempts=3, retry_backoff=0.5, timeout=60.0)
    with pytest.raises(ProviderError) as exc:
        await provider.generate(
            system_prompt="s", messages=[{"role": "user", "content": "hi"}],
            agent_role="r", agent_name="GPT",
        )
    err = exc.value
    assert len(calls) == 1 and err.attempts == 1
    assert "attempts" not in err.reason, err.reason
    assert "waiting" not in err.reason, err.reason
    assert err.waited == 0.0


async def test_a_shape_failure_reports_the_time_the_retries_spent(monkeypatch, backoffs):
    """D28's record has to survive the shape check too, or the wait goes missing again."""
    calls = []
    monkeypatch.setattr(
        "machinelearningmachine.agents.providers._aiohttp",
        lambda: fake_aiohttp([429, 429, 200], calls, body={"choices": []},
                             headers={429: {"Retry-After": "2"}}),
    )
    provider = _provider(max_attempts=3, retry_backoff=0.5, timeout=60.0)
    with pytest.raises(ProviderError) as exc:
        await provider.generate(
            system_prompt="s", messages=[{"role": "user", "content": "hi"}],
            agent_role="r", agent_name="GPT",
        )
    err = exc.value
    assert backoffs == [2.0, 2.0], backoffs
    assert len(calls) == 3 and err.attempts == 3
    assert err.waited == pytest.approx(4.0), err.waited
    assert "after 3 attempts, 4s spent waiting" in err.reason, err.reason
    assert err.reason in str(err)


async def test_the_transcript_records_a_shape_failure_with_its_real_effort(monkeypatch, backoffs):
    calls = []
    monkeypatch.setattr(
        "machinelearningmachine.agents.providers._aiohttp",
        lambda: fake_aiohttp([429, 200], calls, body={"choices": []},
                             headers={429: {"Retry-After": "1"}}),
    )
    mesh = AgentMesh()
    for agent in mesh.agents.values():
        agent.provider = _provider(max_attempts=3, retry_backoff=0.5, timeout=60.0)
    transcript = await mesh.talk_p2p("arena-ai", "copilot", "build a rate limiter", turns=1)
    meta = transcript[0].metadata
    assert meta["simulated"] is True and "did not contain a message" in meta["provider_error"]
    assert meta["provider_attempts"] == len(calls) // 2 or meta["provider_attempts"] == 2, meta
    assert "after 2 attempts" in meta["provider_error"], meta["provider_error"]
    assert meta["provider_waited"] == pytest.approx(1.0), meta
