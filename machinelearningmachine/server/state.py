"""
Per-client server state: one :class:`SessionState` (and therefore one
:class:`~machinelearningmachine.mesh.AgentMesh`) per browser session.

Why this exists
---------------
The dashboard used to keep a single global ``AgentMesh`` for every request, so
anyone who could reach the port could add agents, run tasks, read and delete
saved sessions, or reconfigure providers for everyone else - even *with* a
shared bearer token, because every user would hold the same token and therefore
see the same state.

Here each browser gets its own mesh, agent roster, provider configuration,
rate-limit bucket and WebSocket set. The registry is bounded (LRU + idle TTL)
because every mesh holds agent memory and message history.

Cookies
-------
``mmm_session`` (host-only, HttpOnly, SameSite=Lax) identifies the live state.
``mmm_client`` (persistent) only groups *saved* session files into a per-client
folder, so transcripts survive a restart and never mix between profiles.
Neither cookie carries authorisation: when a token is configured, a session has
to log in again after every server restart, on purpose.
"""

from __future__ import annotations

import logging
import time
import uuid
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from ..mesh import AgentMesh
from .config import ServerConfig

logger = logging.getLogger("server.state")


def new_id() -> str:
    return uuid.uuid4().hex


#: Idle lifetime granted to a session that no cookie pins it to a browser: long
#: enough for a short scripted conversation, short enough that a cookie-less
#: flood cannot hold the registry hostage for hours.
EPHEMERAL_SESSION_TTL = 120.0


@dataclass
class SessionState:
    """Everything that belongs to exactly one browser session."""

    session_id: str
    client_id: str
    mesh: AgentMesh
    config: ServerConfig
    created_at: float = field(default_factory=time.time)
    last_access: float = field(default_factory=time.time)
    authenticated: bool = False
    #: Provider/API-key configuration. In-memory only, never written to disk,
    #: and scoped to this session - keys are not shared between users.
    provider_config: Dict[str, Any] = field(default_factory=dict)
    #: Raw keys live here for the process lifetime only (never logged, never
    #: persisted). Kept out of ``provider_config`` so it cannot leak via /api/*.
    api_keys: Dict[str, str] = field(default_factory=dict)
    websockets: Set[Any] = field(default_factory=set)
    last_run_time: float = 0.0
    last_url_read_time: float = 0.0
    #: Messages whose provider failed during the most recent run.
    last_run_warnings: List[str] = field(default_factory=list)
    #: True for a session created on behalf of a caller that keeps no cookies
    #: (see :meth:`SessionRegistry.create`). Such a session expires early.
    ephemeral: bool = False

    @property
    def max_prompt_length(self) -> int:
        return self.mesh.MAX_PROMPT_LENGTH

    def idle_seconds(self, now: Optional[float] = None) -> float:
        return (now if now is not None else time.time()) - self.last_access

    def effective_ttl(self, ttl: float) -> float:
        """The idle lifetime that applies to this session."""
        if ttl <= 0:
            return ttl
        return min(ttl, EPHEMERAL_SESSION_TTL) if self.ephemeral else ttl

    def is_stale(self, ttl: float, now: Optional[float] = None) -> bool:
        effective = self.effective_ttl(ttl)
        if effective <= 0:
            return False
        return self.idle_seconds(now) > effective


class LoginThrottle:
    """
    Per-client failed-sign-in counter: ``max_failures`` wrong tokens lock the
    caller out for ``lockout_seconds``.

    It is keyed by the *client id* rather than by :class:`SessionState` on
    purpose. If the counter lived inside the session, an attacker could reset it
    at will by simply discarding cookies - and every junk attempt would allocate
    a real session (a mesh, a history buffer) that then evicts legitimate ones
    from the bounded registry. Keys are bounded, and entries expire.
    """

    def __init__(self, max_failures: int = 5, lockout_seconds: float = 60.0, max_keys: int = 4096) -> None:
        self.max_failures = max(1, int(max_failures))
        self.lockout_seconds = float(lockout_seconds)
        self.max_keys = max(16, int(max_keys))
        self._attempts: "OrderedDict[str, Tuple[int, float]]" = OrderedDict()

    def locked_for(self, key: str, now: Optional[float] = None) -> float:
        """Seconds left on the lockout for ``key`` (0.0 when it may try again)."""
        moment = now if now is not None else time.time()
        entry = self._attempts.get(key)
        if not entry:
            return 0.0
        failures, locked_until = entry
        if locked_until > moment:
            return locked_until - moment
        if failures >= self.max_failures and locked_until and moment - locked_until > self.lockout_seconds * 4:
            # Forget long-dead counters instead of growing forever.
            self._attempts.pop(key, None)
        return 0.0

    def record_failure(self, key: str, now: Optional[float] = None) -> int:
        moment = now if now is not None else time.time()
        failures, _ = self._attempts.get(key, (0, 0.0))
        failures += 1
        locked_until = moment + self.lockout_seconds if failures >= self.max_failures else 0.0
        self._attempts[key] = (failures, locked_until)
        self._attempts.move_to_end(key)
        while len(self._attempts) > self.max_keys:
            self._attempts.popitem(last=False)
        return failures

    def reset(self, key: str) -> None:
        self._attempts.pop(key, None)

    def size(self) -> int:
        return len(self._attempts)


