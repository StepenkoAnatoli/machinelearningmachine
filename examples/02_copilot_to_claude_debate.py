"""
Example 2: Copilot talks to Claude and GPT in a Collaborative Debate.
Demonstrates multi-module debate regarding architecture and trade-offs.
"""

import asyncio
from machinelearningmachine import AgentMesh


async def main():
    print("=" * 60)
    print("DEMO: Copilot talks to Claude and GPT (Multi-Module Debate)")
    print("=" * 60)

    mesh = AgentMesh()

    topic = "Should an enterprise real-time event pipeline use Kafka or RabbitMQ with Redis Streams?"
    print(f"\n[Debate Topic]: {topic}\n")

    transcript = await mesh.run_debate(
        prompt=topic,
        agent_ids=["copilot", "claude", "gpt"],
        rounds=1,
    )

    for msg in transcript:
        print(f"\n>>> [{msg.message_type.value.upper()}] {msg.sender_name}:")
        print(msg.content)
        print("-" * 50)

    print("\n[SUCCESS] Debate concluded with synthesized consensus!")


if __name__ == "__main__":
    asyncio.run(main())
