"""
MachineLearningMachine: Multi-Module Inter-Agent Communication Mesh.
Facilitates seamless dialogue between Arena AI, GitHub Copilot, Claude, GPT, and custom modules.
"""

from . import _deps

try:
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
except ModuleNotFoundError as exc:
    # A third-party dependency is not installed: replace the raw traceback with
    # clear installation instructions. Real bugs inside the package still raise.
    if _deps.is_known_dependency(exc.name):
        raise ImportError(_deps.install_hint(exc.name)) from None
    raise

__version__ = "0.1.0"

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
