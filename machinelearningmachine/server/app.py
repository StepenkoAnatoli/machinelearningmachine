"""
FastAPI app & WebSocket hub for the MachineLearningMachine dashboard.

Security model (read this before exposing the port to a network)
----------------------------------------------------------------
* Binds to ``127.0.0.1`` by default. Any other interface needs
  ``--allow-public`` **and** ``--auth-token`` (see
  :func:`machinelearningmachine.server.config.validate_bind_policy`).
* State is per browser session, not global: each session owns its own
  :class:`~machinelearningmachine.mesh.AgentMesh`, provider configuration,
  rate-limit bucket and WebSocket set (``server/state.py``).
* ``/api/read/url`` is disabled unless ``--enable-url-reader`` is passed, and
  even then every hop is validated by :mod:`machinelearningmachine.netguard`.
* Browser assets are vendored under ``/static/vendor`` so the app can run with
  ``Content-Security-Policy: default-src 'self'`` and no third-party origin.
* API keys are kept in memory per session, never persisted and never logged.
  They are format-checked only - a key is not verified against the provider
  unless the caller asks for it explicitly.

The module-level ``app`` keeps ``uvicorn machinelearningmachine.server.app:app``
working; ``create_app(config)`` is what the CLI uses.
"""

from __future__ import annotations

import asyncio
import contextlib
import html
import logging
import re
import secrets
import time
from pathlib import Path
from typing import Any, AsyncIterator, Dict, List, Optional, Tuple
from urllib.parse import urlparse

from fastapi import Depends, FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator

from .. import netguard
from .. import sessions as session_store
from ..agents.custom import CustomAgent
from ..agents.providers import AnthropicProvider, OpenAIProvider, ProviderError
from ..mesh import AgentMesh
from ..protocol.bus import MessageBus
from ..protocol.message import Message, MessageType
from ..run_control import RunCancelled, cancel_event
from .config import (
    AUTH_TOKEN_ENV_VAR,
    CLIENT_COOKIE_NAME,
    SESSION_COOKIE_NAME,
    ServerConfig,
)
from .feed import ClientFeed
from .state import LoginThrottle, QueuedRun, SessionRegistry, SessionState

logger = logging.getLogger("server")

# Validation constants - user-centered limits to prevent abuse and provide clear feedback.
# The two the mesh also enforces are *read from it*, not restated: the request
# models below reject a prompt the mesh would have accepted (and /api/status
# advertises these very numbers to the browser), so two spellings of one policy
# is how the API starts refusing what the dashboard promised was fine.
MAX_PROMPT_LENGTH = AgentMesh.MAX_PROMPT_LENGTH
MAX_AGENT_ID_LENGTH = 50
MAX_NAME_LENGTH = 100
MAX_ROLE_LENGTH = 200
MAX_SYSTEM_PROMPT_LENGTH = 2000
AGENT_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-_]{1,48}[a-z0-9]$|^[a-z0-9]$")
RESERVED_AGENT_IDS = {"system", "broadcast", "all", "*", "api", "admin", "root"}
MAX_AGENTS_PER_SESSION = AgentMesh.MAX_AGENTS  # same limit as the mesh, one source
MAX_CUSTOM_AGENTS_PER_SESSION = 16
RUN_COOLDOWN_SECONDS = 1.0  # Prevent accidental double-clicks / spam
URL_READ_COOLDOWN_SECONDS = 2.0
#: Longest a WebSocket client may make the server hold a text frame before the
#: connection is closed. The handler understands one frame ("ping"); anything
#: bigger is either a bug or an attempt to spend the server's memory.
MAX_WS_INBOUND_CHARS = 4096
#: Wrong tokens an attacker may try before this client is locked out, and for how long.
LOGIN_MAX_FAILURES = 5
LOGIN_LOCKOUT_SECONDS = 60.0
#: How many messages the browser keeps and renders - the same bound the bus
#: applies server-side, so a long-lived tab cannot grow without limit.
CLIENT_HISTORY_LIMIT = MessageBus.MAX_HISTORY

#: Requests that may skip authentication. Everything else 401s when a token is set.
PUBLIC_PATHS = {"/", "/health", "/favicon.ico"}
PUBLIC_PREFIXES = ("/static/", "/api/auth/")

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"

#: Fire-and-forget tasks that must be kept referenced until they finish (closing a
#: socket is best-effort, but an unreferenced task can be garbage-collected first).
#: ``Any`` result type on purpose: this one set holds both the queued-run tasks
#: (which return nothing) and the socket-closing tasks (which return a count), and
#: only their identity matters here - it is a lifetime anchor, not a result.
_DETACHED_TASKS: "set[asyncio.Task[Any]]" = set()


# ---------------------------------------------------------------------------
# Request models with user-centred validation
# ---------------------------------------------------------------------------

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
    avatar: Optional[str] = Field(default="🤖", max_length=8)

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
    #: Ask for a live credentials check instead of trusting the shape of the key.
    verify: bool = False
    clear: bool = False

    @field_validator("openai_base_url")
    @classmethod
    def validate_url(cls, v: Optional[str]) -> Optional[str]:
        if v is None or not v.strip():
            return None
        v = v.strip()
        if not (v.startswith("http://") or v.startswith("https://")):
            raise ValueError("Base URL must start with http:// or https://")
        parsed = urlparse(v)
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            raise ValueError("Base URL must be a valid http(s) URL with a host")
        if parsed.username or parsed.password:
            raise ValueError("Base URL must not embed credentials")
        return v.rstrip("/")


class LoginRequest(BaseModel):
    token: str = Field(..., min_length=1, max_length=512)


class SaveSessionRequest(BaseModel):
    name: Optional[str] = Field(default=None, max_length=120)


class LoadSessionRequest(BaseModel):
    session_id: str = Field(..., min_length=4, max_length=64)


class ReadUrlRequest(BaseModel):
    url: str = Field(..., min_length=3, max_length=2000)

    @field_validator("url")
    @classmethod
    def validate_url(cls, v: str) -> str:
        v = v.strip()
        if not (v.startswith("http://") or v.startswith("https://")):
            raise ValueError("URL must start with http:// or https://")
        return v


# ---------------------------------------------------------------------------
# HTML -> readable text (pure functions, no I/O, unit-testable)
# ---------------------------------------------------------------------------

MAX_READ_CHARS = 60_000
#: Cap on how much of a page we buffer; enforced again inside netguard.
MAX_READ_BYTES = netguard.MAX_FETCH_BYTES


def _html_to_text(raw_html: str) -> str:
    """Reduce an HTML document to readable plain text (no external deps)."""
    html_text = raw_html
    # Drop non-content blocks entirely.
    html_text = re.sub(r"(?is)<(script|style|noscript|svg|head|template)[^>]*>.*?</\1>", " ", html_text)
    html_text = re.sub(r"(?is)<(nav|footer|form|iframe|button|select|dialog)[^>]*>.*?</\1>", " ", html_text)
    # Line breaks for block-level tags.
    html_text = re.sub(r"(?i)<br\s*/?>", "\n", html_text)
    html_text = re.sub(r"(?i)</(p|div|h[1-6]|li|tr|section|article|pre|blockquote|table)>", "\n", html_text)
    html_text = re.sub(r"(?i)<li[^>]*>", "- ", html_text)
    # Remove every remaining tag.
    text = re.sub(r"(?s)<[^>]+>", " ", html_text)
    text = html.unescape(text)
    # Tidy whitespace: one space per line, drop blank runs.
    lines = [re.sub(r"[ \t\u00a0]+", " ", ln).strip() for ln in text.splitlines()]
    text = "\n".join(ln for ln in lines if ln)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _extract_title(raw_html: str) -> str:
    m = re.search(r"(?is)<title[^>]*>(.*?)</title>", raw_html)
    if m:
        title = html.unescape(re.sub(r"(?s)<[^>]+>", " ", m.group(1)))
        return re.sub(r"\s+", " ", title).strip()[:200]
    return ""


