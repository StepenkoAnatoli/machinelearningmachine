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
from typing import List

from . import _deps
from .mesh import AgentMesh
from .server.config import AUTH_TOKEN_ENV_VAR
from .server.config import DEFAULT_MAX_QUEUED as DEFAULT_MAX_QUEUED_CLI
from .server.config import DEFAULT_MAX_SESSIONS as DEFAULT_MAX_SESSIONS_CLI
from .server.config import DEFAULT_RUN_TIMEOUT as DEFAULT_RUN_TIMEOUT_CLI
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
        help=(
            f"Shared token clients must present. Also readable from {AUTH_TOKEN_ENV_VAR} "
            "(or the MODULE_MESH_AUTH_TOKEN alias)."
        ),
    )
    serve_parser.add_argument(
        "--enable-url-reader",
        action="store_true",
        help=(
            "Turn on /api/read/url so the dashboard can read a web page aloud (SSRF-guarded). "
            "Also readable from MACHINELEARNINGMACHINE_ENABLE_URL_READER=1"
        ),
    )
    serve_parser.add_argument(
        "--max-sessions",
        type=int,
        default=None,
        metavar="N",
        help=(
            f"How many browser sessions keep a live mesh at once (default: {DEFAULT_MAX_SESSIONS_CLI}; "
            "or MACHINELEARNINGMACHINE_MAX_SESSIONS)"
        ),
    )
    serve_parser.add_argument(
        "--session-ttl",
        type=int,
        default=None,
        metavar="MINUTES",
        help=(
            f"Minutes an idle browser session keeps its mesh (default: {DEFAULT_SESSION_TTL_CLI // 60}; "
            "or MACHINELEARNINGMACHINE_SESSION_TTL). Idle sessions are swept, not just counted."
        ),
    )
    serve_parser.add_argument(
        "--run-timeout",
        type=float,
        default=None,
        metavar="SECONDS",
        help=(
            f"Longest a single dialogue may run before it is reported as failed (default: "
            f"{DEFAULT_RUN_TIMEOUT_CLI:g}; or MACHINELEARNINGMACHINE_RUN_TIMEOUT). Bounds a provider "
            "that accepts the connection and never answers."
        ),
    )
    serve_parser.add_argument(
        "--max-queued",
        type=int,
        default=None,
        metavar="N",
        help=(
            f"How many runs may wait behind the active one, per browser session (default: "
            f"{DEFAULT_MAX_QUEUED_CLI}; or MACHINELEARNINGMACHINE_MAX_QUEUED). 0 restores the "
            "pre-queue refusal: a second run is a 409 instead of a queued 202."
        ),
    )
    serve_parser.add_argument(
        "--allow-insecure-provider-urls",
        action="store_true",
        help=(
            "Let the dashboard dial out to private/loopback provider base URLs even when the "
            "server is bound to a network-reachable interface. Only for a machine whose own "
            "Ollama/vLLM must be usable from that dashboard. Also readable from "
            "MACHINELEARNINGMACHINE_ALLOW_INSECURE_PROVIDER_URLS=1"
        ),
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
        help=(
            "Extra origin allowed to call the API (repeatable). Avoid unless you need it. "
            "Also readable from MACHINELEARNINGMACHINE_ALLOW_ORIGINS (comma-separated)."
        ),
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
        "--live",
        choices=["openai", "anthropic", "both"],
        default=None,
        help=(
            "Answer from a real provider instead of the simulator. Keys are read from the "
            "environment (OPENAI_API_KEY / ANTHROPIC_API_KEY) *only* when you pass this: "
            "no flag, no outbound call, no spend. Requires the 'aiohttp' extra."
        ),
    )
    run_parser.add_argument(
        "--base-url",
        default=None,
        metavar="URL",
        help=(
            "OpenAI-compatible endpoint for --live openai (e.g. http://localhost:11434/v1 "
            "for Ollama/LMStudio/vLLM). Overrides OPENAI_BASE_URL."
        ),
    )
    run_parser.add_argument(
        "--openai-model",
        default=None,
        metavar="MODEL",
        help="OpenAI model ID for --live openai (default: current GPT-6 Astra; useful for local backends)",
    )
    run_parser.add_argument(
        "--anthropic-model",
        default=None,
        metavar="MODEL",
        help="Anthropic model ID for --live anthropic (default: current Claude Fable 5.1)",
    )
    run_parser.add_argument(
        "--provider-timeout",
        type=float,
        default=60.0,
        metavar="SECONDS",
        help="Per-request provider timeout for --live runs (default: %(default)s)",
    )
    run_parser.add_argument(
        "--speak",
        action="store_true",
        help="Read the dialogue aloud with your computer's built-in text-to-speech "
             "(macOS: say, Windows: SAPI, Linux: espeak-ng)",
    )

    args = parser.parse_args()

    if args.command == "serve" or len(sys.argv) == 1:
        from .env import EnvValueError, resolve_number
        from .env import flag as env_flag
        from .env import get as env_get
        from .server.config import (
            AUTH_TOKEN_SUFFIX,
            INSECURE_PROVIDER_URLS_SUFFIX,
            MAX_QUEUED_SUFFIX,
            MAX_SESSIONS_SUFFIX,
            RUN_TIMEOUT_SUFFIX,
            SESSION_TTL_SUFFIX,
            ServerConfig,
            normalize_origins,
            origins_from_env,
            url_reader_enabled_by_env,
            validate_bind_policy,
        )

        port = getattr(args, "port", 8000)
        host = getattr(args, "host", "127.0.0.1")
        allow_public = bool(getattr(args, "allow_public", False))
        # Env is honoured for every switch a launch script or container may want
        # to set; both MACHINELEARNINGMACHINE_* and MODULE_MESH_* spellings work.
        auth_token = getattr(args, "auth_token", None) or env_get(AUTH_TOKEN_SUFFIX)
        enable_url_reader = bool(getattr(args, "enable_url_reader", False)) or url_reader_enabled_by_env()

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
        # Every switch below accepts the flag first, then the environment, then the
        # default - and a value that is not a number stops startup instead of quietly
        # running with a different limit than the operator asked for.
        try:
            run_timeout = resolve_number(
                getattr(args, "run_timeout", None), RUN_TIMEOUT_SUFFIX,
                label="--run-timeout", default=DEFAULT_RUN_TIMEOUT_CLI,
                minimum=5.0, maximum=3600.0,
            )
            max_sessions = resolve_number(
                getattr(args, "max_sessions", None), MAX_SESSIONS_SUFFIX,
                label="--max-sessions", default=DEFAULT_MAX_SESSIONS_CLI,
                minimum=1.0, maximum=10000.0,
            )
            session_ttl_minutes = resolve_number(
                getattr(args, "session_ttl", None), SESSION_TTL_SUFFIX,
                label="--session-ttl", default=DEFAULT_SESSION_TTL_CLI // 60,
                minimum=1.0, maximum=43200.0,
            )
            max_queued = resolve_number(
                getattr(args, "max_queued", None), MAX_QUEUED_SUFFIX,
                label="--max-queued", default=DEFAULT_MAX_QUEUED_CLI,
                minimum=0.0, maximum=100.0,
            )
        except EnvValueError as exc:
            _print_error(str(exc))
            raise SystemExit(2) from exc

        server_config = ServerConfig(
            auth_token=token,
            enable_url_reader=enable_url_reader,
            allow_public=allow_public,
            fallback_to_mock=not getattr(args, "strict_provider_errors", False),
            run_timeout=run_timeout,
            allow_insecure_provider_urls=(
                bool(getattr(args, "allow_insecure_provider_urls", False))
                or env_flag(INSECURE_PROVIDER_URLS_SUFFIX)
            ),
            max_sessions=int(max_sessions),
            max_queued=int(max_queued),
            session_idle_ttl=session_ttl_minutes * 60,
            allow_origins=normalize_origins(
                list(getattr(args, "allow_origin", None) or []) + list(origins_from_env())
            ),
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
            f"[*] Page reader:    {'enabled (SSRF-guarded)' if enable_url_reader else 'disabled (use --enable-url-reader)'}",
            f"[*] Provider errors: {'fail the run' if not server_config.fallback_to_mock else 'labelled simulator fallback'}",
            f"[*] Sessions:       up to {server_config.max_sessions} live meshes, "
            f"idle for {int(server_config.session_idle_ttl // 60)} min then released by the reaper",
            f"[*] Run limit:      {server_config.run_timeout:.0f}s per dialogue; one run at a time per browser session"
            + (
                ", no queue (--max-queued 0: a second run is refused)"
                if server_config.max_queued <= 0
                else f", up to {server_config.max_queued} queued"
            ),
        ]
        if not server_config.on_loopback:
            banner.append(
                "[!] This port is reachable from other machines. Anyone with the token can "
                "drive this mesh; there is no per-user authorisation beyond that shared token."
            )
            if server_config.allow_insecure_provider_urls:
                banner.append(
                    "[!] --allow-insecure-provider-urls is ON: a token holder can point the "
                    "mesh at any address this machine can reach, including internal services."
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


async def _apply_live_providers(mesh, args) -> None:
    """
    Point the mesh's agents at real providers, if - and only if - asked to.

    The rule this exists to enforce: an ambient ``OPENAI_API_KEY`` in someone's
    shell must never turn a local demo into a paid API call. Reading the
    environment is allowed only behind an explicit ``--live``.
    """
    which = getattr(args, "live", None)
    if not which:
        return

    import os

    from .agents.providers import (
        DEFAULT_ANTHROPIC_MODEL,
        DEFAULT_OPENAI_MODEL,
        AnthropicProvider,
        OpenAIProvider,
    )

    # ``or 60.0`` would be the lazy way and is wrong: 0 is a value the user typed,
    # and silently turning it into the default is how a mistake becomes a surprise.
    raw_timeout = getattr(args, "provider_timeout", None)
    try:
        timeout = 60.0 if raw_timeout is None else float(raw_timeout)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"--provider-timeout must be a number of seconds, got {raw_timeout!r}") from exc
    if not (1.0 <= timeout <= 900.0):
        raise ValueError("--provider-timeout must be between 1 and 900 seconds")
    base_url = (getattr(args, "base_url", None) or "").strip() or os.environ.get("OPENAI_BASE_URL", "")
    openai_model = (getattr(args, "openai_model", None) or "").strip() or None
    anthropic_model = (getattr(args, "anthropic_model", None) or "").strip() or None

    # Read explicitly, then handed over with allow_env_key=False: the provider must
    # not go looking in the environment behind this decision.
    openai_key = os.environ.get("OPENAI_API_KEY", "")
    anthropic_key = os.environ.get("ANTHROPIC_API_KEY", "")
    local_backend = bool(base_url) and any(
        marker in base_url for marker in ("localhost", "127.0.0.1", "::1", "host.docker.internal")
    )

    lines: List[str] = []
    if which in ("openai", "both"):
        if not openai_key and not local_backend:
            raise ValueError(
                "--live openai needs OPENAI_API_KEY in the environment, or --base-url pointing "
                "at a local OpenAI-compatible backend (e.g. http://localhost:11434/v1)."
            )
        # Distinct names per vendor, not one reused ``provider``: both blocks
        # assign agents from *this* provider, so a shared name is the one place a
        # merge could quietly point Claude at an OpenAI endpoint and still typecheck.
        openai_provider = OpenAIProvider(
            api_key=openai_key or None,
            base_url=base_url,
            model=openai_model or DEFAULT_OPENAI_MODEL,
            timeout=timeout,
            allow_env_key=False,
        )
        for agent in (mesh.gpt, mesh.copilot):
            if agent is not None:
                agent.provider = openai_provider
        lines.append(
            f"🌐 --live openai: @{', @'.join(a.agent_id for a in (mesh.gpt, mesh.copilot) if a)} "
            f"will POST to {openai_provider.base_url}/chat/completions "
            f"(model {openai_provider.model}, key {'from OPENAI_API_KEY' if openai_key else 'not set - local backend'})"
        )
    if which in ("anthropic", "both"):
        if not anthropic_key:
            raise ValueError("--live anthropic needs ANTHROPIC_API_KEY in the environment.")
        anthropic_provider = AnthropicProvider(
            api_key=anthropic_key,
            model=anthropic_model or DEFAULT_ANTHROPIC_MODEL,
            timeout=timeout,
            allow_env_key=False,
        )
        for agent in (mesh.claude, mesh.arena_ai):
            if agent is not None:
                agent.provider = anthropic_provider
        lines.append(
            f"🌐 --live anthropic: @{', @'.join(a.agent_id for a in (mesh.claude, mesh.arena_ai) if a)} "
            f"will POST to {anthropic_provider.base_url}/messages (model {anthropic_provider.model}, key from ANTHROPIC_API_KEY)"
        )

    for line in lines:
        print(line)
    print("   The conversation text you are about to send leaves this machine.")


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

    # Validated before anything is printed: a bad --live setup should fail with the
    # reason, not after a banner that implies the run has begun.
    await _apply_live_providers(mesh, args)

    print(f"\n{'='*60}")
    print(f"🤖 MachineLearningMachine - {args.topology.upper()} Topology")
    print(f"{'='*60}")
    print(f"📝 Task: {args.prompt[:100]}{'...' if len(args.prompt) > 100 else ''}")
    print(f"🔧 Agents: {', '.join(agent_ids) if agent_ids else f'{args.agent_a} <-> {args.agent_b}'}")
    print(f"{'='*60}\n")

    # One place builds every run: the delay is a mesh argument like everywhere else,
    # so --no-delay applies to all four topologies (it used to be honoured for p2p
    # and pipeline only, silently ignored for debate and hub).
    delay = 0.0 if args.no_delay else None

    transcript = []
    try:
        if args.topology == "p2p":
            print(f"💬 Initiating direct dialogue: {args.agent_a} <---> {args.agent_b} ({args.turns} turns)")
            transcript = await mesh.talk_p2p(
                args.agent_a, args.agent_b, args.prompt, turns=args.turns, inter_turn_delay=delay
            )
        elif args.topology == "pipeline":
            ids = agent_ids or ["arena-ai", "claude", "copilot", "gpt"]
            print(f"🔗 Initiating pipeline: {' -> '.join(ids)}")
            transcript = await mesh.run_pipeline(args.prompt, agent_ids=ids, inter_step_delay=delay)
        elif args.topology == "debate":
            ids = agent_ids or ["copilot", "claude", "gpt"]
            print(f"🗣️  Initiating debate among: {', '.join(ids)}")
            transcript = await mesh.run_debate(args.prompt, agent_ids=ids, inter_turn_delay=delay)
        elif args.topology == "hub":
            hub_id = args.agent_a
            spoke_ids = agent_ids or [aid for aid in mesh.agents.keys() if aid != hub_id]
            print(f"🎯 Hub: {hub_id} coordinating spokes: {', '.join(spoke_ids)}")
            transcript = await mesh.run_hub_and_spoke(
                args.prompt, hub_id=hub_id, spoke_ids=spoke_ids, inter_step_delay=delay
            )
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
        if getattr(args, "live", None):
            print("   Those replies are labelled fallbacks: the configured provider failed.\n")
        else:
            print(
                "   For real answers pass --live openai / --live anthropic (keys are then read "
                "from\n"
                "   OPENAI_API_KEY / ANTHROPIC_API_KEY), or use ⚙️ Settings in the dashboard.\n"
            )
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
