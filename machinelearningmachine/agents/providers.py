"""
LLM Provider abstractions supporting:
- Mock / Intelligent Simulation (zero setup, deterministic template output)
- OpenAI / OpenAI-compatible API (ChatGPT, GPT-4o, Ollama, LMStudio, vLLM)
- Anthropic API (Claude 3.5 Sonnet, etc.)

Honesty rules for the simulator (these matter - see SECURITY.md / README):
* Mock output is always labelled as simulated, because nothing here is
  compiled, executed, or tested.
* The simulator must never claim verification ("tests pass", "production
  ready", "approved"), because a user can paste that code into a real project.
* A live provider that fails raises :class:`ProviderError`. Callers decide
  whether to fall back to the simulator and mark the message as degraded -
  an error string is never returned as if it were a model answer.

User-centered: the mock provider still generates contextual, copy-pasteable
examples that help without API keys - it just says what it is.
"""

import asyncio
import logging
import os
import random
import re
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any, ClassVar, Dict, List, Optional, Tuple
from urllib.parse import urlparse

from .._deps import install_hint

logger = logging.getLogger("LLMProviders")

#: Prepended to every simulated reply so a transcript can never be mistaken
#: for a real model conversation, even after it is exported to Markdown.
SIMULATION_NOTICE = (
    "> ⚠️ **Simulated output** (`MockLLMProvider`) - no model API was called, "
    "and none of the code below has been compiled, executed, or tested.\n"
    "> Configure an API key (⚙️ Settings) for a real model answer.\n\n"
)

#: Same idea for the case where a real provider was configured but failed.
FALLBACK_NOTICE_TEMPLATE = (
    "> ⚠️ **{provider} failed** ({reason}). The reply below is the built-in "
    "simulator talking, **not** an answer from {provider}.\n\n"
)


class ProviderError(RuntimeError):
    """
    A live provider could not produce an answer.

    ``reason`` is user-presentable; ``detail`` is for logs. Callers should
    either surface this as an error or fall back to the simulator *and say so*
    - never return the failure as a normal agent message.
    """

    def __init__(
        self,
        provider: str,
        reason: str,
        *,
        detail: str = "",
        status_code: Optional[int] = None,
        retryable: bool = False,
        attempts: int = 1,
        retry_after: Optional[float] = None,
        waited: float = 0.0,
    ) -> None:
        super().__init__(f"{provider}: {reason}" + (f" [{detail}]" if detail else ""))
        self.provider = provider
        self.reason = reason
        self.detail = detail
        self.status_code = status_code
        self.retryable = retryable
        #: How many HTTP attempts were made before this error was given up on.
        #: ``retryable`` used to be computed and then ignored, which meant one
        #: transient 429 or 503 cost the whole turn (and, with
        #: ``--strict-provider-errors``, the whole run).
        self.attempts = max(1, int(attempts))
        #: Seconds the upstream asked for in a ``Retry-After`` header, or
        #: ``None`` when it did not ask (or asked in a form nobody can parse).
        #: Kept on the error so a caller can say *why* the wait was what it was
        #: instead of only that there was one.
        self.retry_after = None if retry_after is None else float(retry_after)
        #: Seconds this client actually spent waiting between attempts. Recorded
        #: because a wait is something the client *did*: without it a turn that
        #: burned four seconds on rate limits is indistinguishable from a slow
        #: model, in the log and in the saved transcript alike.
        self.waited = max(0.0, float(waited))


def _aiohttp():
    """
    Import aiohttp lazily: the mock/simulation engine and every topology work
    without it, so it is only needed once a live HTTP API is actually called.
    """
    try:
        import aiohttp
    except ImportError:
        raise ImportError(install_hint("aiohttp")) from None
    return aiohttp


#: HTTP statuses worth another attempt: transient on the provider's side.
RETRYABLE_STATUSES: Tuple[int, ...] = (408, 409, 425, 429, 500, 502, 503, 504)
#: How many times a request may be sent in total (1 = no retry).
DEFAULT_MAX_ATTEMPTS = 2
#: Base delay for the wait between attempts (scaled by the attempt number and
#: jittered, so parallel agents do not retry in lockstep).
DEFAULT_RETRY_BACKOFF = 0.5
#: Seconds an HTTP call may take before the provider is considered unreachable.
DEFAULT_REQUEST_TIMEOUT = 60.0
#: The header an upstream uses to say how long a client should wait. Read
#: case-insensitively - aiohttp's ``CIMultiDict`` does that, and the parser
#: below does not need to.
RETRY_AFTER_HEADER = "Retry-After"
#: RFC 9110 ``delay-seconds`` is ``1*DIGIT``: no sign, no fraction, no units.
#: Anything else is a header we did not understand, and "did not understand"
#: must never be read as "you may retry immediately".
_DELAY_SECONDS = re.compile(r"^\d+$")


async def _backoff_sleep(delay: float) -> None:
    """
    The one place a retry wait happens - the seam tests replace.

    Not ``asyncio.sleep`` called inline: patching the event loop's sleep to
    measure a retry policy also swallows every other wait in the call stack, so
    the number a test records stops being evidence about *this* policy. Tests
    replace this function (the ``backoffs`` fixture does it for the whole suite)
    and assert on the delay that was asked for, which is why a
    ``Retry-After: 600`` can be tested without anybody waiting ten minutes.

    A non-positive delay is not slept at all, so a zero wait is a no-op rather
    than a trip through the loop.
    """
    if delay > 0:
        await asyncio.sleep(delay)


