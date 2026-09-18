"""
Base Agent definition for the Inter-Module Communication Mesh.
Every module (Arena AI, Copilot, Claude, GPT, Custom) inherits from BaseAgent.
"""

import asyncio
import logging
from typing import List, Dict, Any, Optional
from ..protocol.message import Message, MessageType
from ..protocol.bus import MessageBus
from .providers import BaseLLMProvider, MockLLMProvider

logger = logging.getLogger("BaseAgent")


class BaseAgent:
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
        self.agent_id = agent_id
        self.name = name
        self.role = role
        self.system_prompt = system_prompt
        self.color = color
        self.avatar = avatar
        self.provider = provider or MockLLMProvider()
        self.bus = bus
        self.memory: List[Message] = []
        self.status: str = "idle"  # idle, thinking, sent

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
        """Handle incoming message delivered by the bus."""
        self.memory.append(message)
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
        """
        self.status = "thinking"
        # Build prompt context from memory
        recent_messages = []
        for m in self.memory[-6:]:
            role = "assistant" if m.sender_id == self.agent_id else "user"
            recent_messages.append({"role": role, "content": f"[{m.sender_name}]: {m.content}"})

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
        except Exception as e:
            logger.error(f"Error in {self.name} generation: {e}")
            content = f"Error generating response from {self.name}: {str(e)}"

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
        self.status = "idle"

        if self.bus:
            await self.bus.dispatch(msg)

        return msg

    async def send_to(
        self,
        recipient: "BaseAgent",
        content: str,
        message_type: MessageType = MessageType.PROPOSAL,
        topic: str = "direct",
        artifacts: Optional[Dict[str, Any]] = None,
    ) -> Message:
        """Direct message to another agent."""
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
        """Broadcast message to all connected agents."""
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
        if self.bus:
            await self.bus.dispatch(msg)
        return msg

    def clear_memory(self) -> None:
        self.memory.clear()

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
