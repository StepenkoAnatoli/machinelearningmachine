"""
Base Agent definition for the Inter-Module Communication Mesh.
Every module (Arena AI, Copilot, Claude, GPT, Custom) inherits from BaseAgent.

User-centered: bounded memory, better error handling, status tracking.
"""

import asyncio
import logging
from typing import List, Dict, Any, Optional
from ..protocol.message import Message, MessageType
from ..protocol.bus import MessageBus
from .providers import BaseLLMProvider, MockLLMProvider

logger = logging.getLogger("BaseAgent")


class BaseAgent:
    MAX_MEMORY = 100  # Prevent unbounded growth

    def __init__(
        self,
        agent_id: str,
        name: str,
        role: str,
        system_prompt: str,
        color: str = "#6366f1",
        avatar: str = "🤖",
        provider: Optional[BaseLLMProvider] = None,
        bus: Optional[MessageBus] = None,
    ):
        if not agent_id or not agent_id.strip():
            raise ValueError("Agent ID cannot be empty")
        if not name or not name.strip():
            raise ValueError("Agent name cannot be empty")
        if not role or not role.strip():
            raise ValueError("Agent role cannot be empty")

        self.agent_id = agent_id.strip()
        self.name = name.strip()
        self.role = role.strip()
        self.system_prompt = system_prompt
        self.color = color
        self.avatar = avatar
        self.provider = provider or MockLLMProvider()
        self.bus = bus
        self.memory: List[Message] = []
        self.status: str = "idle"  # idle, thinking, error

        if self.bus:
            self.attach_bus(self.bus)

    def attach_bus(self, bus: MessageBus) -> None:
        """Connect agent to message bus."""
        self.bus = bus
        self.bus.register_agent(self.agent_id, self.receive)

    def detach_bus(self) -> None:
        """Disconnect from message bus."""
        if self.bus:
            self.bus.unregister_agent(self.agent_id)
            self.bus = None

    async def receive(self, message: Message) -> None:
        """Handle incoming message delivered by the bus with bounded memory."""
        self.memory.append(message)
        # Prune old memory to prevent unbounded growth
        if len(self.memory) > self.MAX_MEMORY:
            self.memory = self.memory[-self.MAX_MEMORY:]
        logger.debug(f"Agent {self.name} received message from {message.sender_name}")

    async def generate_response(
        self,
        prompt: Optional[str] = None,
        recipient_id: str = "*",
        recipient_name: Optional[str] = None,
        message_type: MessageType = MessageType.PROPOSAL,
        topic: str = "collaboration",
        artifacts: Optional[Dict[str, Any]] = None,
    ) -> Message:
        """
        Synthesize response using the LLM provider and broadcast/send it over the bus.
        User-centered: robust error handling, clear status.
        """
        if prompt is not None and len(prompt) > 10000:
            raise ValueError("Prompt too long (max 10000 chars)")

        self.status = "thinking"
        # Build prompt context from memory - limit for performance
        recent_messages = []
        for m in self.memory[-6:]:
            role = "assistant" if m.sender_id == self.agent_id else "user"
            # Truncate very long messages for context window
            content = m.content[:2000] + "..." if len(m.content) > 2000 else m.content
            recent_messages.append({"role": role, "content": f"[{m.sender_name}]: {content}"})

        if prompt:
            recent_messages.append({"role": "user", "content": prompt})

        try:
            content = await self.provider.generate(
                system_prompt=self.system_prompt,
                messages=recent_messages,
                agent_role=self.role,
                agent_name=self.name,
                task_context=prompt,
            )
            if not content or not content.strip():
                content = f"[{self.name} processed the request but generated empty response - using fallback]"
        except Exception as e:
            logger.error(f"Error in {self.name} generation: {e}", exc_info=True)
            # User-friendly error, not raw exception
            content = (
                f"### [{self.name} - Error Recovery]\n\n"
                f"Encountered an issue while generating response. "
                f"Provider: {self.provider.__class__.__name__}\n\n"
                f"**Fallback response:**\n"
                f"Task received: {prompt[:200] if prompt else 'No prompt'}...\n"
                f"Continuing with simulated response for resilience."
            )
            self.status = "error"
            # Brief pause before returning to idle
            await asyncio.sleep(0.1)

        msg = Message(
            sender_id=self.agent_id,
            sender_name=self.name,
            recipient_id=recipient_id,
            recipient_name=recipient_name,
            topic=topic,
            message_type=message_type,
            content=content,
            artifacts=artifacts or {},
        )

        self.memory.append(msg)
        if len(self.memory) > self.MAX_MEMORY:
            self.memory = self.memory[-self.MAX_MEMORY:]
        self.status = "idle"

        if self.bus:
            try:
                await self.bus.dispatch(msg)
            except Exception as e:
                logger.error(f"Failed to dispatch message from {self.name}: {e}")

        return msg

    async def send_to(
        self,
        recipient: "BaseAgent",
        content: str,
        message_type: MessageType = MessageType.PROPOSAL,
        topic: str = "direct",
        artifacts: Optional[Dict[str, Any]] = None,
    ) -> Message:
        """Direct message to another agent with validation."""
        if not content or not content.strip():
            raise ValueError("Message content cannot be empty")
        if len(content) > 50000:
            raise ValueError("Message content too long (max 50000 chars)")

        msg = Message(
            sender_id=self.agent_id,
            sender_name=self.name,
            recipient_id=recipient.agent_id,
            recipient_name=recipient.name,
            topic=topic,
            message_type=message_type,
            content=content,
            artifacts=artifacts or {},
        )
        self.memory.append(msg)
        if len(self.memory) > self.MAX_MEMORY:
            self.memory = self.memory[-self.MAX_MEMORY:]
        if self.bus:
            await self.bus.dispatch(msg)
        return msg

    async def broadcast(
        self,
        content: str,
        message_type: MessageType = MessageType.PROPOSAL,
        topic: str = "general",
        artifacts: Optional[Dict[str, Any]] = None,
    ) -> Message:
        """Broadcast message to all connected agents with validation."""
        if not content or not content.strip():
            raise ValueError("Broadcast content cannot be empty")
        if len(content) > 50000:
            raise ValueError("Broadcast content too long (max 50000 chars)")

        msg = Message(
            sender_id=self.agent_id,
            sender_name=self.name,
            recipient_id="*",
            topic=topic,
            message_type=message_type,
            content=content,
            artifacts=artifacts or {},
        )
        self.memory.append(msg)
        if len(self.memory) > self.MAX_MEMORY:
            self.memory = self.memory[-self.MAX_MEMORY:]
        if self.bus:
            await self.bus.dispatch(msg)
        return msg

    def clear_memory(self) -> None:
        self.memory.clear()
        self.status = "idle"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "agent_id": self.agent_id,
            "name": self.name,
            "role": self.role,
            "color": self.color,
            "avatar": self.avatar,
            "status": self.status,
            "provider": self.provider.__class__.__name__,
            "memory_count": len(self.memory),
        }
