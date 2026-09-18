"""
Example 3: Four-Agent Pipeline Relay.
Chain: Arena AI (Spec) -> Claude (Architecture Critique) -> Copilot (Implementation) -> GPT (Test Verification)
"""

import asyncio
from machinelearningmachine import AgentMesh


async def main():
    print("=" * 60)
    print("DEMO: 4-Agent Pipeline Relay (Arena -> Claude -> Copilot -> GPT)")
    print("=" * 60)

    mesh = AgentMesh()

    prompt = "Build a high-performance TTL cache decorator with eviction policies."
    print(f"\n[Task Prompt]: {prompt}\n")

    transcript = await mesh.run_pipeline(
        prompt=prompt,
        agent_ids=["arena-ai", "claude", "copilot", "gpt"],
    )

    for msg in transcript:
        target = f" -> @{msg.recipient_name or msg.recipient_id}" if msg.recipient_id != "*" else ""
        print(f"\n>>> [{msg.message_type.value.upper()}] {msg.sender_name}{target}:")
        print(msg.content)
        print("-" * 50)

    print("\n[SUCCESS] Pipeline relay completed across all 4 modules!")


if __name__ == "__main__":
    asyncio.run(main())
