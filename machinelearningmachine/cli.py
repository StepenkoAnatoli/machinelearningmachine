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
import os
import sys
import textwrap

from . import _deps
from .mesh import AgentMesh
from .server.config import AUTH_TOKEN_ENV_VAR
from .server.config import DEFAULT_MAX_SESSIONS as DEFAULT_MAX_SESSIONS_CLI
from .server.config import DEFAULT_SESSION_IDLE_TTL as DEFAULT_SESSION_TTL_CLI


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
    serve_parser = subparsers.add_parser(
        "serve",
        help="Launch the real-time interactive web dashboard",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""
        Bind policy (see SECURITY.md):
          * The dashboard binds to 127.0.0.1 only. It can add agents, run tasks,
            hold API keys and read saved sessions, so it is not a multi-user service.
          * Listening on another interface needs BOTH --allow-public and
            --auth-token <token>. The token is then required by every API call and
            WebSocket connection.
          * /api/read/url (read a web page aloud) is disabled unless you pass
            --enable-url-reader; when enabled it refuses loopback/private/link-local
            targets and validates every redirect.

        Examples:
          module-mesh serve                                  # local, safest
          module-mesh serve --port 9000
          module-mesh serve --enable-url-reader              # allow page reading locally
          module-mesh serve --host 0.0.0.0 --allow-public \\
              --auth-token "$(python -c 'import secrets;print(secrets.token_urlsafe(32))')"
        """),
    )
    serve_parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Host interface to bind (default: 127.0.0.1 - loopback only)",
    )
    serve_parser.add_argument("--port", type=int, default=8000, help="Port to bind (default: 8000)")
    serve_parser.add_argument(
        "--allow-public",
        action="store_true",
        help="Acknowledge that --host is reachable from other machines (requires --auth-token)",
    )
    serve_parser.add_argument(
        "--auth-token",
        default=None,
        help=f"Shared token clients must present. Also readable from {AUTH_TOKEN_ENV_VAR}.",
    )
    serve_parser.add_argument(
        "--enable-url-reader",
        action="store_true",
        help="Turn on /api/read/url so the dashboard can read a web page aloud (SSRF-guarded)",
    )
    serve_parser.add_argument(
        "--max-sessions",
        type=int,
        default=DEFAULT_MAX_SESSIONS_CLI,
        help="How many browser sessions keep a live mesh at once (default: %(default)s)",
    )
    serve_parser.add_argument(
        "--session-ttl",
        type=int,
        default=DEFAULT_SESSION_TTL_CLI // 60,
        help="Minutes an idle browser session keeps its mesh (default: %(default)s)",
    )
    serve_parser.add_argument(
        "--strict-provider-errors",
        action="store_true",
        help="Fail a run when a live provider errors, instead of falling back to the labelled simulator",
    )
    serve_parser.add_argument(
        "--allow-origin",
        action="append",
        default=None,
        metavar="ORIGIN",
        help="Extra origin allowed to call the API (repeatable). Avoid unless you need it.",
    )

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
        from .server.config import ServerConfig, normalize_origins, validate_bind_policy

        port = getattr(args, "port", 8000)
        host = getattr(args, "host", "127.0.0.1")
        allow_public = bool(getattr(args, "allow_public", False))
        auth_token = getattr(args, "auth_token", None) or os.environ.get(AUTH_TOKEN_ENV_VAR)
        enable_url_reader = bool(getattr(args, "enable_url_reader", False))

        try:
            port = int(port)
        except (TypeError, ValueError) as exc:
            _print_error(f"Port {port!r} is not a number")
            raise SystemExit(2) from exc
        if not (1024 <= port <= 65535):
            _print_error(f"Port {port} is out of valid range (1024-65535)")
            raise SystemExit(2)

        # The bind policy is enforced here, once, for every entry point (CLI,
        # python -m, launch scripts). No token is ever printed or logged.
        problems = validate_bind_policy(
            host, allow_public=allow_public, auth_token=auth_token, port=port
        )
        if problems:
            for problem in problems:
                _print_error(problem)
            raise SystemExit(2)

        token = (auth_token or "").strip() or None
        server_config = ServerConfig(
            auth_token=token,
            enable_url_reader=enable_url_reader,
            allow_public=allow_public,
            fallback_to_mock=not getattr(args, "strict_provider_errors", False),
            max_sessions=max(1, int(getattr(args, "max_sessions", DEFAULT_MAX_SESSIONS_CLI))),
            session_idle_ttl=max(60, int(getattr(args, "session_ttl", DEFAULT_SESSION_TTL_CLI // 60)) * 60),
            allow_origins=normalize_origins(getattr(args, "allow_origin", None)),
            host=host,
            port=port,
        )

        # Imported here (not at module level) so that `run` works without the
        # web-dashboard extras, and so a missing one yields a clear message.
        missing = _deps.missing(["uvicorn", "fastapi"])
        if missing:
            print(_deps.install_hint(missing), file=sys.stderr)
            raise SystemExit(1)

        import uvicorn

        from .server.app import create_app

        banner = [
            f"[*] MachineLearningMachine dashboard: http://{host}:{port}",
            f"[*] Auth:           {'token required (clients must sign in)' if token else 'off - loopback only'}",
            f"[*] Page reader:    {'enabled (SSRF-guarded)' if enable_url_reader else 'disabled (start with --enable-url-reader)'}",
            f"[*] Provider errors: {'fail the run' if not server_config.fallback_to_mock else 'labelled simulator fallback'}",
            f"[*] Sessions:       up to {server_config.max_sessions} live meshes, "
            f"idle for {int(server_config.session_idle_ttl // 60)} min then released",
        ]
        if not server_config.on_loopback:
            banner.append(
                "[!] This port is reachable from other machines. Anyone with the token can "
                "drive this mesh; there is no per-user authorisation beyond that shared token."
            )
        banner.append("[*] Press Ctrl+C to stop\n")
        print("\n".join(banner))
        try:
            # The app object (not an import string) carries the config.
            uvicorn.run(create_app(server_config), host=host, port=port, log_level="info")
        except OSError as e:
            if "Address already in use" in str(e):
                _print_error(f"Port {port} is already in use. Try a different port: --port {port + 1}")
            else:
                _print_error(str(e))
            raise SystemExit(1) from e
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
            raise SystemExit(1) from None  # the user stopped it; nothing to chain
        except ValueError as e:
            _print_error(str(e))
            raise SystemExit(1) from e
        except Exception as e:
            _print_error(f"Unexpected error: {e}")
            print("\nFor debugging, run with Python traceback:", file=sys.stderr)
            print(f"  python -m machinelearningmachine.cli run --topology {args.topology} --prompt \"...\"", file=sys.stderr)
            raise SystemExit(1) from e


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
    except ValueError:
        raise
    except Exception as e:
        print(f"\n❌ Dialogue failed: {e}")
        raise

    simulated = sum(1 for m in transcript if m.metadata.get("simulated"))
    if simulated:
        print(
            f"\n⚠️  {simulated}/{len(transcript)} answers came from the built-in simulator: "
            "no model API was called and no code was executed or tested."
        )
        print("   Configure OPENAI_API_KEY / ANTHROPIC_API_KEY (or the dashboard's ⚙️ Settings) for real answers.\n")
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
    print("💡 Tip: Run 'serve' to see this in the web dashboard")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    main()