def parse_retry_after(value: Any, *, now: Optional[datetime] = None) -> Optional[float]:
    """
    Seconds the upstream asked us to wait, or ``None`` when it did not say.

    Both forms RFC 9110 allows are understood: ``delay-seconds`` ("3") and an
    HTTP-date ("Wed, 21 Oct 2026 07:28:00 GMT"). Everything else - absent,
    empty, negative, fractional, a word, an impossible date, a digit string too
    long to become a float - returns ``None``, which means "no opinion" and
    leaves the exponential budget in charge. This function never raises: it
    reads text somebody else controls, and an exception from here would be
    caught by the retry loop's catch-all and reported as the failure instead of
    the 429 that actually arrived.

    An *enormous but representable* ask is deliberately not ``None``: that is a
    real request to wait, and D26's budget refuses it with both numbers named
    rather than pretending the server said nothing.

    Guessing zero for a header we could not read would be the wrong kind of
    helpful: it retries at once against a server that just said "not now", and
    turns one rate limit into a longer one.

    ``now`` exists so an HTTP-date can be tested against a fixed instant
    instead of against whatever the clock says when the suite runs.
    """
    if value is None:
        return None
    if isinstance(value, (bytes, bytearray)):
        try:
            value = value.decode("ascii", "replace")
        except Exception:  # pragma: no cover - decode above cannot realistically fail
            return None
    if not isinstance(value, str):
        # aiohttp hands back strings; a numeric header object from some other
        # transport is still usable, anything else is not a header.
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            value = str(int(value)) if float(value).is_integer() else str(value)
        else:
            return None
    text = value.strip()
    if not text:
        return None
    if _DELAY_SECONDS.match(text):
        # ``float(int(...))`` overflows on a long enough digit string, and a
        # header is text somebody else controls. Letting that escape would hand
        # the loop's catch-all an OverflowError, which relabels a transient 429
        # as a permanent "the request failed (OverflowError)" and loses the
        # status code - one odd header rewriting what the failure was.
        try:
            return float(int(text))
        except OverflowError:
            return None
    try:
        when = parsedate_to_datetime(text)
    except (TypeError, ValueError):
        return None
    if when is None:
        return None
    if when.tzinfo is None:
        # An IMF-fixdate is always GMT; a naive date from a sloppy server is
        # read as UTC rather than as local time, which would otherwise make the
        # wait depend on the machine's timezone.
        when = when.replace(tzinfo=timezone.utc)
    reference = now or datetime.now(timezone.utc)
    if reference.tzinfo is None:
        reference = reference.replace(tzinfo=timezone.utc)
    # A date already behind us means "now". Not ``None``: that would invent an
    # exponential delay the server never asked for.
    return max(0.0, (when - reference).total_seconds())


class BaseLLMProvider:
    #: True only for the deterministic simulator. The UI, the transcript and
    #: ``/api/run`` all surface this, so simulated text can never be mistaken
    #: for a real model answer.
    is_simulated = False
    #: Short label used in message metadata and error notices.
    label = "provider"
    #: Total HTTP attempts per turn (1 disables retrying). Transient failures only.
    max_attempts: int = DEFAULT_MAX_ATTEMPTS
    retry_backoff: float = DEFAULT_RETRY_BACKOFF
    timeout: float = DEFAULT_REQUEST_TIMEOUT

    async def generate(
        self,
        system_prompt: str,
        messages: List[Dict[str, str]],
        agent_role: str,
        agent_name: str,
        task_context: Optional[str] = None,
    ) -> str:
        raise NotImplementedError


def _wait_does_not_fit(
    err: ProviderError,
    delay: float,
    *,
    remaining: float,
    budget: float,
    waited: float,
) -> ProviderError:
    """
    Turn "we could wait, but not that long" into the error it deserves.

    The alternative shapes were both worse. Sleeping a *truncated* wait retries
    before the upstream's limit resets, spends the attempt, and usually earns a
    second 429 - the D25 bug wearing a different hat. Sleeping the full wait
    hands the upstream control of this client's clock, which is how a
    ``Retry-After: 600`` from one server becomes a ten-minute hang the operator
    cannot explain, because ``--provider-timeout`` says 60.

    So the loop stops, and the reason carries both numbers: what was asked for
    and what this client is allowed to spend. It stays ``retryable`` - the
    upstream failure *is* transient, this client just ran out of patience.
    """
    asked = (
        f"and asked to wait {delay:g}s"
        if err.retry_after is not None
        else f"and the next retry would wait {delay:g}s"
    )
    room = (
        f"more than the {budget:g}s this client may spend waiting between attempts"
        if remaining >= budget
        else f"more than the {remaining:g}s left of the {budget:g}s this client may spend waiting"
    )
    return ProviderError(
        err.provider,
        f"{err.reason} {asked}, {room}",
        detail=err.detail,
        status_code=err.status_code,
        retryable=True,
        attempts=err.attempts,
        retry_after=err.retry_after,
        waited=waited,
    )


def _shape_error(
    provider: BaseLLMProvider,
    reason: str,
    stats: Optional[Dict[str, float]],
) -> ProviderError:
    """
    A 200 whose body is not an answer, labelled like every other failure.

    These checks belong to each provider (OpenAI reads ``choices[0].message.content``,
    Anthropic reads ``content[].text``), so they run *after* ``post_for_json`` returns -
    outside the loop that knows how many requests the exchange took. Without the
    ``stats`` handoff they defaulted to ``attempts=1`` and told the transcript that a
    body which only arrived on the second request had cost one, with no mention of the
    retry or of the seconds spent waiting for it.
    """
    attempts = int((stats or {}).get("attempts", 1))
    waited = float((stats or {}).get("waited", 0.0))
    return _labelled_with_attempts(
        ProviderError(provider.label, reason, attempts=attempts), waited=waited
    )


def _labelled_with_attempts(err: ProviderError, *, waited: float = 0.0) -> ProviderError:
    """
    Make ``attempts``, ``reason`` and ``str(exc)`` tell one story, then raise it.

    The suffix used to be gated on ``retryable`` as well as ``attempts > 1``, so a
    turn that began with a 429 and *ended* on a 401 reported ``attempts=2`` -
    correctly - next to ``"the API answered HTTP 401"``, which describes one
    request. ``metadata.provider_attempts`` said 2 and the sentence beside it said
    nothing, and a reader had no way to tell which was the truth. Both are: two
    requests were sent and the last answer was a 401.

    ``args`` is rebuilt from the same pieces ``__init__`` used, because ``str(exc)``
    is what reaches the toast and the ``run_error`` frame while ``reason`` is what
    reaches the transcript - two strings for one failure is how a user ends up with
    "timed out" on screen and "timed out (after 2 attempts)" in the export.

    A single attempt is never labelled: "(after 1 attempts)" would be noise, and a
    test pins its absence so the count cannot drift into decoration.
    """
    err.waited = round(max(0.0, float(waited)), 3)
    if err.attempts > 1:
        note = f"after {err.attempts} attempts"
        if err.waited > 0:
            # Named only when it happened: "(after 2 attempts, 0s spent waiting)"
            # would be a claim about a wait that never took place.
            note += f", {err.waited:g}s spent waiting"
        err.reason = f"{err.reason} ({note})"
        err.args = (
            f"{err.provider}: {err.reason}" + (f" [{err.detail}]" if err.detail else ""),
        )
    return err


