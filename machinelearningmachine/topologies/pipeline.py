"""
Pipeline (Sequential Relay) Topology:
Modules pass state sequentially along a processing chain.
Example:
Arena AI (Specification) -> Claude (Architecture Critique) -> Copilot (Implementation) -> GPT (Test Verification)
"""

import asyncio
import logging
from typing import List, Dict, Any, Optional
from .base import BaseTopology
from ..protocol.bus import MessageBus
from ..protocol.message import Message, MessageType
from ..agents.base import BaseAgent

logger = logging.getLogger("PipelineTopology")


class PipelineTopology(BaseTopology):
    def __init__(
        self,
        agents_sequence: List[BaseAgent],
        bus: MessageBus,
    ):
        name = " -> ".join([a.name for a in agents_sequence])
        super().__init__(
            name=f"Pipeline Relay: {name}",
            description="Sequential handover across specialized modules",
            bus=bus,
        )
        self.sequence = agents_sequence
        for agent in agents_sequence:
            self.register_agent(agent)

    async def execute(self, prompt: str, **kwargs) -> List[Message]:
        self.is_running = True
        transcript: List[Message] = []
        current_context = prompt

        for i, agent in enumerate(self.sequence):
            next_agent = self.sequence[i + 1] if i + 1 < len(self.sequence) else None
            recipient_id = next_agent.agent_id if next_agent else "*"
            recipient_name = next_agent.name if next_agent else "All"

            # Determine appropriate message type along the relay
            if i == 0:
                msg_type = MessageType.TASK_SPEC
            elif i == len(self.sequence) - 1:
                msg_type = MessageType.CONSENSUS
            elif i % 2 == 1:
                msg_type = MessageType.PROPOSAL
            else:
                msg_type = MessageType.CRITIQUE

            logger.info(f"[Step {i + 1}/{len(self.sequence)}] {agent.name} processing...")
            msg = await agent.generate_response(
                prompt=current_context,
                recipient_id=recipient_id,
                recipient_name=recipient_name,
                message_type=msg_type,
                topic="pipeline_relay",
            )
            transcript.append(msg)
            current_context = f"Previous stage output from @{agent.name}:\n{msg.content}"
            await asyncio.sleep(0.4)

        self.is_running = False
        return transcript