def page_text_from_body(body: str, content_type: str) -> Dict[str, Any]:
    """Turn a fetched body into {title, text, truncated} for the reader."""
    title = ""
    if "json" in content_type:
        text = body
    elif "html" in content_type or body.lstrip()[:1] == "<":
        title = _extract_title(body)
        text = _html_to_text(body)
    else:
        text = body
    text = text.strip()
    truncated = len(text) > MAX_READ_CHARS
    return {"title": title, "text": text[:MAX_READ_CHARS], "truncated": truncated}


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------

def create_app(config: Optional[ServerConfig] = None) -> FastAPI:
    """Build the dashboard app. ``config`` controls auth and the URL reader."""
    config = config or ServerConfig()
    registry = _build_registry(config)

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        """Own the reaper task, so it starts and stops with the server."""
        reaper = _start_reaper(registry)
        app.state.reaper = reaper
        try:
            yield
        finally:
            if reaper is not None:
                reaper.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await reaper

    app = FastAPI(
        title="MachineLearningMachine - Inter-Module Communication Mesh",
        description=(
            "Orchestration platform enabling Arena AI, Copilot, Claude, GPT, and custom "
            "modules to talk to each other. Local tool: authenticated, per-session state, "
            "no third-party assets."
        ),
        version="0.1.0",
        lifespan=lifespan,
    )
    app.state.config = config
    app.state.registry = registry
    # Failed sign-ins are counted per client, not per session, and the counter
    # survives a discarded cookie jar (see state.LoginThrottle).
    throttle = LoginThrottle(max_failures=LOGIN_MAX_FAILURES, lockout_seconds=LOGIN_LOCKOUT_SECONDS)
    app.state.login_throttle = throttle

    if config.allow_origins:
        # Only when an operator explicitly names origins; never "*" with cookies in play.
        from fastapi.middleware.cors import CORSMiddleware

        app.add_middleware(
            CORSMiddleware,
            allow_origins=list(config.allow_origins),
            allow_credentials=True,
            allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
            allow_headers=["Content-Type", "Authorization"],
        )

    app.add_middleware(SecurityHeadersMiddleware)
    _register_routes(app, config, registry, throttle)
    _mount_static(app)
    return app


def _start_reaper(registry: SessionRegistry) -> Optional["asyncio.Task[None]"]:
    """
    Reap idle sessions on the interval the registry advertises.

    Idle-timeout enforcement cannot be left to the requests of the sessions it is
    meant to expire: an abandoned tab with a live WebSocket stops making requests,
    and would keep its mesh, transcript and key material until the registry
    happened to fill up. This task is what makes ``--session-ttl`` real.
    """
    interval = registry.sweep_interval_seconds()
    if interval <= 0:
        return None  # TTL disabled on purpose: nothing to sweep for

    async def loop() -> None:
        while True:
            try:
                await asyncio.sleep(interval)
                swept = registry.sweep_expired("idle_timeout")
                if swept:
                    logger.info("Reaped %d idle mesh session(s)", len(swept))
            except asyncio.CancelledError:
                raise
            except Exception:  # a bad sweep must not kill the server
                logger.warning("Session sweep failed; retrying next interval", exc_info=True)

    try:
        return asyncio.create_task(loop(), name="mesh-session-reaper")
    except RuntimeError:  # no running loop (app built outside a server)
        return None


def _build_registry(config: ServerConfig) -> SessionRegistry:
    def make_mesh(cfg: ServerConfig) -> AgentMesh:
        return AgentMesh(max_history=CLIENT_HISTORY_LIMIT)

    registry = SessionRegistry(
        max_sessions=config.max_sessions,
        idle_ttl=config.session_idle_ttl,
        mesh_factory=make_mesh,
        config=config,
    )
    return registry