async def post_for_json(
    *,
    provider: BaseLLMProvider,
    url: str,
    headers: Dict[str, str],
    payload: Dict[str, Any],
    stats: Optional[Dict[str, float]] = None,
) -> Dict[str, Any]:
    """
    POST ``payload`` as JSON and hand back the parsed body, retrying transient failures.

    One place owns the policy the two live providers share, so a retry, a timeout or
    a "this is why it failed" message cannot drift between them:

    * only statuses in :data:`RETRYABLE_STATUSES` (and transport errors/timeouts) are
      retried, at most ``provider.max_attempts`` times in total;
    * the wait before a retry is the upstream's own ``Retry-After`` when it sent one
      (:func:`parse_retry_after`), and the jittered exponential budget only when it did
      not - a server that says "come back in 3 s" is not retried after 0.8 s;
    * every wait goes through :func:`_backoff_sleep`, the seam a test can measure
      instead of sleep;
    * the *sum* of those waits may not exceed ``provider.timeout`` - the operator's
      ``--provider-timeout`` is a ceiling on how long one turn may spend waiting, so a
      server that asks for ten minutes gets an error naming both numbers rather than a
      client that sleeps for ten;
    * the failure that ends the loop is raised as :class:`ProviderError` carrying
      ``attempts`` and ``retry_after``, so the transcript can say how hard it tried;
    * response bodies are truncated before they reach a log line or an error, and an
      API key is never part of either;
    * a caller that passes a ``stats`` dict learns what a *successful* exchange cost -
      ``{"attempts": n, "waited": seconds}`` - because the checks a provider runs on the
      body afterwards happen outside this loop and still have to report the truth. A
      200 that only arrived on the second request cost two requests, and an error that
      says otherwise is the D27 disagreement one frame up.
    """
    aiohttp = _aiohttp()
    attempts = provider.max_attempts
    backoff = provider.retry_backoff
    timeout = provider.timeout
    #: The ceiling on *waiting*, charged across the whole call: the same number
    #: the operator gave one request (``--provider-timeout``) is what the sum of
    #: the waits between attempts may not exceed. Without it the flag bounded a
    #: single request and nothing bounded the sleeps around it - measured at
    #: 454.1 s of waiting against ``timeout=2.0`` with ``retry_backoff=100``.
    wait_budget = max(0.0, float(timeout))
    waited = 0.0
    last_error: ProviderError

    for attempt in range(1, attempts + 1):
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=timeout)) as session:
                async with session.post(url, headers=headers, json=payload) as resp:
                    status = resp.status
                    if status != 200:
                        err_txt = (await resp.text())[:400]
                        logger.error("%s error %s: %s", provider.label, status, err_txt)
                        retryable = status in RETRYABLE_STATUSES
                        # ``Retry-After`` is only read for a status this client
                        # would actually retry. Recording "asked to wait an
                        # hour" on a 401 it never intended to wait for would be
                        # a claim about a wait that did not happen.
                        asked_for = None
                        if retryable:
                            resp_headers = getattr(resp, "headers", None)
                            asked_for = parse_retry_after(
                                resp_headers.get(RETRY_AFTER_HEADER)
                                if resp_headers is not None
                                else None
                            )
                        raise ProviderError(
                            provider.label,
                            f"the API answered HTTP {status}",
                            detail=err_txt,
                            status_code=status,
                            retryable=retryable,
                            attempts=attempt,
                            retry_after=asked_for,
                        )
                    try:
                        data = await resp.json()
                    except Exception as exc:
                        raise ProviderError(
                            provider.label, "the API answered with something that is not JSON",
                            attempts=attempt,
                        ) from exc
            if not isinstance(data, dict):
                raise ProviderError(
                    provider.label, "the API response was not a JSON object", attempts=attempt
                )
            if stats is not None:
                stats["attempts"] = float(attempt)
                stats["waited"] = waited
            return data
        except ProviderError as exc:
            last_error = exc
        except aiohttp.ClientError as exc:
            logger.error("%s request failed: %s", provider.label, exc)
            last_error = ProviderError(
                provider.label,
                f"could not reach {urlparse(url).netloc or url}",
                detail=str(exc)[:200],
                retryable=True,
                attempts=attempt,
            )
        except asyncio.TimeoutError:
            last_error = ProviderError(
                provider.label,
                f"timed out after {timeout:g}s",
                retryable=True,
                attempts=attempt,
            )
        except Exception as exc:  # the turn must be labelled, never raw
            # Anything unexpected (proxy/auth/SSL weirdness from aiohttp internals)
            # still has to arrive as a ProviderError, or the caller would treat it as
            # an internal failure with no provider attached.
            logger.error("%s request raised %s", provider.label, exc.__class__.__name__)
            last_error = ProviderError(
                provider.label,
                f"the request failed ({exc.__class__.__name__})",
                detail=str(exc)[:200],
                retryable=isinstance(exc, OSError),
                attempts=attempt,
            )
        if last_error.retryable and attempt < attempts:
            # The upstream gets the first word: it knows when its own
            # rate-limit window opens and this client does not. Only when it
            # said nothing - no header, or one nobody can parse - does the
            # exponential budget decide, and it is jittered so parallel agents
            # do not retry in lockstep. Not a security-relevant use of
            # random(): a fixed delay would be a worse request, not an unsafe one.
            if last_error.retry_after is not None:
                delay = last_error.retry_after
            else:
                delay = backoff * attempt + random.uniform(0, backoff)  # noqa: S311
            remaining = wait_budget - waited
            if delay > remaining:
                # Cannot be honoured without outliving the operator's ceiling,
                # and truncating it would retry before the limit resets. Stop,
                # and say which two numbers collided.
                raise _labelled_with_attempts(
                    _wait_does_not_fit(
                        last_error, delay, remaining=remaining, budget=wait_budget, waited=waited
                    ),
                    waited=waited,
                )
            waited += delay
            # Announced *before* it is spent and at WARNING, not DEBUG: this is
            # the only record of where a slow turn's wall clock went, and a wait
            # the log does not mention is a wait nobody can explain afterwards.
            logger.warning(
                "%s: waiting %.2fs before attempt %d/%d (%s)",
                provider.label,
                delay,
                attempt + 1,
                attempts,
                # Two different waits with two different meanings, named so a
                # reader cannot mistake one for the other: this delay came from
                # the server, or it came from this client's own budget.
                f"upstream Retry-After: {delay:g}s"
                if last_error.retry_after is not None
                else "jittered backoff, no usable Retry-After",
            )
            await _backoff_sleep(delay)
            continue
        raise _labelled_with_attempts(last_error, waited=waited)


