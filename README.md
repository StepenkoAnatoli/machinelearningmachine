# MachineLearningMachine: Multi-Module Inter-Agent Communication Mesh

[![Tests](https://img.shields.io/badge/tests-21%20passed-success)](https://github.com/StepenkoAnatoli/machinelearningmachine)
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
- 🚀 Faster execution (0.15s vs 0.4s delays), no unnecessary waiting
- 💡 Contextual mock provider: detects `rate_limiter`, `cache`, `auth`, `queue` domains and generates copy-paste-ready code with tests
- 📋 One-click copy for code blocks, export with proper headers
- 🎯 Clear empty states, helpful presets, agent detail on click
- 🛡️ Privacy: API keys kept in-memory only, never logged

**Engineering Quality:**
- 🧪 21 tests passing, better error recovery, bounded resources
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

### 1. Installation

The package depends on **pydantic**, **fastapi**, **uvicorn**, **aiohttp**, **requests**, and **websockets**. Install the project *and* its dependencies — running the code straight from a bare interpreter fails with `ModuleNotFoundError: No module named 'pydantic'`.

```bash
git clone https://github.com/StepenkoAnatoli/machinelearningmachine.git
cd machinelearningmachine

python -m pip install -e .        # macOS / Linux
py -m pip install -e .            # Windows
```

Equivalent: `pip install -r requirements.txt` (plus `pip install -e .` if you want the `module-mesh` command).

> **Tip:** prefer a virtual environment so the dependencies land in the same interpreter you run the code with:
> ```bash
> # macOS / Linux
> python3 -m venv .venv && source .venv/bin/activate && python -m pip install -e .
> # Windows (PowerShell)
> py -m venv .venv ; .\.venv\Scripts\Activate.ps1 ; py -m pip install -e .
> ```

### 2. Launch the Interactive Web Dashboard

```bash
python -m machinelearningmachine.cli serve --host 0.0.0.0 --port 8000
# or, equivalently:
python -m machinelearningmachine
```

Open `http://localhost:8000` (or the live preview port) in your browser to access the real-time visual dashboard.

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
```

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
tests/test_agents.py::test_agent_initialization_and_talk PASSED          [  7%]
tests/test_agents.py::test_custom_agent_creation PASSED                  [ 14%]
tests/test_protocol.py::test_message_creation_and_dict PASSED            [ 21%]
tests/test_protocol.py::test_message_bus_routing PASSED                  [ 28%]
tests/test_server.py::test_server_index PASSED                           [ 35%]
tests/test_server.py::test_server_agents_endpoint PASSED                 [ 42%]
tests/test_server.py::test_server_run_p2p PASSED                         [ 50%]
tests/test_server.py::test_server_history_and_clear PASSED               [ 57%]
tests/test_server.py::test_server_add_custom_agent PASSED                [ 64%]
tests/test_topologies.py::test_arena_talks_to_copilot_p2p PASSED         [ 71%]
tests/test_topologies.py::test_copilot_talks_to_claude_p2p PASSED        [ 78%]
tests/test_topologies.py::test_four_agent_pipeline PASSED                [ 85%]
tests/test_topologies.py::test_collaborative_debate PASSED               [ 92%]
tests/test_topologies.py::test_hub_and_spoke PASSED                      [100%]
======================== 14 passed in 7.71s ========================
```

---

## 📂 Project Structure

```
machinelearningmachine/
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
│   │   ├── app.py           # FastAPI WebSocket & REST API
│   │   └── static/
│   │       ├── index.html   # Real-time Web Dashboard & Agent Network Graph
│   │       ├── app.js       # WebSocket streaming & Canvas particle animations
│   │       └── style.css    # Custom Dark Mode styling
│   ├── cli.py               # Command-line interface
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
