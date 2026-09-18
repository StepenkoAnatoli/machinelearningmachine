"""
CLI tool for MachineLearningMachine:
Interactively trigger module conversations, run topologies, or launch the live web dashboard.

User-centered improvements:
- Friendly error messages instead of stack traces
- Validation with helpful hints
- Progress indicators
- More options for real user workflows
- Better help text
"""

import argparse
import asyncio
import sys
import textwrap

from . import _deps
from .mesh import AgentMesh


def _print_error(msg: str):
    """Print error in user-friendly way."""
    print(f"\n❌ Error: {msg}\n", file=sys.stderr)
    print("💡 Tip: Run with --help for usage examples", file=sys.stderr)


def main():
    parser = argparse.ArgumentParser(
        description="MachineLearningMachine: Multi-Module Inter-Agent Communication Mesh",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""
        Examples:
          # Launch web dashboard
          python -m machinelearningmachine serve --port 8000

          # Quick P2P dialogue
          python -m machinelearningmachine run --topology p2p --prompt "Build a rate limiter"

          # Full pipeline with custom agents
          python -m machinelearningmachine run --topology pipeline --agent-ids arena-ai,claude,copilot,gpt

          # Debate with specific agents
          python -m machinelearningmachine run --topology debate --agent-ids copilot,claude,gpt --prompt "Kafka vs Redis"

          # Hub & spoke with export
          python -m machinelearningmachine run --topology hub --prompt "Design auth system" --export markdown
        """)
    )
    subparsers = parser.add_subparsers(dest="command", help="Command to run")

    # Serve command (Live Web Dashboard)
    serve_parser = subparsers.add_parser("serve", help="Launch the real-time interactive web dashboard")
    serve_parser.add_argument("--host", default="0.0.0.0", help="Host interface (default: 0.0.0.0)")
    serve_parser.add_argument("--port", type=int, default=8000, help="Port to bind (default: 8000)")

    # Run command
    run_parser = subparsers.add_parser("run", help="Run a multi-agent dialogue from CLI")
    run_parser.add_argument(
        "--topology",
        choices=["p2p", "pipeline", "debate", "hub"],
        default="p2p",
        help="Communication topology (default: p2p)",
    )
    run_parser.add_argument(
        "--agent-a",
        default="arena-ai",
        help="First agent for P2P/hub (default: arena-ai). Available: arena-ai, copilot, claude, gpt",
    )
    run_parser.add_argument(
        "--agent-b",
        default="copilot",
        help="Second agent for P2P (default: copilot)",
    )
    run_parser.add_argument(
        "--agent-ids",
        default=None,
        help="Comma-separated list of agent IDs for pipeline/debate/hub (e.g. 'copilot,claude,gpt')",
    )
    run_parser.add_argument(
        "--turns",
        type=int,
        default=4,
        help="Number of turns for P2P (default: 4, max 10)",
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
    run_parser.add_argument(
        "--no-delay",
        action="store_true",
        help="Disable inter-turn delays for faster execution (useful for testing)",
    )
    run_parser.add_argument(
        "--speak",
        action="store_true",
        help="Read the dialogue aloud with your computer's built-in text-to-speech "
             "(macOS: say, Windows: SAPI, Linux: espeak-ng)",
    )

    args = parser.parse_args()

    if args.command == "serve" or len(sys.argv) == 1:
        port = getattr(args, "port", 8000)
        host = getattr(args, "host", "0.0.0.0")
        # Validate port
        if not (1024 <= port <= 65535):
            _print_error(f"Port {port} is out of valid range (1024-65535)")
            raise SystemExit(1)

        # Imported here (not at module level) so that `run` works without the
        # web-dashboard extras, and so a missing one yields a clear message.
        missing = _deps.missing(["uvicorn", "fastapi"])
        if missing:
            print(_deps.install_hint(missing), file=sys.stderr)
            raise SystemExit(1)

        import uvicorn

        print(f"[*] Starting MachineLearningMachine Live Dashboard on http://{host}:{port} ...")
        print(f"[*] Open http://localhost:{port} in your browser")
        print(f"[*] Press Ctrl+C to stop\n")
        try:
            uvicorn.run("machinelearningmachine.server.app:app", host=host, port=port, log_level="info")
        except OSError as e:
            if "Address already in use" in str(e):
                _print_error(f"Port {port} is already in use. Try a different port: --port {port+1}")
            else:
                _print_error(str(e))
            raise SystemExit(1)
        return

    if args.command == "run":
        # Validate prompt early
        if not args.prompt or not args.prompt.strip():
            _print_error("Prompt cannot be empty. Provide --prompt 'your task here'")
            raise SystemExit(1)
        if len(args.prompt) > 5000:
            _print_error(f"Prompt too long ({len(args.prompt)} chars). Max 5000 characters.")
            raise SystemExit(1)
        if args.turns and (args.turns < 1 or args.turns > 10):
            _print_error(f"Turns must be between 1 and 10, got {args.turns}")
            raise SystemExit(1)

        try:
            asyncio.run(execute_cli_run(args))
        except KeyboardInterrupt:
            print("\n\n⚠️  Interrupted by user")
            raise SystemExit(1)
        except ValueError as e:
            _print_error(str(e))
            raise SystemExit(1)
        except Exception as e:
            _print_error(f"Unexpected error: {e}")
            print("\nFor debugging, run with Python traceback:", file=sys.stderr)
            print(f"  python -m machinelearningmachine.cli run --topology {args.topology} --prompt \"...\"", file=sys.stderr)
            raise SystemExit(1)


async def execute_cli_run(args):
    mesh = AgentMesh()

    # Parse agent_ids if provided
    agent_ids = None
    if args.agent_ids:
        agent_ids = [aid.strip() for aid in args.agent_ids.split(",") if aid.strip()]
        # Validate existence
        missing = [aid for aid in agent_ids if aid not in mesh.agents]
        if missing:
            raise ValueError(f"Agents not found: {', '.join(missing)}. Available: {', '.join(mesh.agents.keys())}")

    print(f"\n{'='*60}")
    print(f"🤖 MachineLearningMachine - {args.topology.upper()} Topology")
    print(f"{'='*60}")
    print(f"📝 Task: {args.prompt[:100]}{'...' if len(args.prompt) > 100 else ''}")
    print(f"🔧 Agents: {', '.join(agent_ids) if agent_ids else f'{args.agent_a} <-> {args.agent_b}'}")
    print(f"{'='*60}\n")

    # Configure delay for faster testing if requested
    delay_kwargs = {"inter_turn_delay": 0} if args.no_delay else {}
    if args.topology in ("pipeline", "hub", "debate"):
        delay_kwargs = {"inter_step_delay": 0} if args.no_delay else {}
        if args.topology == "debate":
            delay_kwargs = {"inter_turn_delay": 0} if args.no_delay else {}

    transcript = []
    try:
        if args.topology == "p2p":
            print(f"💬 Initiating direct dialogue: {args.agent_a} <---> {args.agent_b} ({args.turns} turns)")
            # Monkey-patch delay if needed
            from .topologies.p2p import P2PTopology
            if args.no_delay:
                # Create topology directly with no delay
                agent_a = mesh.get_agent(args.agent_a)
                agent_b = mesh.get_agent(args.agent_b)
                if not agent_a or not agent_b:
                    raise ValueError(f"Agents not found: {args.agent_a}, {args.agent_b}")
                topo = P2PTopology(agent_a, agent_b, mesh.bus, max_turns=args.turns, inter_turn_delay=0)
                transcript = await topo.execute(args.prompt)
            else:
                transcript = await mesh.talk_p2p(args.agent_a, args.agent_b, args.prompt, turns=args.turns)
        elif args.topology == "pipeline":
            ids = agent_ids or ["arena-ai", "claude", "copilot", "gpt"]
            print(f"🔗 Initiating pipeline: {' -> '.join(ids)}")
            if args.no_delay:
                from .topologies.pipeline import PipelineTopology
                seq = [mesh.get_agent(aid) for aid in ids if mesh.get_agent(aid)]
                topo = PipelineTopology(seq, mesh.bus, inter_step_delay=0)
                transcript = await topo.execute(args.prompt)
            else:
                transcript = await mesh.run_pipeline(args.prompt, agent_ids=ids)
        elif args.topology == "debate":
            ids = agent_ids or ["copilot", "claude", "gpt"]
            print(f"🗣️  Initiating debate among: {', '.join(ids)}")
            transcript = await mesh.run_debate(args.prompt, agent_ids=ids)
        elif args.topology == "hub":
            hub_id = args.agent_a
            spoke_ids = agent_ids or [aid for aid in mesh.agents.keys() if aid != hub_id]
            print(f"🎯 Hub: {hub_id} coordinating spokes: {', '.join(spoke_ids)}")
            transcript = await mesh.run_hub_and_spoke(args.prompt, hub_id=hub_id, spoke_ids=spoke_ids)
    except ValueError as e:
        raise
    except Exception as e:
        print(f"\n❌ Dialogue failed: {e}")
        raise

    print(f"\n✅ Dialogue completed - {len(transcript)} messages\n")

    for i, msg in enumerate(transcript, 1):
        target = f" -> @{msg.recipient_name or msg.recipient_id}" if msg.recipient_id != "*" else " (broadcast)"
        print(f"\n{'─'*60}")
        print(f"[{i}/{len(transcript)}] [{msg.message_type.value.upper()}] {msg.sender_name}{target}")
        print(f"{'─'*60}\n")
        print(msg.content)
        # Small pause for readability in CLI
        if i < len(transcript):
            await asyncio.sleep(0.1)

    if args.export == "markdown":
        print("\n\n" + "="*60)
        print("📄 MARKDOWN EXPORT")
        print("="*60 + "\n")
        print(mesh.export_markdown())
    elif args.export == "json":
        print("\n\n" + "="*60)
        print("📄 JSON EXPORT")
        print("="*60 + "\n")
        print(mesh.export_json())

    if args.speak:
        print(f"\n{'='*60}")
        print("🔊 Reading the dialogue aloud...")
        print(f"{'='*60}\n")
        from . import tts
        lines = []
        for msg in transcript:
            target = f" to {msg.recipient_name or msg.recipient_id}" if msg.recipient_id != "*" else ""
            lines.append(f"{msg.sender_name}{target} says: {msg.content}")
        tts.speak("\n\n".join(lines))

    print(f"\n{'='*60}")
    print(f"✨ Done! {len(transcript)} messages exchanged")
    print(f"💡 Tip: Run 'serve' to see this in the web dashboard")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    main()
