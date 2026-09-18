"""
AgentMesh: The Central Inter-Module Orchestration Mesh.
Connects Arena AI, Copilot, Claude, GPT, and custom agents over a shared message bus.
"""

import asyncio
from typing import Dict, List, Optional, Callable, Awaitable
from .protocol.bus import MessageBus
from .protocol.message import Message, MessageType
from .agents.base import BaseAgent
from .agents.arena_ai import ArenaAIAgent
from .agents.copilot import CopilotAgent
from .agents.claude import ClaudeAgent
from .agents.gpt import GPTAgent
from .agents.custom import CustomAgent
from .agents.providers import BaseLLMProvider, MockLLMProvider, OpenAIProvider, AnthropicProvider
from .topologies.p2p import P2PTopology
from .topologies.pipeline import PipelineTopology
from .topologies.debate import DebateTopology
from .topologies.hub_spoke import HubSpokeTopology


class AgentMesh:
    """
    Main communication mesh managing agents, topologies, message routing, and sessions.
    """

    def __init__(self, with_default_agents: bool = True):
        self.bus = MessageBus()
        self.agents: Dict[str, BaseAgent] = {}

        if with_default_agents:
            self._init_default_agents()

    def _init_default_agents(self):
        self.register_agent(ArenaAIAgent(bus=self.bus))
        self.register_agent(CopilotAgent(bus=self.bus))
        self.register_agent(ClaudeAgent(bus=self.bus))
        self.register_agent(GPTAgent(bus=self.bus))

    @property
    def arena_ai(self) -> Optional[BaseAgent]:
        return self.agents.get("arena-ai")

    @property
    def copilot(self) -> Optional[BaseAgent]:
        return self.agents.get("copilot")

    @property
    def claude(self) -> Optional[BaseAgent]:
        return self.agents.get("claude")

    @property
    def gpt(self) -> Optional[BaseAgent]:
        return self.agents.get("gpt")

    def register_agent(self, agent: BaseAgent) -> None:
        """Register an agent in the mesh."""
        self.agents[agent.agent_id] = agent
        agent.attach_bus(self.bus)

    def unregister_agent(self, agent_id: str) -> None:
        """Remove an agent from the mesh."""
        if agent_id in self.agents:
            self.agents[agent_id].detach_bus()
            del self.agents[agent_id]

    def get_agent(self, agent_id: str) -> Optional[BaseAgent]:
        return self.agents.get(agent_id)

    def list_agents(self) -> List[Dict]:
        return [a.to_dict() for a in self.agents.values()]

    def on_message(self, callback: Callable[[Message], Awaitable[None]]) -> None:
        """Attach a global listener to all bus messages."""
        self.bus.add_global_listener(callback)

    async def talk_p2p(
        self,
        from_agent_id: str,
        to_agent_id: str,
        prompt: str,
        turns: int = 4,
    ) -> List[Message]:
        """
        Direct peer-to-peer dialogue between two modules.
        Example: Arena AI talks to Copilot, or Copilot talks to Claude/GPT.
        """
        agent_a = self.agents.get(from_agent_id)
        agent_b = self.agents.get(to_agent_id)
        if not agent_a or not agent_b:
            raise ValueError(f"One or both agents not found: '{from_agent_id}', '{to_agent_id}'")

        topology = P2PTopology(agent_a=agent_a, agent_b=agent_b, bus=self.bus, max_turns=turns)
        return await topology.execute(prompt)

    async def run_pipeline(
        self,
        prompt: str,
        agent_ids: Optional[List[str]] = None,
    ) -> List[Message]:
        """
        Run a sequential relay pipeline across an ordered sequence of agents.
        Default: Arena AI -> Claude -> Copilot -> GPT.
        """
        if agent_ids is None:
            agent_ids = ["arena-ai", "claude", "copilot", "gpt"]

        sequence = []
        for aid in agent_ids:
            agent = self.agents.get(aid)
            if agent:
                sequence.append(agent)

        if not sequence:
            raise ValueError("No valid agents found for pipeline sequence")

        topology = PipelineTopology(agents_sequence=sequence, bus=self.bus)
        return await topology.execute(prompt)

    async def run_debate(
        self,
        prompt: str,
        agent_ids: Optional[List[str]] = None,
        rounds: int = 1,
    ) -> List[Message]:
        """
        Run a multi-agent debate session.
        """
        if agent_ids is None:
            agent_ids = ["copilot", "claude", "gpt"]

        participants = [self.agents[aid] for aid in agent_ids if aid in self.agents]
        topology = DebateTopology(agents=participants, bus=self.bus, rounds=rounds)
        return await topology.execute(prompt)

    async def run_hub_and_spoke(
        self,
        prompt: str,
        hub_id: str = "arena-ai",
        spoke_ids: Optional[List[str]] = None,
    ) -> List[Message]:
        """
        Run supervisor orchestration: Hub agent plans, delegates to spokes, and aggregates.
        """
        hub = self.agents.get(hub_id)
        if not hub:
            raise ValueError(f"Hub agent '{hub_id}' not found")

        if spoke_ids is None:
            spoke_ids = [aid for aid in self.agents.keys() if aid != hub_id]

        spokes = [self.agents[aid] for aid in spoke_ids if aid in self.agents]
        topology = HubSpokeTopology(hub_agent=hub, spoke_agents=spokes, bus=self.bus)
        return await topology.execute(prompt)

    def get_history(self) -> List[Message]:
        return self.bus.get_history()

    def clear_history(self) -> None:
        self.bus.clear_history()
        for a in self.agents.values():
            a.clear_memory()

    def export_markdown(self) -> str:
        return self.bus.export_markdown()

    def export_json(self) -> str:
        return self.bus.export_json()
