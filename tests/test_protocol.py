import pytest
import asyncio
from machinelearningmachine.protocol.message import Message, MessageType
from machinelearningmachine.protocol.bus import MessageBus


def test_message_creation_and_dict():
    msg = Message(
        sender_id="arena-ai",
        sender_name="Arena AI",
        recipient_id="copilot",
        recipient_name="GitHub Copilot",
        topic="code.generation",
        message_type=MessageType.TASK_SPEC,
        content="Implement an LRU cache decorator in Python.",
    )
    d = msg.to_dict()
    assert d["sender_id"] == "arena-ai"
    assert d["recipient_id"] == "copilot"
    assert d["message_type"] == "task_spec"
    assert "Implement an LRU cache" in d["content"]
    assert "formatted_time" in d


@pytest.mark.asyncio
async def test_message_bus_routing():
    bus = MessageBus()
    received_copilot = []
    received_claude = []

    async def copilot_handler(msg: Message):
        received_copilot.append(msg)

    async def claude_handler(msg: Message):
        received_claude.append(msg)

    bus.register_agent("copilot", copilot_handler)
    bus.register_agent("claude", claude_handler)

    # 1. Point-to-point message from Arena to Copilot
    msg1 = Message(
        sender_id="arena-ai",
        sender_name="Arena AI",
        recipient_id="copilot",
        content="Write code for Copilot",
    )
    await bus.dispatch(msg1)
    assert len(received_copilot) == 1
    assert len(received_claude) == 0
    assert received_copilot[0].content == "Write code for Copilot"

    # 2. Broadcast message
    msg2 = Message(
        sender_id="arena-ai",
        sender_name="Arena AI",
        recipient_id="*",
        content="Broadcast to all",
    )
    await bus.dispatch(msg2)
    assert len(received_copilot) == 2
    assert len(received_claude) == 1

    # Verify history
    history = bus.get_history()
    assert len(history) == 2
    md = bus.export_markdown()
    assert "# Multi-Agent Dialogue Transcript" in md
