"""
Hub & Spoke (Supervisor / Orchestrator) Topology:
A central coordinator module manages task distribution, dispatches sub-assignments
to worker modules, and synthesizes final results.

User-centered: faster, more reliable, better error handling.
"""

import asyncio
import logging
from typing import List

from ..agents.base import BaseAgent
from ..protocol.bus import MessageBus
from ..protocol.message import Message, MessageType
from .base import BaseTopology

logger = logging.getLogger("HubSpokeTopology")


class HubSpokeTopology(BaseTopology):
    def __init__(
        self,
        hub_agent: BaseAgent,
        spoke_agents: List[BaseAgent],
        bus: MessageBus,
        inter_step_delay: float = 0.15,
    ):
        if not hub_agent:
            raise ValueError("Hub agent is required")
        if not spoke_agents:
            raise ValueError("At least one spoke agent is required")
        super().__init__(
            name=f"Hub & Spoke: {hub_agent.name} (Lead)",
            description=f"{hub_agent.name} coordinates and delegates to specialized worker agents",
            bus=bus,
        )
        self.hub = hub_agent
        self.spokes = spoke_agents
        self.inter_step_delay = inter_step_delay
        self.register_agent(hub_agent)
        for spoke in spoke_agents:
            self.register_agent(spoke)

    async def execute(self, prompt: str, **kwargs) -> List[Message]:
        if not prompt or not prompt.strip():
            raise ValueError("Prompt cannot be empty")

        self.is_running = True
        transcript: List[Message] = []

        try:
            # Step 1: Hub breaks down task and assigns
            logger.info(f"Hub {self.hub.name} formulating execution plan...")
            plan_msg = await self.hub.generate_response(
                prompt=f"Task: {prompt}\nFormulate a coordinated plan delegating sub-components to {', '.join([s.name for s in self.spokes])}.",
                recipient_id="*",
                recipient_name="All Spokes",
                message_type=MessageType.TASK_SPEC,
                topic="hub_coordination",
            )
            transcript.append(plan_msg)
            if self.inter_step_delay > 0:
                await asyncio.sleep(self.inter_step_delay)

            # Step 2: Each spoke performs its assigned domain work
            spoke_responses = []
            for spoke in self.spokes:
                logger.info(f"Spoke {spoke.name} executing assigned role...")
                spoke_msg = await spoke.generate_response(
                    prompt=f"Supervisor plan:\n{plan_msg.content}\nExecute your role as {spoke.role}.",
                    recipient_id=self.hub.agent_id,
                    recipient_name=self.hub.name,
                    message_type=MessageType.PROPOSAL,
                    topic="hub_coordination",
                )
                transcript.append(spoke_msg)
                spoke_responses.append(spoke_msg)
                if self.inter_step_delay > 0:
                    await asyncio.sleep(self.inter_step_delay)

            # Step 3: Hub aggregates and finalizes
            logger.info(f"Hub {self.hub.name} synthesizing spoke deliverables...")
            summary_context = "\n\n".join([f"Deliverable from @{s.sender_name}:\n{s.content}" for s in spoke_responses])
            final_msg = await self.hub.generate_response(
                prompt=f"Review all worker deliverables and produce the unified production solution:\n\n{summary_context}",
                recipient_id="*",
                recipient_name="All",
                message_type=MessageType.CONSENSUS,
                topic="hub_coordination",
            )
            transcript.append(final_msg)

        finally:
            self.is_running = False

        return transcript
