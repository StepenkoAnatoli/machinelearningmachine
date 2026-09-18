"""
Session persistence for MachineLearningMachine.

Saves the full mesh state (registered agents + message history) as JSON files
so users can store a conversation and continue it later — no export/import
file juggling required. Sessions are written to the user's home directory
(``~/.module_mesh/sessions``) so they survive repo updates and are never
committed to git. Override the location with the environment variable
``MODULE_MESH_SESSIONS_DIR`` (handy for tests).

User-centered design:
- Human-friendly session names, sanitized safely for filenames
- Oldest sessions are pruned automatically (bounded storage)
- Defensive JSON handling: a corrupt file never crashes the app
- Per-client namespaces, so one user can never list or load another's files
"""

from __future__ import annotations

import json
import os
import re
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

#: Environment variable that overrides the session storage directory.
SESSIONS_ENV_VAR = "MODULE_MESH_SESSIONS_DIR"

#: Default location: outside the repo, inside the user's home directory.
DEFAULT_SESSIONS_DIR = Path.home() / ".module_mesh" / "sessions"

#: Keep at most this many sessions on disk (oldest are pruned).
MAX_SESSIONS = 50

#: Hard safety cap per session file (~8 MB). Sessions larger than this get
#: their oldest messages trimmed so the file still saves.
MAX_SESSION_BYTES = 8 * 1024 * 1024

#: Only allow these characters in session IDs (prevents path traversal).
_SAFE_ID = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,63}$")

#: Saved sessions can be split into per-client sub-directories ("namespaces"),
#: so a multi-user server never lists or loads somebody else's transcripts.
_SAFE_NAMESPACE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,63}$")


def sanitize_namespace(namespace: Optional[str]) -> Optional[str]:
    """
    Validate a namespace (per-client sub-folder).

    Anything that is not a plain id - ``..``, absolute paths, dots and slashes
    of any kind - collapses to ``None``, i.e. the shared root folder. The
    dashboard passes the browser's persistent client id here.
    """
    if not namespace:
        return None
    candidate = str(namespace).strip()
    if not _SAFE_NAMESPACE.match(candidate) or candidate in (".", ".."):
        return None
    return candidate


def sessions_dir(namespace: Optional[str] = None) -> Path:
    """
    Return (and create) the directory where sessions are stored.

    ``namespace`` selects a per-client sub-folder (see :func:`sanitize_namespace`);
    ``None`` means the shared root, which is what a single-user local install uses.
    """
    env = os.environ.get(SESSIONS_ENV_VAR, "").strip()
    directory = Path(env).expanduser() if env else DEFAULT_SESSIONS_DIR
    namespace = sanitize_namespace(namespace)
    if namespace:
        directory = directory / namespace
    try:
        directory.mkdir(parents=True, exist_ok=True)
    except OSError:
        # Fall back to a temp dir we definitely can write to.
        import tempfile

        directory = Path(tempfile.gettempdir()) / "module_mesh_sessions"
        directory.mkdir(parents=True, exist_ok=True)
    return directory


def _path_for(session_id: str, namespace: Optional[str] = None) -> Path:
    return sessions_dir(namespace) / f"{session_id}.json"


def new_session_id() -> str:
    """Timestamp + short random suffix, e.g. ``20260918-123456-a1b2c3``."""
    return f"{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"


def _sanitize_name(name: Optional[str]) -> str:
    """Turn user input into a safe, friendly session name."""
    name = (name or "").strip()
    if not name:
        name = f"Session {time.strftime('%Y-%m-%d %H:%M')}"
    # Remove characters that are unsafe in filenames / confusing in lists.
    name = re.sub(r"[\\/:*?\"<>|\r\n\t]+", " ", name)
    name = re.sub(r"\s{2,}", " ", name).strip()
    if len(name) > 120:
        name = name[:117].rstrip() + "..."
    return name


def _meta(session_id: str, name: str, message_count: int, saved_at: float) -> Dict[str, Any]:
    return {
        "id": session_id,
        "name": name,
        "saved_at": saved_at,
        "message_count": message_count,
    }


def save_session(
    name: Optional[str],
    agents: List[Dict[str, Any]],
    messages: List[Dict[str, Any]],
    extra: Optional[Dict[str, Any]] = None,
    namespace: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Persist the current mesh state and return the session's metadata.

    ``agents``    - list of agent dicts (see ``AgentMesh.list_agents``)
    ``messages``  - list of message dicts (see ``Message.to_dict``)
    ``namespace`` - per-client sub-folder, so sessions never cross users
    """
    if not isinstance(agents, list) or not isinstance(messages, list):
        raise ValueError("agents and messages must be lists")

    clean_name = _sanitize_name(name)
    session_id = new_session_id()
    saved_at = time.time()

    payload = {
        "id": session_id,
        "version": 1,
        "name": clean_name,
        "created_at": saved_at,
        "agents": agents,
        "messages": messages,
        "extra": extra or {},
    }

    # If the session is huge, trim oldest messages until it fits the cap.
    data = json.dumps(payload, ensure_ascii=False)
    while len(data.encode("utf-8")) > MAX_SESSION_BYTES and payload["messages"]:
        payload["messages"] = payload["messages"][len(payload["messages"]) // 2:]
        data = json.dumps(payload, ensure_ascii=False)

    path = _path_for(session_id, namespace)
    path.write_text(data, encoding="utf-8")
    _prune_old_sessions(namespace)
    return _meta(session_id, clean_name, len(payload["messages"]), saved_at)


def list_sessions(namespace: Optional[str] = None) -> List[Dict[str, Any]]:
    """Return lightweight metadata for all stored sessions, newest first."""
    results: List[Dict[str, Any]] = []
    for f in sessions_dir(namespace).glob("*.json"):
        try:
            with open(f, "r", encoding="utf-8") as fh:
                d = json.load(fh)
            results.append(
                _meta(
                    d["id"],
                    d.get("name") or f.stem,
                    len(d.get("messages", [])),
                    float(d.get("created_at", f.stat().st_mtime)),
                )
            )
        except Exception:
            # Corrupt / partial file - skip it instead of crashing the UI.
            continue
    results.sort(key=lambda s: s["saved_at"], reverse=True)
    return results[:MAX_SESSIONS]


def get_session(session_id: str, namespace: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Return the full session document, or None if it doesn't exist."""
    if not session_id or not _SAFE_ID.match(session_id):
        return None
    path = _path_for(session_id, namespace)
    if not path.is_file():
        return None
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if not isinstance(data, dict) or "id" not in data:
            return None
        data.setdefault("agents", [])
        data.setdefault("messages", [])
        data.setdefault("extra", {})
        return data
    except Exception:
        return None


def delete_session(session_id: str, namespace: Optional[str] = None) -> bool:
    """Delete a stored session. Returns True when a file was removed."""
    if not session_id or not _SAFE_ID.match(session_id):
        return False
    path = _path_for(session_id, namespace)
    try:
        if path.is_file():
            path.unlink()
            return True
    except OSError:
        return False
    return False


def _prune_old_sessions(namespace: Optional[str] = None) -> None:
    """Keep at most MAX_SESSIONS files, deleting the oldest first."""
    try:
        files = [
            (f.stat().st_mtime, f.name, f)
            for f in sessions_dir(namespace).glob("*.json")
            if _SAFE_ID.match(f.stem)
        ]
        # Newest first; filename is a deterministic tie-breaker when
        # filesystem timestamps share the same second.
        files.sort(reverse=True)
        for _, _, stale in files[MAX_SESSIONS:]:
            try:
                stale.unlink()
            except OSError:
                pass
    except OSError:
        pass
