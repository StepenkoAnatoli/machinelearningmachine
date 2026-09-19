"""
Example 1: Arena AI talks to GitHub Copilot directly.
Demonstrates bilateral dialogue where Arena AI issues a specification,
Copilot implements code, Arena reviews and requests refinement, and Copilot finalizes.
"""

import asyncio

from machinelearningmachine import AgentMesh


async def main():
    print("=" * 60)
    print("DEMO: Arena AI talks to GitHub Copilot (P2P Dialogue)")
    print("=" * 60)

    mesh = AgentMesh()

    # Define a coding task
    task = "Implement a robust, thread-safe rate limiter in Python using the token-bucket algorithm."
    print(f"\n[Task Prompt]: {task}\n")

    # Run direct peer-to-peer conversation
    transcript = await mesh.talk_p2p(
        from_agent_id="arena-ai",
        to_agent_id="copilot",
        prompt=task,
        turns=4,
    )

    for msg in transcript:
        target = f" -> @{msg.recipient_name or msg.recipient_id}" if msg.recipient_id != "*" else ""
        print(f"\n>>> [{msg.message_type.value.upper()}] {msg.sender_name}{target}:")
        print(msg.content)
        print("-" * 50)

    print("\n[SUCCESS] Arena AI and Copilot completed dialogue!")


if __name__ == "__main__":
    asyncio.run(main())
