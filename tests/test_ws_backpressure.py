"""
A browser is a display, not a dependency of a run.

The bug this file exists to keep dead: the WebSocket fan-out was ``await
ws.send_json(...)`` *inside* the message bus's listener, so a tab that stopped
reading (backgrounded, throttled, a laptop lid, a phone off Wi-Fi) blocked
``MessageBus.dispatch`` and the run with it - forever. Measured before the fix with
a socket whose ``send_json`` never returned: the dispatch had still not finished
after 8 seconds, and the message was already in the history.

The replacement is a bounded per-connection outbox
(:mod:`machinelearningmachine.server.feed`): publishing is ``put_nowait``, a pump
task does the waiting, a client that cannot keep up loses frames and is told so.
"""

import asyncio
import time

import pytest
from fastapi.testclient import TestClient

from machinelearningmachine.agents.providers import BaseLLMProvider
from machinelearningmachine.mesh import AgentMesh
from machinelearningmachine.server.app import create_app
from machinelearningmachine.server.config import ServerConfig
from machinelearningmachine.server.feed import ClientFeed
from machinelearningmachine.server.state import SessionState


class StalledSocket:
    """A peer that accepts no more bytes: its write never completes."""

    def __init__(self):
        self.blocked = asyncio.Event()
        self.writes = 0

    async def send_json(self, payload):
        self.writes += 1
        await self.blocked.wait()   # never set in these tests

    async def close(self, code=1000):
        self.code = code


class FastSocket:
    def __init__(self):
        self.sent = []

    async def send_json(self, payload):
        self.sent.append(payload)

    async def close(self, code=1000):
        self.closed = code


class QuietProvider(BaseLLMProvider):
    is_simulated = False
    label = "Quiet"
    fallback_to_mock = True

    async def generate(self, system_prompt, messages, agent_role, agent_name, task_context=None):
        return f"{agent_name} replied"


def _mesh_with_stalled_client():
    mesh = AgentMesh()
    for agent in mesh.agents.values():
        agent.provider = QuietProvider()
    state = SessionState(session_id="s-1", client_id="c-1", mesh=mesh, config=ServerConfig())
    socket = StalledSocket()
    feed = ClientFeed(socket, send_timeout=0.05).start()
    state.feeds.add(feed)
    # Exactly what the server wires up, minus the HTTP layer.
    async def on_bus_message(msg):
        state.publish({"type": "new_message", "message": msg.to_dict(), "run_id": state.active_run_id})

    mesh.on_message(on_bus_message)
    return mesh, state, socket, feed


async def test_a_stalled_tab_cannot_stall_the_run():
    mesh, state, socket, feed = _mesh_with_stalled_client()
    assert len(state.feeds) == 1
    started = time.monotonic()
    try:
        transcript = await asyncio.wait_for(mesh.run_pipeline("build a rate limiter"), timeout=5)
    except asyncio.TimeoutError:  # pragma: no cover - the failure mode under test
        pytest.fail("bus dispatch was blocked by a socket that is not reading")
    finally:
        feed.abort()
    assert len(transcript) == 4
    assert state.publish({"type": "after_abort"}) == 0, "a released feed is gone from the session"
    assert time.monotonic() - started < 5
    # The stalled client got *something* (the queue feeds it as fast as it can take
    # it) and the run never waited for it.
    assert socket.writes <= 1


async def test_frames_are_queued_not_awaited_by_the_producer():
    """publish() returns synchronously even while the pump is blocked mid-write."""
    socket = StalledSocket()
    feed = ClientFeed(socket, send_timeout=0.05).start()
    started = time.monotonic()
    for i in range(20):
        assert feed.publish({"type": "new_message", "i": i}) is True
    assert time.monotonic() - started < 2.0
    feed.abort()


async def test_overflow_drops_the_oldest_and_reports_a_gap():
    released = asyncio.Event()

    class SlowSocket:
        def __init__(self):
            self.sent = []

        async def send_json(self, payload):
            self.sent.append(payload)
            if not released.is_set():
                await released.wait()

        async def close(self, code=1000):
            pass

    socket = SlowSocket()
    feed = ClientFeed(socket, max_queue=8, send_timeout=1.0).start()
    for i in range(40):
        feed.publish({"type": "new_message", "i": i})
    assert feed.dropped >= 30, feed.dropped
    await asyncio.sleep(0)          # let the pump take the head and stall on it
    released.set()
    await _drain(feed, socket)

    assert any(f["type"] == "stream_gap" for f in socket.sent), "the tab must be told it missed frames"
    gap = next(f for f in socket.sent if f["type"] == "stream_gap")
    assert gap["dropped"] >= 30
    # Nothing lost is nothing *kept*: the newest frames survived the overflow.
    kept = [f["i"] for f in socket.sent if f["type"] == "new_message"]
    assert kept and max(kept) == 39
    feed.abort()


async def _drain(feed, socket, frames: int = 9, timeout: float = 2.0):
    """Wait for the backlog (plus the gap notice) to be written, or give up loudly."""
    deadline = time.monotonic() + timeout
    while len(socket.sent) < frames and time.monotonic() < deadline:
        await asyncio.sleep(0.01)
    assert len(socket.sent) >= frames, f"only {len(socket.sent)} frames were written"


def test_publish_after_abort_is_ignored_not_raised():
    feed = ClientFeed(FastSocket())
    feed.abort()
    assert feed.publish({"type": "x"}) is False
    assert feed.closed is True


def test_api_run_completes_with_a_saturated_feed():
    """
    The same guarantee through the real wiring: a session whose browser has stopped
    reading (queue full, frames being dropped) still gets a completed run, and the
    loss is visible on /api/status rather than silent.
    """
    app = create_app(ServerConfig(host="127.0.0.1"))
    with TestClient(app) as client:
        client.get("/api/agents")
        state = app.state.registry._states[client.cookies.get("mmm_session")]
        for agent in state.mesh.agents.values():
            agent.provider = QuietProvider()

        feed = ClientFeed(StalledSocket(), max_queue=8, send_timeout=30.0)  # never pumped
        state.feeds.add(feed)
        for i in range(8):
            feed.publish({"type": "filler", "i": i})

        started = time.monotonic()
        response = client.post("/api/run", json={"topology": "pipeline", "prompt": "build a rate limiter"})
        assert response.status_code == 200
        assert time.monotonic() - started < 5

        status = client.get("/api/status").json()
        assert status["connections"] == 1
        assert status["dropped_frames"] > 0, "a degraded client must be observable"
        feed.abort()


async def test_slow_client_is_closed_by_the_send_timeout():
    """A peer that stops mid-write is released, not waited on."""
    socket = StalledSocket()
    feed = ClientFeed(socket, send_timeout=0.05).start()
    feed.publish({"type": "new_message"})
    for _ in range(30):
        await asyncio.sleep(0.05)
        if feed.closed:
            break
    assert feed.closed is True, "the pump must give up on a socket that stalls"
    assert getattr(socket, "code", None) == 1011
    assert feed.publish({"type": "after"}) is False


async def test_aclose_flushes_a_final_frame_then_closes():
    socket = FastSocket()
    feed = ClientFeed(socket).start()
    feed.publish({"type": "new_message", "i": 1})
    await feed.aclose(final={"type": "session_released", "reason": "idle_timeout"}, code=4408)
    assert [f["type"] for f in socket.sent] == ["new_message", "session_released"]
    assert socket.closed == 4408
    assert feed.closed is True
