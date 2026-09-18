"""
MachineLearningMachine: Multi-Module Inter-Agent Communication Mesh.
Facilitates seamless dialogue between Arena AI, GitHub Copilot, Claude, GPT, and custom modules.
"""

from .mesh import AgentMesh
from .protocol.message import Message, MessageType
from .protocol.bus import MessageBus
from .agents.base import BaseAgent
from .agents.arena_ai import ArenaAIAgent
from .agents.copilot import CopilotAgent
from .agents.claude import ClaudeAgent
from .agents.gpt import GPTAgent
from .agents.custom import CustomAgent
from .agents.providers import BaseLLMProvider, MockLLMProvider, OpenAIProvider, AnthropicProvider
from .topologies.base import BaseTopology
from .topologies.p2p import P2PTopology
from .topologies.pipeline import PipelineTopology
from .topologies.debate import DebateTopology
from .topologies.hub_spoke import HubSpokeTopology

__all__ = [
    "AgentMesh",
    "Message",
    "MessageType",
    "MessageBus",
    "BaseAgent",
    "ArenaAIAgent",
    "CopilotAgent",
    "ClaudeAgent",
    "GPTAgent",
    "CustomAgent",
    "BaseLLMProvider",
    "MockLLMProvider",
    "OpenAIProvider",
    "AnthropicProvider",
    "BaseTopology",
    "P2PTopology",
    "PipelineTopology",
    "DebateTopology",
    "HubSpokeTopology",
]
