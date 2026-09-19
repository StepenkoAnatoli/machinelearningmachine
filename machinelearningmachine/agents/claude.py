"""
Claude Agent Module:
Acts as the Deep Systems Architect, Safety Specialist, and Code Review Critic.
Examines concurrency safety, algorithmic complexity, boundary conditions, and security.
"""

from typing import Optional

from ..protocol.bus import MessageBus
from .base import BaseAgent
from .providers import ANTHROPIC_MODEL_DISPLAY_NAME, BaseLLMProvider

DEFAULT_CLAUDE_PROMPT = """You are Claude Fable 5.1, a deep systems architect, code reviewer, and safety analyst.
Your role:
1. Thoroughly critique implementations from Copilot and proposals from Arena AI.
2. Uncover edge cases, race conditions, memory leaks, algorithmic inefficiencies, and security flaws.
3. Suggest concrete, actionable improvements, better data structures, and architectural refinements.
Tone: Thoughtful, highly analytical, constructive, safety-conscious."""


class ClaudeAgent(BaseAgent):
    def __init__(
        self,
        agent_id: str = "claude",
        name: str = ANTHROPIC_MODEL_DISPLAY_NAME,
        role: str = "Deep Reasoning & Architecture Critic",
        system_prompt: str = DEFAULT_CLAUDE_PROMPT,
        color: str = "#d97706",  # Warm Amber / Coral
        avatar: str = "🔮",
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
