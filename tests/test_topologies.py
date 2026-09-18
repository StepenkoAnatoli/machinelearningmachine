import pytest

from machinelearningmachine.mesh import AgentMesh


@pytest.mark.asyncio
async def test_arena_talks_to_copilot_p2p():
    mesh = AgentMesh()
    # Arena AI talks to Copilot directly for 4 turns
    transcript = await mesh.talk_p2p(
        from_agent_id="arena-ai",
        to_agent_id="copilot",
        prompt="Create a thread-safe in-memory cache with TTL eviction in Python.",
        turns=4,
    )
    assert len(transcript) == 4
    # Check turns: Turn 1 is Arena, Turn 2 is Copilot, Turn 3 is Arena, Turn 4 is Copilot
    assert transcript[0].sender_id == "arena-ai"
    assert transcript[1].sender_id == "copilot"
    assert transcript[2].sender_id == "arena-ai"
    assert transcript[3].sender_id == "copilot"


@pytest.mark.asyncio
async def test_copilot_talks_to_claude_p2p():
    mesh = AgentMesh()
    transcript = await mesh.talk_p2p(
        from_agent_id="copilot",
        to_agent_id="claude",
        prompt="Review this implementation for potential race conditions.",
        turns=2,
    )
    assert len(transcript) == 2
    assert transcript[0].sender_id == "copilot"
    assert transcript[1].sender_id == "claude"


@pytest.mark.asyncio
async def test_four_agent_pipeline():
    mesh = AgentMesh()
    transcript = await mesh.run_pipeline(
        prompt="Implement an asynchronous task event emitter with backpressure",
        agent_ids=["arena-ai", "claude", "copilot", "gpt"],
    )
    assert len(transcript) == 4
    assert transcript[0].sender_id == "arena-ai"
    assert transcript[1].sender_id == "claude"
    assert transcript[2].sender_id == "copilot"
    assert transcript[3].sender_id == "gpt"


@pytest.mark.asyncio
async def test_collaborative_debate():
    mesh = AgentMesh()
    transcript = await mesh.run_debate(
        prompt="Compare Event-Driven Microservices vs Monolithic Architecture for a real-time analytics system",
        agent_ids=["copilot", "claude", "gpt"],
        rounds=1,
    )
    # Init msg + 3 agent contributions + 1 consensus = 5 messages
    assert len(transcript) >= 4


@pytest.mark.asyncio
async def test_hub_and_spoke():
    mesh = AgentMesh()
    transcript = await mesh.run_hub_and_spoke(
        prompt="Design an end-to-end data ingestion pipeline",
        hub_id="arena-ai",
        spoke_ids=["copilot", "claude"],
    )
    # Plan + 2 spokes + Final consensus = 4 messages
    assert len(transcript) == 4
    assert transcript[0].sender_id == "arena-ai"
    assert transcript[-1].sender_id == "arena-ai"
