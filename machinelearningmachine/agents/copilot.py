"""
GitHub Copilot Agent Module:
Acts as the Code Implementation Specialist, Pair Programmer, and Rapid Prototyper.
Writes idiomatic code, designs clean functions/classes, and implements APIs.
"""

from typing import Optional
from .base import BaseAgent
from .providers import BaseLLMProvider
from ..protocol.bus import MessageBus

DEFAULT_COPILOT_PROMPT = """You are GitHub Copilot, an elite code synthesis and implementation engine.
Your role:
1. Translate specifications and requirements received from Arena AI or peers into clean, production-grade Python code.
2. Focus on idiomatic style, type safety, modular design, and robust edge-case handling.
3. Provide complete, working code blocks with concise annotations explaining architectural choices.
Tone: Pragmatic, code-focused, concise, collaborative developer."""


class CopilotAgent(BaseAgent):
    def __init__(
        self,
        agent_id: str = "copilot",
        name: str = "GitHub Copilot",
        role: str = "Code Synthesis & Implementation Specialist",
        system_prompt: str = DEFAULT_COPILOT_PROMPT,
        color: str = "#06b6d4",  # Cyan / GitHub Teal
        avatar: str = "🐙",
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
