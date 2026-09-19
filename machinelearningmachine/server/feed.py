"""
Per-WebSocket outbox: the mesh must never wait for a browser.

The dashboard used to fan messages out with ``await ws.send_json(...)`` inside the
message bus's own listener. That put a display concern on the execution path: one
tab whose TCP buffer was full (a laptop lid closing, a throttled background tab, a
phone dropping off Wi-Fi) blocked ``MessageBus.dispatch`` and therefore the whole
run - for that session, indefinitely. Reproduced before this module existed: a
single stalled socket kept a two-turn dialogue from finishing after 8 seconds.

``ClientFeed`` decouples them. Publishing is a non-blocking ``put_nowait`` into a
bounded queue; a per-connection task drains it. A client that cannot keep up loses
frames and is *told* it lost frames (``{"type": "stream_gap", "dropped": N}``), so
the browser can re-sync from ``GET /api/history`` rather than silently showing an
incomplete transcript.

Nothing here decides policy; it only moves bytes and counts what it had to drop.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, Optional

logger = logging.getLogger("server.feed")

#: Frames buffered per connection before the oldest are dropped. A four-agent run
#: emits a handful of messages per turn, so this covers a run with room to spare.
DEFAULT_MAX_QUEUE = 128
#: How long one frame may take to leave before the peer is considered gone. A write
#: that blocks past this is a dead or hostile client; the run must not care.
DEFAULT_SEND_TIMEOUT = 10.0
#: Report a dropped-frame backlog to the browser only once it has drained this far,
#: so a briefly slow client is not told to re-fetch on every frame.
_GAP_NOTICE_WATERMARK = 0.5


class ClientFeed:
    """A bounded, non-blocking send queue for exactly one WebSocket connection."""

    __slots__ = (
        "_closed",
        "_dropped",
        "_gap_pending",
        "_max_queue",
        "_queue",
        "_send_timeout",
        "_task",
        "websocket",
    )

    def __init__(
        self,
        websocket: Any,
        *,
        max_queue: int = DEFAULT_MAX_QUEUE,
        send_timeout: float = DEFAULT_SEND_TIMEOUT,
    ) -> None:
        self.websocket = websocket
        self._max_queue = max(8, int(max_queue))
        self._send_timeout = float(send_timeout)
        self._queue: "asyncio.Queue[Optional[Dict[str, Any]]]" = asyncio.Queue(maxsize=self._max_queue)
        self._task: Optional[asyncio.Task] = None
        self._dropped = 0
        self._gap_pending = 0
        self._closed = False

    # -- producer side (called from the message bus) ----------------------
    def publish(self, payload: Dict[str, Any]) -> bool:
        """
        Queue ``payload`` for this connection. Never blocks, never raises.

        Returns ``False`` when the frame had to be dropped (or the feed is already
        closed), which is how a caller counts degraded clients without waiting.
        """
        if self._closed:
            return False
        try:
            self._queue.put_nowait(payload)
            return True
        except asyncio.QueueFull:
            pass
        # Make room by discarding the oldest frame: the newest message is the one
        # worth showing, and the browser re-syncs the gap anyway.
        try:
            self._queue.get_nowait()
            self._queue.task_done()
        except asyncio.QueueEmpty:  # pragma: no cover - racing with the pump
            pass
        self._dropped += 1
        self._gap_pending += 1
        try:
            self._queue.put_nowait(payload)
        except asyncio.QueueFull:  # pragma: no cover - only if the pump died
            self._dropped += 1
            return False
        return False

    # -- consumer side ----------------------------------------------------
    def start(self) -> "ClientFeed":
        """Attach the pump task. Call once, straight after construction."""
        if self._task is None and not self._closed:
            self._task = asyncio.create_task(self._pump(), name="mesh-ws-feed")
        return self

    async def _pump(self) -> None:
        try:
            while True:
                payload = await self._queue.get()
                try:
                    if payload is None:  # sentinel from aclose()
                        return
                    try:
                        await asyncio.wait_for(
                            self.websocket.send_json(payload), timeout=self._send_timeout
                        )
                    except asyncio.CancelledError:
                        raise
                    except Exception:
                        # A socket that errors, or stalls past the send timeout, is
                        # gone. Closing it also ends the handler's receive loop.
                        self._closed = True
                        await self._hard_close()
                        return
                finally:
                    self._queue.task_done()
                if self._gap_pending and self._queue.qsize() <= int(
                    self._max_queue * _GAP_NOTICE_WATERMARK
                ):
                    count, self._gap_pending = self._gap_pending, 0
                    try:
                        self._queue.put_nowait({"type": "stream_gap", "dropped": count})
                    except asyncio.QueueFull:  # pragma: no cover - refilled mid-drain
                        self._gap_pending = count
        except asyncio.CancelledError:  # pragma: no cover - shutdown path
            return
        except Exception:  # pragma: no cover - a pump must never crash the loop
            logger.warning("WebSocket feed crashed", exc_info=True)
            self._closed = True
            await self._hard_close()

    async def _hard_close(self) -> None:
        close = getattr(self.websocket, "close", None)
        if close is None:
            return
        try:
            await close(code=1011)
        except Exception:
            pass

    # -- lifecycle --------------------------------------------------------
    @property
    def dropped(self) -> int:
        """Frames this client could not keep up with, since it connected."""
        return self._dropped

    @property
    def closed(self) -> bool:
        return self._closed

    def abort(self) -> None:
        """
        Stop the pump without waiting for the peer.

        Used when the browser has already disconnected: nothing is queued for a
        socket that is gone, and waiting for a stalled write would keep the
        handler alive for no reason.
        """
        self._closed = True
        task, self._task = self._task, None
        if task is not None and not task.done():
            task.cancel()

    async def aclose(self, final: Optional[Dict[str, Any]] = None, *, code: int = 1000) -> None:
        """
        Flush what is already queued (plus an optional last frame), then stop.

        Best-effort by design: an unreachable browser must not turn into an
        exception inside the request that happened to evict it.
        """
        if self._closed:
            return
        self._closed = True
        if final is not None:
            try:
                self._queue.put_nowait(final)
            except asyncio.QueueFull:
                pass
        try:
            self._queue.put_nowait(None)
        except asyncio.QueueFull:
            pass
        task, self._task = self._task, None
        if task is not None and not task.done():
            try:
                await asyncio.wait_for(asyncio.shield(task), timeout=self._send_timeout + 1.0)
            except asyncio.CancelledError:  # pragma: no cover - outer shutdown
                task.cancel()
                raise
            except Exception:
                task.cancel()
        try:
            await self.websocket.close(code=code)
        except Exception:
            pass
