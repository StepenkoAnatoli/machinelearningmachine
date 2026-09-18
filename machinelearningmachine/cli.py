"""
CLI tool for MachineLearningMachine:
Interactively trigger module conversations, run topologies, or launch the live web dashboard.
"""

import argparse
import asyncio
import sys
import uvicorn
from .mesh import AgentMesh


def main():
    parser = argparse.ArgumentParser(
        description="MachineLearningMachine: Multi-Module Inter-Agent Communication Mesh"
    )
    subparsers = parser.add_subparsers(dest="command", help="Command to run")

    # Serve command (Live Web Dashboard)
    serve_parser = subparsers.add_parser("serve", help="Launch the real-time interactive web preview UI")
    serve_parser.add_argument("--host", default="0.0.0.0", help="Host interface (default: 0.0.0.0)")
    serve_parser.add_argument("--port", type=int, default=8000, help="Port to bind (default: 8000)")

    # Run command
    run_parser = subparsers.add_parser("run", help="Run a multi-agent dialogue from CLI")
    run_parser.add_argument(
        "--topology",
        choices=["p2p", "pipeline", "debate", "hub"],
        default="p2p",
        help="Communication topology",
    )
    run_parser.add_argument(
        "--agent-a",
        default="arena-ai",
        help="First agent for P2P (default: arena-ai)",
    )
    run_parser.add_argument(
        "--agent-b",
        default="copilot",
        help="Second agent for P2P (default: copilot)",
    )
    run_parser.add_argument(
        "--prompt",
        default="Design and implement a resilient token bucket rate limiter in Python.",
        help="Prompt or task for the modules",
    )
    run_parser.add_argument(
        "--export",
        choices=["json", "markdown"],
        default=None,
        help="Export transcript to stdout in format",
    )

    args = parser.parse_args()

    if args.command == "serve" or len(sys.argv) == 1:
        port = getattr(args, "port", 8000)
        host = getattr(args, "host", "0.0.0.0")
        print(f"[*] Starting MachineLearningMachine Live Dashboard on http://{host}:{port} ...")
        uvicorn.run("machinelearningmachine.server.app:app", host=host, port=port, log_level="info")
        return

    if args.command == "run":
        asyncio.run(execute_cli_run(args))


async def execute_cli_run(args):
    mesh = AgentMesh()
    print(f"\n[+] Topology: {args.topology}")
    print(f"[+] Task: {args.prompt}\n")

    if args.topology == "p2p":
        print(f"[*] Initiating direct dialogue: {args.agent_a} <---> {args.agent_b}")
        transcript = await mesh.talk_p2p(args.agent_a, args.agent_b, args.prompt)
    elif args.topology == "pipeline":
        print(f"[*] Initiating 4-module sequential pipeline: Arena AI -> Claude -> Copilot -> GPT")
        transcript = await mesh.run_pipeline(args.prompt)
    elif args.topology == "debate":
        print(f"[*] Initiating collaborative debate among Copilot, Claude, and GPT")
        transcript = await mesh.run_debate(args.prompt)
    elif args.topology == "hub":
        print(f"[*] Initiating Hub & Spoke coordination with Arena AI as lead")
        transcript = await mesh.run_hub_and_spoke(args.prompt)
    else:
        transcript = []

    for msg in transcript:
        target = f" -> @{msg.recipient_name or msg.recipient_id}" if msg.recipient_id != "*" else ""
        print(f"\n=======================================================")
        print(f"[{msg.message_type.value.upper()}] {msg.sender_name}{target}")
        print(f"=======================================================\n")
        print(msg.content)

    if args.export == "markdown":
        print("\n\n--- MARKDOWN EXPORT ---")
        print(mesh.export_markdown())
    elif args.export == "json":
        print("\n\n--- JSON EXPORT ---")
        print(mesh.export_json())


if __name__ == "__main__":
    main()
