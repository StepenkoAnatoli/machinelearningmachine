"""
FastAPI Server & WebSocket Hub for MachineLearningMachine Dashboard.
Hosts real-time inter-module communication events, REST endpoints, and UI preview.
User-centered improvements: input validation, rate limiting, security fixes, better errors.
"""

import asyncio
import logging
import re
import time
from pathlib import Path
from typing import Dict, Any, List, Optional
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from pydantic import BaseModel, Field, field_validator

from ..mesh import AgentMesh
from ..protocol.message import Message, MessageType
from ..agents.custom import CustomAgent
from ..agents.providers import MockLLMProvider, OpenAIProvider, AnthropicProvider

logger = logging.getLogger("server")

# Validation constants - user-centered limits to prevent abuse and provide clear feedback
MAX_PROMPT_LENGTH = 5000
MAX_AGENT_ID_LENGTH = 50
MAX_NAME_LENGTH = 100
MAX_ROLE_LENGTH = 200
MAX_SYSTEM_PROMPT_LENGTH = 2000
AGENT_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-_]{1,48}[a-z0-9]$|^[a-z0-9]$")
RESERVED_AGENT_IDS = {"system", "broadcast", "all", "*", "api", "admin", "root"}

# Simple in-memory rate limiting for user protection
_last_run_time: Dict[str, float] = {}
RUN_COOLDOWN_SECONDS = 1.0  # Prevent accidental double-clicks / spam

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"

app = FastAPI(
    title="MachineLearningMachine - Inter-Module Communication Mesh",
    description="Orchestration platform enabling Arena AI, Copilot, Claude, GPT, and custom modules to talk to each other.",
    version="0.1.0",
)

# Enable CORS for all hosts (including preview URLs)
# Note: allow_credentials=False when using wildcard origins - browsers reject
# wildcard + credentials combination. This is intentional for security.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
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


# Request Models with user-centered validation
class RunTaskRequest(BaseModel):
    topology: str = Field(..., description="Communication topology")
    prompt: str = Field(..., min_length=1, max_length=MAX_PROMPT_LENGTH, description="Task prompt")
    from_agent: Optional[str] = Field(default="arena-ai", max_length=MAX_AGENT_ID_LENGTH)
    to_agent: Optional[str] = Field(default="copilot", max_length=MAX_AGENT_ID_LENGTH)
    agent_ids: Optional[List[str]] = Field(default=None, max_length=10)
    turns: Optional[int] = Field(default=4, ge=1, le=10)

    @field_validator("topology")
    @classmethod
    def validate_topology(cls, v: str) -> str:
        allowed = {"p2p", "pipeline", "debate", "hub"}
        if v not in allowed:
            raise ValueError(f"Topology must be one of {allowed}")
        return v

    @field_validator("prompt")
    @classmethod
    def validate_prompt(cls, v: str) -> str:
        stripped = v.strip()
        if not stripped:
            raise ValueError("Prompt cannot be empty")
        if len(stripped) < 5:
            raise ValueError("Prompt too short - please describe your task more fully")
        return stripped

    @field_validator("agent_ids")
    @classmethod
    def validate_agent_ids(cls, v: Optional[List[str]]) -> Optional[List[str]]:
        if v is None:
            return v
        if len(v) > 10:
            raise ValueError("Too many agent IDs (max 10)")
        for aid in v:
            if not aid or len(aid) > MAX_AGENT_ID_LENGTH:
                raise ValueError(f"Invalid agent ID: {aid}")
        return v


