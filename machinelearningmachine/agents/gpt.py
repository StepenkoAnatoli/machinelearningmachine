"""
GPT Agent Module (ChatGPT / GPT-4o):
Acts as the Verification Engineer, Test Harness Synthesizer, and Logic Validator.
Builds comprehensive pytest suites, fuzz tests, integration benchmarks, and validation criteria.
"""

from typing import Optional

from ..protocol.bus import MessageBus
from .base import BaseAgent
from .providers import BaseLLMProvider

DEFAULT_GPT_PROMPT = """You are GPT-4o, a verification engineer and test harness synthesizer.
Your role:
1. Examine code proposals from Copilot and critique from Claude.
2. Generate comprehensive, production-grade pytest unit and integration test suites.
3. Validate performance constraints, fuzzing edge cases, and ensure robust assertions.
Tone: Structured, rigorous, verification-obsessed, empirical."""


class GPTAgent(BaseAgent):
    def __init__(
        self,
        agent_id: str = "gpt",
        name: str = "GPT-4o",
        role: str = "Test Suite Synthesizer & Logic Validator",
        system_prompt: str = DEFAULT_GPT_PROMPT,
        color: str = "#10b981",  # OpenAI Emerald
        avatar: str = "🌐",
        provider: Optional[BaseLLMProvider] = None,
        bus: Optional[MessageBus] = None,
    ):
        super().__init__(
            agent_id=agent_id,
            name=name,
            role=role,
            system_prompt=system_prompt,
            color=color,
            avatar=avatar,
            provider=provider,
            bus=bus,
        )
