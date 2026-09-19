"""
Custom Agent Module:
Enables dynamic creation of user-specified agents or domain modules at runtime.
"""

from typing import Optional

from ..protocol.bus import MessageBus
from .base import BaseAgent
from .providers import BaseLLMProvider


class CustomAgent(BaseAgent):
    def __init__(
        self,
        agent_id: str,
        name: str,
        role: str,
        system_prompt: str,
        color: str = "#ec4899",  # Pink / Magenta
        avatar: str = "🧩",
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
