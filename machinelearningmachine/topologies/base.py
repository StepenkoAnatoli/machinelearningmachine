"""
Base Topology definition.
A topology defines the interaction pattern and message routing rules among modules.
"""

from typing import Any, Dict, List

from ..agents.base import BaseAgent
from ..protocol.bus import MessageBus
from ..protocol.message import Message


class BaseTopology:
    def __init__(self, name: str, description: str, bus: MessageBus):
        self.name = name
        self.description = description
        self.bus = bus
        self.agents: Dict[str, BaseAgent] = {}
        self.is_running = False

    def register_agent(self, agent: BaseAgent) -> None:
        self.agents[agent.agent_id] = agent
        agent.attach_bus(self.bus)

    async def execute(self, prompt: str, **kwargs) -> List[Message]:
        raise NotImplementedError

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "agents": [a.to_dict() for a in self.agents.values()],
        }