class AddAgentRequest(BaseModel):
    agent_id: str = Field(..., min_length=2, max_length=MAX_AGENT_ID_LENGTH)
    name: str = Field(..., min_length=1, max_length=MAX_NAME_LENGTH)
    role: str = Field(..., min_length=1, max_length=MAX_ROLE_LENGTH)
    system_prompt: str = Field(..., min_length=10, max_length=MAX_SYSTEM_PROMPT_LENGTH)
    color: Optional[str] = Field(default="#3b82f6", pattern=r"^#[0-9a-fA-F]{6}$")
    avatar: Optional[str] = Field(default="🤖", max_length=10)

    @field_validator("agent_id")
    @classmethod
    def validate_agent_id(cls, v: str) -> str:
        v = v.strip().lower()
        if v in RESERVED_AGENT_IDS:
            raise ValueError(f"Agent ID '{v}' is reserved")
        if not AGENT_ID_PATTERN.match(v):
            raise ValueError(
                "Agent ID must be lowercase alphanumeric with dashes/underscores, "
                "e.g. 'my-agent' or 'security_auditor', 2-50 chars"
            )
        return v

    @field_validator("name", "role", "system_prompt")
    @classmethod
    def validate_no_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("Field cannot be empty or whitespace only")
        return v.strip()


class ConfigApiKeysRequest(BaseModel):
    openai_api_key: Optional[str] = Field(default=None, max_length=500)
    anthropic_api_key: Optional[str] = Field(default=None, max_length=500)
    openai_base_url: Optional[str] = Field(default=None, max_length=500)

    @field_validator("openai_base_url")
    @classmethod
    def validate_url(cls, v: Optional[str]) -> Optional[str]:
        if v is None or not v.strip():
            return None
        v = v.strip()
        if not (v.startswith("http://") or v.startswith("https://")):
            raise ValueError("Base URL must start with http:// or https://")
        return v.rstrip("/")


@app.get("/api/agents")
async def get_agents():
    """List all registered modules."""
    return mesh.list_agents()