def _register_routes(
    app: FastAPI, config: ServerConfig, registry: SessionRegistry, throttle: LoginThrottle
) -> None:
    # -- helpers ----------------------------------------------------------
    def _schedule_feed_close(state: SessionState) -> None:
        """Release a disposed session's sockets, saying why when we know why."""
        reason, state.evict_reason = state.evict_reason, None
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return  # disposed outside an event loop (CLI, unit tests): nothing to close
        expired = reason == "idle_timeout"
        payload = None
        if reason:
            payload = {
                "type": "session_released",
                "reason": reason,
                "detail": (
                    "This browser session was idle for longer than the configured "
                    "session lifetime, so its mesh was released. Reload the page for "
                    "a fresh one; saved sessions are untouched."
                    if expired
                    else "This browser session's mesh was released by the server."
                ),
            }
        task = loop.create_task(state.close_feeds(final=payload, code=4408 if expired else 1000))
        _DETACHED_TASKS.add(task)
        task.add_done_callback(_DETACHED_TASKS.discard)

    registry.on_evict = _schedule_feed_close

    def token_from_request(request: Request) -> Optional[str]:
        header = request.headers.get("authorization", "")
        if header.lower().startswith("bearer "):
            return header[7:].strip()
        return request.headers.get("x-mesh-token")

    def state_of(request: Request) -> Optional[SessionState]:
        return getattr(request.state, "session_state", None)

    # -- dependency: session + authentication -----------------------------
    @app.middleware("http")
    async def session_and_auth(request: Request, call_next):
        path = request.url.path
        if path.startswith("/static"):
            return await call_next(request)

        client_id = session_store.sanitize_namespace(request.cookies.get(CLIENT_COOKIE_NAME))
        issued_client_cookie = False
        if not client_id:
            client_id, issued_client_cookie = secrets.token_hex(12), True

        supplied = token_from_request(request)
        token_ok = bool(supplied) and config.check_token(supplied)

        presented_session = request.cookies.get(SESSION_COOKIE_NAME)
        state = registry.get(presented_session)
        if state is not None and state.ephemeral:
            # The client is echoing the cookie back, so this session now *is*
            # pinned to that browser: give it the normal idle lifetime.
            state.ephemeral = False
        if state is None and (not config.require_auth or token_ok):
            # A token-protected server never allocates a session for a caller who
            # has not proven the token yet: otherwise anyone could fill the
            # bounded registry (each entry owns a mesh, a transcript and
            # listeners) and evict real users without authenticating at all.
            # A caller that proves the token by header but keeps no cookies is
            # marked ephemeral: short idle lifetime, and evicted first.
            state = _state_for(client_id, ephemeral=config.require_auth and not presented_session)
        if state is not None and token_ok:
            # Header-based access (API clients) also unlocks this browser session.
            state.authenticated = True
        request.state.session_state = state
        request.state.client_id = client_id
        request.state.new_client_cookie = issued_client_cookie

        if config.require_auth and not _is_public_path(path):
            if state is None or not state.authenticated:
                return JSONResponse(
                    status_code=401,
                    content={
                        "detail": "This server requires an access token.",
                        "auth_required": True,
                        "hint": (
                            "Paste the token in the dashboard, or send "
                            "'Authorization: Bearer <token>'. It is configured with "
                            f"--auth-token or {AUTH_TOKEN_ENV_VAR}."
                        ),
                    },
                    headers={"WWW-Authenticate": "Bearer"},
                )

        # CSRF: a *body-carrying* mutation must be JSON. A cross-site <form> can
        # only send urlencoded / multipart / text/plain, all of which are
        # rejected here, so it cannot drive this API. Bodiless POSTs (e.g.
        # /api/clear) carry nothing for a form to forge, so they are allowed.
        if request.method in ("POST", "DELETE", "PUT", "PATCH") and state is not None:
            length = (request.headers.get("content-length") or "0").strip()
            has_body = length not in ("", "0") or "transfer-encoding" in request.headers
            content_type = (request.headers.get("content-type") or "").split(";")[0].strip().lower()
            if has_body and content_type != "application/json":
                return JSONResponse(
                    status_code=415,
                    content={
                        "detail": "Requests with a body must use Content-Type: application/json."
                    },
                )

        response = await call_next(request)

        # A route may have created the session on the way through (sign-in does),
        # so re-read it before deciding which cookies the client gets to keep.
        state = state_of(request) or state

        if response.status_code < 500:
            # Secure follows the actual scheme (including X-Forwarded-Proto from a
            # TLS-terminating proxy): a Secure-only cookie over plain http would
            # simply be dropped by the browser and every session would reset.
            secure = (
                config.secure_cookies
                if config.secure_cookies is not None
                else request_is_https(request)
            )
            if state is not None:
                _set_cookie(response, SESSION_COOKIE_NAME, state.session_id, max_age=None, secure=secure)
            if issued_client_cookie:
                _set_cookie(
                    response, CLIENT_COOKIE_NAME, client_id,
                    max_age=60 * 60 * 24 * 365, secure=secure,
                )
        return response

    def _state_for(client_id: str, *, ephemeral: bool = False) -> SessionState:
        """
        Create a session state and wire its private broadcast listener.

        ``ephemeral`` marks a session that was created for a caller we could not
        bind to a persistent cookie (a bearer-token client that keeps no cookies).
        Those get a much shorter idle lifetime and are evicted first, so a
        script cannot crowd real browsers out of the bounded registry.
        """
        state = registry.create(client_id, ephemeral=ephemeral)
        _attach_listener(state)
        return state

    def _attach_listener(state: SessionState) -> None:
        """
        Mirror every bus message onto this session's sockets.

        ``run_id`` lets the browser attribute a message to the run it started;
        runs are serialised per session, so the session has at most one active.
        """
        async def on_bus_message(msg: Message) -> None:
            state.publish({
                "type": "new_message",
                "message": msg.to_dict(),
                "run_id": state.active_run_id,
            })

        state.mesh.on_message(on_bus_message)

    def request_is_https(request: Request) -> bool:
        scheme = (request.url.scheme or "").lower()
        forwarded = (request.headers.get("x-forwarded-proto") or "").split(",")[0].strip().lower()
        return scheme == "https" or forwarded == "https"

    def _set_cookie(response: Response, name: str, value: str, *, max_age: Optional[int], secure: bool) -> None:
        if not value:
            return
        response.set_cookie(
            key=name,
            value=value,
            max_age=max_age,
            httponly=True,
            samesite="lax",
            secure=secure,
            path="/",
        )

    def current_state(request: Request) -> SessionState:
        state = state_of(request)
        if state is None:
            raise HTTPException(status_code=503, detail="No session available - reload the page.")
        return state

    # -- authentication endpoints ----------------------------------------
    @app.get("/api/auth/status")
    async def auth_status(request: Request):
        state = state_of(request)
        return {
            "auth_required": config.require_auth,
            "authenticated": bool(state and state.authenticated) or not config.require_auth,
            "url_reader_enabled": config.enable_url_reader,
            "version": app.version,
        }

    @app.post("/api/auth/login", status_code=200)
    async def auth_login(req: LoginRequest, request: Request):
        if not config.require_auth:
            # Loopback mode needs no token. Accepting (and remembering) one here
            # would imply a protection that is not switched on, so refuse instead
            # of pretending the sign-in did something.
            raise HTTPException(
                status_code=400,
                detail=(
                    "This server does not require an access token (it is bound to "
                    "localhost), so there is nothing to sign in to."
                ),
            )
        # Keyed by the client cookie the browser *presented*, falling back to the
        # peer address - never by a freshly generated id, or discarding cookies
        # would reset the counter. (The session itself is not yet created here.)
        key = session_store.sanitize_namespace(request.cookies.get(CLIENT_COOKIE_NAME)) or (
            request.client.host if request.client else "unknown"
        )
        retry = throttle.locked_for(key)
        if retry > 0:
            raise HTTPException(
                status_code=429,
                detail=f"Too many incorrect tokens. Try again in {max(1, int(retry))}s.",
            )
        # Constant-time compare, and never log or echo the token itself.
        if not config.check_token(req.token.strip()):
            throttle.record_failure(key)
            raise HTTPException(status_code=401, detail="That access token is not valid.")
        throttle.reset(key)
        state = state_of(request)
        if state is None:
            # The session exists only once the token checked out.
            state = _state_for(key)
            request.state.session_state = state
        state.authenticated = True
        return {
            "status": "ok",
            "authenticated": True,
            "agents": state.mesh.list_agents(),
            "history": [m.to_dict() for m in state.mesh.get_history()][-200:],
        }

    @app.post("/api/auth/logout")
    async def auth_logout(request: Request):
        state = state_of(request)
        if state is not None:
            state.authenticated = False
        return {"status": "signed_out"}

    # -- agents -----------------------------------------------------------
    @app.get("/api/agents")
    async def get_agents(state: SessionState = Depends(current_state)):
        """List the modules registered in *this* session."""
        return state.mesh.list_agents()

    @app.post("/api/agents")
    async def add_agent(req: AddAgentRequest, state: SessionState = Depends(current_state)):
        """Dynamically register a new custom module with validation."""
        mesh = state.mesh
        if req.agent_id in mesh.agents:
            raise HTTPException(
                status_code=400,
                detail=f"Agent ID '{req.agent_id}' already exists. Choose a different ID.",
            )
        custom_count = sum(1 for aid in mesh.agents if aid not in mesh.BUILTIN_AGENT_IDS)
        if len(mesh.agents) >= MAX_AGENTS_PER_SESSION or custom_count >= MAX_CUSTOM_AGENTS_PER_SESSION:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Too many agents in this session (max {MAX_AGENTS_PER_SESSION}). "
                    "Remove some or clear the session."
                ),
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
            state.publish({"type": "agents_updated", "agents": mesh.list_agents()})
            logger.info("Custom agent registered: %s", req.agent_id)
            return {"status": "success", "agent": agent.to_dict()}
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        except Exception as exc:
            logger.error("Failed to add agent %s", req.agent_id, exc_info=True)
            raise HTTPException(status_code=500, detail="Failed to register agent. Please try again.") from exc

    # -- transcript -------------------------------------------------------
    @app.get("/api/history")
    async def get_history(limit: Optional[int] = None, state: SessionState = Depends(current_state)):
        history = state.mesh.get_history()
        if limit is not None:
            limit = max(1, min(limit, 500))
            history = history[-limit:]
        return [m.to_dict() for m in history]

    @app.post("/api/clear")
    async def clear_session(state: SessionState = Depends(current_state)):
        """Clear *this session's* message history and module memory."""
        state.mesh.clear_history()
        state.last_run_warnings = []
        state.publish({"type": "history_cleared"})
        return {"status": "cleared"}

    @app.get("/api/export/markdown")
    async def export_markdown(state: SessionState = Depends(current_state)):
        markdown = state.mesh.export_markdown()
        if not markdown.strip() or markdown.strip() == "# Multi-Agent Dialogue Transcript":
            return {"markdown": "# No messages yet\n\nStart a dialogue to generate a transcript."}
        return {"markdown": _with_provenance_header(markdown, state)}

    @app.get("/api/export/json")
    async def export_json(state: SessionState = Depends(current_state)):
        json_str = state.mesh.export_json()
        return {"json": json_str if json_str.strip() != "[]" else "[]"}

    @app.get("/api/export/markdown/download")
    async def export_markdown_download(state: SessionState = Depends(current_state)):
        markdown = _with_provenance_header(state.mesh.export_markdown(), state)
        return PlainTextResponse(
            content=markdown,
            media_type="text/markdown",
            headers={"Content-Disposition": "attachment; filename=module_mesh_transcript.md"},
        )

    @app.get("/api/export/json/download")
    async def export_json_download(state: SessionState = Depends(current_state)):
        return PlainTextResponse(
            content=state.mesh.export_json(),
            media_type="application/json",
            headers={"Content-Disposition": "attachment; filename=module_mesh_transcript.json"},
        )

    # -- providers / keys -------------------------------------------------
    @app.post("/api/config")
    async def update_config(req: ConfigApiKeysRequest, state: SessionState = Depends(current_state)):
        """
        Configure real LLM providers for *this session only*.

        Keys live in memory for the lifetime of the process, are never written
        to disk, never returned by any endpoint and never logged. They are only
        shape-checked here; pass ``"verify": true`` to actually test them
        against the provider.
        """
        mesh = state.mesh

        if req.clear:
            for agent in (mesh.gpt, mesh.copilot, mesh.claude, mesh.arena_ai):
                if agent is not None:
                    agent.provider = _simulator_for(config)
            state.api_keys = {}
            state.provider_config = {"mode": "simulated", "configured": []}
            state.publish({"type": "agents_updated", "agents": mesh.list_agents()})
            return {"status": "success", "message": "Back to the built-in simulator for this session."}

        configured: List[str] = []
        problems: List[str] = []
        try:
            if req.openai_api_key or req.openai_base_url:
                if req.openai_api_key and not _looks_like_key(req.openai_api_key):
                    if not _is_local_url(req.openai_base_url):
                        raise HTTPException(
                            status_code=400,
                            detail="That does not look like an OpenAI-style key (expected 'sk-...' or a local backend).",
                        )
                if req.openai_base_url:
                    _guard_provider_url(config, req.openai_base_url)
                openai_provider = OpenAIProvider(
                    api_key=req.openai_api_key or state.api_keys.get("openai", ""),
                    base_url=req.openai_base_url,
                    fallback_to_mock=config.fallback_to_mock,
                    # The single most important line in this endpoint: a key that
                    # happens to live in the *server's* environment must never be
                    # attached to an endpoint a browser chose.
                    allow_env_key=False,
                )
                if req.openai_api_key:
                    state.api_keys["openai"] = req.openai_api_key
                if req.verify:
                    ok, why = await _verify_openai(openai_provider)
                    if not ok:
                        problems.append(f"OpenAI check failed: {why}")
                for agent in (mesh.gpt, mesh.copilot):
                    if agent is not None:
                        agent.provider = openai_provider
                configured.append("OpenAI")

            if req.anthropic_api_key:
                if not req.anthropic_api_key.startswith("sk-ant-") and len(req.anthropic_api_key) < 10:
                    raise HTTPException(
                        status_code=400,
                        detail="That does not look like an Anthropic key (expected 'sk-ant-...').",
                    )
                anthropic_provider = AnthropicProvider(
                    api_key=req.anthropic_api_key,
                    fallback_to_mock=config.fallback_to_mock,
                    allow_env_key=False,
                )
                state.api_keys["anthropic"] = req.anthropic_api_key
                for agent in (mesh.claude, mesh.arena_ai):
                    if agent is not None:
                        agent.provider = anthropic_provider
                configured.append("Anthropic")

            if not configured:
                state.provider_config = {"mode": "simulated", "configured": []}
                return {
                    "status": "success",
                    "message": "No keys provided - this session keeps using the built-in simulator.",
                }

            state.provider_config = {
                "mode": "live" if not problems else "unverified",
                "configured": configured,
                "verified": not problems,
                "problems": problems,
                # Honest about what we did and did not check.
                "note": "Keys are shape-checked only, kept in memory, and never sent back to the browser.",
            }
            state.publish({"type": "agents_updated", "agents": mesh.list_agents()})
            message = (
                f"Configured: {', '.join(configured)} for this session only. "
                "Keys stay in memory and are never saved or logged."
            )
            if problems:
                message += " " + " ".join(problems)
                return {"status": "partial", "message": message, "configured": configured, "problems": problems}
            return {
                "status": "success",
                "message": message,
                "configured": configured,
                "verified": bool(req.verify),
            }
        except HTTPException:
            raise
        except Exception as exc:
            logger.error("Config error", exc_info=True)
            raise HTTPException(status_code=500, detail="Failed to configure providers") from exc

    @app.get("/api/config")
    async def read_config(state: SessionState = Depends(current_state)):
        """Provider status for this session. Deliberately never includes keys."""
        return {
            "mode": state.provider_config.get("mode", "simulated"),
            "configured": state.provider_config.get("configured", []),
            "verified": state.provider_config.get("verified"),
            "has_openai_key": bool(state.api_keys.get("openai")),
            "has_anthropic_key": bool(state.api_keys.get("anthropic")),
            "note": "Keys are held in memory for this session only and are never returned to the client.",
        }

    # -- saved sessions (namespaced per client) ---------------------------
    @app.post("/api/sessions")
    async def save_session(req: SaveSessionRequest, state: SessionState = Depends(current_state)):
        if not state.mesh.get_history():
            raise HTTPException(
                status_code=400,
                detail="There is nothing to save yet - run a dialogue first, then save it.",
            )
        try:
            meta = session_store.save_session(
                name=req.name,
                agents=state.mesh.list_agents(),
                messages=[m.to_dict() for m in state.mesh.get_history()],
                namespace=state.client_id,
            )
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        except OSError as exc:
            # A full or read-only disk is the operator's problem to read about, so
            # the reason is passed through; the directory it happened in is not.
            reason = str(getattr(exc, "reason_detail", "") or "").strip()
            logger.error("Failed to save session: %s", exc)
            raise HTTPException(
                status_code=500,
                detail=f"Could not save the session{' (' + reason + ')' if reason else ''}. Please try again.",
            ) from exc
        except Exception as exc:
            logger.error("Failed to save session", exc_info=True)
            raise HTTPException(status_code=500, detail="Could not save the session. Please try again.") from exc
        logger.info("Session saved: %s (%s)", meta["id"], meta["name"])
        return {"status": "saved", "session": meta}

    @app.get("/api/sessions")
    async def list_sessions(state: SessionState = Depends(current_state)):
        return {"sessions": session_store.list_sessions(state.client_id), "namespaced": bool(state.client_id)}

    @app.get("/api/sessions/{session_id}")
    async def get_saved_session(session_id: str, state: SessionState = Depends(current_state)):
        data = session_store.get_session(session_id, state.client_id)
        if not data:
            raise HTTPException(status_code=404, detail="Session not found")
        return data

    @app.delete("/api/sessions/{session_id}")
    async def delete_saved_session(session_id: str, state: SessionState = Depends(current_state)):
        if not session_store.delete_session(session_id, state.client_id):
            raise HTTPException(status_code=404, detail="Session not found")
        return {"status": "deleted"}

    @app.post("/api/sessions/load")
    async def load_session(req: LoadSessionRequest, state: SessionState = Depends(current_state)):
        data = session_store.get_session(req.session_id, state.client_id)
        if not data:
            raise HTTPException(status_code=404, detail="Session not found - it may have been deleted.")
        mesh = state.mesh
        try:
            mesh.reset_to_session(data.get("agents", []))
            messages = []
            for m in data.get("messages", []):
                clean = {k: v for k, v in m.items() if k != "formatted_time"}
                try:
                    messages.append(Message(**clean))
                except Exception:
                    logger.warning("Skipping unreadable message in saved session %s", req.session_id)
            mesh.load_messages(messages)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=f"Could not restore this session: {e}") from e
        except Exception as exc:
            logger.error("Failed to load session %s", req.session_id, exc_info=True)
            raise HTTPException(status_code=500, detail="Failed to load the session. Please try again.") from exc

        state.publish({
            "type": "session_loaded",
            "name": data.get("name"),
            "agents": mesh.list_agents(),
            "history": [m.to_dict() for m in mesh.get_history()],
        })
        return {"status": "loaded", "name": data.get("name"), "messages": len(messages)}

    # -- web page reader (opt-in, SSRF-guarded) ---------------------------
    @app.post("/api/read/url")
    async def read_url(req: ReadUrlRequest, request: Request, state: SessionState = Depends(current_state)):
        """
        Fetch a public web page and return its readable text.

        Off unless the server was started with ``--enable-url-reader``. When on,
        :mod:`machinelearningmachine.netguard` validates the URL and *every*
        redirect against the loopback/private/link-local/reserved ranges, so
        this endpoint cannot be pointed at internal services.
        """
        if not config.enable_url_reader:
            raise HTTPException(
                status_code=404,
                detail=(
                    "The page reader is disabled on this server. Start it with "
                    "--enable-url-reader if you want to read web pages aloud."
                ),
            )
        now = time.time()
        if now - state.last_url_read_time < URL_READ_COOLDOWN_SECONDS:
            raise HTTPException(status_code=429, detail="Please wait a moment between page reads")
        state.last_url_read_time = now

        try:
            result = await asyncio.to_thread(
                netguard.guarded_fetch, req.url, max_bytes=MAX_READ_BYTES
            )
        except netguard.UnsafeURL as e:
            # 403-ish for the client, but never echo the blocked URL's internals.
            raise HTTPException(status_code=400, detail=e.reason) from e
        except netguard.FetchError as e:
            raise HTTPException(status_code=400, detail=e.reason) from e
        except Exception as exc:
            logger.error("URL read failed (host withheld to avoid logging user data)", exc_info=True)
            raise HTTPException(status_code=500, detail="Could not read that page. Please try again.") from exc

        data = page_text_from_body(result.text, result.content_type)
        if result.truncated:
            data["truncated"] = True
        if not data["text"]:
            raise HTTPException(status_code=422, detail="No readable text found at that URL")
        return {
            "status": "ok",
            "url": result.final_url,
            "redirects": result.redirects,
            **data,
        }

    # -- runs -------------------------------------------------------------
    async def _call_topology(
        mesh: AgentMesh,
        *,
        topology: str,
        prompt: str,
        from_agent: Optional[str],
        to_agent: Optional[str],
        agent_ids: Optional[List[str]],
        turns: Optional[int],
    ) -> List[Message]:
        """One topology call, shared by immediate and queued executions."""
        if topology == "p2p":
            return await mesh.talk_p2p(
                from_agent_id=from_agent or "arena-ai",
                to_agent_id=to_agent or "copilot",
                prompt=prompt,
                turns=turns or 4,
            )
        if topology == "pipeline":
            return await mesh.run_pipeline(prompt=prompt, agent_ids=agent_ids)
        if topology == "debate":
            return await mesh.run_debate(prompt=prompt, agent_ids=agent_ids)
        if topology == "hub":
            return await mesh.run_hub_and_spoke(
                prompt=prompt,
                hub_id=from_agent or "arena-ai",
                spoke_ids=agent_ids,
            )
        raise ValueError(f"Unknown topology '{topology}'")  # validated by the model

    def _timeout_reason() -> str:
        """The one explanation for a run that was cut off, worded once for both paths."""
        return (
            f"the run was stopped after {config.run_timeout:.0f}s: a provider accepted "
            "the request and never answered. Check the endpoint in Settings, then try a "
            "shorter prompt or a longer --run-timeout."
        )

    def _cancel_notice(run_id: str) -> Message:
        """The transcript line a stopped run leaves behind, identical in both paths."""
        return Message(
            sender_id="system", sender_name="System", message_type=MessageType.SYSTEM,
            content="Run cancelled. Replies already received have been kept.",
            metadata={"run_id": run_id, "cancelled": True},
        )

    def _publish_terminal(
        state: SessionState, run_id: str, transcript: List[Message], cancelled: bool
    ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[str]]:
        """
        Describe a finished run once, for both paths.

        Counts what was simulated and what was clamped, remembers the provider
        warnings on the session, publishes the terminal frame, and returns those same
        three collections so the immediate path can build its JSON body from the
        numbers it just broadcast. This is the part worth sharing: an immediate run
        and a queued run that describe the *same* transcript differently - a badge
        count, a warning list, a ``run_cancelled`` where the other said
        ``run_completed`` - is a divergence no single-path test would ever catch.
        """
        meta = [m.metadata or {} for m in transcript]
        simulated = [md for md in meta if md.get("simulated")]
        clamped = [md for md in meta if md.get("content_truncated")]
        warnings = sorted({str(md["provider_error"]) for md in meta if md.get("provider_error")})
        state.last_run_warnings = warnings
        state.publish({
            "type": "run_cancelled" if cancelled else "run_completed",
            "run_id": run_id,
            "simulated_count": len(simulated),
            "truncated_count": len(clamped),
        })
        return simulated, clamped, warnings

    def _schedule_queued(state: SessionState) -> None:
        """Start the next waiting run, if any (called before the lock is released)."""
        if not state.run_queue:
            return
        # The only caller is ``_release_run``, which both run paths invoke from a
        # coroutine's ``finally`` - so a running loop is always there to ask.
        task = asyncio.get_running_loop().create_task(_run_queued_entry(state))
        _DETACHED_TASKS.add(task)
        task.add_done_callback(_DETACHED_TASKS.discard)

    def _release_run(state: SessionState) -> None:
        """
        Give up a run's hold on the session, identically from both run paths.

        The order is the contract: the next waiting run is scheduled *before* the
        lock is released, so a run that was queued cannot be beaten to it by a
        request that arrives in the same instant. Every line is load-bearing and
        none of them is obvious - omit ``_schedule_queued`` and the queue stalls
        forever behind a run that already finished; omit the ``release()`` and this
        browser session deadlocks for the rest of its lifetime; omit either reset
        and ``/api/status`` keeps reporting a run that is over, which is a Stop
        button that never comes back. Both callers run this from a ``finally``, so
        it happens on success, on cancellation, on timeout and on error alike.
        """
        _schedule_queued(state)
        state.active_run_id = None
        state.cancel_event = None
        state.run_lock.release()

    async def _run_queued_entry(state: SessionState) -> None:
        """Execute the oldest waiting run (FIFO) after the active one released the lock."""
        await state.run_lock.acquire()
        if not state.run_queue:
            # Evicted or cancelled between scheduling and acquiring: nothing to do.
            state.run_lock.release()
            return
        entry = state.run_queue.pop(0)
        mesh = state.mesh
        run_id = entry.run_id
        state.active_run_id = run_id
        state.cancel_event = asyncio.Event()
        previous_ids = {m.id for m in mesh.get_history()}
        cancelled = False
        state.publish({
            "type": "run_started",
            "run_id": run_id,
            "topology": entry.topology,
            "prompt": entry.prompt[:200],
            "queued": True,
        })

        async def _execute() -> List[Message]:
            cancel_event.set(state.cancel_event)
            return await _call_topology(
                mesh,
                topology=entry.topology,
                prompt=entry.prompt,
                from_agent=entry.from_agent,
                to_agent=entry.to_agent,
                agent_ids=entry.agent_ids,
                turns=entry.turns,
            )

        transcript: List[Message] = []
        failed = False
        try:
            try:
                transcript = await asyncio.wait_for(_execute(), timeout=config.run_timeout)
            except RunCancelled:
                transcript = [m for m in mesh.get_history() if m.id not in previous_ids]
            except asyncio.TimeoutError:
                state.publish({
                    "type": "run_error", "run_id": run_id, "error": _timeout_reason(),
                })
                failed = True
            if not failed and state.cancel_event.is_set():
                cancelled = True
                notice = _cancel_notice(run_id)
                await mesh.bus.dispatch(notice)
                transcript.append(notice)
        except ProviderError as e:
            state.publish({
                "type": "run_error", "run_id": run_id, "error": f"{e.provider}: {e.reason}",
            })
            failed = True
        except ValueError as e:
            state.publish({"type": "run_error", "run_id": run_id, "error": _safe_reason(e)})
            failed = True
        except Exception:
            logger.error("Queued dialogue execution failed", exc_info=True)
            state.publish({
                "type": "run_error", "run_id": run_id, "error": "Internal error during dialogue execution",
            })
            failed = True
        finally:
            _release_run(state)

        if failed:
            return
        _publish_terminal(state, run_id, transcript, cancelled)

    @app.post("/api/runs/{run_id}/cancel")
    async def cancel_run(run_id: str, state: SessionState = Depends(current_state)):
        if state.active_run_id == run_id and state.cancel_event is not None:
            state.cancel_event.set()
            return {"status": "cancelling", "run_id": run_id}
        removed = state.remove_queued(run_id)
        if removed is not None:
            # Never started, so nothing to keep: no transcript change, just the frame.
            state.publish({"type": "run_cancelled", "run_id": run_id, "queued": True})
            return {"status": "cancelled", "run_id": run_id}
        raise HTTPException(status_code=404, detail="No active or queued run with that id in this session.")

    @app.post("/api/run")
    async def run_dialogue(req: RunTaskRequest, request: Request, state: SessionState = Depends(current_state)):
        """
        Execute one multi-agent dialogue for this session.

        * **Nothing is stamped as "recent" for a request that never ran.** The
          cooldown used to be spent by invalid prompts, so a typo cost the next
          legitimate run a second of waiting.
        * **One run at a time, the rest wait in a bounded per-session queue.**
          Agents, transcript, export and provider memory are shared per session;
          two overlapping runs produced a history that belonged to neither. The
          second caller is queued (202 with a 1-indexed ``queue_position``), not
          refused - up to ``--max-queued`` waiting runs, then 429 with
          ``Retry-After``. ``--max-queued 0`` restores the pre-queue 409 refusal.
          The queue is FIFO, in-memory and per process: a restart or an evicted
          session drops whatever was waiting.
        * **No unbounded execution.** A provider that accepts a connection and
          never answers is cut off after ``--run-timeout`` with a 504 that says
          so (queued runs are each bounded when they execute, not while waiting).
        """
        mesh = state.mesh

        if req.topology == "p2p":
            if req.from_agent not in mesh.agents:
                raise HTTPException(status_code=400, detail=f"Initiating agent '{req.from_agent}' not found")
            if req.to_agent not in mesh.agents:
                raise HTTPException(status_code=400, detail=f"Responding agent '{req.to_agent}' not found")
            if req.from_agent == req.to_agent:
                raise HTTPException(
                    status_code=400, detail="Cannot start dialogue with same agent as both sides"
                )
        elif req.topology in ("pipeline", "debate", "hub"):
            # The mesh rejects these before doing any work, with these exact words -
            # so the endpoint rejects them before the busy check too. Otherwise the
            # same payload is a 400 when idle and a 202 when busy (an empty list
            # would even run the default roster, having been stored as None).
            if req.topology == "hub":
                hub_id = req.from_agent or "arena-ai"
                if hub_id not in mesh.agents:
                    available = ", ".join(mesh.agents.keys())
                    raise HTTPException(
                        status_code=400,
                        detail=f"Hub agent '{hub_id}' not found. Available: {available}",
                    )
            if req.agent_ids is not None and not req.agent_ids:
                if req.topology == "pipeline":
                    detail = "At least one agent ID required for pipeline"
                elif req.topology == "debate":
                    detail = "At least one agent required for debate"
                else:  # hub: agent_ids become the spokes
                    detail = "At least one spoke agent required"
                raise HTTPException(status_code=400, detail=detail)
            if req.agent_ids:
                missing = [aid for aid in req.agent_ids if aid not in mesh.agents]
                if missing:
                    raise HTTPException(
                        status_code=400, detail=f"Agents not found: {', '.join(missing)}"
                    )
            if req.topology == "hub" and req.agent_ids and hub_id in req.agent_ids:
                raise HTTPException(status_code=400, detail="Hub agent cannot also be a spoke")
        # No agent ids for pipeline/debate/hub is not an error: the mesh has a
        # documented default roster, and inventing a requirement here would break
        # every caller that relies on it (the dashboard included).

        # A session is busy when something runs *or* waits: a lock-free moment with
        # a non-empty queue still means "behind the queue", or a newcomer would
        # jump ahead of runs that arrived earlier. Busy outranks the cooldown,
        # as it always has - waiting out one second would not make the request
        # runnable - so a double-click while busy queues instead of 429ing.
        #
        # Checked and enqueued with no await in between, so two requests arriving
        # in the same loop iteration cannot both take the last queue slot.
        now = time.time()
        busy = state.run_lock.locked() or bool(state.run_queue)
        if busy:
            if config.max_queued <= 0:
                raise HTTPException(
                    status_code=409,
                    detail=(
                        "A run is already in progress in this browser session"
                        + (f" ({state.active_run_id})" if state.active_run_id else "")
                        + ". Wait for it to finish - the transcript, the agents' memory "
                        "and the export are shared, so a second run would mix into the first."
                    ),
                    headers={"Retry-After": str(int(config.run_timeout) + 1)},
                )
            if len(state.run_queue) >= config.max_queued:
                raise HTTPException(
                    status_code=429,
                    detail=(
                        f"Run queue is full ({len(state.run_queue)} waiting, max {config.max_queued}). "
                        "Wait for a run to finish and try again."
                    ),
                    headers={"Retry-After": str(int(config.run_timeout) + 1)},
                )
            queued_id = state.next_queued_run_id()
            state.run_queue.append(QueuedRun(
                run_id=queued_id,
                topology=req.topology,
                prompt=req.prompt,
                from_agent=req.from_agent,
                to_agent=req.to_agent,
                agent_ids=list(req.agent_ids) if req.agent_ids else None,
                turns=req.turns,
            ))
            position = len(state.run_queue)
            state.last_run_time = now
            state.publish({
                "type": "run_queued",
                "run_id": queued_id,
                "queue_position": position,
                "queue_depth": position,
                "topology": req.topology,
                # A tab that missed run_started (a gap ate it) attributes the
                # later completion by this id instead of wedging busy.
                "active_run_id": state.active_run_id,
            })
            return JSONResponse(
                status_code=202,
                content={
                    "status": "queued",
                    "run_id": queued_id,
                    "queue_position": position,
                    "max_queued": config.max_queued,
                    "detail": (
                        f"Queued at position {position} behind the active run"
                        + (f" ({state.active_run_id})" if state.active_run_id else "")
                        + ". It runs automatically; watch the feed or GET /api/history."
                    ),
                },
            )

        # Rate limiting only *after* the request is known to be runnable, so a
        # rejected prompt cannot spend the session's cooldown for everybody.
        if now - state.last_run_time < RUN_COOLDOWN_SECONDS:
            wait = max(0.1, RUN_COOLDOWN_SECONDS - (now - state.last_run_time))
            raise HTTPException(
                status_code=429,
                detail=f"Please wait {wait:.1f}s between runs",
                headers={"Retry-After": str(int(wait) + 1)},
            )
        state.last_run_time = now
        await state.run_lock.acquire()
        run_id = state.next_run_id()
        state.cancel_event = asyncio.Event()
        previous_ids = {m.id for m in mesh.get_history()}
        cancelled = False

        state.publish({
            "type": "run_started",
            "run_id": run_id,
            "topology": req.topology,
            "prompt": req.prompt[:200],  # Don't broadcast full prompt for privacy
        })

        async def _execute() -> List[Message]:
            """One topology call, kept separate so the timeout can wrap exactly it."""
            cancel_event.set(state.cancel_event)  # confined to wait_for's child task
            return await _call_topology(
                mesh,
                topology=req.topology,
                prompt=req.prompt,
                from_agent=req.from_agent,
                to_agent=req.to_agent,
                agent_ids=req.agent_ids,
                turns=req.turns,
            )

        try:
            try:
                transcript = await asyncio.wait_for(_execute(), timeout=config.run_timeout)
            except RunCancelled:
                transcript = [m for m in mesh.get_history() if m.id not in previous_ids]
            except asyncio.TimeoutError as exc:
                reason = _timeout_reason()
                state.publish({"type": "run_error", "run_id": run_id, "error": reason})
                raise HTTPException(status_code=504, detail=reason) from exc
            if state.cancel_event.is_set():
                cancelled = True
                notice = _cancel_notice(run_id)
                await mesh.bus.dispatch(notice)
                transcript.append(notice)
        except HTTPException:
            raise
        except ProviderError as e:
            # fallback_to_mock=False: report the provider failure instead of
            # pretending the stage produced an answer.
            state.publish({
                "type": "run_error", "run_id": run_id, "error": f"{e.provider}: {e.reason}",
            })
            raise HTTPException(status_code=502, detail=f"{e.provider} failed: {e.reason}") from e
        except ValueError as e:
            reason = _safe_reason(e)
            state.publish({"type": "run_error", "run_id": run_id, "error": reason})
            raise HTTPException(status_code=400, detail=reason) from e
        except Exception as exc:
            logger.error("Dialogue execution failed", exc_info=True)
            state.publish({
                "type": "run_error", "run_id": run_id, "error": "Internal error during dialogue execution",
            })
            raise HTTPException(status_code=500, detail="Failed to execute dialogue. Please try again.") from exc
        finally:
            _release_run(state)

        messages = [m.to_dict() for m in transcript]
        simulated, clamped, warnings = _publish_terminal(state, run_id, transcript, cancelled)
        return {
            "status": "cancelled" if cancelled else "completed",
            "run_id": run_id,
            "queue_position": 0,
            "messages": messages,
            # Tell the client what it is looking at; the transcript is never
            # silently "verified" output.
            "simulated": bool(simulated) and len(simulated) == len(messages),
            "partially_simulated": bool(simulated) and len(simulated) != len(messages),
            "provider_warnings": warnings,
            # A long answer that had to be cut is a property of the result the
            # caller is about to trust, so it is reported next to it.
            "truncated_messages": len(clamped),
        }

    # -- websocket --------------------------------------------------------
    @app.websocket("/ws")
    async def websocket_endpoint(websocket: WebSocket):
        """
        Live feed for one browser tab.

        The handler never writes to the socket itself: everything (``init``, the
        pong, and every mesh event) goes through the session's
        :class:`~machinelearningmachine.server.feed.ClientFeed`, so there is exactly
        one writer per connection and a stalled client can neither block a run nor
        be blocked by one. Inbound traffic is capped, because the only thing the
        server understands from a browser here is a four-letter keepalive.
        """
        state = _ws_state(websocket, config, registry)
        if state is None:
            # Refuse before accepting: an unauthenticated socket must not even
            # get as far as a 101 response.
            await websocket.close(code=4401)
            return

        await websocket.accept()
        feed = ClientFeed(websocket).start()
        state.feeds.add(feed)
        try:
            feed.publish({
                "type": "init",
                "active_run_id": state.active_run_id,
                "cancel_requested": bool(state.cancel_event and state.cancel_event.is_set()),
                "queued_run_ids": [entry.run_id for entry in state.run_queue],
                "queue_depth": len(state.run_queue),
                "agents": state.mesh.list_agents(),
                "history": [m.to_dict() for m in state.mesh.get_history()],
                "authenticated": state.authenticated or not config.require_auth,
                "limits": {
                    "max_prompt_length": MAX_PROMPT_LENGTH,
                    "max_history": state.mesh.bus._max_history,
                    "max_messages_client": state.mesh.bus._max_history,
                    "max_agents": MAX_AGENTS_PER_SESSION,
                },
                "flags": {
                    "url_reader_enabled": config.enable_url_reader,
                    "run_timeout_seconds": config.run_timeout,
                    "max_queued": config.max_queued,
                },
            })
            while True:
                data = await websocket.receive_text()
                if len(data) > MAX_WS_INBOUND_CHARS:
                    # Unbounded inbound frames are a memory offer, not a protocol.
                    await feed.aclose(code=1009)
                    return
                if data == "ping":
                    feed.publish({"type": "pong"})
        except WebSocketDisconnect:
            pass
        except Exception as e:
            logger.warning("WebSocket error: %s", e)
        finally:
            state.feeds.discard(feed)
            feed.abort()

    def _ws_state(websocket: WebSocket, cfg: ServerConfig, reg: SessionRegistry) -> Optional[SessionState]:
        """Resolve (and authorise) the session for a WebSocket handshake."""
        client_id = session_store.sanitize_namespace(websocket.cookies.get(CLIENT_COOKIE_NAME))
        state = reg.get(websocket.cookies.get(SESSION_COOKIE_NAME))
        if cfg.require_auth and (state is None or not state.authenticated):
            header = websocket.headers.get("authorization", "")
            token = header[7:].strip() if header.lower().startswith("bearer ") else None
            if not cfg.check_token(token):
                # Refused without creating anything. Note the token is only read
                # from a header on purpose: a ?token= query parameter would end up
                # in access logs.
                return None
            if state is None:
                state = _state_for(client_id or "")
            state.authenticated = True
            return state
        if state is None:
            # The page normally created one with its first API call; this keeps a
            # freshly opened dashboard working before any fetch has happened.
            state = _state_for(client_id or "")
        return state

    # -- meta / static ----------------------------------------------------
    @app.api_route("/", methods=["GET", "HEAD"])
    async def serve_index():
        index_file = STATIC_DIR / "index.html"
        if index_file.exists():
            return FileResponse(str(index_file))
        return JSONResponse({"status": "healthy", "service": "MachineLearningMachine Agent Mesh API"})

    @app.api_route("/health", methods=["GET", "HEAD"])
    async def health():
        """
        Unauthenticated liveness probe, deliberately minimal.

        Operational detail (agents, transcripts, session count) lives behind
        /api/status, because /health cannot be authenticated in most uptime
        monitors.
        """
        return {
            "status": "ok",
            "version": app.version,
            "auth_required": config.require_auth,
            "url_reader_enabled": config.enable_url_reader,
        }

    @app.get("/api/status")
    async def status(state: SessionState = Depends(current_state)):
        """Per-session detail for the status panel (authenticated like the rest)."""
        return {
            "agents": len(state.mesh.agents),
            "messages": len(state.mesh.get_history()),
            "connections": len(state.feeds),
            # Visible so a user can tell "the server is slow" from "my tab is
            # not keeping up", and so tests can assert frames were never lost.
            "dropped_frames": sum(feed.dropped for feed in state.feeds),
            "provider_mode": state.provider_config.get("mode", "simulated"),
            "last_run_warnings": state.last_run_warnings,
            "url_reader_enabled": config.enable_url_reader,
            "active_run_id": state.active_run_id,
            "queued_runs": len(state.run_queue),
            "max_queued": config.max_queued,
            **registry.stats(),
        }

    def _with_provenance_header(markdown: str, state: SessionState) -> str:
        """Exported transcripts must say which provider produced them."""
        messages = state.mesh.get_history()
        simulated = sum(1 for m in messages if m.metadata.get("simulated"))
        mode = state.provider_config.get("mode", "simulated")
        stamp = (
            f"> Provider mode: **{mode}**. {simulated} of {len(messages)} messages were "
            "simulated, i.e. generated by the built-in templates and never executed or tested.\n\n"
        )
        return stamp + markdown


