import pytest

from machinelearningmachine.agents.arena_ai import ArenaAIAgent
from machinelearningmachine.agents.copilot import CopilotAgent
from machinelearningmachine.agents.custom import CustomAgent
from machinelearningmachine.protocol.bus import MessageBus


@pytest.mark.asyncio
async def test_agent_initialization_and_talk():
    bus = MessageBus()
    arena = ArenaAIAgent(bus=bus)
    copilot = CopilotAgent(bus=bus)

    assert arena.agent_id == "arena-ai"
    assert copilot.agent_id == "copilot"
    assert arena.color == "#8b5cf6"
    assert copilot.color == "#06b6d4"

    # Arena generates a message to Copilot
    msg = await arena.generate_response(
        prompt="Design an asynchronous task queue",
        recipient_id=copilot.agent_id,
        recipient_name=copilot.name,
    )

    assert msg.sender_id == "arena-ai"
    assert msg.recipient_id == "copilot"
    assert len(msg.content) > 20
    # Copilot should have received it via the bus
    assert len(copilot.memory) == 1
    assert copilot.memory[0].id == msg.id


@pytest.mark.asyncio
async def test_custom_agent_creation():
    bus = MessageBus()
    dba = CustomAgent(
        agent_id="dba-agent",
        name="Database Architect",
        role="PostgreSQL Schema Specialist",
        system_prompt="Optimize database schemas and index design.",
        bus=bus,
    )
    assert dba.agent_id == "dba-agent"
    d = dba.to_dict()
    assert d["role"] == "PostgreSQL Schema Specialist"