@app.post("/api/agents")
async def add_agent(req: AddAgentRequest):
    """Dynamically register a new custom module with validation."""
    if req.agent_id in mesh.agents:
        raise HTTPException(
            status_code=400,
            detail=f"Agent ID '{req.agent_id}' already exists. Choose a different ID."
        )

    # Prevent too many custom agents (resource protection)
    if len(mesh.agents) >= 20:
        raise HTTPException(
            status_code=400,
            detail="Too many agents registered (max 20). Remove some or clear session."
        )

    try:
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
        logger.info(f"Custom agent registered: {req.agent_id}")
        return {"status": "success", "agent": agent.to_dict()}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Failed to add agent {req.agent_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to register agent. Please try again.")


@app.get("/api/history")
async def get_history(limit: Optional[int] = None):
    """Get past messages in the session, optionally limited."""
    history = mesh.get_history()
    if limit is not None:
        limit = max(1, min(limit, 500))
        history = history[-limit:]
    return [m.to_dict() for m in history]


@app.post("/api/clear")
async def clear_session():
    """Clear message history and reset module memory."""
    mesh.clear_history()
    await broadcast_ws({"type": "history_cleared"})
    return {"status": "cleared"}


@app.get("/api/export/markdown")
async def export_markdown():
    """Export transcript as markdown with proper content type."""
    markdown = mesh.export_markdown()
    if not markdown.strip() or markdown.strip() == "# Multi-Agent Dialogue Transcript":
        return {"markdown": "# No messages yet\n\nStart a dialogue to generate a transcript."}
    return {"markdown": markdown}


@app.get("/api/export/json")
async def export_json():
    """Export transcript as JSON."""
    json_str = mesh.export_json()
    # Return parsed validation
    try:
        import json
        parsed = json.loads(json_str)
        if not parsed:
            return {"json": "[]"}
    except Exception:
        pass
    return {"json": json_str}


@app.get("/api/export/markdown/download")
async def export_markdown_download():
    """Download markdown as file with proper headers."""
    markdown = mesh.export_markdown()
    return PlainTextResponse(
        content=markdown,
        media_type="text/markdown",
        headers={"Content-Disposition": "attachment; filename=module_mesh_transcript.md"}
    )


@app.get("/api/export/json/download")
async def export_json_download():
    """Download JSON as file with proper headers."""
    json_str = mesh.export_json()
    return PlainTextResponse(
        content=json_str,
        media_type="application/json",
        headers={"Content-Disposition": "attachment; filename=module_mesh_transcript.json"}
    )


@app.post("/api/config")
async def update_config(req: ConfigApiKeysRequest):
    """Configure API keys for real LLM models. Keys are kept in-memory only."""
    configured = []
    try:
        if req.openai_api_key or req.openai_base_url:
            # Validate key format loosely - don't log it
            if req.openai_api_key and not req.openai_api_key.startswith(("sk-", "ollama", "lm-")) and len(req.openai_api_key) < 8:
                if "localhost" not in (req.openai_base_url or "") and "127.0.0.1" not in (req.openai_base_url or ""):
                    raise HTTPException(status_code=400, detail="OpenAI API key looks invalid")

            provider = OpenAIProvider(api_key=req.openai_api_key, base_url=req.openai_base_url)
            if mesh.gpt:
                mesh.gpt.provider = provider
            if mesh.copilot:
                mesh.copilot.provider = provider
            configured.append("OpenAI")

        if req.anthropic_api_key:
            if not req.anthropic_api_key.startswith("sk-ant-") and len(req.anthropic_api_key) < 10:
                raise HTTPException(status_code=400, detail="Anthropic API key looks invalid")

            provider = AnthropicProvider(api_key=req.anthropic_api_key)
            if mesh.claude:
                mesh.claude.provider = provider
            if mesh.arena_ai:
                mesh.arena_ai.provider = provider
            configured.append("Anthropic")

        if not configured:
            return {"status": "success", "message": "No keys provided - using built-in simulator"}

        return {"status": "success", "message": f"Configured: {', '.join(configured)}. Keys kept in-memory only."}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Config error: {e}")
        raise HTTPException(status_code=500, detail="Failed to configure providers")


@app.post("/api/run")
async def run_dialogue(req: RunTaskRequest, request: Request):
    """Execute a multi-agent dialogue based on chosen topology with rate limiting and validation."""
    # Simple rate limiting by IP to prevent spam / accidental double-clicks
    client_id = request.client.host if request.client else "unknown"
    now = time.time()
    last = _last_run_time.get(client_id, 0)
    if now - last < RUN_COOLDOWN_SECONDS:
        raise HTTPException(
            status_code=429,
            detail=f"Please wait {RUN_COOLDOWN_SECONDS:.0f}s between runs"
        )
    _last_run_time[client_id] = now

    # Validate agent existence early for better error messages
    if req.topology == "p2p":
        if req.from_agent not in mesh.agents:
            raise HTTPException(status_code=400, detail=f"Initiating agent '{req.from_agent}' not found")
        if req.to_agent not in mesh.agents:
            raise HTTPException(status_code=400, detail=f"Responding agent '{req.to_agent}' not found")
        if req.from_agent == req.to_agent:
            raise HTTPException(status_code=400, detail="Cannot start dialogue with same agent as both sides")
    elif req.agent_ids:
        missing = [aid for aid in req.agent_ids if aid not in mesh.agents]
        if missing:
            raise HTTPException(status_code=400, detail=f"Agents not found: {', '.join(missing)}")

    await broadcast_ws({
        "type": "run_started",
        "topology": req.topology,
        "prompt": req.prompt[:200],  # Don't broadcast full prompt for privacy
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

    except HTTPException:
        raise
    except ValueError as e:
        # User errors - 400
        await broadcast_ws({"type": "run_error", "error": str(e)})
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Dialogue execution failed: {e}", exc_info=True)
        await broadcast_ws({"type": "run_error", "error": "Internal error during dialogue execution"})
        raise HTTPException(status_code=500, detail="Failed to execute dialogue. Please try again.")


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
            "limits": {
                "max_prompt_length": MAX_PROMPT_LENGTH,
                "max_history": mesh.bus._max_history,
            }
        })
        while True:
            data = await websocket.receive_text()
            # Heartbeat / ping handling
            if data == "ping":
                await websocket.send_text("pong")
    except WebSocketDisconnect:
        if websocket in active_connections:
            active_connections.remove(websocket)
    except Exception as e:
        logger.warning(f"WebSocket error: {e}")
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
    return {
        "status": "ok",
        "agents": len(mesh.agents),
        "messages": len(mesh.get_history()),
        "connections": len(active_connections),
        "version": "0.1.0"
    }
