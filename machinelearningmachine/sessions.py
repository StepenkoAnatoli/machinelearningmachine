"""
Session persistence for MachineLearningMachine.

Saves the full mesh state (registered agents + message history) as JSON files
so users can store a conversation and continue it later — no export/import
file juggling required. Sessions are written to the user's home directory
(``~/.module_mesh/sessions``) so they survive repo updates and are never
committed to git. Override the location with the environment variable
``MACHINELEARNINGMACHINE_SESSIONS_DIR`` (alias ``MODULE_MESH_SESSIONS_DIR``),
which is also how tests point the store at a temp directory.

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

from .env import get as env_get

#: Environment variable that overrides the session storage directory.
#: Both ``MACHINELEARNINGMACHINE_SESSIONS_DIR`` and this short alias are honoured
#: (see :mod:`machinelearningmachine.env`); the constant keeps the legacy spelling
#: because tests and docs refer to it.
SESSIONS_ENV_VAR = "MODULE_MESH_SESSIONS_DIR"
SESSIONS_DIR_SUFFIX = "SESSIONS_DIR"

#: Default location: outside the repo, inside the user's home directory.
DEFAULT_SESSIONS_DIR = Path.home() / ".module_mesh" / "sessions"

#: Keep at most this many sessions on disk (oldest are pruned).
MAX_SESSIONS = 50

#: Hard safety cap per session file (~8 MB). Sessions larger than this get
#: their oldest messages trimmed so the file still saves.
MAX_SESSION_BYTES = 8 * 1024 * 1024

#: Only allow these characters in session IDs (prevents path traversal).
_SAFE_ID = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,63}$")

#: How much of a stored file the metadata is read from. The writer keeps a fixed
#: key order with all four metadata fields inside the first ~200 bytes, so a
#: listing never needs the transcript: 50 sessions used to mean parsing 78 MB of
#: JSON on every call to ``GET /api/sessions``.
META_HEAD_BYTES = 4096

#: The leading metadata of a document written by :func:`save_session`
#: (``json.dumps`` defaults: ``", "`` between fields, ``": "`` after each key).
#: Anything that does not match this shape - a hand-edited file, an older layout -
#: falls back to a full parse, so the fast path is an optimisation, never a
#: correctness assumption.
_CREATED_AT = re.compile(r'"created_at": (-?\d+(?:\.\d+)?)')

_HEAD_META = re.compile(
    r'^\{"id": "(?P<id>(?:[^"\\]|\\.)*)", "version": \d+, "name": "(?P<name>(?:[^"\\]|\\.)*)", '
    r'"created_at": -?\d+(?:\.\d+)?, "message_count": (?P<count>\d+)'
)

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
    override = env_get(SESSIONS_DIR_SUFFIX)
    directory = Path(override).expanduser() if override else DEFAULT_SESSIONS_DIR
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


#: A stored session's name prefix is its creation time, to the second.
_TIMESTAMPED_ID = re.compile(r"^(\d{8}-\d{6}(?:\.\d{1,6})?)")


def _prune_sort_key(stem: str, mtime: float) -> str:
    """
    The value pruning is ordered by: a fixed-width, time-sortable string.

    Names this module wrote carry their own timestamp; anything else falls back to
    the modification time, formatted the same way so the two compare sensibly.
    """
    match = _TIMESTAMPED_ID.match(stem)
    if match:
        return match.group(1)
    return time.strftime("%Y%m%d-%H%M%S", time.localtime(mtime))


def new_session_id() -> str:
    """
    A creation stamp to the microsecond plus a short random suffix, e.g.
    ``20260918-123456.482913-a1b2c3``.

    The timestamp is part of the name for a reason: pruning keeps the newest N
    files, and ordering by modification time inherits whatever precision the
    filesystem happens to have (FAT and some NFS mounts keep whole seconds). With
    several saves in one second, "newest" then means "whichever random suffix sorts
    last" - which can delete the session that was just saved.
    """
    now = time.time()
    stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(now))
    micro = int((now % 1) * 1_000_000)
    return f"{stamp}.{micro:06d}-{uuid.uuid4().hex[:6]}"


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

    # Key order is part of the format: list_sessions() reads metadata out of the
    # first kilobytes, so "message_count" has to be in the header, not derived
    # from the transcript body.
    payload = {
        "id": session_id,
        "version": 1,
        "name": clean_name,
        "created_at": saved_at,
        "message_count": len(messages),
        "agents": agents,
        "messages": messages,
        "extra": extra or {},
    }

    # If the session is huge, trim oldest messages until it fits the cap - and say
    # in the file itself what was dropped, so a trimmed transcript never reads as
    # the complete conversation.
    dropped_on_trim = 0
    data = json.dumps(payload, ensure_ascii=False)
    # The guard and the "cut == 0" branch both exist because halving a list of one
    # makes no progress: a single oversized message used to spin here forever, in
    # the request thread, with the file still unwritten.
    for _ in range(64):
        if len(data.encode("utf-8")) <= MAX_SESSION_BYTES or not payload["messages"]:
            break
        cut = len(payload["messages"]) // 2
        payload["messages"] = payload["messages"][cut:] if cut else []
        dropped_on_trim += cut or 1
        payload["message_count"] = len(payload["messages"])
        data = json.dumps(payload, ensure_ascii=False)
    else:
        if len(data.encode("utf-8")) > MAX_SESSION_BYTES:
            raise ValueError(
                "This session cannot be trimmed below the per-file size limit, because "
                "the saved agents and their prompts alone are that large. Remove some "
                "custom modules or shorten their system prompts, then save again."
            )
    if dropped_on_trim:
        payload["messages_dropped_on_save"] = dropped_on_trim
        payload["trimmed"] = True
        data = json.dumps(payload, ensure_ascii=False)

    _write_atomic(_path_for(session_id, namespace), data)
    _prune_old_sessions(namespace)
    return _meta(session_id, clean_name, len(payload["messages"]), saved_at)


def _write_atomic(path: Path, data: str) -> None:
    """
    Write a session so a reader sees either the old file or the whole new one.

    ``path.write_text(...)`` truncated the file before filling it in: a crash, an
    interrupted shutdown or a full disk left a half-written transcript that
    :func:`list_sessions` then skipped *silently* - which is how "my saved session
    disappeared" was implemented. Writing a temp file in the same directory and
    renaming it is the one mechanism that is atomic on every OS Python runs on.

    No fsync: the promise is "a process crash never corrupts a saved session",
    not "survives the machine losing power" - and a sync on every save would make
    the common case (a small transcript, a spinning disk) feel slow for nothing.
    """
    tmp = path.with_name(f"{path.stem}.json.{os.getpid()}.tmp")
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write(data)
        os.replace(tmp, path)
    except OSError as exc:
        try:
            tmp.unlink()
        except OSError:
            pass
        reason = getattr(exc, "strerror", "") or str(exc) or exc.__class__.__name__
        error = OSError(f"The session could not be written to {path.parent}: {reason}")
        #: The reason on its own, for callers that must tell the user *why* without
        #: echoing a server filesystem path (the dashboard's save endpoint).
        error.reason_detail = reason  # type: ignore[attr-defined]
        raise error from exc


def _meta(session_id: str, name: str, message_count: int, saved_at: float, trimmed: bool = False) -> Dict[str, Any]:
    """The list view's row. Kept tiny on purpose: no transcript reaches a listing."""
    meta: Dict[str, Any] = {
        "id": session_id,
        "name": name,
        "saved_at": saved_at,
        "message_count": message_count,
    }
    if trimmed:
        # Honest in the list, not only in the file: this transcript had its oldest
        # messages dropped to fit the size cap.
        meta["trimmed"] = True
    return meta


