"""Task-local cooperative cancellation; no flag is shared between browser runs."""
import asyncio
from contextvars import ContextVar
from typing import Optional

cancel_event: ContextVar[Optional[asyncio.Event]] = ContextVar("cancel_event", default=None)


class RunCancelled(Exception):
    """A requested stop reached an agent boundary (not task cancellation)."""


def check_cancelled() -> None:
    event = cancel_event.get()
    if event is not None and event.is_set():
        raise RunCancelled()