def _safe_reason(exc: Exception) -> str:
    """
    A plain-language reason for a rejected run, with no payload in it.

    ``str(exc)`` is tempting and wrong: a pydantic ``ValidationError`` interpolates
    the offending *value*, which here is the model's answer. Echoing that into an
    API response and into every open WebSocket of the session is how a robustness
    limit turns into an information leak, so unrecognised value errors are named by
    class and shortened instead.
    """
    text = str(exc).strip()
    if exc.__class__.__name__ == "ValidationError" or "\n" in text or len(text) > 300:
        return (
            "The dialogue could not be assembled into a valid message. The run was "
            "stopped and nothing was added to the transcript."
        )
    return text[:300]


def _is_public_path(path: str) -> bool:
    if path in PUBLIC_PATHS:
        return True
    return any(path.startswith(prefix) for prefix in PUBLIC_PREFIXES)


def _guard_provider_url(config: ServerConfig, base_url: str) -> None:
    """
    Refuse a provider base URL the mesh must not dial out to.

    ``/api/config`` takes an arbitrary URL and ``/api/run`` then POSTs the
    conversation to it, so on any bind another machine can reach, a browser picking
    ``http://169.254.169.254/`` is an outbound-request primitive with a
    status-code oracle (``{"verify": true}`` makes that oracle explicit). The page
    reader has had the netguard policy from the start; this is the same policy,
    with the one difference the use case requires - any port is fine, because
    Ollama and vLLM do not live on 80/443.

    On a loopback bind the operator *is* the caller, so local backends stay
    working, which is the entire point of the setting.
    """
    allow_private = config.on_loopback or config.allow_insecure_provider_urls
    if allow_private:
        return
    try:
        netguard.validate_provider_target(
            base_url,
            allow_private=False,
            extra_allowed_hosts=config.url_allowlist,
        )
    except netguard.UnsafeURL as e:
        raise HTTPException(status_code=400, detail=e.reason) from e
    except Exception as exc:  # resolver blowups must not look like a 500
        raise HTTPException(
            status_code=400,
            detail="That provider address could not be checked. Use a full http(s):// URL.",
        ) from exc


