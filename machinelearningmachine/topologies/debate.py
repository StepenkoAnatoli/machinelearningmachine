"""
Collaborative Debate Topology:
Modules engage in a structured multi-round debate to critique trade-offs
and reach consensus on complex engineering decisions.

User-centered: faster, bounded, better error handling.
"""

import asyncio
import logging
from typing import List, Dict, Any, Optional
from .base import BaseTopology
from ..protocol.bus import MessageBus
from ..protocol.message import Message, MessageType
from ..agents.base import BaseAgent

logger = logging.getLogger("DebateTopology")


class DebateTopology(BaseTopology):
    def __init__(
        self,
        agents: List[BaseAgent],
        bus: MessageBus,
        rounds: int = 1,
        inter_turn_delay: float = 0.15,
    ):
        if not agents:
            raise ValueError("Debate requires at least one agent")
        if rounds < 1 or rounds > 5:
            raise ValueError("Rounds must be between 1 and 5")
        super().__init__(
            name="Collaborative Multi-Agent Debate",
            description="Round-table discussion where modules critique trade-offs and build consensus",
            bus=bus,
        )
        self.participant_agents = agents
        self.rounds = rounds
        self.inter_turn_delay = inter_turn_delay
        for agent in agents:
            self.register_agent(agent)

    async def execute(self, prompt: str, **kwargs) -> List[Message]:
        if not prompt or not prompt.strip():
            raise ValueError("Prompt cannot be empty")

        self.is_running = True
        transcript: List[Message] = []

        try:
            # Broadcast debate topic to all
            init_msg = Message(
                sender_id="system",
                sender_name="Debate Moderator",
                recipient_id="*",
                topic="debate_room",
                message_type=MessageType.SYSTEM,
                content=f"Debate Session Initiated:\n**Topic:** {prompt}\nParticipants: {', '.join([a.name for a in self.participant_agents])}",
            )
            transcript.append(init_msg)
            await self.bus.dispatch(init_msg)

            for round_idx in range(self.rounds):
                logger.info(f"--- Debate Round {round_idx + 1}/{self.rounds} ---")
                for agent in self.participant_agents:
                    # Build context from previous statements (limit length for performance)
                    recent = transcript[-4:]
                    prev_points = "\n".join(
                        [f"[{m.sender_name}]: {m.content[:200]}..." for m in recent if m.sender_id != agent.agent_id]
                    )
                    debate_prompt = (
                        f"Debate topic: {prompt}\n"
                        f"Points raised by peers:\n{prev_points}\n"
                        f"Provide your perspective, critique prior arguments, highlight edge cases, and propose actionable solutions."
                    )

                    msg = await agent.generate_response(
                        prompt=debate_prompt,
                        recipient_id="*",
                        recipient_name="Debate Room",
                        message_type=MessageType.CRITIQUE if round_idx > 0 else MessageType.PROPOSAL,
                        topic="debate_room",
                    )
                    transcript.append(msg)
                    if self.inter_turn_delay > 0:
                        await asyncio.sleep(self.inter_turn_delay)

            # Final synthesis
            synthesizer = self.participant_agents[0]
            consensus_prompt = (
                f"Synthesize the debate on '{prompt}'. Reconcile differences between "
                f"{', '.join([a.name for a in self.participant_agents])} and present the agreed consensus."
            )
            consensus_msg = await synthesizer.generate_response(
                prompt=consensus_prompt,
                recipient_id="*",
                recipient_name="All",
                message_type=MessageType.CONSENSUS,
                topic="debate_room",
            )
            transcript.append(consensus_msg)

        finally:
            self.is_running = False

        return transcript
