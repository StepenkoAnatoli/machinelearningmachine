"""
Server configuration and the network-exposure policy.

The dashboard keeps mutable state (agents, API keys, transcripts) in memory and
can execute multi-agent runs, so it is a *local tool by default*: it binds to
loopback, and binding anywhere else is a deliberate act that requires both
``--allow-public`` and a non-empty ``--auth-token``.

Nothing here decides policy on its own - :func:`validate_bind_policy` returns
the reasons a launch must be refused and the CLI is what enforces it.
"""

from __future__ import annotations

import hmac
import os
from dataclasses import dataclass, field
from typing import Iterable, List, Optional, Tuple

#: Hosts that only the machine itself can reach.
LOOPBACK_HOSTS: Tuple[str, ...] = ("127.0.0.1", "localhost", "::1")

AUTH_TOKEN_ENV_VAR = "MACHINELEARNINGMACHINE_AUTH_TOKEN"  # noqa: S105  - a variable name, not a value
MIN_TOKEN_LENGTH = 16

SESSION_COOKIE_NAME = "mmm_session"
CLIENT_COOKIE_NAME = "mmm_client"

#: Default cap on simultaneously live per-browser meshes, and how long an idle
#: one is kept before it (and its WebSocket clients) are released.
DEFAULT_MAX_SESSIONS = 32
DEFAULT_SESSION_IDLE_TTL = 6 * 60 * 60


def is_loopback_host(host: str) -> bool:
    """True for addresses that cannot be reached from another machine."""
    h = (host or "").strip().lower().strip("[]")
    if h in LOOPBACK_HOSTS:
        return True
    # 127.0.0.0/8 is loopback in its entirety, not just 127.0.0.1.
    if h.startswith("127."):
        return True
    return False


@dataclass
class ServerConfig:
    """Runtime knobs for the FastAPI app. Constructed by the CLI or ``create_app``."""

    #: Shared secret that every API/WebSocket caller must present. ``None``
    #: means no authentication - only ever valid on a loopback bind.
    auth_token: Optional[str] = None
    #: ``/api/read/url`` fetches a user-supplied URL, so it is off unless the
    #: operator opts in explicitly (see machinelearningmachine/netguard.py).
    enable_url_reader: bool = False
    #: Whether the operator acknowledged that the bind is network-reachable.
    allow_public: bool = False
    #: Live providers that fail fall back to the simulator *and label the
    #: message*. Set False to make provider failures fail the run instead.
    fallback_to_mock: bool = True
    max_sessions: int = DEFAULT_MAX_SESSIONS
    session_idle_ttl: float = DEFAULT_SESSION_IDLE_TTL
    #: ``None`` = decide automatically (secure cookies when not on loopback).
    secure_cookies: Optional[bool] = None
    #: Extra origins allowed to call the API. Empty (the default) means the
    #: app is same-origin only, so no CORS headers are emitted at all.
    allow_origins: Tuple[str, ...] = field(default_factory=tuple)
    #: Bound interface, used for the cookie/CSRF decisions and the banner.
    host: str = "127.0.0.1"
    port: int = 8000
    #: Operator allowlist forwarded to :mod:`machinelearningmachine.netguard`.
    url_allowlist: Tuple[str, ...] = field(default_factory=tuple)

    @property
    def require_auth(self) -> bool:
        return bool(self.auth_token)

    @property
    def on_loopback(self) -> bool:
        return is_loopback_host(self.host)

    def cookies_should_be_secure(self) -> bool:
        if self.secure_cookies is not None:
            return self.secure_cookies
        # Loopback http:// is fine without Secure; anything else should be https.
        return not self.on_loopback

    def check_token(self, candidate: Optional[str]) -> bool:
        """
        Constant-time token comparison.

        Returns False when no token is configured *and* is compared against a
        None candidate, so callers can use it for both modes.
        """
        if not self.require_auth:
            return True
        if candidate is None:
            return False
        return hmac.compare_digest(str(candidate), str(self.auth_token))


def validate_bind_policy(
    host: str,
    *,
    allow_public: bool = False,
    auth_token: Optional[str] = None,
    port: int = 8000,
) -> List[str]:
    """
    Return a list of reasons why this launch must be refused (empty = allowed).

    Kept as a pure function so the rule is testable and so every entry point
    (CLI, ``python -m``, launch scripts) refuses the same way.
    """
    errors: List[str] = []
    token = (auth_token or os.environ.get(AUTH_TOKEN_ENV_VAR) or "").strip()

    if not is_loopback_host(host):
        if not allow_public:
            errors.append(
                f"Refusing to listen on {host}:{port}: that is reachable from other machines, "
                "and the dashboard can add agents, run tasks, change API keys, load and delete "
                "saved sessions, and (with --enable-url-reader) make outbound requests.\n"
                "Use --host 127.0.0.1 for local use, or pass --allow-public together with "
                "--auth-token <long-random-string> if you really mean it."
            )
        elif not token:
            errors.append(
                "--allow-public requires --auth-token (or "
                f"{AUTH_TOKEN_ENV_VAR}); a network-reachable mesh without authentication lets "
                "anyone who can open the port change your agents, keys and transcripts."
            )
    if token and len(token) < MIN_TOKEN_LENGTH:
        errors.append(
            f"The auth token is too short ({len(token)} characters); use at least "
            f"{MIN_TOKEN_LENGTH}, ideally 32+ random ones (e.g. `python -c \"import secrets;print(secrets.token_urlsafe(32))\"`)."
        )
    if not (1 <= int(port) <= 65535):
        errors.append(f"Port {port} is out of range (1-65535).")
    return errors


def normalize_origins(origins: Optional[Iterable[str]]) -> Tuple[str, ...]:
    """Clean up user-supplied CORS origins; wildcard origins are rejected."""
    cleaned: List[str] = []
    for origin in origins or ():
        origin = str(origin).strip().rstrip("/")
        if not origin or origin == "*":
            continue
        cleaned.append(origin)
    return tuple(dict.fromkeys(cleaned))