class MockLLMProvider(BaseLLMProvider):
    """
    Intelligent simulated LLM provider.
    Generates realistic, contextual multi-turn responses with code, reviews,
    critiques, and synthesis without requiring external API keys.

    Every reply is prefixed with :data:`SIMULATION_NOTICE` and tagged as
    simulated, and the wording deliberately avoids claiming that anything was
    built, run, reviewed, or tested - because nothing was.

    User-centered improvements:
    - Detects many more domains (auth, rate limiting, caching, queues, API, DB, etc.)
    - Produces practical copy-pasteable starting points
    - Includes helpful explanations tailored to actual user prompt
    - Better turn-awareness for coherent multi-turn dialogues
    """

    is_simulated = True
    label = "simulator"

    # Domain detection patterns for more relevant responses. Read-only: it is a
    # lookup table, not per-instance state (hence ClassVar).
    DOMAIN_PATTERNS: ClassVar[dict] = {
        "rate_limiter": ["rate limit", "throttle", "token bucket", "leaky bucket"],
        "cache": ["cache", "caching", "ttl", "lru", "memoize", "eviction"],
        "queue": ["queue", "event emitter", "pubsub", "pub/sub", "message bus"],
        "async": ["async", "await", "concurrent", "parallel", "non-blocking"],
        "auth": ["auth", "jwt", "oauth", "login", "password", "token", "middleware"],
        "api": ["api", "rest", "endpoint", "fastapi", "flask", "http"],
        "db": ["database", "sql", "postgres", "mongodb", "orm", "query"],
        "singleton": ["singleton", "single instance"],
        "retry": ["retry", "backoff", "resilient", "circuit breaker"],
        "validation": ["validation", "schema", "pydantic", "sanitize"],
    }

    def _detect_domain(self, prompt_lower: str) -> str:
        """Detect the primary domain from prompt for contextual response."""
        for domain, keywords in self.DOMAIN_PATTERNS.items():
            if any(kw in prompt_lower for kw in keywords):
                return domain
        return "general"

    async def generate(
        self,
        system_prompt: str,
        messages: List[Dict[str, str]],
        agent_role: str,
        agent_name: str,
        task_context: Optional[str] = None,
    ) -> str:
        # Every simulated reply is prefixed so it can never pass for a real one.
        return SIMULATION_NOTICE + self._generate_reply(
            system_prompt, messages, agent_role, agent_name, task_context
        )

    def _generate_reply(self, system_prompt, messages, agent_role, agent_name, task_context) -> str:
        last_msg = messages[-1]["content"] if messages else (task_context or "")
        history_len = len(messages)
        prompt_lower = (task_context or last_msg).lower()
        domain = self._detect_domain(prompt_lower)
        # Differentiate based on agent role and conversation turn
        if "arena" in agent_name.lower():
            return self._generate_arena_response(last_msg, prompt_lower, history_len, domain)
        elif "copilot" in agent_name.lower():
            return self._generate_copilot_response(last_msg, prompt_lower, history_len, domain)
        elif "claude" in agent_name.lower():
            return self._generate_claude_response(last_msg, prompt_lower, history_len, domain)
        elif "gpt" in agent_name.lower():
            return self._generate_gpt_response(last_msg, prompt_lower, history_len, domain)
        else:
            return self._generate_generic_response(agent_name, agent_role, last_msg, history_len, domain)

    def _generate_arena_response(self, last_msg: str, prompt_lower: str, turn: int, domain: str) -> str:
        if turn <= 1:
            # Initial spec - make it specific to detected domain
            domain_specs = {
                "rate_limiter": "Rate limiting with token bucket, Redis backing, sliding window fallback",
                "cache": "Caching with TTL, LRU eviction, async invalidation, size bounds",
                "auth": "JWT authentication, refresh tokens, RBAC, secure middleware",
                "queue": "Event queue with backpressure, retries, dead-letter handling",
                "api": "REST API with validation, rate limiting, OpenAPI docs",
            }
            focus = domain_specs.get(domain, "Modular architecture with type safety and resilience")

            return (
                f"### [Arena AI System Spec & Task Breakdown]\n\n"
                f"Analyzed request: *\"{last_msg[:150]}...\"*\n\n"
                f"**Domain Detected:** `{domain}` | **Focus:** {focus}\n\n"
                f"**Requirements & Scope:**\n"
                f"1. **Core Domain Model:** Define robust state contracts and type annotations for `{domain}` domain.\n"
                f"2. **Processing Pipeline:** Implement asynchronous event execution with non-blocking I/O and proper error boundaries.\n"
                f"3. **Resilience & Validation:** Graceful degradation, input sanitization, and failure mode handling.\n"
                f"4. **Observability:** Logging, metrics, and clear status reporting for debugging.\n\n"
                f"**Acceptance criteria this simulation would aim at:**\n"
                f"- Typed, documented code that a reviewer could accept\n"
                f"- Edge cases to cover: empty inputs, concurrency, resource limits\n"
                f"- Usage examples in the docstrings\n\n"
                f"_Nothing has been written to disk or run yet - the criteria above are a plan, not a result._\n\n"
                f"**Handoff to @Copilot:**\n"
                f"Please implement the core module according to this specification. Ensure strict typing and comprehensive inline documentation."
            )
        else:
            return (
                f"### [Arena AI Synthesis & Final Summary]\n\n"
                f"Recap of what the simulated Copilot, Claude and GPT turns said.\n\n"
                f"**Context:** {last_msg[:200]}...\n\n"
                f"**Status: ⚠️ UNVERIFIED - simulated consensus, no human or model review took place**\n"
                f"- The modules above agreed in shape, not in fact: no code was executed\n"
                f"- No tests were run, so no claim of passing coverage is made here\n"
                f"- Treat the deliverable as a draft to review, not a sign-off\n\n"
                f"**Suggested next steps:**\n"
                f"1. Copy the implementation into your project\n"
                f"2. Run the proposed tests yourself with `pytest`\n"
                f"3. Adjust constants (TTL, limits) for your use case\n\n"
                f"The dialogue converged; the verification is still yours to do."
            )

    def _generate_copilot_response(self, last_msg: str, prompt_lower: str, turn: int, domain: str) -> str:
        # Contextual code generation based on domain
        code_examples = {
            "rate_limiter": (
                "import time\n"
                "import threading\n"
                "from collections import deque\n"
                "from typing import Optional\n\n"
                "class TokenBucketRateLimiter:\n"
                "    \"\"\"Thread-safe token bucket rate limiter.\n"
                "    \n"
                "    Example:\n"
                "        limiter = TokenBucketRateLimiter(rate=10, capacity=20)\n"
                "        if limiter.allow():\n"
                "            print('Request allowed')\n"
                "    \"\"\"\n"
                "    def __init__(self, rate: float, capacity: int):\n"
                "        self.rate = rate  # tokens per second\n"
                "        self.capacity = capacity\n"
                "        self._tokens = capacity\n"
                "        self._last_refill = time.monotonic()\n"
                "        self._lock = threading.Lock()\n\n"
                "    def allow(self, tokens: int = 1) -> bool:\n"
                "        with self._lock:\n"
                "            now = time.monotonic()\n"
                "            elapsed = now - self._last_refill\n"
                "            self._tokens = min(self.capacity, self._tokens + elapsed * self.rate)\n"
                "            self._last_refill = now\n"
                "            if self._tokens >= tokens:\n"
                "                self._tokens -= tokens\n"
                "                return True\n"
                "            return False\n"
            ),
            "cache": (
                "import time\n"
                "from functools import wraps\n"
                "from collections import OrderedDict\n"
                "from typing import Callable, Any, Dict, Tuple\n"
                "import threading\n\n"
                "class TTLCache:\n"
                "    \"\"\"Thread-safe LRU cache with TTL.\n"
                "    \n"
                "    Example:\n"
                "        @TTLCache(ttl_seconds=60, max_size=128)\n"
                "        def fetch_user(id: int):\n"
                "            return db.query(id)\n"
                "    \"\"\"\n"
                "    def __init__(self, ttl_seconds: int = 60, max_size: int = 1000):\n"
                "        self.ttl = ttl_seconds\n"
                "        self.max_size = max_size\n"
                "        self._store: OrderedDict[Tuple, Tuple[Any, float]] = OrderedDict()\n"
                "        self._lock = threading.Lock()\n\n"
                "    def __call__(self, func: Callable) -> Callable:\n"
                "        @wraps(func)\n"
                "        def wrapper(*args, **kwargs) -> Any:\n"
                "            key = (args, tuple(sorted(kwargs.items())))\n"
                "            now = time.time()\n"
                "            with self._lock:\n"
                "                if key in self._store:\n"
                "                    val, exp = self._store[key]\n"
                "                    if now < exp:\n"
                "                        self._store.move_to_end(key)\n"
                "                        return val\n"
                "                    del self._store[key]\n"
                "            res = func(*args, **kwargs)\n"
                "            with self._lock:\n"
                "                if len(self._store) >= self.max_size:\n"
                "                    self._store.popitem(last=False)\n"
                "                self._store[key] = (res, now + self.ttl)\n"
                "            return res\n"
                "        return wrapper\n"
            ),
            "queue": (
                "import asyncio\n"
                "from typing import Any, Callable, Dict, List, Optional\n"
                "import logging\n\n"
                "logger = logging.getLogger(__name__)\n\n"
                "class AsyncEventQueue:\n"
                "    \"\"\"Async event queue with backpressure and graceful shutdown.\n"
                "    \n"
                "    Example:\n"
                "        queue = AsyncEventQueue(maxsize=1000)\n"
                "        queue.on('user_created', handle_user)\n"
                "        await queue.emit('user_created', {'id': 1})\n"
                "        await queue.start_worker()\n"
                "    \"\"\"\n"
                "    def __init__(self, maxsize: int = 5000):\n"
                "        self._queue: asyncio.Queue = asyncio.Queue(maxsize=maxsize)\n"
                "        self._handlers: Dict[str, List[Callable]] = {}\n"
                "        self._running = False\n"
                "        self._worker_task: Optional[asyncio.Task] = None\n\n"
                "    async def emit(self, event_type: str, payload: Any) -> None:\n"
                "        try:\n"
                "            await asyncio.wait_for(self._queue.put((event_type, payload)), timeout=1.0)\n"
                "        except asyncio.TimeoutError:\n"
                "            logger.warning(f\"Queue full, dropping event {event_type}\")\n\n"
                "    def on(self, event_type: str, handler: Callable) -> None:\n"
                "        self._handlers.setdefault(event_type, []).append(handler)\n\n"
                "    async def start_worker(self) -> None:\n"
                "        self._running = True\n"
                "        while self._running:\n"
                "            try:\n"
                "                event_type, payload = await asyncio.wait_for(self._queue.get(), timeout=0.5)\n"
                "                handlers = self._handlers.get(event_type, [])\n"
                "                await asyncio.gather(*(h(payload) for h in handlers), return_exceptions=True)\n"
                "                self._queue.task_done()\n"
                "            except asyncio.TimeoutError:\n"
                "                continue\n\n"
                "    async def stop(self):\n"
                "        self._running = False\n"
                "        if self._worker_task:\n"
                "            self._worker_task.cancel()\n"
            ),
            "auth": (
                "import jwt\n"
                "import time\n"
                "from typing import Optional, Dict\n"
                "from functools import wraps\n"
                "from datetime import datetime, timedelta\n\n"
                "class JWTAuth:\n"
                "    \"\"\"JWT authentication with refresh support.\n"
                "    \n"
                "    Example:\n"
                "        auth = JWTAuth(secret='my-secret')\n"
                "        token = auth.create_token({'user_id': 123})\n"
                "        payload = auth.verify_token(token)\n"
                "    \"\"\"\n"
                "    def __init__(self, secret: str, algorithm: str = 'HS256'):\n"
                "        self.secret = secret\n"
                "        self.algorithm = algorithm\n\n"
                "    def create_token(self, payload: Dict, expires_in: int = 3600) -> str:\n"
                "        to_encode = payload.copy()\n"
                "        to_encode['exp'] = datetime.utcnow() + timedelta(seconds=expires_in)\n"
                "        to_encode['iat'] = datetime.utcnow()\n"
                "        return jwt.encode(to_encode, self.secret, algorithm=self.algorithm)\n\n"
                "    def verify_token(self, token: str) -> Optional[Dict]:\n"
                "        try:\n"
                "            return jwt.decode(token, self.secret, algorithms=[self.algorithm])\n"
                "        except jwt.ExpiredSignatureError:\n"
                "            return None\n"
                "        except jwt.InvalidTokenError:\n"
                "            return None\n"
            ),
            "retry": (
                "import time\n"
                "import random\n"
                "from functools import wraps\n"
                "from typing import Callable, Type, Tuple\n\n"
                "def retry_with_backoff(\n"
                "    max_retries: int = 3,\n"
                "    base_delay: float = 1.0,\n"
                "    max_delay: float = 60.0,\n"
                "    exceptions: Tuple[Type[Exception], ...] = (Exception,)\n"
                "):\n"
                "    \"\"\"Retry decorator with exponential backoff and jitter.\n"
                "    \n"
                "    Example:\n"
                "        @retry_with_backoff(max_retries=5, base_delay=0.5)\n"
                "        def call_api():\n"
                "            return requests.get('https://api.example.com')\n"
                "    \"\"\"\n"
                "    def decorator(func: Callable) -> Callable:\n"
                "        @wraps(func)\n"
                "        def wrapper(*args, **kwargs):\n"
                "            delay = base_delay\n"
                "            for attempt in range(max_retries + 1):\n"
                "                try:\n"
                "                    return func(*args, **kwargs)\n"
                "                except exceptions as e:\n"
                "                    if attempt == max_retries:\n"
                "                        raise\n"
                "                    jitter = random.uniform(0, delay * 0.1)\n"
                "                    time.sleep(delay + jitter)\n"
                "                    delay = min(delay * 2, max_delay)\n"
                "        return wrapper\n"
                "    return decorator\n"
            ),
        }

        code = code_examples.get(domain, (
            "from dataclasses import dataclass, field\n"
            "from typing import List, Dict, Optional\n"
            "import time\n"
            "import threading\n\n"
            "@dataclass\n"
            "class ModulePayload:\n"
            "    task_id: str\n"
            "    payload: Dict[str, any]\n"
            "    created_at: float = field(default_factory=time.time)\n"
            "    status: str = 'pending'\n\n"
            "class CoreServiceEngine:\n"
            "    \"\"\"Service engine with a lock around shared state (draft, untested).\n"
            "    \n"
            "    Example:\n"
            "        engine = CoreServiceEngine('my-service')\n"
            "        engine.execute_pipeline(payload)\n"
            "    \"\"\"\n"
            "    def __init__(self, service_name: str):\n"
            "        self.name = service_name\n"
            "        self.registry: Dict[str, ModulePayload] = {}\n"
            "        self._lock = threading.Lock()\n\n"
            "    def execute_pipeline(self, payload: ModulePayload) -> bool:\n"
            "        with self._lock:\n"
            "            if not payload.task_id:\n"
            "                raise ValueError('task_id required')\n"
            "            payload.status = 'processed'\n"
            "            self.registry[payload.task_id] = payload\n"
            "            return True\n"
        ))

        # If this is a revision turn, show improved version
        if "critique" in prompt_lower or "review" in prompt_lower or turn > 2:
            improvement_note = (
                "\n\n**🔄 Revision drafted against the critique:**\n"
                "- Locks around shared state\n"
                "- Input validation and error handling\n"
                "- Resource bounds and graceful degradation\n"
                "- Docstring with usage example\n\n"
                "_Changes are proposed only - no test run has confirmed them._"
            )
        else:
            improvement_note = ""

        return (
            f"### [GitHub Copilot Implementation Draft - {domain}]\n\n"
            f"A draft implementation shaped to your request (untested, unrun):\n\n"
            f"```python\n{code}\n```\n"
            f"{improvement_note}\n\n"
            f"**What the draft tries to do (not yet verified):**\n"
            f"- Thread safety via a lock around shared state\n"
            f"- Type annotations and basic input validation\n"
            f"- A docstring example you can paste and run\n"
            f"- Some edge cases in mind: empty inputs, resource limits, timeouts\n"
            f"- Ready for review by **@Claude** (architectural critique) and **@GPT** (test harness).\n\n"
            f"_Run it and add your own tests before trusting it._"
        )

    def _generate_claude_response(self, last_msg: str, prompt_lower: str, turn: int, domain: str) -> str:
        domain_concerns = {
            "rate_limiter": [
                "Token refill precision - use monotonic clock to avoid time drift",
                "Burst handling - consider allowing configurable burst capacity",
                "Distributed case - this is local only, need Redis for multi-instance",
            ],
            "cache": [
                "O(1) eviction - using OrderedDict is good, avoids O(n) scan",
                "Memory pressure - add max memory check, not just count",
                "Cache stampede - consider adding lock per key for thundering herd",
            ],
            "queue": [
                "Backpressure strategy - dropping vs blocking needs to be configurable",
                "Ordering guarantees - current impl doesn't guarantee order across handlers",
                "Poison pill - one bad event could block worker, need try/except per handler",
            ],
            "auth": [
                "Secret management - hardcoding secret is insecure, use env var",
                "Token revocation - no way to revoke token before expiry, need blocklist",
                "Timing attacks - use constant-time compare for token verification",
            ],
        }

        concerns = domain_concerns.get(domain, [
            "Concurrency & resource bounds - ensure upper bound to prevent memory exhaustion",
            "Error handling - add specific exception types, not bare Exception",
            "Graceful shutdown - add cancellation handlers for in-flight tasks",
        ])

        return (
            f"### [Claude Architectural & Safety Critique - {domain}]\n\n"
            f"Analyzed Copilot's implementation for `{domain}` domain.\n\n"
            f"**Strengths Identified:**\n"
            f"1. **Clean Separation of Concerns:** Core logic avoids unnecessary coupling\n"
            f"2. **Type Precision:** Good adherence to modern Python type hints\n"
            f"3. **Usability:** Includes docstring with example - great for developer experience\n\n"
            f"**Critical Observations & Recommendations:**\n"
            + "\n".join([f"- **{c.split(' - ')[0]}:** {c.split(' - ')[1] if ' - ' in c else c}" for c in concerns]) +
            "\n\n"
            "**Security & Reliability Checklist (unchecked - this is a review prompt, not an audit):**\n"
            "- [ ] Input validation on all public methods\n"
            "- [ ] Thread safety verified under load\n"
            "- [ ] Resource limits enforced (memory, queue size)\n"
            "- [ ] Graceful degradation on failure\n\n"
            "**Suggested Improvement:**\n"
            "```python\n"
            "# Add this to improve robustness:\n"
            "def __repr__(self):\n"
            "    return f\"{self.__class__.__name__}(...)\"  # Don't leak secrets in logs\n"
            "```\n\n"
            "Passing to **@GPT** to construct integration test suites verifying these boundary conditions."
        )

    def _generate_gpt_response(self, last_msg: str, prompt_lower: str, turn: int, domain: str) -> str:
        test_templates = {
            "rate_limiter": (
                "import pytest\n"
                "import time\n"
                "import threading\n\n"
                "def test_allows_within_capacity():\n"
                "    limiter = TokenBucketRateLimiter(rate=10, capacity=5)\n"
                "    assert all(limiter.allow() for _ in range(5))\n"
                "    assert not limiter.allow()\n\n"
                "def test_refills_over_time():\n"
                "    limiter = TokenBucketRateLimiter(rate=10, capacity=10)\n"
                "    for _ in range(10):\n"
                "        limiter.allow()\n"
                "    time.sleep(0.2)\n"
                "    assert limiter.allow()\n\n"
                "def test_thread_safety():\n"
                "    limiter = TokenBucketRateLimiter(rate=100, capacity=100)\n"
                "    results = []\n"
                "    def worker():\n"
                "        for _ in range(20):\n"
                "            results.append(limiter.allow())\n"
                "    threads = [threading.Thread(target=worker) for _ in range(5)]\n"
                "    for t in threads: t.start()\n"
                "    for t in threads: t.join()\n"
                "    assert sum(results) == 100  # Only capacity allowed\n"
            ),
            "cache": (
                "import pytest\n"
                "import time\n\n"
                "def test_cache_hit():\n"
                "    @TTLCache(ttl_seconds=10, max_size=10)\n"
                "    def fetch(x):\n"
                "        return x * 2\n"
                "    assert fetch(5) == 10\n"
                "    assert fetch(5) == 10  # cached\n\n"
                "def test_ttl_expiry():\n"
                "    @TTLCache(ttl_seconds=0.1, max_size=10)\n"
                "    def fetch(x):\n"
                "        return x\n"
                "    fetch(1)\n"
                "    time.sleep(0.2)\n"
                "    # Should recompute after expiry\n"
                "    assert fetch(1) == 1\n\n"
                "def test_lru_eviction():\n"
                "    @TTLCache(ttl_seconds=60, max_size=2)\n"
                "    def fetch(x):\n"
                "        return x\n"
                "    fetch(1); fetch(2); fetch(3)\n"
                "    # 1 should be evicted\n"
                "    assert len(fetch.__wrapped__._store) == 2\n"
            ),
        }

        test_code = test_templates.get(domain, (
            "import pytest\n"
            "import asyncio\n\n"
            "@pytest.mark.asyncio\n"
            "async def test_module_lifecycle():\n"
            "    # 1. Test nominal execution\n"
            "    assert True, 'Module initialized successfully'\n\n"
            "@pytest.mark.asyncio\n"
            "async def test_concurrent_throughput():\n"
            "    # 2. Test high volume - should handle 100 concurrent ops\n"
            "    messages = [f'msg_{i}' for i in range(100)]\n"
            "    assert len(messages) == 100\n\n"
            "def test_edge_cases():\n"
            "    # 3. Test boundaries: empty, None, max size\n"
            "    data = {}\n"
            "    for i in range(150):\n"
            "        data[f'key_{i}'] = i\n"
            "    assert len(data) == 150\n"
            "    # Should handle empty input gracefully\n"
            "    assert data.get('nonexistent') is None\n\n"
            "def test_error_handling():\n"
            "    # 4. Test invalid inputs raise clear errors\n"
            "    with pytest.raises((ValueError, TypeError)):\n"
            "        # Should validate inputs\n"
            "        pass\n"
        ))

        return (
            f"### [GPT Proposed Test Suite - {domain}]\n\n"
            f"Synthesizing test coverage for `{domain}` based on Claude's review and Copilot's code:\n\n"
            f"```python\n{test_code}\n```\n\n"
            f"**What these tests would cover (nothing has been executed yet):**\n"
            f"- `test_allows_within_capacity` / `test_cache_hit`: the nominal path\n"
            f"- `test_refills_over_time` / `test_ttl_expiry`: time-based behaviour\n"
            f"- `test_thread_safety` / `test_lru_eviction`: concurrency and bounds\n"
            f"- `test_edge_cases`: empty inputs and max limits\n\n"
            f"**How to run them yourself:**\n"
            f"```bash\n"
            f"pytest -v --tb=short\n"
            f"```\n\n"
            f"The simulator cannot know whether they pass - save the file, run `pytest`, "
            f"and read the real result. Returning to **@Arena AI** for the summary."
        )

    def _generate_generic_response(self, name: str, role: str, last_msg: str, turn: int, domain: str) -> str:
        return (
            f"### [{name} - {role}]\n\n"
            f"**Domain:** `{domain}` | **Turn:** {turn}\n\n"
            f"Received: *\"{last_msg[:150]}...\"*\n\n"
            f"Simulated `{domain}` step for **{role}** - a template answer, not a tool run.\n"
            f"- Read the incoming context and pulled out requirements\n"
            f"- Listed constraints and edge cases worth checking\n"
            f"- Drafted output in the shape a {domain} reviewer expects\n"
            f"- Forwarding to peer modules for cross-review\n\n"
            f"**Next:** peers should look at thread safety, error handling, and resource bounds."
        )


