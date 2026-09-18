"""
Peer-to-Peer (P2P) Direct Dialogue Topology:
Enables two specific modules to conduct a direct multi-turn dialogue.
Examples:
- Arena AI talks to GitHub Copilot (Spec -> Code -> Review -> Refined Code)
- Copilot talks to Claude (Code -> Architectural Critique -> Optimization)
- Copilot talks to GPT (Code -> Test Generation -> Coverage Analysis)
"""

import asyncio
import logging
from typing import List, Dict, Any, Optional
from .base import BaseTopology
from ..protocol.bus import MessageBus
from ..protocol.message import Message, MessageType
from ..agents.base import BaseAgent

logger = logging.getLogger("P2PTopology")


class P2PTopology(BaseTopology):
    def __init__(
        self,
        agent_a: BaseAgent,
        agent_b: BaseAgent,
        bus: MessageBus,
        max_turns: int = 4,
    ):
        super().__init__(
            name=f"P2P: {agent_a.name} <-> {agent_b.name}",
            description=f"Direct bilateral dialogue between {agent_a.name} and {agent_b.name}",
            bus=bus,
        )
        self.agent_a = agent_a
        self.agent_b = agent_b
        self.max_turns = max_turns
        self.register_agent(agent_a)
        self.register_agent(agent_b)

    async def execute(self, prompt: str, **kwargs) -> List[Message]:
        """
        Execute full direct dialogue between Agent A and Agent B.
        """
        self.is_running = True
        transcript: List[Message] = []

        # Turn 1: Agent A initiates task to Agent B
        logger.info(f"[Turn 1] {self.agent_a.name} -> {self.agent_b.name}: Task Specification")
        msg1 = await self.agent_a.generate_response(
            prompt=f"Task for @{self.agent_b.name}:\n{prompt}",
            recipient_id=self.agent_b.agent_id,
            recipient_name=self.agent_b.name,
            message_type=MessageType.TASK_SPEC,
            topic="p2p_collaboration",
        )
        transcript.append(msg1)
        await asyncio.sleep(0.4)

        # Turn 2: Agent B implements/responds to Agent A
        logger.info(f"[Turn 2] {self.agent_b.name} -> {self.agent_a.name}: Initial Implementation")
        msg2 = await self.agent_b.generate_response(
            prompt=f"Responding to @{self.agent_a.name}'s task specification:\n{msg1.content}",
            recipient_id=self.agent_a.agent_id,
            recipient_name=self.agent_a.name,
            message_type=MessageType.PROPOSAL,
            topic="p2p_collaboration",
        )
        transcript.append(msg2)
        await asyncio.sleep(0.4)

        if self.max_turns >= 4:
            # Turn 3: Agent A reviews and provides feedback
            logger.info(f"[Turn 3] {self.agent_a.name} -> {self.agent_b.name}: Review & Critique")
            msg3 = await self.agent_a.generate_response(
                prompt=f"Reviewing @{self.agent_b.name}'s code proposal:\n{msg2.content}\nProvide constructive critique, edge cases, and optimization requests.",
                recipient_id=self.agent_b.agent_id,
                recipient_name=self.agent_b.name,
                message_type=MessageType.CRITIQUE,
                topic="p2p_collaboration",
            )
            transcript.append(msg3)
            await asyncio.sleep(0.4)

            # Turn 4: Agent B refines and finalizes
            logger.info(f"[Turn 4] {self.agent_b.name} -> {self.agent_a.name}: Refined Solution")
            msg4 = await self.agent_b.generate_response(
                prompt=f"Addressing critique and feedback from @{self.agent_a.name}:\n{msg3.content}\nDeliver updated solution.",
                recipient_id=self.agent_a.agent_id,
                recipient_name=self.agent_a.name,
                message_type=MessageType.REVISION,
                topic="p2p_collaboration",
            )
            transcript.append(msg4)
            await asyncio.sleep(0.4)

        self.is_running = False
        return transcript
