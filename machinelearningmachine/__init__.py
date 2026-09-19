"""
MachineLearningMachine: Multi-Module Inter-Agent Communication Mesh.
Facilitates seamless dialogue between Arena AI, GitHub Copilot, Claude, GPT, and custom modules.
"""

from . import _deps

try:
    from .agents.arena_ai import ArenaAIAgent
    from .agents.base import BaseAgent
    from .agents.claude import ClaudeAgent
    from .agents.copilot import CopilotAgent
    from .agents.custom import CustomAgent
    from .agents.gpt import GPTAgent
    from .agents.providers import AnthropicProvider, BaseLLMProvider, MockLLMProvider, OpenAIProvider
    from .mesh import AgentMesh
    from .protocol.bus import MessageBus
    from .protocol.message import Message, MessageType
    from .topologies.base import BaseTopology
    from .topologies.debate import DebateTopology
    from .topologies.hub_spoke import HubSpokeTopology
    from .topologies.p2p import P2PTopology
    from .topologies.pipeline import PipelineTopology
except ModuleNotFoundError as exc:
    # A third-party dependency is not installed: replace the raw traceback with
    # clear installation instructions. Real bugs inside the package still raise.
    if _deps.is_known_dependency(exc.name):
        raise ImportError(_deps.install_hint(exc.name)) from None
    raise

__version__ = "0.1.0"

__all__ = [
    "AgentMesh",
    "AnthropicProvider",
    "ArenaAIAgent",
    "BaseAgent",
    "BaseLLMProvider",
    "BaseTopology",
    "ClaudeAgent",
    "CopilotAgent",
    "CustomAgent",
    "DebateTopology",
    "GPTAgent",
    "HubSpokeTopology",
    "Message",
    "MessageBus",
    "MessageType",
    "MockLLMProvider",
    "OpenAIProvider",
    "P2PTopology",
    "PipelineTopology",
]
