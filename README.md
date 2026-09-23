# MachineLearningMachine: Multi-Module Inter-Agent Communication Mesh

[![CI](https://github.com/StepenkoAnatoli/machinelearningmachine/actions/workflows/ci.yml/badge.svg)](https://github.com/StepenkoAnatoli/machinelearningmachine/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Bind default: loopback](https://img.shields.io/badge/binds-127.0.0.1-informational)](SECURITY.md)
[![Python](https://img.shields.io/badge/python-3.10%20%7C%203.11%20%7C%203.12-blue)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.110+-teal)](https://fastapi.tiangolo.com)
[![WebSocket](https://img.shields.io/badge/WebSocket-Real--Time-orange)](https://websockets.readthedocs.io/)
[![User-Centered](https://img.shields.io/badge/design-user--centered-purple)](https://github.com/StepenkoAnatoli/machinelearningmachine)

A modular orchestration system that enables AI modules to talk directly to each other — connecting **Arena AI**, **GitHub Copilot**, **Claude**, **GPT-6 Astra**, and custom user-defined modules across standardized inter-agent communication topologies.

> **Never installed anything from a download before? Start here:**
> **[INSTALL-WINDOWS.md](INSTALL-WINDOWS.md)** is a step-by-step guide written for
> complete beginners on Windows — no command line, no jargon, with what to click
> and what to do when Windows or antivirus asks a question. The short version is
> two clicks: install Python once, then double-click `launch-windows.bat`.

> **What the name does and does not mean:** despite the name, this is **not** a
> machine-learning system. It is an *inter-agent orchestration demo*: a message bus,
> four scripted agent roles, four topologies, and a dashboard. By default nothing is
> trained, inferred, or even executed - the "modules" answer from a deterministic
> template simulator unless you configure a real API key. Version `0.1.0`, single
> maintainer, no stability promises.

> **User-Centered Engineering:** This project prioritizes real user value over mere technical correctness. Every feature is designed to be intuitive, accessible, secure, and genuinely useful — with thoughtful error handling, clear feedback, and practical defaults that work out of the box.

## ✨ User-Centered Design Highlights

**Security, honestly stated** (full model in [SECURITY.md](SECURITY.md)):
- 🔒 The server binds `127.0.0.1` by default and **refuses** to listen on any other
  interface unless you pass `--allow-public` **and** `--auth-token <token>`
- 👥 Per-browser state isolation: each session gets its own mesh, its own provider
  keys, its own WebSockets, and its own saved-session folder (bounded LRU + TTL)
- 🛡️ SSRF-guarded page reader, **off unless you pass `--enable-url-reader`**; every
  hop (including redirects) is checked against loopback/private/link-local/reserved ranges
- 🧼 XSS: transcript text is sanitized with DOMPurify against an explicit allowlist
  (no regex "sanitizing"), rendered via DOM nodes; colours/avatars are validated
- 📦 No third-party origins: Tailwind, FontAwesome, Marked, DOMPurify and
  Highlight.js are vendored, version-pinned, checksummed and audited (`npm audit`/`pip-audit` in CI); CSP is `default-src 'self'`
- ⏱️ Input validation with plain-language errors, run/page-read cooldowns, login lockout
- 🔒 An `OPENAI_API_KEY` in your shell is never used by the dashboard, and never sent
  anywhere you did not aim it: providers configured by a browser session are built with
  `allow_env_key=False`, and the CLI only dials out when you pass `--live` (which prints
  the endpoint, model and where the key came from before it sends a byte)
- 🧵 One run at a time per browser session - a second run waits its turn in a bounded
  per-session queue (`202` with its position, `429` + `Retry-After` when the queue is
  full, `409` only when `--max-queued 0` restores refusal), because agents, transcript
  and provider memory are shared per session. A run that never answers is cut off after
  `--run-timeout` (504); a queued run that never starts is dropped with the process,
  like the lock itself
- 🩺 A slow tab cannot stall a run: each connection has its own bounded outbox
  (128 frames, drop-oldest), and when frames are dropped the client is told and
  re-pulls the transcript instead of showing you a conversation with holes in it
- 🧹 Idle sessions are actually reclaimed: a background sweep expires them, closes their
  sockets with a visible "session released" notice, and the next request from that
  browser starts clean rather than silently inheriting a mesh nobody owns
- 🚫 What you still do **not** get: per-user authorisation beyond one shared token,
  encryption at rest, or multi-worker scaling - see SECURITY.md §1 and §7. Stop is
  cooperative (the current provider request finishes before stopping), and the run
  queue is per-process: it dies with the server and does not span workers

**Intuitive & Accessible UX:**
- 🎨 Toast notifications instead of jarring `alert()`/`confirm()`
- ♿ Full keyboard navigation, focus traps in modals, ARIA labels, skip links
- ⌨️ Shortcuts: `Ctrl+Enter` to run, `Esc` to close modals
- 🔍 Search/filter messages, character count, auto-resizing prompt
- 📱 Responsive, respects `prefers-reduced-motion`, and the network canvas draws **on demand** - it repaints when something changes and then stops (no permanent animation loop, idle tab costs nothing)
- 🌗 **Light or dark** — the dashboard follows your OS preference on the first visit and remembers your choice afterwards (header toggle, one `localStorage` key). Dark stays the default when the OS expresses no preference, and both themes cover the whole page: chrome, panels, modals, toasts, code blocks and the network graph

**Practical & Pleasant:**
- 🚀 **One-click install & launch** — double-click `launch-windows.bat` / `launch-macos.command` / `launch-linux.sh` and the app sets itself up (Python check → venv → dependencies → dashboard → browser) and runs. It refuses to start on a Python older than 3.10 with a message naming the download page, and if port 8000 is already taken it uses the next free port instead of exiting — both of which used to look like "the app is broken"
- 📖 **[INSTALL-WINDOWS.md](INSTALL-WINDOWS.md)** — the same procedure written for someone who has never installed software from a download: what to click, what the warnings mean, and a troubleshooting table
- 🧭 **First screen explains itself** — an empty transcript shows three numbered steps (say what you want → Execute Dialogue → read the answers), a **How to use** button reopens them any time, and the simulator-labelling is explained where the answers appear
- 💾 **Saved sessions** — store any conversation (modules + full transcript) on your computer and reload it later from the *Sessions* panel
- 🧩 **Module presets** — five built-in templates (Security Auditor, DB Expert, …) fill the *Add Module* form in one click, and you can save your own from a filled-in form, rename or delete them, and export/import them as plain JSON files to carry a kit to another browser. The library lives in this browser (`localStorage`) — the server never stores or serves presets
- 🔊 **Reads what you write** — your prompt, every agent reply, any message, any text file, or (if the operator enabled it) a web page is read aloud with your computer's own voices (no API keys); plus 🎙️ voice dictation
- 🔌 **Genuinely offline UI**: nothing in the dashboard is fetched from a third party, so it renders with the network unplugged
- 🚀 Faster execution (0.15s vs 0.4s delays), no unnecessary waiting
- 💡 Contextual simulator: detects `rate_limiter`, `cache`, `auth`, `queue` domains and drafts code plus *proposed* tests - as a starting point, explicitly not as verified output
- 📋 One-click copy for code blocks, export with proper headers
- 🎯 Clear empty states, helpful presets, agent detail on click
- 🛡️ Privacy: provider keys are held per browser session in memory, never written to
  disk, never returned by the API, never logged; transcripts are stored in your own
  home directory and are namespaced per browser

**Engineering Quality:**
- 🧪 626 tests (484 Python + 142 jsdom browser cases) running in CI on Python 3.10/3.11/3.12, plus ruff, `pip-audit`, a vendor-integrity check, and a job that installs the built wheel and *serves* it
- 🔒 Simulated output is labelled as simulated - see [Mock output vs. real output](#-mock-output-vs-real-output-read-this)
- 📝 Friendly CLI with validation, progress indicators, `--agent-ids` and `--no-delay` options
- 🔧 Realistic examples that actually help users get started

---

## 🌟 Key Features

- 🤝 **Direct Module-to-Module Communication**:
  - **Arena AI talks to Copilot**: Spec formulation ⇄ Code implementation ⇄ Review ⇄ Finalization.
  - **Copilot talks to Claude**: Implementation proposals ⇄ In-depth architectural & safety critiques.
  - **Copilot / Claude to GPT**: Logic validation ⇄ Pytest unit test suites ⇄ Boundary assertions.
- 📐 **Multiple Interaction Topologies**:
  1. **Peer-to-Peer (P2P)**: Direct bilateral dialogue between any two modules.
  2. **Sequential Pipeline Relay**: Step-by-step handover chain (`Arena AI -> Claude -> Copilot -> GPT`).
  3. **Collaborative Multi-Agent Debate**: Round-table discussion where modules critique trade-offs and arrive at consensus.
  4. **Hub & Spoke (Supervisor)**: Lead module coordinates, assigns subtasks to spokes, and aggregates results.
- ⚡ **Standardized Inter-Agent Message Protocol**:
  - Typed messages: `TASK_SPEC`, `PROPOSAL`, `CRITIQUE`, `REVISION`, `QUERY`, `ANSWER`, `HANDOFF`, `CONSENSUS`, `SYSTEM`.
  - Point-to-point addressing (`sender_id -> recipient_id`) and topic-based broadcast pub/sub.
  - Persistent message history and 1-click Markdown / JSON transcript export.
- 🖥️ **Live Interactive Web Dashboard**:
  - Real-time WebSocket feed with code highlighting and 1-click copy.
  - Dynamic visual network graph showing packet animations between active agents.
  - Quick-launch presets and custom module builder.
- 🔊 **Read-Aloud Studio (Text-to-Speech)**:
  - Read **what you write**: your prompt, individual replies, or the whole conversation — with per-message speaker buttons.
  - Read **other things too**: paste any text, open a local file (`.txt`, `.md`, `.csv`, `.json`, code…), or paste a web page link and the machine fetches, cleans and reads it aloud.
  - Pick your voice and speed; auto-read replies as they arrive; dictate your prompt by voice (🎙️).
  - Uses the Web Speech API — built into Chrome/Edge/Safari, no install, no keys, offline.
  - CLI: `module-mesh run --speak` reads the dialogue with the OS voice (`say` / SAPI / espeak-ng).
- 💾 **Session Persistence**:
  - Save the current agents + full transcript with one click; reload, inspect or delete saved sessions from the *Sessions* panel.
  - Stored locally in `~/.module_mesh/sessions` (never uploaded, never committed).
- 🔌 **Dual Engine: Zero-Config Simulation or Live APIs**:
  - Out of the box every module answers from a deterministic **simulator** - useful for
    demoing the protocol and the UI, and clearly labelled as simulated (see
    [Mock output vs. real output](#-mock-output-vs-real-output-read-this)).
  - ⚙️ Settings can point the modules at real **OpenAI (GPT-6 Astra)**, **Anthropic (Claude Fable 5.1)**,
    or **local Ollama / LMStudio / vLLM** backends. Keys are held in memory for your
    browser session only, are never saved or returned by the API, are only
    *shape-checked* unless you tick **Verify the OpenAI key now** (one `GET /models` call),
    and a base URL you type there is checked against the same SSRF policy as the page
    reader - on a publicly reachable bind, private/loopback addresses are refused
    (`--allow-insecure-provider-urls` re-enables them for a machine that really does run
    its own Ollama). The live defaults are GPT-6 Astra (`gpt-6-astra`) and Claude Fable
    5.1 (`claude-fable-5-1`); Python callers can pass `model=` to either provider when
    using a pinned or local-compatible model. In the terminal, `run --live openai` is
    what reads your environment keys.
  - A live provider that fails raises `ProviderError`: you either get a reply visibly
    marked `provider failed → simulated`, or - with `--strict-provider-errors` - a 502.

---

## 🏗️ System Architecture

```
                  ┌─────────────────────────────────────────────────┐
                  │                 MessageBus                      │
                  │   - Point-to-Point Routing   - Topic Pub/Sub    │
                  │   - Event Streaming Hooks    - Full Audit Log   │
                  └──────┬──────────────┬──────────────┬────────────┘
                         │              │              │
        ┌────────────────┴──┐   ┌───────┴──────┐   ┌───┴───────────────┐
        │     Arena AI      │   │GitHub Copilot│   │    Claude Fable 5.1 │
        │(Lead Orchestrator)│   │(Code Synth)  │   │(Systems Architect)│
        └─────────┬─────────┘   └───────┬──────┘   └───┬───────────────┘
                  │                     │              │
                  └───────────────┬─────┴──────────────┘
                                  │
                        ┌─────────┴─────────┐
                        │      GPT-6 Astra    │
                        │(Test Synthesizer) │
                        └───────────────────┘
```

---

## 🚀 Quick Start

### 1. One-Click Launch (recommended)

> ❓ **Using Windows and never done this before?** Read
> **[INSTALL-WINDOWS.md](INSTALL-WINDOWS.md)** instead of this section — it walks
> through installing Python and unzipping the download, with no commands at all.

Clone the repository, then **double-click the launcher for your computer**:

| Your OS    | File to double-click      |
|------------|---------------------------|
| Windows    | `launch-windows.bat`      |
| macOS      | `launch-macos.command`    |
| Linux      | `launch-linux.sh` (run: `./launch-linux.sh`) |

That single file does everything for you:

1. 🔍 Finds your Python and checks it is 3.10 or newer (install [python.org](https://www.python.org/downloads/) first if you don't have it — the launcher tells you if it is too old or is the Microsoft Store placeholder)
2. 📦 Creates a private `.venv` environment (first run only)
3. ⬇️ Installs the dependencies once — a few minutes the first time, instant afterwards
4. 🚀 Starts the dashboard on `127.0.0.1` — port 8000, or the next free port if 8000 is busy
5. 🌐 Opens your browser at that address automatically

Close the terminal window (or press `Ctrl+C`) to stop the app.

The launcher checks that your Python is 3.10 or newer (and says what to install if
it is not), and starts on the first free port if 8000 is already taken, so a copy
of the app left running from an earlier session cannot stop the next launch.

> **macOS note:** if your Mac blocks the script the first time, right-click it → *Open* → *Open*.
> **Linux note:** if the file isn't executable, run `chmod +x launch-linux.sh` once.

### 2. Manual installation (if you prefer the terminal)

The package depends on **pydantic**, **fastapi**, **uvicorn**, **aiohttp**, **requests**, and **websockets**. Install the project *and* its dependencies:

```bash
git clone https://github.com/StepenkoAnatoli/machinelearningmachine.git
cd machinelearningmachine

python3 -m venv .venv && source .venv/bin/activate   # Windows: py -m venv .venv
pip install -e .
```

Then launch the dashboard:

```bash
python -m machinelearningmachine serve            # binds 127.0.0.1:8000
# or, equivalently:
python -m machinelearningmachine
```

Open `http://localhost:8000` in your browser to access the real-time visual dashboard.

#### Environment variables

Every setting accepts the canonical `MACHINELEARNINGMACHINE_*` name or the short
`MODULE_MESH_*` alias (the long name wins if both are set). Booleans are `1`,
`true`, `yes` or `on` - anything else, including a typo, leaves the default in
place, so a misspelled value never silently enables a feature.

| Variable | Effect |
| --- | --- |
| `MACHINELEARNINGMACHINE_AUTH_TOKEN` | The token clients must present (required for any non-loopback bind) |
| `MACHINELEARNINGMACHINE_ENABLE_URL_READER` | Turns on `/api/read/url`; off unless this or `--enable-url-reader` |
| `MACHINELEARNINGMACHINE_ALLOW_ORIGINS` | Comma-separated CORS origins to add (avoid unless needed) |
| `MACHINELEARNINGMACHINE_SESSIONS_DIR` | Where saved transcripts live (default `~/.module_mesh/sessions`) |
| `MACHINELEARNINGMACHINE_URL_ALLOWLIST` | Hosts the page reader may fetch - *replaces* the private-address blocklist |
| `MACHINELEARNINGMACHINE_RUN_TIMEOUT` | Seconds a single dialogue may run before it is failed with a 504 (5-3600) |
| `MACHINELEARNINGMACHINE_SESSION_TTL` | Minutes an idle browser session keeps its mesh (swept, not just counted) |
| `MACHINELEARNINGMACHINE_MAX_SESSIONS` | How many browser sessions keep a live mesh at once (LRU-evicted) |
| `MACHINELEARNINGMACHINE_MAX_QUEUED` | How many runs may wait behind the active one per session (0-100, default 5; `0` refuses instead of queueing) |
| `MACHINELEARNINGMACHINE_ALLOW_INSECURE_PROVIDER_URLS` | Lets a *publicly bound* dashboard dial out to private/loopback provider URLs. Off unless `1`/`true`/`yes`/`on` |
| `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` | Read **only** by `run --live …` (and by `OpenAIProvider()`/`AnthropicProvider()` if you construct them yourself). `serve` never reads them |

A numeric setting that is not a number stops startup with a message naming the variable
(`SESSION_TTL=5m` would otherwise leave a server running a limit nobody chose).

<details>
<summary><strong>Running it on a network (read SECURITY.md first)</strong></summary>

The old README example here was `--host 0.0.0.0`. That is no longer accepted: the
server refuses any non-loopback bind unless you acknowledge it *and* set a token.

```bash
TOKEN="$(python -c 'import secrets; print(secrets.token_urlsafe(32))')"
python -m machinelearningmachine serve \
  --host 0.0.0.0 --port 8000 \
  --allow-public --auth-token "$TOKEN"
```

- Every `/api/*` call and the `/ws` handshake then needs that token; the dashboard
  shows a sign-in field and stores it in an HttpOnly, SameSite=Lax cookie.
- One token = one trust domain. It is *not* per-user access control, and there is
  still no TLS, no roles, and no real rate limiting. Signing in is refused on a
  loopback server (there is no token to check there), and a script that authenticates
  by header but keeps no cookies gets an *ephemeral* session: short idle lifetime,
  evicted before a real browser's. Prefer an SSH tunnel:
  `ssh -L 8000:localhost:8000 host` and keep binding loopback.
- `--enable-url-reader` stays off unless you truly want the "read a web page aloud"
  fetcher; `--strict-provider-errors` makes live-provider failures fail the run
  instead of falling back to the labelled simulator.

</details>

#### Troubleshooting: `ModuleNotFoundError: No module named 'pydantic'`

This means the dependencies were never installed into the Python interpreter that is running the code — not a problem with the package itself. Fix it with:

```bash
cd machinelearningmachine        # the folder containing pyproject.toml
py -m pip install -e .           # Windows  (python -m pip install -e . on macOS/Linux)
```

If the error persists, you are likely using a different interpreter than the one you installed into. Compare these two commands — they must point at the same environment:

```bash
py -m pip --version
py -c "import sys; print(sys.executable)"
```

Other modules import only what they need: the simulation engine and CLI dialogues work with `pydantic` alone, and `fastapi`/`uvicorn` are only required for the web dashboard.
(`requirements.txt` installs all of them in one go, which is why the launch scripts need a single command.)

#### Troubleshooting: what the dashboard is telling you

The dashboard's messages quote the server verbatim, so these are the strings that
actually appear and what to do about each.

| You see | What happened | What to do |
| --- | --- | --- |
| *Queued at position 2 (run-7). It runs automatically when the active run finishes.* | This session is busy, so the run waits its turn (per-session FIFO, bounded by `--max-queued`). Stop cancels the queued run; the position is also in the `202` response. | Nothing - it starts on its own. The queue is per-process: a restart drops it. |
| *Run queue is full (5 waiting, max 5). Wait for a run to finish and try again.* | The queue is bounded. The response is `429` with a `Retry-After` header. | Wait for a completion, then retry. Operators can raise `--max-queued` (0-100). |
| *A run is already in progress in this browser session (run-3)* | Only with `--max-queued 0`: queueing disabled, so a second run is refused. Agents, transcript and export are shared, so runs cannot overlap. | Wait for it. A run that never finishes is stopped by `--run-timeout` and reported as `504`. |
| *Please wait 0.8s between runs* | The per-session run cadence gate (also applied to page reads). | Nothing - the button re-enables itself. |
| *the run was stopped after 180s: a provider accepted the request and never answered* | A live endpoint that hangs. The run is cut off rather than left running against your session. | Check the base URL/model in Settings; raise `--run-timeout` for a slow local model. |
| *Some messages were skipped while this tab was not keeping up (N) - refetched* | This tab's send queue (128 frames) overflowed - a backgrounded tab, a stalled connection. The transcript on the bus is intact; only this tab missed frames, so it re-pulled them. | Nothing. If it happens on every run, close other tabs on that session. |
| *Session released - Reload* | The idle sweep or capacity limit released this tab's mesh, so the socket was closed (code 4408) instead of letting you type into a session nobody owns. | Reload for a fresh session. Saved sessions are untouched - this is in-memory state only. |
| badge *simulated* / *provider failed → simulated* | No live provider, or the live call failed and the labelled simulator answered. | See [Mock output vs. real output](#-mock-output-vs-real-output-read-this). |
| *1 reply was longer than the 50,000-character message limit, so the transcript is cut short* | An answer exceeded the protocol's content limit, so it was clamped rather than dropped. The message says so in the transcript too. | Ask for something narrower, or raise `Message.model_config` limits if you really need more. |

---

## 💬 Code Examples: Modules Talking to Each Other

### Example 1: Arena AI Talks to GitHub Copilot (P2P)

```python
import asyncio
from machinelearningmachine import AgentMesh

async def main():
    mesh = AgentMesh()

    # Arena AI initiates task to Copilot for 4 turns:
    # Turn 1: Arena AI specs the task
    # Turn 2: Copilot implements code
    # Turn 3: Arena AI critiques and requests optimizations
    # Turn 4: Copilot delivers refined production implementation
    transcript = await mesh.talk_p2p(
        from_agent_id="arena-ai",
        to_agent_id="copilot",
        prompt="Build a thread-safe token bucket rate limiter in Python.",
        turns=4
    )

    for msg in transcript:
        print(f"[{msg.message_type.value.upper()}] {msg.sender_name} -> {msg.recipient_name}:")
        print(msg.content)
        print("-" * 40)

if __name__ == "__main__":
    asyncio.run(main())
```

### Example 2: Copilot Talks to Claude in an Architectural Debate

```python
import asyncio
from machinelearningmachine import AgentMesh

async def main():
    mesh = AgentMesh()

    # Copilot, Claude, and GPT debate architecture
    transcript = await mesh.run_debate(
        prompt="Kafka vs Redis Streams for ultra-low latency event sourcing",
        agent_ids=["copilot", "claude", "gpt"],
        rounds=1
    )

    for msg in transcript:
        print(f"[{msg.sender_name}]: {msg.content}\n")

if __name__ == "__main__":
    asyncio.run(main())
```

### Example 3: 4-Agent Sequential Pipeline Relay

```python
import asyncio
from machinelearningmachine import AgentMesh

async def main():
    mesh = AgentMesh()

    # Sequential relay:
    # Arena AI (Spec) -> Claude (Architecture Critique) -> Copilot (Code) -> GPT (Unit Tests)
    transcript = await mesh.run_pipeline(
        prompt="Implement an asynchronous task event emitter with backpressure",
        agent_ids=["arena-ai", "claude", "copilot", "gpt"]
    )

    print(mesh.export_markdown())

if __name__ == "__main__":
    asyncio.run(main())
```

---

## 🛠️ CLI Usage

You can also run any topology directly from your terminal:

```bash
# 1. Arena AI talks to Copilot
python3 -m machinelearningmachine.cli run --topology p2p --agent-a arena-ai --agent-b copilot --prompt "Build a JWT authentication middleware"

# 2. Multi-agent debate
python3 -m machinelearningmachine.cli run --topology debate --prompt "Monolith vs Microservices for MVP"

# 3. 4-module sequential pipeline relay
python3 -m machinelearningmachine.cli run --topology pipeline --prompt "Create an LRU cache decorator"

# 4. Export transcript to Markdown or JSON
python3 -m machinelearningmachine.cli run --topology p2p --prompt "Build a retry decorator" --export markdown

# 5. Hear the dialogue: read it with your computer's built-in voice
python3 -m machinelearningmachine.cli run --topology debate --prompt "Kafka vs Redis" --speak

# 6. No simulated pauses (all four topologies honour this; it used to be two of them)
python3 -m machinelearningmachine.cli run --topology hub --prompt "Plan the migration" --no-delay
```

### Real answers from the terminal

`run` is the simulator unless you say otherwise - deliberately, because the simulator is
what makes the demo reproducible, and because a key in your shell must never be able to
turn a demo into a paid API call on its own.

```bash
# Uses OPENAI_API_KEY (or ANTHROPIC_API_KEY); prints what it is about to send and where.
python3 -m machinelearningmachine.cli run --topology p2p --prompt "Design a retry decorator" --live openai

# A local OpenAI-compatible backend needs no key at all.
python3 -m machinelearningmachine.cli run --live openai --base-url http://localhost:11434/v1 --prompt "…"

python3 -m machinelearningmachine.cli run --live both --provider-timeout 30 --prompt "…"   # both vendors
```

| Flag | Effect |
| --- | --- |
| `--live {openai,anthropic,both}` | Which modules get real providers. `--live openai` wires `@gpt` and `@copilot`; `anthropic` wires `@claude` and `@arena-ai`; `both` wires all four |
| `--base-url URL` | Send the live calls somewhere else (Ollama, vLLM, LM Studio, a proxy). Validated by the same outbound policy as the dashboard |
| `--openai-model MODEL` / `--anthropic-model MODEL` | Override the current GPT-6 Astra / Claude Fable 5.1 defaults, especially when a local backend exposes a different model ID |
| `--provider-timeout SECONDS` | Per-request ceiling for a live call (1-900), and the ceiling on the *total* time one turn may spend waiting between retries: an upstream `Retry-After` longer than that is reported, not slept |
| `--strict-provider-errors` | A provider failure fails the run instead of falling back to the labelled simulator |

`--live openai` with no `OPENAI_API_KEY` and no `--base-url` is refused with exit code 1
and a message that names both fixes; it does not quietly run the simulator instead.

### The dashboard from the terminal

```bash
python3 -m machinelearningmachine.cli serve                      # 127.0.0.1:8000
python3 -m machinelearningmachine.cli serve --run-timeout 60     # cut off a stuck provider sooner
python3 -m machinelearningmachine.cli serve --session-ttl 30 --max-sessions 16
python3 -m machinelearningmachine.cli serve --max-queued 0       # refuse a second run instead of queueing it
python3 -m machinelearningmachine.cli serve --help               # every switch, with its default
```

---

## 🔊 Reading It Aloud & 💾 Saving Sessions

Both features live in the web dashboard — no extra setup, everything uses what's already on your computer.

### Reading aloud (Text-to-Speech)

| What you want | How |
|---------------|-----|
| Read **what I just wrote** | Put text in the prompt box → click **🔊 Read My Prompt** (or enable *Read my prompt when I run* and it happens automatically on every run) |
| Read **an agent's reply** | Click the **🔊 speaker button** on any message card |
| Read **everything automatically** | Tick *Read replies automatically* in the Read-Aloud Studio bar |
| Read **the whole conversation** | **Read All** button above the transcript |
| Read **any text** | *Read-Aloud Studio → My Text* tab → paste → **Read This Text** |
| Read **a file** | *From File* tab → pick a `.txt / .md / .csv / .json / .log / code…` file (≤ 1 MB) → **Read This File** |
| Read **a web page** | *Web Page* tab → paste a link → **Fetch & Read** (the page is fetched, cleaned of HTML, shown and read) |
| **Stop** any of the above | **Stop** button, or click the speaker that is currently pulsing |
| Change **voice / speed** | Voice + speed dropdowns in the Read-Aloud Studio bar, **🔊 Test Voice** to audition |
| **Dictate** my prompt | Click **🎙️ Dictate** next to the prompt, speak (Chrome/Edge; needs mic permission) |

Speech uses the browser's built-in Web Speech API — your OS voices, no accounts, no API keys, and it works offline.

### Saving sessions

1. Run a dialogue (and register any custom modules you like).
2. Click **Sessions** in the top bar → name it (optional) → **Save**.
3. Later — even after restarting the app — open **Sessions** again and click **Load** on that session. Its modules and full conversation reappear in the dashboard.

Sessions are stored as JSON on your machine in `~/.module_mesh/sessions` (set `MODULE_MESH_SESSIONS_DIR` to change). The 50 most recent sessions are kept; older ones are pruned automatically. API-key settings are *not* stored in sessions.

---

## 🧩 Adding Custom Modules

You can dynamically register custom modules in Python or through the Web UI:

```python
from machinelearningmachine import AgentMesh, CustomAgent

mesh = AgentMesh()

devops_agent = CustomAgent(
    agent_id="devops-module",
    name="DevOps Engineer",
    role="Kubernetes & CI/CD Deployment Specialist",
    system_prompt="Generate optimized Dockerfiles, GitHub Actions workflows, and Helm charts.",
    color="#3b82f6",
    avatar="🐳"
)

mesh.register_agent(devops_agent)

# Copilot now talks to DevOps Module:
transcript = await mesh.talk_p2p(
    from_agent_id="copilot",
    to_agent_id="devops-module",
    prompt="Containerize this FastAPI application with multi-stage build"
)
```

---

## 🧪 Running Tests

Everything is offline and hermetic - network calls are injected, never performed.

```bash
pip install -e ".[dev]" -c constraints.txt   # pinned, reproducible environment (3.11+)
# on Python 3.10 install without -c; websockets 17 in the pin file needs >=3.11
pytest -q                                    # 476 tests, all offline
node --test tests/js/*.test.mjs          # 142 jsdom browser tests (needs: npm ci)
ruff check machinelearningmachine tests scripts examples   # lint
python scripts/e2e_server_check.py --base http://127.0.0.1:8000   # against a running server
```

```
$ pytest -q
475 passed in 32.5s   # the count is asserted by CI; the seconds (and any warnings) are your machine's
```

Coverage by area: protocol/bus bounds, agents and topologies, provider provenance,
retry and failure handling, reply/context length limits, the SSRF policy
(loopback/private/link-local/metadata/redirect matrix) *and* the same policy applied to
provider base URLs, ambient-key isolation, authentication, per-session isolation,
one-run-at-a-time, WebSocket backpressure and frame-loss recovery, the session reaper
and eviction notices, saved-session atomicity and listing, the CLI's bind policy, its
`--live` opt-in and the env layer, packaging, and the dashboard's headers/asset
integrity. `tests/test_docs_are_accurate.py` fails the build when the test count in
this README stops matching reality - the claim is checked, not curated.

`scripts/e2e_server_check.py` is the one check that does not use `TestClient`: it
drives a real uvicorn process over real HTTP and a real WebSocket, with two browser
sessions, and CI runs it against the installed wheel. It is opt-in locally because it
needs a server on `--base`.

> The test count is asserted by CI rather than by a hand-updated badge in this README.
> CI also rebuilds `static/vendor/` and fails if it drifts from the committed manifest.

---

### 🚨 Mock output vs. real output (read this)

| What you see | What it is |
| --- | --- |
| badge `simulated` | Deterministic template text from `MockLLMProvider`. **No model was called; no code was compiled, executed, or tested.** |
| badge `live` | A reply that actually came from OpenAI/Anthropic (or your Ollama/vLLM endpoint). |
| badge `provider failed → simulated` | Your configured provider errored; the simulator answered instead and says so. |

Previously the simulator wrote "production-ready", "✅ APPROVED" and "All assertions
should PASS" about code nobody had run. Those claims are gone, every simulated reply
carries a warning line, exports record the provider mode, and a failing provider now
raises `ProviderError` rather than returning `"[Error calling OpenAI API: HTTP 500]"`
as if it were an answer. Treat anything marked `simulated` as an unreviewed draft.

---

## 📂 Project Structure

```
machinelearningmachine/
├── launch-windows.bat       # One-click launcher (Windows: double-click)
├── launch-macos.command     # One-click launcher (macOS: double-click)
├── launch-linux.sh          # One-click launcher (Linux: ./launch-linux.sh)
├── INSTALL-WINDOWS.md       # Step-by-step install guide for complete beginners (Windows)
├── machinelearningmachine/
│   ├── __init__.py          # Public API surface, wrapped so a missing dep prints install hints
│   ├── __main__.py          # `python -m machinelearningmachine` entry point
│   ├── _deps.py             # Dependency diagnostics; imports nothing third-party
│   ├── env.py               # Env config in one place: MACHINELEARNINGMACHINE_* + MODULE_MESH_* aliases
│   ├── run_control.py       # Task-local cooperative cancellation: check_cancelled / RunCancelled
│   ├── protocol/
│   │   ├── message.py       # InterAgentMessage, MessageType, ContentPayload
│   │   └── bus.py           # MessageBus, routing, topic pub/sub, history
│   ├── agents/
│   │   ├── base.py          # BaseAgent with memory & async send/receive
│   │   ├── arena_ai.py      # Arena AI Lead Orchestrator
│   │   ├── copilot.py       # GitHub Copilot Implementation Specialist
│   │   ├── claude.py        # Claude Fable 5.1 Deep Systems Critic
│   │   ├── gpt.py           # GPT-6 Astra Verification & Test Synthesizer
│   │   ├── custom.py        # Custom user-defined modules
│   │   └── providers.py     # Real LLM drivers (OpenAI, Anthropic, Ollama) & Mock Engine
│   ├── topologies/
│   │   ├── base.py          # BaseTopology orchestrator
│   │   ├── p2p.py           # Peer-to-Peer direct dialogue
│   │   ├── pipeline.py      # Sequential pipeline relay
│   │   ├── debate.py        # Collaborative multi-agent debate
│   │   └── hub_spoke.py     # Supervisor orchestrator
│   ├── netguard.py          # SSRF boundary: URL/IP policy, redirects, byte+type caps,
│   │                        #   and the provider base-URL policy (validate_provider_target)
│   ├── server/
│   │   ├── app.py           # FastAPI REST + WebSocket hub (session-scoped)
│   │   ├── config.py        # ServerConfig + the bind/auth policy (validate_bind_policy)
│   │   ├── state.py         # Bounded per-browser session registry, sweeper, run lock
│   │   ├── feed.py          # Per-connection outbox: bounded, drop-oldest, never stalls a run
│   │   └── static/
│   │       ├── index.html   # Dashboard markup (no CDN tags, no inline script)
│   │       ├── app.js       # WebSocket streaming, Read-Aloud Studio, sessions UI
│   │       ├── presets.js   # Module presets: built-in templates, saved kits (localStorage),
│   │       │                #   JSON import/export - the mirror table lives in tests/
│   │       ├── markdown.js  # The XSS boundary: marked + DOMPurify allowlist (tested in tests/js)
│   │       ├── theme.js     # Light/dark: resolution in <head> (no flash) + the canvas palette
│   │       ├── style.css    # Dark styling plus the hand-written light layer (html:not(.dark))
│   │       └── vendor/      # Pinned Tailwind/FontAwesome/marked/DOMPurify/highlight.js + MANIFEST.json
│   ├── sessions.py          # Session save/load/delete: atomic writes, header index for
│   │                        #   cheap listings, per-client folder, trim is recorded
│   ├── tts.py               # OS text-to-speech for the CLI (say / SAPI / espeak-ng)
│   ├── cli.py               # Command-line interface (--speak, --export, serve)
│   └── mesh.py              # Central AgentMesh API entry point
├── examples/
│   ├── 01_arena_talks_to_copilot.py
│   ├── 02_copilot_to_claude_debate.py
│   └── 03_four_agent_pipeline.py
├── scripts/
│   ├── build_vendor.py      # Regenerate static/vendor/ from pinned npm deps
│   ├── pick_port.py         # First free local port, so a busy 8000 cannot stop a launch
│   ├── bench_sessions.py    # Session-listing benchmark: header read vs full parse
│   └── e2e_server_check.py  # End-to-end pass against a running server (HTTP + WS)
├── tests/
│   ├── test_protocol.py     # message + bus bounds
│   ├── test_deps.py         # missing-dependency hints name package + commands
│   ├── test_agents.py       # agents, memory limits
│   ├── test_topologies.py   # p2p / pipeline / debate / hub
│   ├── test_server.py       # API basics
│   ├── test_sessions.py     # saved sessions + per-client namespacing
│   ├── test_netguard.py     # SSRF policy matrix (48 cases, all offline)
│   ├── test_server_url_reader.py  # page-reader endpoint: gating, errors, byte cap
│   ├── test_provider_url_policy.py   # provider base URLs meet the same policy, per bind
│   ├── test_env_key_isolation.py     # an ambient OPENAI_API_KEY is never used or sent
│   ├── test_env_config.py            # env knobs: canonical names and MODULE_MESH_ aliases
│   ├── test_provider_retry.py        # retry budget, backoff, attempt accounting
│   ├── test_provider_output_bounds.py# long replies and long prompts stay loadable
│   ├── test_run_serialization.py     # one run at a time, 409, no interleaved history
│   ├── test_run_cancellation.py      # Stop at agent boundaries, scoped cancel, 404s
│   ├── test_run_queue.py             # bounded FIFO: 202+position, 429, FIFO order, queued cancel
│   ├── test_queued_run_validation.py # invalid runs fail identically idle or busy
│   ├── test_ws_backpressure.py       # stalled tabs, dropped frames, gap notice, recovery
│   ├── test_session_lifecycle.py     # the reaper, eviction reasons, close 4408
│   ├── test_session_storage.py       # atomic saves, header listings, trimming, pruning
│   ├── test_cli_live.py              # --live opt-in, --no-delay, flag/env precedence
│   ├── test_launchers.py             # one-click launchers: line endings, Python check, free port
│   ├── test_server_auth.py  # bind policy, token gate, WS auth, lockout
│   ├── test_server_isolation.py  # two browsers cannot touch each other's state
│   ├── test_provider_provenance.py  # simulated vs live vs failed-provider labelling
│   ├── test_frontend_security.py    # headers, vendor integrity, no CDN refs
│   ├── test_packaging.py            # every module dir is a real package; package-data globs match
│   ├── test_preset_contract.py      # drift lock: the preset mirror table vs AddAgentRequest
│   ├── test_docs_are_accurate.py      # docs claims that fail the build when stale
│   └── js/
│       ├── sanitize.test.mjs          # 15 XSS/invariant tests through the real sanitizer
│       ├── client-lifecycle.test.mjs  # 19 tests: gap refetch, released session, 409/queue lifecycle, badges, reconnect recovery, stale-record guard
│       ├── presets.test.mjs           # 76 tests: mirror table, localStorage store, chips, save/rename/delete manager, import/export
│       ├── preset-contract.test.mjs   # 6 tests: drift lock — the client's LIMITS + gallery vs the spec's table
│       ├── theme.test.mjs             # 14 tests: resolution, persistence, toggle wiring, canvas palette, and both light-layer drift locks (utilities + hand-written components)
│       └── audit_vendor_deps.test.mjs # 12 tests: the npm-audit wrapper's classification
│                                      #   (CVE vs unreachable endpoint vs vacuous pass)
├── .github/workflows/ci.yml # tests x3 pythons, ruff, wheel contents + serving the wheel,
│                            #   vendor integrity, jsdom, pip-audit, npm audit, secret scan
├── .github/dependabot.yml   # pip + npm + actions
├── constraints.txt           # the pinned reference environment
├── package.json              # pinned versions for the vendored browser assets
├── SECURITY.md               # the security model, in full, including what it does not do
├── LICENSE                   # MIT
├── pyproject.toml
└── requirements.txt
```

---

## 🔐 Security & deployment

- **[SECURITY.md](SECURITY.md)** is the real reference: what the bind/token policy
  enforces, how per-session isolation works, exactly what the SSRF guard checks
  (including the DNS-rebinding window it cannot close), how untrusted text is
  sanitized, and a checklist for running this on a network.
- **Reporting:** use a [private security advisory](https://github.com/StepenkoAnatoli/machinelearningmachine/security/advisories/new), not a public issue.
- This is a single-user local tool. One shared token is a trust boundary, not
  multi-user access control - read SECURITY.md §1 before exposing the port.

---

## 📄 License

MIT - see [LICENSE](LICENSE). Vendored browser assets keep their own licenses under
`machinelearningmachine/server/static/vendor/licenses/`, one per package, copied
verbatim by `scripts/build_vendor.py` (which fails the build if one is missing, since
guessing the filename once silently dropped marked's):

| Vendored | License |
| --- | --- |
| marked | MIT |
| DOMPurify | Apache-2.0 **or** MPL-2.0 (dual-licensed; both texts ship) |
| highlight.js | BSD-3-Clause |
| Tailwind (compiled `tailwind.css`) | MIT |
| FontAwesome icons / fonts / code | CC BY 4.0 / SIL OFL 1.1 / MIT |

`static/vendor/MANIFEST.json` records each package's exact version, byte size and
sha384 hash.

Designed for collaborative multi-agent engineering. No warranty: as the license says,
the software is provided "AS IS" - which is also the honest description of the
simulator's code snippets.

An active dashboard run can be stopped with **Stop**. Cancellation takes effect at
the next agent boundary, keeping the current reply and a cancellation notice in
the transcript. It does not abort or undo an upstream provider request; the run
timeout still applies while waiting. Stop is disabled while idle.