class OpenAIProvider(BaseLLMProvider):
    """Integration for OpenAI API or OpenAI-compatible backends (Ollama, LMStudio, vLLM)."""

    label = "OpenAI"

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        model: str = "gpt-4o",
        fallback_to_mock: bool = True,
        allow_env_key: bool = True,
        timeout: float = DEFAULT_REQUEST_TIMEOUT,
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
        retry_backoff: float = DEFAULT_RETRY_BACKOFF,
    ):
        # ``allow_env_key`` is the boundary the dashboard needs: a browser-supplied
        # base URL must never pick up the operator's ambient credential, or pointing
        # the config at an attacker host turns into stealing that key.
        self.allow_env_key = bool(allow_env_key)
        env_key = os.environ.get("OPENAI_API_KEY", "") if self.allow_env_key else ""
        self.api_key = (api_key or env_key).strip()
        self.base_url = (base_url or os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1")).rstrip("/")
        self.model = model
        #: When False, provider problems raise :class:`ProviderError` instead of
        #: quietly answering with the simulator.
        self.fallback_to_mock = fallback_to_mock
        self.timeout = float(timeout)
        self.max_attempts = max(1, int(max_attempts))
        self.retry_backoff = max(0.0, float(retry_backoff))

    async def _simulate(self, system_prompt, messages, agent_role, agent_name, task_context, reason: str) -> str:
        """Simulator answer, explicitly labelled as *not* an OpenAI answer."""
        if not self.fallback_to_mock:
            raise ProviderError(self.label, reason)
        text = await MockLLMProvider().generate(system_prompt, messages, agent_role, agent_name, task_context)
        return FALLBACK_NOTICE_TEMPLATE.format(provider=self.label, reason=reason) + text

    async def generate(
        self,
        system_prompt: str,
        messages: List[Dict[str, str]],
        agent_role: str,
        agent_name: str,
        task_context: Optional[str] = None,
    ) -> str:
        local_backend = "localhost" in self.base_url or "127.0.0.1" in self.base_url or "::1" in self.base_url
        if not self.api_key and not local_backend:
            # Zero-config mode: be explicit that this is not an OpenAI answer.
            logger.info("No OpenAI API key configured - answering with the built-in simulator")
            return await self._simulate(
                system_prompt, messages, agent_role, agent_name, task_context,
                reason="no API key is configured, so this is not an OpenAI answer",
            )

        url = f"{self.base_url}/chat/completions"
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            # Sent only when this session actually supplied a key: an empty
            # "Bearer " header is both a broken credential and a signal that the
            # server has one to lose.
            headers["Authorization"] = f"Bearer {self.api_key}"
        formatted_msgs = [{"role": "system", "content": system_prompt}, *messages]

        payload = {
            "model": self.model,
            "messages": formatted_msgs,
            "temperature": 0.7,
        }

        # A truncated answer is still an answer, but it must never *look* complete.
        # ``stats`` carries what the exchange cost, so the shape checks below can
        # report the real attempt count instead of an assumed one.
        stats: Dict[str, float] = {}
        data = await post_for_json(provider=self, url=url, headers=headers, payload=payload, stats=stats)

        try:
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise _shape_error(self, "the API response did not contain a message", stats) from exc
        if not isinstance(content, str) or not content.strip():
            raise _shape_error(self, "the API returned an empty answer", stats)
        return content


class AnthropicProvider(BaseLLMProvider):
    """Integration for Anthropic Messages API (Claude)."""

    label = "Anthropic"

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: str = "claude-3-5-sonnet-20241022",
        fallback_to_mock: bool = True,
        allow_env_key: bool = True,
        timeout: float = DEFAULT_REQUEST_TIMEOUT,
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
        retry_backoff: float = DEFAULT_RETRY_BACKOFF,
    ):
        self.allow_env_key = bool(allow_env_key)
        env_key = os.environ.get("ANTHROPIC_API_KEY", "") if self.allow_env_key else ""
        self.api_key = (api_key or env_key).strip()
        self.model = model
        self.fallback_to_mock = fallback_to_mock
        self.timeout = float(timeout)
        self.max_attempts = max(1, int(max_attempts))
        self.retry_backoff = max(0.0, float(retry_backoff))
        self.base_url = "https://api.anthropic.com/v1"

    async def _simulate(self, system_prompt, messages, agent_role, agent_name, task_context, reason: str) -> str:
        if not self.fallback_to_mock:
            raise ProviderError(self.label, reason)
        text = await MockLLMProvider().generate(system_prompt, messages, agent_role, agent_name, task_context)
        return FALLBACK_NOTICE_TEMPLATE.format(provider=self.label, reason=reason) + text

    async def generate(
        self,
        system_prompt: str,
        messages: List[Dict[str, str]],
        agent_role: str,
        agent_name: str,
        task_context: Optional[str] = None,
    ) -> str:
        if not self.api_key:
            logger.info("No Anthropic API key configured - answering with the built-in simulator")
            return await self._simulate(
                system_prompt, messages, agent_role, agent_name, task_context,
                reason="no API key is configured, so this is not an Anthropic answer",
            )

        url = f"{self.base_url}/messages"
        headers = {
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }

        # Filter out system message and format for Anthropic
        anthropic_msgs = []
        for m in messages:
            role = "assistant" if m.get("role") == "assistant" else "user"
            anthropic_msgs.append({"role": role, "content": m.get("content", "")})

        if not anthropic_msgs:
            anthropic_msgs = [{"role": "user", "content": task_context or "Hello"}]

        payload = {
            "model": self.model,
            "system": system_prompt,
            "messages": anthropic_msgs,
            "max_tokens": 2048,
        }

        stats: Dict[str, float] = {}
        data = await post_for_json(provider=self, url=url, headers=headers, payload=payload, stats=stats)

        # A block is text only when its ``text`` *is* a string, and ``content``
        # itself has to be a list to be iterable. Both guards are load-bearing: a
        # gateway that answers ``{"text": 123}`` (or a ``content`` that is not even
        # a list) used to reach the ``join`` below and raise a raw ``TypeError``
        # from after the retry loop - not a ``ProviderError``, so the turn lost its
        # provider, its attempt count and its ``waited``, and with fallback off the
        # run died on a join (D36). A block this provider cannot read is skipped,
        # exactly as a block that is not ``type: "text"`` already was.
        blocks = data.get("content") if isinstance(data, dict) else None
        texts = [
            block["text"]
            for block in (blocks if isinstance(blocks, list) else [])
            if isinstance(block, dict)
            and block.get("type") == "text"
            and isinstance(block.get("text"), str)
        ]
        content = "\n".join(texts).strip()
        if not content:
            raise _shape_error(self, "the API response did not contain a text block", stats)
        return content
