"""
AgentMesh: The Central Inter-Module Orchestration Mesh.
Connects Arena AI, Copilot, Claude, GPT, and custom agents over a shared message bus.

User-centered improvements:
- Clear validation with helpful error messages
- Resource limits and safety checks
- Better documentation
- Bounded history to prevent memory issues
"""

import logging
from typing import Any, Awaitable, Callable, Dict, List, Optional

from .agents.arena_ai import ArenaAIAgent
from .agents.base import BaseAgent
from .agents.claude import ClaudeAgent
from .agents.copilot import CopilotAgent
from .agents.custom import CustomAgent
from .agents.gpt import GPTAgent
from .protocol.bus import MessageBus
from .protocol.message import Message
from .topologies.debate import DebateTopology
from .topologies.hub_spoke import HubSpokeTopology
from .topologies.p2p import P2PTopology
from .topologies.pipeline import PipelineTopology

logger = logging.getLogger("AgentMesh")


def _delay_kwargs(name: str, value: Optional[float]) -> Dict[str, float]:
    """``{name: value}`` when the caller named a delay, else nothing: the default
    stays on the topology, which is the only place that knows the demo pace."""
    return {} if value is None else {name: float(value)}


class AgentMesh:
    """
    Main communication mesh managing agents, topologies, message routing, and sessions.

    User-centered design:
    - Validates inputs early with clear messages
    - Prevents resource exhaustion
    - Provides helpful defaults
    """

    MAX_AGENTS = 20
    MAX_PROMPT_LENGTH = 5000

    def __init__(self, with_default_agents: bool = True, max_history: int = 1000):
        self.bus = MessageBus(max_history=max_history)
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
        """Register an agent in the mesh with validation."""
        if not agent.agent_id or not agent.agent_id.strip():
            raise ValueError("Agent ID cannot be empty")
        if len(self.agents) >= self.MAX_AGENTS:
            raise ValueError(f"Too many agents (max {self.MAX_AGENTS}). Clear or remove some.")
        if len(agent.agent_id) > 50:
            raise ValueError("Agent ID too long (max 50 chars)")

        self.agents[agent.agent_id] = agent
        agent.attach_bus(self.bus)
        logger.info(f"Agent registered: {agent.agent_id} ({agent.name})")

    def unregister_agent(self, agent_id: str) -> None:
        """Remove an agent from the mesh."""
        if agent_id in self.agents:
            # Prevent removing all agents
            if len(self.agents) <= 1:
                raise ValueError("Cannot remove last agent")
            self.agents[agent_id].detach_bus()
            del self.agents[agent_id]
            logger.info(f"Agent unregistered: {agent_id}")

    def get_agent(self, agent_id: str) -> Optional[BaseAgent]:
        return self.agents.get(agent_id)

    def list_agents(self) -> List[Dict]:
        return [a.to_dict() for a in self.agents.values()]

    def on_message(self, callback: Callable[[Message], Awaitable[None]]) -> None:
        """Attach a global listener to all bus messages."""
        self.bus.add_global_listener(callback)

    def _validate_prompt(self, prompt: str) -> str:
        """Validate prompt with user-friendly errors."""
        if not prompt or not prompt.strip():
            raise ValueError("Prompt cannot be empty. Please describe the task you want the agents to work on.")
        if len(prompt) > self.MAX_PROMPT_LENGTH:
            raise ValueError(f"Prompt too long ({len(prompt)} chars). Max {self.MAX_PROMPT_LENGTH} characters for readability.")
        if len(prompt.strip()) < 5:
            raise ValueError("Prompt too short. Please provide more details (min 5 characters).")
        return prompt.strip()

    def _resolve_agents(self, agent_ids: List[str], *, label: str = "Agents") -> List[BaseAgent]:
        """
        Turn agent ids into agents, or say exactly which ones do not exist.

        Every topology entry point needs this, and the wording is what makes it
        worth sharing: it names the ids that were not found *and* the ones that
        are, which is the difference between "your pipeline is broken" and
        something the caller can act on. Each entry point used to carry its own
        copy of the loop and the message, so a change to one - a clearer wording,
        a different case-folding rule - quietly applied to one topology only.
        ``label`` keeps the hub-and-spoke phrasing ("Spoke agents not found") that
        callers already get.
        """
        resolved: List[BaseAgent] = []
        missing: List[str] = []
        for aid in agent_ids:
            agent = self.agents.get(aid)
            if agent:
                resolved.append(agent)
            else:
                missing.append(aid)

        if missing:
            available = ", ".join(self.agents.keys())
            raise ValueError(f"{label} not found: {', '.join(missing)}. Available: {available}")
        return resolved

    async def talk_p2p(
        self,
        from_agent_id: str,
        to_agent_id: str,
        prompt: str,
        turns: int = 4,
        inter_turn_delay: Optional[float] = None,
    ) -> List[Message]:
        """
        Direct peer-to-peer dialogue between two modules.
        Example: Arena AI talks to Copilot, or Copilot talks to Claude/GPT.

        User-centered: validates early, clear errors.
        """
        prompt = self._validate_prompt(prompt)

        if not from_agent_id or not to_agent_id:
            raise ValueError("Both from_agent_id and to_agent_id are required")
        if from_agent_id == to_agent_id:
            raise ValueError("Cannot start dialogue with same agent. Choose two different agents.")
        if turns < 1 or turns > 10:
            raise ValueError("Turns must be between 1 and 10")

        agent_a = self.agents.get(from_agent_id)
        agent_b = self.agents.get(to_agent_id)
        if not agent_a:
            available = ", ".join(self.agents.keys())
            raise ValueError(f"Agent '{from_agent_id}' not found. Available: {available}")
        if not agent_b:
            available = ", ".join(self.agents.keys())
            raise ValueError(f"Agent '{to_agent_id}' not found. Available: {available}")

        topology = P2PTopology(
            agent_a=agent_a,
            agent_b=agent_b,
            bus=self.bus,
            max_turns=turns,
            **_delay_kwargs("inter_turn_delay", inter_turn_delay),
        )
        return await topology.execute(prompt)

    async def run_pipeline(
        self,
        prompt: str,
        agent_ids: Optional[List[str]] = None,
        inter_step_delay: Optional[float] = None,
    ) -> List[Message]:
        """
        Run a sequential relay pipeline across an ordered sequence of agents.
        Default: Arena AI -> Claude -> Copilot -> GPT.
        """
        prompt = self._validate_prompt(prompt)

        if agent_ids is None:
            agent_ids = ["arena-ai", "claude", "copilot", "gpt"]

        if not agent_ids:
            raise ValueError("At least one agent ID required for pipeline")
        if len(agent_ids) > 10:
            raise ValueError("Too many agents for pipeline (max 10)")

        sequence = self._resolve_agents(agent_ids)

        if not sequence:
            raise ValueError("No valid agents found for pipeline sequence")

        topology = PipelineTopology(
            agents_sequence=sequence, bus=self.bus, **_delay_kwargs("inter_step_delay", inter_step_delay)
        )
        return await topology.execute(prompt)

    async def run_debate(
        self,
        prompt: str,
        agent_ids: Optional[List[str]] = None,
        rounds: int = 1,
        inter_turn_delay: Optional[float] = None,
    ) -> List[Message]:
        """
        Run a multi-agent debate session.
        """
        prompt = self._validate_prompt(prompt)

        if agent_ids is None:
            agent_ids = ["copilot", "claude", "gpt"]

        if not agent_ids:
            raise ValueError("At least one agent required for debate")
        if len(agent_ids) > 10:
            raise ValueError("Too many agents for debate (max 10)")
        if rounds < 1 or rounds > 5:
            raise ValueError("Rounds must be between 1 and 5")

        participants = self._resolve_agents(agent_ids)

        if not participants:
            raise ValueError("No valid participants for debate")

        topology = DebateTopology(
            agents=participants, bus=self.bus, rounds=rounds,
            **_delay_kwargs("inter_turn_delay", inter_turn_delay),
        )
        return await topology.execute(prompt)

    async def run_hub_and_spoke(
        self,
        prompt: str,
        hub_id: str = "arena-ai",
        spoke_ids: Optional[List[str]] = None,
        inter_step_delay: Optional[float] = None,
    ) -> List[Message]:
        """
        Run supervisor orchestration: Hub agent plans, delegates to spokes, and aggregates.
        """
        prompt = self._validate_prompt(prompt)

        hub = self.agents.get(hub_id)
        if not hub:
            available = ", ".join(self.agents.keys())
            raise ValueError(f"Hub agent '{hub_id}' not found. Available: {available}")

        if spoke_ids is None:
            spoke_ids = [aid for aid in self.agents.keys() if aid != hub_id]

        if not spoke_ids:
            raise ValueError("At least one spoke agent required")

        spokes = self._resolve_agents(spoke_ids, label="Spoke agents")

        if not spokes:
            raise ValueError("No valid spoke agents")

        if hub_id in [s.agent_id for s in spokes]:
            raise ValueError("Hub agent cannot also be a spoke")

        topology = HubSpokeTopology(
            hub_agent=hub, spoke_agents=spokes, bus=self.bus,
            **_delay_kwargs("inter_step_delay", inter_step_delay),
        )
        return await topology.execute(prompt)

    #: Agent IDs that exist in every fresh mesh (recreated, never deleted).
    BUILTIN_AGENT_IDS = ("arena-ai", "copilot", "claude", "gpt")

    def reset_to_session(self, agents_data: List[Dict[str, Any]]) -> None:
        """
        Restore the mesh's agent roster from a saved session.

        - Built-in agents (arena-ai, copilot, claude, gpt) are kept and their
          memory is cleared, so any provider/API-key configuration survives.
        - Custom agents that are no longer in the session are removed.
        - Custom agents present in the session are re-registered.
        """
        if not isinstance(agents_data, list) or not agents_data:
            return

        saved_ids = {
            a.get("agent_id") for a in agents_data if isinstance(a, dict) and a.get("agent_id")
        }

        # Remove custom agents that are not part of the saved roster.
        for aid in list(self.agents.keys()):
            if aid in saved_ids:
                continue
            if aid in self.BUILTIN_AGENT_IDS:
                # Built-in missing from an old session - just reset its memory.
                self.agents[aid].clear_memory()
                continue
            try:
                self.unregister_agent(aid)
            except ValueError:
                pass

        # Clear memory on built-ins that are part of the saved roster.
        for aid in self.BUILTIN_AGENT_IDS:
            agent = self.agents.get(aid)
            if agent:
                agent.clear_memory()

        # Re-register the session's custom agents.
        for a in agents_data:
            if not isinstance(a, dict):
                continue
            aid = (a.get("agent_id") or "").strip()
            if not aid or aid in self.BUILTIN_AGENT_IDS or aid in self.agents:
                continue
            try:
                agent = CustomAgent(
                    agent_id=aid,
                    name=(a.get("name") or aid).strip() or aid,
                    role=(a.get("role") or "Custom module").strip() or "Custom module",
                    system_prompt=(
                        a.get("system_prompt") or a.get("role") or "Custom user-defined module."
                    ).strip(),
                    color=a.get("color") or "#ec4899",
                    avatar=(a.get("avatar") or "🧩").strip() or "🧩",
                    bus=self.bus,
                )
                self.register_agent(agent)
            except ValueError as e:
                logger.warning(f"Skipping invalid saved agent '{aid}': {e}")

    def load_messages(self, messages: List[Message]) -> None:
        """Replace the bus history (used when loading a saved session)."""
        self.bus.set_history(messages)

    def get_history(self) -> List[Message]:
        return self.bus.get_history()

    def clear_history(self) -> None:
        self.bus.clear_history()
        for a in self.agents.values():
            a.clear_memory()
        logger.info("History cleared")

    def export_markdown(self) -> str:
        return self.bus.export_markdown()

    def export_json(self) -> str:
        return self.bus.export_json()
