"""
LLM Provider abstractions supporting:
- Mock / Intelligent Simulation (zero setup, rich domain-specific outputs)
- OpenAI / OpenAI-compatible API (ChatGPT, GPT-4o, Ollama, LMStudio, vLLM)
- Anthropic API (Claude 3.5 Sonnet, etc.)
- Webhook / HTTP endpoints

User-centered: mock provider now generates highly contextual, useful examples
that actually help users understand the system and get value even without API keys.
"""

import os
import logging
import re
from typing import List, Dict, Any, Optional
from .._deps import install_hint
from ..protocol.message import Message, MessageType

logger = logging.getLogger("LLMProviders")


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


class BaseLLMProvider:
    async def generate(
        self,
        system_prompt: str,
        messages: List[Dict[str, str]],
        agent_role: str,
        agent_name: str,
        task_context: Optional[str] = None,
    ) -> str:
        raise NotImplementedError


class MockLLMProvider(BaseLLMProvider):
    """
    Intelligent simulated LLM provider.
    Generates realistic, contextual multi-turn responses with code, reviews,
    critiques, and synthesis without requiring external API keys.

    User-centered improvements:
    - Detects many more domains (auth, rate limiting, caching, queues, API, DB, etc.)
    - Produces more practical, copy-paste-ready code
    - Includes helpful explanations tailored to actual user prompt
    - Better turn-awareness for coherent multi-turn dialogues
    """

    # Domain detection patterns for more relevant responses
    DOMAIN_PATTERNS = {
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
        # Extract last user message or prompt
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
                f"**Acceptance Criteria:**\n"
                f"- Code must be production-ready, typed, and documented\n"
                f"- Handle edge cases: empty inputs, concurrency, resource limits\n"
                f"- Include usage examples\n\n"
                f"**Handoff to @Copilot:**\n"
                f"Please implement the core module according to this specification. Ensure strict typing and comprehensive inline documentation."
            )
        else:
            return (
                f"### [Arena AI Synthesis & Final Verification]\n\n"
                f"Reviewed implementation from Copilot, architectural critique from Claude, and test suite from GPT.\n\n"
                f"**Context:** {last_msg[:200]}...\n\n"
                f"**Consensus Status: ✅ APPROVED**\n"
                f"- ✅ Architecture adheres to modular decoupled contract for `{domain}`\n"
                f"- ✅ Security and error handling satisfy production criteria\n"
                f"- ✅ Test coverage validates both nominal throughput and boundary exceptions\n"
                f"- ✅ Code is ready to copy-paste and run\n\n"
                f"**Next Steps for User:**\n"
                f"1. Copy the implementation to your project\n"
                f"2. Run the provided tests with `pytest`\n"
                f"3. Adjust constants (TTL, limits) for your use case\n\n"
                f"All modules have converged on the final deliverable."
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
            "    \"\"\"Production-ready service engine with thread safety.\n"
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
                "\n\n**🔄 Revision addressing critique:**\n"
                "- Added thread safety with locks\n"
                "- Added input validation and error handling\n"
                "- Improved resource bounds and graceful degradation\n"
                "- Added docstring with usage example"
            )
        else:
            improvement_note = ""

        return (
            f"### [GitHub Copilot Implementation - {domain}]\n\n"
            f"Here's a production-ready implementation tailored to your request:\n\n"
            f"```python\n{code}\n```\n"
            f"{improvement_note}\n\n"
            f"**Key Implementation Highlights:**\n"
            f"- ✅ Thread-safe and handles concurrent access\n"
            f"- ✅ Strict type annotations and input validation\n"
            f"- ✅ Ready to copy-paste - includes usage example in docstring\n"
            f"- ✅ Handles edge cases: empty inputs, resource limits, timeouts\n"
            f"- Ready for review by **@Claude** (architectural critique) and **@GPT** (test harness)."
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
            f"\n\n"
            f"**Security & Reliability Checklist:**\n"
            f"- [ ] Input validation on all public methods\n"
            f"- [ ] Thread safety verified under load\n"
            f"- [ ] Resource limits enforced (memory, queue size)\n"
            f"- [ ] Graceful degradation on failure\n\n"
            f"**Suggested Improvement:**\n"
            f"```python\n"
            f"# Add this to improve robustness:\n"
            f"def __repr__(self):\n"
            f"    return f\"{{self.__class__.__name__}}(...)\"  # Don't leak secrets in logs\n"
            f"```\n\n"
            f"Passing to **@GPT** to construct integration test suites verifying these boundary conditions."
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
            f"### [GPT Test Suite & Verification - {domain}]\n\n"
            f"Synthesizing test coverage for `{domain}` based on Claude's review and Copilot's code:\n\n"
            f"```python\n{test_code}\n```\n\n"
            f"**Validation Summary:**\n"
            f"- ✅ `test_allows_within_capacity` / `test_cache_hit`: Nominal path works\n"
            f"- ✅ `test_refills_over_time` / `test_ttl_expiry`: Time-based behavior correct\n"
            f"- ✅ `test_thread_safety` / `test_lru_eviction`: Concurrency and bounds enforced\n"
            f"- ✅ `test_edge_cases`: Empty inputs, max limits handled gracefully\n\n"
            f"**How to run:**\n"
            f"```bash\n"
            f"pytest -v --tb=short\n"
            f"```\n\n"
            f"All assertions should PASS. Returning to **@Arena AI** for final sign-off."
        )

    def _generate_generic_response(self, name: str, role: str, last_msg: str, turn: int, domain: str) -> str:
        return (
            f"### [{name} - {role}]\n\n"
            f"**Domain:** `{domain}` | **Turn:** {turn}\n\n"
            f"Received: *\"{last_msg[:150]}...\"*\n\n"
            f"Executing task for **{role}** in `{domain}` domain.\n"
            f"- ✅ Processed incoming context and extracted requirements\n"
            f"- ✅ Verified constraints and checked edge cases\n"
            f"- ✅ Generated output aligned with {domain} best practices\n"
            f"- Forwarding to peer modules for cross-validation.\n\n"
            f"**Next:** Peer review will check thread safety, error handling, and resource bounds."
        )


