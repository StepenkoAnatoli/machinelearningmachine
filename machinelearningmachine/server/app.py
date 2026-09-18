"""
FastAPI Server & WebSocket Hub for MachineLearningMachine Dashboard.
Hosts real-time inter-module communication events, REST endpoints, and UI preview.
"""

import asyncio
import os
from pathlib import Path
from typing import Dict, Any, List, Optional
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

from ..mesh import AgentMesh
from ..protocol.message import Message, MessageType
from ..agents.custom import CustomAgent
from ..agents.providers import MockLLMProvider, OpenAIProvider, AnthropicProvider

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"

app = FastAPI(
    title="MachineLearningMachine - Inter-Module Communication Mesh",
    description="Orchestration platform enabling Arena AI, Copilot, Claude, GPT, and custom modules to talk to each other.",
    version="0.1.0",
)

# Enable CORS for all hosts (including preview URLs)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Global AgentMesh instance
mesh = AgentMesh()

# Active WebSocket connections
active_connections: List[WebSocket] = []


async def broadcast_ws(payload: Dict[str, Any]):
    disconnected = []
    for ws in active_connections:
        try:
            await ws.send_json(payload)
        except Exception:
            disconnected.append(ws)
    for ws in disconnected:
        if ws in active_connections:
            active_connections.remove(ws)


async def on_bus_message(msg: Message):
    """Broadcast new bus message to connected WebSockets."""
    await broadcast_ws({
        "type": "new_message",
        "message": msg.to_dict(),
    })


# Register global bus hook
mesh.on_message(on_bus_message)


# Request Models
class RunTaskRequest(BaseModel):
    topology: str  # p2p, pipeline, debate, hub
    prompt: str
    from_agent: Optional[str] = "arena-ai"
    to_agent: Optional[str] = "copilot"
    agent_ids: Optional[List[str]] = None
    turns: Optional[int] = 4


class AddAgentRequest(BaseModel):
    agent_id: str
    name: str
    role: str
    system_prompt: str
    color: Optional[str] = "#3b82f6"
    avatar: Optional[str] = "🤖"


class ConfigApiKeysRequest(BaseModel):
    openai_api_key: Optional[str] = None
    anthropic_api_key: Optional[str] = None
    openai_base_url: Optional[str] = None


@app.get("/api/agents")
async def get_agents():
    """List all registered modules."""
    return mesh.list_agents()


@app.post("/api/agents")
async def add_agent(req: AddAgentRequest):
    """Dynamically register a new custom module."""
    if req.agent_id in mesh.agents:
        raise HTTPException(status_code=400, detail="Agent ID already exists")

    agent = CustomAgent(
        agent_id=req.agent_id,
        name=req.name,
        role=req.role,
        system_prompt=req.system_prompt,
        color=req.color or "#ec4899",
        avatar=req.avatar or "🧩",
        bus=mesh.bus,
    )
    mesh.register_agent(agent)
    await broadcast_ws({"type": "agents_updated", "agents": mesh.list_agents()})
    return {"status": "success", "agent": agent.to_dict()}


@app.get("/api/history")
async def get_history():
    """Get all past messages in the session."""
    return [m.to_dict() for m in mesh.get_history()]


@app.post("/api/clear")
async def clear_session():
    """Clear message history and reset module memory."""
    mesh.clear_history()
    await broadcast_ws({"type": "history_cleared"})
    return {"status": "cleared"}


@app.get("/api/export/markdown")
async def export_markdown():
    return {"markdown": mesh.export_markdown()}


@app.get("/api/export/json")
async def export_json():
    return {"json": mesh.export_json()}


@app.post("/api/config")
async def update_config(req: ConfigApiKeysRequest):
    """Configure API keys for real LLM models."""
    if req.openai_api_key or req.openai_base_url:
        provider = OpenAIProvider(api_key=req.openai_api_key, base_url=req.openai_base_url)
        if mesh.gpt:
            mesh.gpt.provider = provider
        if mesh.copilot:
            mesh.copilot.provider = provider

    if req.anthropic_api_key:
        provider = AnthropicProvider(api_key=req.anthropic_api_key)
        if mesh.claude:
            mesh.claude.provider = provider
        if mesh.arena_ai:
            mesh.arena_ai.provider = provider

    return {"status": "success", "message": "API keys configured"}


@app.post("/api/run")
async def run_dialogue(req: RunTaskRequest):
    """Execute a multi-agent dialogue based on chosen topology."""
    await broadcast_ws({
        "type": "run_started",
        "topology": req.topology,
        "prompt": req.prompt,
    })

    try:
        if req.topology == "p2p":
            transcript = await mesh.talk_p2p(
                from_agent_id=req.from_agent or "arena-ai",
                to_agent_id=req.to_agent or "copilot",
                prompt=req.prompt,
                turns=req.turns or 4,
            )
        elif req.topology == "pipeline":
            transcript = await mesh.run_pipeline(
                prompt=req.prompt,
                agent_ids=req.agent_ids,
            )
        elif req.topology == "debate":
            transcript = await mesh.run_debate(
                prompt=req.prompt,
                agent_ids=req.agent_ids,
            )
        elif req.topology == "hub":
            transcript = await mesh.run_hub_and_spoke(
                prompt=req.prompt,
                hub_id=req.from_agent or "arena-ai",
                spoke_ids=req.agent_ids,
            )
        else:
            raise HTTPException(status_code=400, detail=f"Unknown topology '{req.topology}'")

        await broadcast_ws({"type": "run_completed"})
        return {"status": "completed", "messages": [m.to_dict() for m in transcript]}

    except Exception as e:
        await broadcast_ws({"type": "run_error", "error": str(e)})
        raise HTTPException(status_code=500, detail=str(e))


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    active_connections.append(websocket)
    try:
        # Send initial status snapshot
        await websocket.send_json({
            "type": "init",
            "agents": mesh.list_agents(),
            "history": [m.to_dict() for m in mesh.get_history()],
        })
        while True:
            data = await websocket.receive_text()
            # Heartbeat / ping handling
            if data == "ping":
                await websocket.send_text("pong")
    except WebSocketDisconnect:
        if websocket in active_connections:
            active_connections.remove(websocket)


# Mount static files and index
STATIC_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.api_route("/", methods=["GET", "HEAD"])
async def serve_index():
    index_file = STATIC_DIR / "index.html"
    if index_file.exists():
        return FileResponse(str(index_file))
    return JSONResponse({"status": "healthy", "service": "MachineLearningMachine Agent Mesh API"})


@app.api_route("/health", methods=["GET", "HEAD"])
async def health():
    return {"status": "ok"}