def _head_meta(path: Path) -> Optional[Dict[str, Any]]:
    """
    Read a stored session's metadata from the head of the file.

    Returns ``None`` when the prefix does not look like a document this module
    wrote, so the caller can fall back to the expensive, always-correct path.
    """
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            head = fh.read(META_HEAD_BYTES)
    except OSError:
        return None
    match = _HEAD_META.match(head)
    if not match:
        return None
    try:
        session_id = json.loads(f'"{match.group("id")}"')
        name = json.loads(f'"{match.group("name")}"')
    except (json.JSONDecodeError, ValueError):
        return None
    created = _CREATED_AT.search(head)
    if created is None:
        # The prefix ended mid-number (or the file was written by an older build).
        # Nothing is lost by asking the filesystem instead of the document.
        try:
            stamp = path.stat().st_mtime
        except OSError:
            return None
    else:
        stamp = float(created.group(1))
    return _meta(
        session_id,
        name or path.stem,
        int(match.group("count")),
        stamp,
        trimmed='"trimmed": true' in head,
    )


def list_sessions(namespace: Optional[str] = None) -> List[Dict[str, Any]]:
    """
    Return lightweight metadata for all stored sessions, newest first.

    Only the header of each file is read (see :func:`_head_meta`); the transcript
    itself is parsed on demand by :func:`get_session`.
    """
    results: List[Dict[str, Any]] = []
    for f in sessions_dir(namespace).glob("*.json"):
        meta = _head_meta(f)
        if meta is not None:
            results.append(meta)
            continue
        try:
            with open(f, "r", encoding="utf-8") as fh:
                d = json.load(fh)
            results.append(
                _meta(
                    d["id"],
                    d.get("name") or f.stem,
                    len(d.get("messages", [])),
                    float(d.get("created_at", f.stat().st_mtime)),
                    bool(d.get("trimmed")),
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


def _prune_old_sessions(namespace: Optional[str] = None, *, now: Optional[float] = None) -> None:
    """
    Keep at most MAX_SESSIONS files, deleting the oldest first.

    Also clears ``*.tmp`` left behind by a process that was killed between the
    write and the rename: harmless to read (nothing globs them) but they would
    otherwise accumulate in a directory the user can never be asked to clean.
    """
    directory = sessions_dir(namespace)
    try:
        moment = now if now is not None else time.time()
        for stale in directory.glob("*.json.*.tmp"):
            try:
                if moment - stale.stat().st_mtime > 3600:
                    stale.unlink()
            except OSError:
                pass
    except OSError:
        pass
    try:
        files = []
        for f in directory.glob("*.json"):
            if not (_SAFE_ID.match(f.stem) and f.suffix == ".json"):
                continue
            try:
                mtime = f.stat().st_mtime
            except OSError:
                continue
            files.append((_prune_sort_key(f.stem, mtime), f.name, f))
        # Newest first. The *name* leads, because it embeds the creation second
        # (see new_session_id); mtime only orders hand-written files. Sorting by
        # mtime alone is what made "the session you just saved" the deletion
        # candidate: on a filesystem with one-second granularity, two saves in
        # the same second were ordered by their random suffix.
        files.sort(reverse=True)
        for _, _, stale in files[MAX_SESSIONS:]:
            try:
                stale.unlink()
            except OSError:
                pass
    except OSError:
        pass