def _looks_like_key(key: str) -> bool:
    return key.startswith(("sk-", "ollama", "lm-")) or len(key) >= 20


def _is_local_url(base_url: Optional[str]) -> bool:
    if not base_url:
        return False
    return any(marker in base_url for marker in ("localhost", "127.0.0.1", "::1", "host.docker.internal"))


def _simulator_for(config: ServerConfig):
    from ..agents.providers import MockLLMProvider

    return MockLLMProvider()


async def _verify_openai(provider: OpenAIProvider) -> Tuple[bool, str]:
    """
    Ask the configured OpenAI-compatible backend whether the key works.

    Only used when the caller explicitly asks for verification, and it only
    ever sends the key to the base URL the operator typed in.
    """
    try:
        aiohttp_module = __import__("aiohttp", fromlist=["ClientSession"])
    except ImportError:
        return False, "aiohttp is not installed, so the key could not be verified"
    try:
        async with aiohttp_module.ClientSession(timeout=aiohttp_module.ClientTimeout(total=15)) as session:
            async with session.get(
                f"{provider.base_url}/models",
                headers={"Authorization": f"Bearer {provider.api_key}"},
            ) as resp:
                if resp.status == 200:
                    return True, ""
                return False, f"the provider answered HTTP {resp.status}"
    except Exception as exc:  # network down, bad host, ...
        return False, f"could not reach the provider ({exc.__class__.__name__})"