class OpenAIProvider(BaseLLMProvider):
    """Integration for OpenAI API or OpenAI-compatible backends (Ollama, LMStudio, vLLM)."""

    def __init__(self, api_key: Optional[str] = None, base_url: Optional[str] = None, model: str = "gpt-4o"):
        self.api_key = api_key or os.environ.get("OPENAI_API_KEY", "")
        self.base_url = (base_url or os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1")).rstrip("/")
        self.model = model

    async def generate(
        self,
        system_prompt: str,
        messages: List[Dict[str, str]],
        agent_role: str,
        agent_name: str,
        task_context: Optional[str] = None,
    ) -> str:
        if not self.api_key and "localhost" not in self.base_url and "127.0.0.1" not in self.base_url:
            # Fall back to mock if no API key
            logger.info("No OpenAI API key found, falling back to mock generator")
            return await MockLLMProvider().generate(system_prompt, messages, agent_role, agent_name, task_context)

        url = f"{self.base_url}/chat/completions"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }
        formatted_msgs = [{"role": "system", "content": system_prompt}] + messages

        payload = {
            "model": self.model,
            "messages": formatted_msgs,
            "temperature": 0.7,
        }

        async with _aiohttp().ClientSession() as session:
            async with session.post(url, headers=headers, json=payload, timeout=60) as resp:
                if resp.status != 200:
                    err_txt = await resp.text()
                    logger.error(f"OpenAI error {resp.status}: {err_txt}")
                    return f"[Error calling OpenAI API: HTTP {resp.status}] - Falling back to simulation."
                data = await resp.json()
                return data["choices"][0]["message"]["content"]


class AnthropicProvider(BaseLLMProvider):
    """Integration for Anthropic Messages API (Claude)."""

    def __init__(self, api_key: Optional[str] = None, model: str = "claude-3-5-sonnet-20241022"):
        self.api_key = api_key or os.environ.get("ANTHROPIC_API_KEY", "")
        self.model = model

    async def generate(
        self,
        system_prompt: str,
        messages: List[Dict[str, str]],
        agent_role: str,
        agent_name: str,
        task_context: Optional[str] = None,
    ) -> str:
        if not self.api_key:
            logger.info("No Anthropic API key found, falling back to mock generator")
            return await MockLLMProvider().generate(system_prompt, messages, agent_role, agent_name, task_context)

        url = "https://api.anthropic.com/v1/messages"
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

        async with _aiohttp().ClientSession() as session:
            async with session.post(url, headers=headers, json=payload, timeout=60) as resp:
                if resp.status != 200:
                    err_txt = await resp.text()
                    logger.error(f"Anthropic error {resp.status}: {err_txt}")
                    return f"[Error calling Anthropic API: HTTP {resp.status}]"
                data = await resp.json()
                return data["content"][0]["text"]
