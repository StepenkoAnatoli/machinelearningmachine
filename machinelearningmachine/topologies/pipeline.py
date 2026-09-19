"""
Pipeline (Sequential Relay) Topology:
Modules pass state sequentially along a processing chain.
Example:
Arena AI (Specification) -> Claude (Architecture Critique) -> Copilot (Implementation) -> GPT (Test Verification)

User-centered: faster, more reliable, better error handling.
"""

import asyncio
import logging
from typing import List

from ..agents.base import BaseAgent
from ..protocol.bus import MessageBus
from ..protocol.message import Message, MessageType
from .base import BaseTopology

logger = logging.getLogger("PipelineTopology")


class PipelineTopology(BaseTopology):
    def __init__(
        self,
        agents_sequence: List[BaseAgent],
        bus: MessageBus,
        inter_step_delay: float = 0.15,
    ):
        if not agents_sequence:
            raise ValueError("Pipeline requires at least one agent")
        name = " -> ".join([a.name for a in agents_sequence])
        super().__init__(
            name=f"Pipeline Relay: {name}",
            description="Sequential handover across specialized modules",
            bus=bus,
        )
        self.sequence = agents_sequence
        self.inter_step_delay = inter_step_delay
        for agent in agents_sequence:
            self.register_agent(agent)

    async def execute(self, prompt: str, **kwargs) -> List[Message]:
        if not prompt or not prompt.strip():
            raise ValueError("Prompt cannot be empty")

        self.is_running = True
        transcript: List[Message] = []
        current_context = prompt

        try:
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
                if self.inter_step_delay > 0 and i < len(self.sequence) - 1:
                    await asyncio.sleep(self.inter_step_delay)
        finally:
            self.is_running = False

        return transcript
