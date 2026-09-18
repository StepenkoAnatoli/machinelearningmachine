# MachineLearningMachine: Multi-Module Inter-Agent Communication Mesh

[![Tests](https://img.shields.io/badge/tests-30%20passed-success)](https://github.com/StepenkoAnatoli/machinelearningmachine)
[![Python](https://img.shields.io/badge/python-3.10%20%7C%203.11%20%7C%203.12-blue)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.110+-teal)](https://fastapi.tiangolo.com)
[![WebSocket](https://img.shields.io/badge/WebSocket-Real--Time-orange)](https://websockets.readthedocs.io/)
[![User-Centered](https://img.shields.io/badge/design-user--centered-purple)](https://github.com/StepenkoAnatoli/machinelearningmachine)

A modular orchestration system that enables AI modules to talk directly to each other — connecting **Arena AI**, **GitHub Copilot**, **Claude**, **GPT-4o**, and custom user-defined modules across standardized inter-agent communication topologies.

> **User-Centered Engineering:** This project prioritizes real user value over mere technical correctness. Every feature is designed to be intuitive, accessible, secure, and genuinely useful — with thoughtful error handling, clear feedback, and practical defaults that work out of the box.

## ✨ User-Centered Design Highlights

**Security & Reliability First:**
- ✅ Fixed CORS misconfiguration (`*` + credentials → secure defaults)
- ✅ XSS protection via safe markdown parsing
- ✅ Input validation with helpful error messages (not stack traces)
- ✅ Bounded message history (1000 msgs) & agent memory (100 msgs) to prevent leaks
- ✅ Rate limiting & resource limits to prevent abuse

**Intuitive & Accessible UX:**
- 🎨 Toast notifications instead of jarring `alert()`/`confirm()`
- ♿ Full keyboard navigation, focus traps in modals, ARIA labels, skip links
- ⌨️ Shortcuts: `Ctrl+Enter` to run, `Esc` to close modals
- 🔍 Search/filter messages, character count, auto-resizing prompt
- 📱 Responsive, respects `prefers-reduced-motion`, optimized canvas (30fps, pauses when hidden)

**Practical & Pleasant:**
- 🚀 **One-click install & launch** — double-click `launch-windows.bat` / `launch-macos.command` / `launch-linux.sh` and the app sets itself up (Python check → venv → dependencies → dashboard → browser) and runs
- 💾 **Saved sessions** — store any conversation (modules + full transcript) on your computer and reload it later from the *Sessions* panel
- 🔊 **Reads what you write** — your prompt, every agent reply, any message, any text file, any web page, or the whole conversation is read aloud with your computer's own voices (no API keys, works offline); plus 🎙️ voice dictation for your prompt
- 🚀 Faster execution (0.15s vs 0.4s delays), no unnecessary waiting
- 💡 Contextual mock provider: detects `rate_limiter`, `cache`, `auth`, `queue` domains and generates copy-paste-ready code with tests
- 📋 One-click copy for code blocks, export with proper headers
- 🎯 Clear empty states, helpful presets, agent detail on click
- 🛡️ Privacy: API keys kept in-memory only, never logged

**Engineering Quality:**
- 🧪 30 tests passing, better error recovery, bounded resources
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
  - Works out of the box with realistic domain simulations (no API keys required).
  - One-click configuration for real **OpenAI (GPT-4o)**, **Anthropic (Claude 3.5)**, or **local Ollama / LMStudio / vLLM** backends.

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
        │     Arena AI      │   │GitHub Copilot│   │    Claude 3.5     │
        │(Lead Orchestrator)│   │(Code Synth)  │   │(Systems Architect)│
        └─────────┬─────────┘   └───────┬──────┘   └───┬───────────────┘
                  │                     │              │
                  └───────────────┬─────┴──────────────┘
                                  │
                        ┌─────────┴─────────┐
                        │      GPT-4o       │
                        │(Test Synthesizer) │
                        └───────────────────┘
```

---

## 🚀 Quick Start

### 1. One-Click Launch (recommended)

Clone the repository, then **double-click the launcher for your computer**:

| Your OS    | File to double-click      |
|------------|---------------------------|
| Windows    | `launch-windows.bat`      |
| macOS      | `launch-macos.command`    |
| Linux      | `launch-linux.sh` (run: `./launch-linux.sh`) |

That single file does everything for you:

1. 🔍 Finds your Python (install [python.org](https://www.python.org/downloads/) first if you don't have it)
2. 📦 Creates a private `.venv` environment (first run only)
3. ⬇️ Installs the dependencies once — a few minutes the first time, instant afterwards
4. 🚀 Starts the dashboard at `http://127.0.0.1:8000`
5. 🌐 Opens your browser automatically

Close the terminal window (or press `Ctrl+C`) to stop the app.

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
python -m machinelearningmachine.cli serve --host 0.0.0.0 --port 8000
# or, equivalently:
python -m machinelearningmachine
```

Open `http://localhost:8000` in your browser to access the real-time visual dashboard.

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

Run the full test suite with `pytest`:

```bash
pytest -v
```

Output:
```
tests/test_agents.py::test_agent_initialization_and_talk PASSED
tests/test_agents.py::test_custom_agent_creation PASSED
tests/test_deps.py::test_install_hint_names_package_and_commands PASSED
tests/test_deps.py::test_install_hint_uses_import_root_for_submodules PASSED
tests/test_deps.py::test_install_hint_deduplicates_and_sorts_packages PASSED
tests/test_deps.py::test_missing_detects_absent_module PASSED
tests/test_deps.py::test_known_dependency_classification PASSED
tests/test_deps.py::test_require_raises_importerror_for_missing_dependency PASSED
tests/test_deps.py::test_require_passes_when_dependencies_present PASSED
tests/test_protocol.py::test_message_creation_and_dict PASSED
tests/test_protocol.py::test_message_bus_routing PASSED
tests/test_server.py::test_server_index PASSED
tests/test_server.py::test_server_agents_endpoint PASSED
tests/test_server.py::test_server_run_p2p PASSED
tests/test_server.py::test_server_history_and_clear PASSED
tests/test_server.py::test_server_add_custom_agent PASSED
tests/test_sessions.py::test_store_roundtrip PASSED
tests/test_sessions.py::test_store_name_sanitization PASSED
tests/test_sessions.py::test_store_rejects_path_traversal PASSED
tests/test_sessions.py::test_store_pruning_keeps_max_sessions PASSED
tests/test_sessions.py::test_server_sessions_roundtrip PASSED
tests/test_sessions.py::test_save_session_requires_history PASSED
tests/test_sessions.py::test_read_url_strips_html PASSED
tests/test_sessions.py::test_read_url_endpoint_validates PASSED
tests/test_sessions.py::test_tts_engine_reporting PASSED
tests/test_topologies.py::test_arena_talks_to_copilot_p2p PASSED
tests/test_topologies.py::test_copilot_talks_to_claude_p2p PASSED
tests/test_topologies.py::test_four_agent_pipeline PASSED
tests/test_topologies.py::test_collaborative_debate PASSED
tests/test_topologies.py::test_hub_and_spoke PASSED
======================== 30 passed ========================
```

---

## 📂 Project Structure

```
machinelearningmachine/
├── launch-windows.bat       # One-click launcher (Windows: double-click)
├── launch-macos.command     # One-click launcher (macOS: double-click)
├── launch-linux.sh          # One-click launcher (Linux: ./launch-linux.sh)
├── machinelearningmachine/
│   ├── protocol/
│   │   ├── message.py       # InterAgentMessage, MessageType, ContentPayload
│   │   └── bus.py           # MessageBus, routing, topic pub/sub, history
│   ├── agents/
│   │   ├── base.py          # BaseAgent with memory & async send/receive
│   │   ├── arena_ai.py      # Arena AI Lead Orchestrator
│   │   ├── copilot.py       # GitHub Copilot Implementation Specialist
│   │   ├── claude.py        # Claude 3.5 Deep Systems Critic
│   │   ├── gpt.py           # GPT-4o Verification & Test Synthesizer
│   │   ├── custom.py        # Custom user-defined modules
│   │   └── providers.py     # Real LLM drivers (OpenAI, Anthropic, Ollama) & Mock Engine
│   ├── topologies/
│   │   ├── base.py          # BaseTopology orchestrator
│   │   ├── p2p.py           # Peer-to-Peer direct dialogue
│   │   ├── pipeline.py      # Sequential pipeline relay
│   │   ├── debate.py        # Collaborative multi-agent debate
│   │   └── hub_spoke.py     # Supervisor orchestrator
│   ├── server/
│   │   ├── app.py           # FastAPI WebSocket & REST API (sessions, page reader)
│   │   └── static/
│   │       ├── index.html   # Real-time Web Dashboard & Agent Network Graph
│   │       ├── app.js       # WebSocket streaming, Read-Aloud Studio, sessions UI
│   │       └── style.css    # Custom Dark Mode styling
│   ├── sessions.py          # Session save/load/delete (JSON files in ~/.module_mesh)
│   ├── tts.py               # OS text-to-speech for the CLI (say / SAPI / espeak-ng)
│   ├── cli.py               # Command-line interface (--speak, --export, serve)
│   └── mesh.py              # Central AgentMesh API entry point
├── examples/
│   ├── 01_arena_talks_to_copilot.py
│   ├── 02_copilot_to_claude_debate.py
│   └── 03_four_agent_pipeline.py
├── tests/
│   ├── test_protocol.py
│   ├── test_agents.py
│   ├── test_topologies.py
│   └── test_server.py
├── pyproject.toml
└── requirements.txt
```

---

## 📄 License

MIT License. Designed for collaborative multi-agent autonomous engineering.