class SessionRegistry:
    """
    Bounded LRU registry of :class:`SessionState` objects.

    ``on_evict`` is called with the state being dropped so the app can close its
    WebSocket connections and detach its message-bus listener.
    """

    def __init__(
        self,
        *,
        max_sessions: int = 32,
        idle_ttl: float = 6 * 3600,
        mesh_factory: Callable[[ServerConfig], AgentMesh] | None = None,
        config: ServerConfig,
    ) -> None:
        self.max_sessions = max(1, int(max_sessions))
        self.idle_ttl = float(idle_ttl)
        self.config = config
        self._mesh_factory = mesh_factory or (lambda cfg: AgentMesh())
        self._states: "OrderedDict[str, SessionState]" = OrderedDict()
        self.on_evict: Optional[Callable[[SessionState], Any]] = None

    # -- lookup / creation -------------------------------------------------
    def create(self, client_id: Optional[str] = None, *, ephemeral: bool = False) -> SessionState:
        """Create a state, evicting the least recently used one when full."""
        state = SessionState(
            session_id=new_id(),
            client_id=client_id or new_id(),
            mesh=self._mesh_factory(self.config),
            config=self.config,
            ephemeral=ephemeral,
        )
        self._states[state.session_id] = state
        self._states.move_to_end(state.session_id)
        self._evict_over_capacity()
        return state

    def get(self, session_id: Optional[str]) -> Optional[SessionState]:
        """Return a live state (touching it), or None. Expired entries are dropped."""
        if not session_id:
            return None
        state = self._states.get(session_id)
        if state is None:
            return None
        if state.is_stale(self.idle_ttl):
            self.drop(session_id)
            return None
        self.touch(state)
        return state

    def touch(self, state: SessionState) -> None:
        state.last_access = time.time()
        if state.session_id in self._states:
            self._states.move_to_end(state.session_id)

    # -- removal -----------------------------------------------------------
    def drop(self, session_id: str) -> bool:
        state = self._states.pop(session_id, None)
        if state is None:
            return False
        self._dispose(state)
        return True

    def sweep_expired(self) -> List[SessionState]:
        """Drop every idle-expired state; returns what was removed."""
        removed: List[SessionState] = []
        now = time.time()
        for sid in list(self._states.keys()):
            state = self._states.get(sid)
            if state is None:
                continue
            if state.is_stale(self.idle_ttl, now):
                removed.append(self._states.pop(sid))
                self._dispose(removed[-1])
        return removed

    def clear(self) -> None:
        for sid in list(self._states.keys()):
            state = self._states.pop(sid, None)
            if state is not None:
                self._dispose(state)

    def _evict_over_capacity(self) -> None:
        """
        Drop entries until the cap holds, preferring the ephemeral ones.

        A session no cookie pins to a browser is the cheapest thing to lose: its
        owner can simply make another request, whereas evicting a browser's mesh
        silently discards that user's agents, transcript and keys.
        """
        while len(self._states) > self.max_sessions:
            victim_id = next((sid for sid, st in self._states.items() if st.ephemeral), None)
            if victim_id is None:
                victim_id = next(iter(self._states))  # plain LRU order
            state = self._states.pop(victim_id)
            logger.info(
                "Evicting mesh session %s (%s, max_sessions=%d)",
                victim_id[:8], "ephemeral" if state.ephemeral else "idle", self.max_sessions,
            )
            self._dispose(state)

    def _dispose(self, state: SessionState) -> None:
        state.provider_config = {"discarded": True}
        state.api_keys = {}
        if self.on_evict:
            try:
                self.on_evict(state)
            except Exception:  # never let cleanup break a request
                logger.warning("Eviction cleanup failed for session %s", state.session_id[:8], exc_info=True)

    # -- introspection -----------------------------------------------------
    def stats(self) -> Dict[str, Any]:
        now = time.time()
        return {
            "live_sessions": len(self._states),
            "max_sessions": self.max_sessions,
            "idle_ttl_seconds": self.idle_ttl,
            "oldest_idle_seconds": max(
                (round(s.idle_seconds(now), 1) for s in self._states.values()), default=0.0
            ),
        }

    def __len__(self) -> int:
        return len(self._states)