class SecurityHeadersMiddleware:
    """
    ASGI middleware adding a strict CSP and friends.

    Everything the dashboard needs is served from this origin (see
    ``static/vendor/``), so ``default-src 'self'`` is enough and kills the
    "any CDN can execute code in the dashboard" class of problems. Written as a
    plain ASGI middleware (not BaseHTTPMiddleware) so WebSocket upgrades are
    untouched.
    """

    CSP = (
        "default-src 'self'; "
        "script-src 'self'; "
        "style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data:; "
        "font-src 'self'; "
        "connect-src 'self'; "
        "form-action 'none'; "
        "frame-ancestors 'none'; "
        "base-uri 'self'; "
        "object-src 'none'"
    )

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_wrapper(message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(message)
                headers.set("Content-Security-Policy", self.CSP)
                headers.set("X-Content-Type-Options", "nosniff")
                headers.set("X-Frame-Options", "DENY")
                headers.set("Referrer-Policy", "no-referrer")
                headers.set("Permissions-Policy", "microphone=(self)")
            await send(message)

        await self.app(scope, receive, send_wrapper)


class MutableHeaders:
    """Tiny helper over the raw ASGI header list."""

    def __init__(self, message: Dict[str, Any]) -> None:
        self._message = message
        if "headers" not in message or message["headers"] is None:
            message["headers"] = []

    def set(self, name: str, value: str) -> None:
        raw_name = name.lower().encode("latin-1")
        headers = self._message["headers"]
        for index, (key, _value) in enumerate(headers):
            if key.lower() == raw_name:
                headers[index] = (raw_name, value.encode("latin-1"))
                return
        headers.append((raw_name, value.encode("latin-1")))


def _mount_static(app: FastAPI) -> None:
    STATIC_DIR.mkdir(parents=True, exist_ok=True)
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


#: Default app: loopback-safe, URL reader off, no auth (matching a local demo).
app = create_app()
