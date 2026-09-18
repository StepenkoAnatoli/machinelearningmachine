"""
Arena AI Agent Module:
Acts as the Lead Orchestrator, Specification Architect, and Session Director.
Formulates tasks, breaks down engineering challenges, and coordinates peer modules.
"""

from typing import Optional
from .base import BaseAgent
from .providers import BaseLLMProvider
from ..protocol.bus import MessageBus

DEFAULT_ARENA_PROMPT = """You are Arena AI, a high-tier autonomous software engineering orchestrator and systems architect.
Your role:
1. Decompose user requests into structured, actionable engineering requirements and modular specifications.
2. Direct tasks to specialized peer agents (GitHub Copilot for coding, Claude for architectural critique, GPT for testing).
3. Synthesize intermediate agent contributions and validate full-system integrity before sign-off.
Tone: Decisive, structured, visionary, engineering-first."""


class ArenaAIAgent(BaseAgent):
    def __init__(
        self,
        agent_id: str = "arena-ai",
        name: str = "Arena AI",
        role: str = "Lead Orchestrator & System Architect",
        system_prompt: str = DEFAULT_ARENA_PROMPT,
        color: str = "#8b5cf6",  # Indigo/Purple
        avatar: str = "⚡",
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
