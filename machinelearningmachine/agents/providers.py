"""
LLM Provider abstractions supporting:
- Mock / Intelligent Simulation (zero setup, rich domain-specific outputs)
- OpenAI / OpenAI-compatible API (ChatGPT, GPT-4o, Ollama, LMStudio, vLLM)
- Anthropic API (Claude 3.5 Sonnet, etc.)
- Webhook / HTTP endpoints
"""

import os
import aiohttp
import logging
from typing import List, Dict, Any, Optional
from ..protocol.message import Message, MessageType

logger = logging.getLogger("LLMProviders")


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
    """

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

        # Differentiate based on agent role and conversation turn
        if "arena" in agent_name.lower():
            return self._generate_arena_response(last_msg, prompt_lower, history_len)
        elif "copilot" in agent_name.lower():
            return self._generate_copilot_response(last_msg, prompt_lower, history_len)
        elif "claude" in agent_name.lower():
            return self._generate_claude_response(last_msg, prompt_lower, history_len)
        elif "gpt" in agent_name.lower():
            return self._generate_gpt_response(last_msg, prompt_lower, history_len)
        else:
            return self._generate_generic_response(agent_name, agent_role, last_msg, history_len)

    def _generate_arena_response(self, last_msg: str, prompt_lower: str, turn: int) -> str:
        if turn <= 1:
            return (
                f"### [Arena AI System Spec & Task Breakdown]\n\n"
                f"I have analyzed the objective and decomposed it into a clean, modular architecture.\n\n"
                f"**Requirements & Scope:**\n"
                f"1. **Core Domain Model:** Define robust state contracts and type annotations.\n"
                f"2. **Processing Pipeline:** Implement asynchronous event execution with non-blocking I/O.\n"
                f"3. **Resilience & Validation:** Graceful degradation on edge cases and failure modes.\n\n"
                f"**Handoff to @Copilot:**\n"
                f"Please implement the core module according to this specification. Ensure strict typing and comprehensive inline documentation."
            )
        else:
            return (
                f"### [Arena AI Synthesis & Final Verification]\n\n"
                f"Reviewed the implementation from Copilot, the architectural critique from Claude, and the test suite from GPT.\n\n"
                f"**Consensus Status: APPROVED**\n"
                f"- Architecture adheres to our modular decoupled contract.\n"
                f"- Security and error handling satisfy production criteria.\n"
                f"- Test coverage validates both nominal throughput and boundary exceptions.\n\n"
                f"All modules have converged on the final deliverable."
            )

    def _generate_copilot_response(self, last_msg: str, prompt_lower: str, turn: int) -> str:
        if "cache" in prompt_lower or "caching" in prompt_lower:
            code = (
                "import time\n"
                "from functools import wraps\n"
                "from typing import Callable, Any, Dict, Tuple\n\n"
                "class TTLCache:\n"
                "    def __init__(self, ttl_seconds: int = 60, max_size: int = 1000):\n"
                "        self.ttl = ttl_seconds\n"
                "        self.max_size = max_size\n"
                "        self._store: Dict[Tuple, Tuple[Any, float]] = {}\n\n"
                "    def __call__(self, func: Callable) -> Callable:\n"
                "        @wraps(func)\n"
                "        def wrapper(*args, **kwargs) -> Any:\n"
                "            key = (args, tuple(sorted(kwargs.items())))\n"
                "            now = time.time()\n"
                "            if key in self._store:\n"
                "                val, exp = self._store[key]\n"
                "                if now < exp:\n"
                "                    return val\n"
                "            res = func(*args, **kwargs)\n"
                "            if len(self._store) >= self.max_size:\n"
                "                # Evict oldest entry\n"
                "                oldest_key = min(self._store.keys(), key=lambda k: self._store[k][1])\n"
                "                del self._store[oldest_key]\n"
                "            self._store[key] = (res, now + self.ttl)\n"
                "            return res\n"
                "        return wrapper\n"
            )
        elif "queue" in prompt_lower or "event" in prompt_lower or "async" in prompt_lower:
            code = (
                "import asyncio\n"
                "from typing import Any, Callable, Dict, List\n\n"
                "class AsyncEventQueue:\n"
                "    def __init__(self, maxsize: int = 5000):\n"
                "        self._queue: asyncio.Queue = asyncio.Queue(maxsize=maxsize)\n"
                "        self._handlers: Dict[str, List[Callable]] = {}\n"
                "        self._running = False\n\n"
                "    async def emit(self, event_type: str, payload: Any) -> None:\n"
                "        await self._queue.put((event_type, payload))\n\n"
                "    def on(self, event_type: str, handler: Callable) -> None:\n"
                "        self._handlers.setdefault(event_type, []).append(handler)\n\n"
                "    async def start_worker(self) -> None:\n"
                "        self._running = True\n"
                "        while self._running:\n"
                "            event_type, payload = await self._queue.get()\n"
                "            handlers = self._handlers.get(event_type, [])\n"
                "            await asyncio.gather(*(h(payload) for h in handlers), return_exceptions=True)\n"
                "            self._queue.task_done()\n"
            )
        else:
            code = (
                "from dataclasses import dataclass, field\n"
                "from typing import List, Dict, Optional\n"
                "import time\n\n"
                "@dataclass\n"
                "class ModulePayload:\n"
                "    task_id: str\n"
                "    payload: Dict[str, any]\n"
                "    created_at: float = field(default_factory=time.time)\n"
                "    status: str = 'pending'\n\n"
                "class CoreServiceEngine:\n"
                "    def __init__(self, service_name: str):\n"
                "        self.name = service_name\n"
                "        self.registry: Dict[str, ModulePayload] = {}\n\n"
                "    def execute_pipeline(self, payload: ModulePayload) -> bool:\n"
                "        # Process payload across modular lifecycle\n"
                "        payload.status = 'processed'\n"
                "        self.registry[payload.task_id] = payload\n"
                "        return True\n"
            )

        return (
            f"### [GitHub Copilot Implementation]\n\n"
            f"Here is the code implementation designed for modularity, safety, and performance:\n\n"
            f"```python\n{code}```\n\n"
            f"**Key Implementation Highlights:**\n"
            f"- Thread-safe and asynchronous non-blocking design.\n"
            f"- Strict type annotations across all inputs and returns.\n"
            f"- Ready for review by **@Claude** (architectural critique) and **@GPT** (test harness)."
        )

    def _generate_claude_response(self, last_msg: str, prompt_lower: str, turn: int) -> str:
        return (
            f"### [Claude Architectural & Safety Critique]\n\n"
            f"I have analyzed Copilot's proposed implementation from a systems and reliability perspective.\n\n"
            f"**Strengths Identified:**\n"
            f"1. **Clean Separation of Concerns:** Core logic avoids unnecessary coupling.\n"
            f"2. **Type Precision:** High adherence to modern Python type hint conventions.\n\n"
            f"**Critical Observations & Recommendations:**\n"
            f"- **Concurrency & Eviction Overhead:** The eviction step in in-memory storage could be O(1) if backed by an `OrderedDict` or deque rather than scanning keys with `min()`.\n"
            f"- **Memory Bounds:** Ensure an upper bound on unprocessed task queues to prevent memory exhaustion under sudden traffic spikes.\n"
            f"- **Graceful Shutdown:** Add cancellation signal handlers so in-flight tasks can flush cleanly.\n\n"
            f"Passing to **@GPT** to construct integration test suites verifying these boundary conditions."
        )

    def _generate_gpt_response(self, last_msg: str, prompt_lower: str, turn: int) -> str:
        test_code = (
            "import pytest\n"
            "import asyncio\n"
            "import time\n\n"
            "@pytest.mark.asyncio\n"
            "async def test_module_lifecycle():\n"
            "    # 1. Test nominal execution\n"
            "    assert True, 'Module initialized successfully'\n\n"
            "@pytest.mark.asyncio\n"
            "async def test_concurrent_message_throughput():\n"
            "    # 2. Test high volume message exchange\n"
            "    messages = [f'msg_{i}' for i in range(100)]\n"
            "    assert len(messages) == 100\n\n"
            "def test_edge_case_eviction_and_boundaries():\n"
            "    # 3. Test memory boundary resilience\n"
            "    data = {}\n"
            "    for i in range(150):\n"
            "        data[f'key_{i}'] = i\n"
            "    assert len(data) == 150\n"
        )
        return (
            f"### [GPT Test Suite & Verification Harness]\n\n"
            f"Synthesizing full unit and edge-case test coverage based on Claude's review and Copilot's code:\n\n"
            f"```python\n{test_code}```\n\n"
            f"**Validation Summary:**\n"
            f"- `test_module_lifecycle`: Verifies state transitions and error recovery.\n"
            f"- `test_concurrent_message_throughput`: Confirms latency stability under concurrent agent loads.\n"
            f"- `test_edge_case_eviction_and_boundaries`: Proves bounds limits and graceful eviction.\n\n"
            f"All integration assertions PASS. Returning control to **@Arena AI** for final release authorization."
        )

    def _generate_generic_response(self, name: str, role: str, last_msg: str, turn: int) -> str:
        return (
            f"### [{name} - {role}]\n\n"
            f"Received payload: *\"{last_msg[:120]}...\"*\n\n"
            f"Executing module task according to assigned role: **{role}**.\n"
            f"- Processed incoming context (turn {turn}).\n"
            f"- Verified constraints and updated shared workspace state.\n"
            f"- Forwarding output to peer modules."
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

        async with aiohttp.ClientSession() as session:
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

        async with aiohttp.ClientSession() as session:
            async with session.post(url, headers=headers, json=payload, timeout=60) as resp:
                if resp.status != 200:
                    err_txt = await resp.text()
                    logger.error(f"Anthropic error {resp.status}: {err_txt}")
                    return f"[Error calling Anthropic API: HTTP {resp.status}]"
                data = await resp.json()
                return data["content"][0]["text"]
