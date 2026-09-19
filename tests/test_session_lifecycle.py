"""
The idle-session TTL has to be *executed*, not just compared against.

``SessionRegistry.sweep_expired`` existed and was never called: expiry was checked
only when a session made another request. A browser tab that goes quiet - which is
what "idle" means, and what every abandoned dashboard tab does - keeps its mesh,
its transcript, its provider configuration and its open WebSocket until the
registry happens to fill up. SECURITY.md's "Idle sessions are released after
``--session-ttl`` minutes" was therefore only true for sessions that came back.

These tests pin the reaper: the interval it runs on, the state it releases, the
sockets it closes, and the reason it tells the browser.
"""

import time

from fastapi.testclient import TestClient

from machinelearningmachine.server.app import create_app
from machinelearningmachine.server.config import ServerConfig
from machinelearningmachine.server.feed import ClientFeed
from machinelearningmachine.server.state import EPHEMERAL_SESSION_TTL, SessionRegistry


class RecordingSocket:
    def __init__(self):
        self.sent = []
        self.closed = None

    async def send_json(self, payload):
        self.sent.append(payload)

    async def close(self, code=1000):
        self.closed = code


# ------------------------------------------------------------------ the interval

def test_sweep_interval_is_derived_from_the_ttl():
    # A six-hour TTL does not need a check every five seconds...
    assert SessionRegistry(max_sessions=4, idle_ttl=6 * 3600, config=ServerConfig()).sweep_interval_seconds() == 60.0
    # ...but a one-minute one must not wait a whole minute.
    assert SessionRegistry(max_sessions=4, idle_ttl=60, config=ServerConfig()).sweep_interval_seconds() == 15.0


def test_sweep_interval_never_becomes_a_hot_loop():
    registry = SessionRegistry(max_sessions=4, idle_ttl=1.0, config=ServerConfig(session_idle_ttl=1.0))
    assert registry.sweep_interval_seconds() == 5.0


def test_disabled_ttl_means_no_reaper():
    registry = SessionRegistry(max_sessions=4, idle_ttl=0, config=ServerConfig(session_idle_ttl=0))
    assert registry.sweep_interval_seconds() == 0.0


def test_sweep_interval_can_be_overridden_for_tests():
    config = ServerConfig(session_idle_ttl=3600, session_sweep_interval=0.05)
    assert SessionRegistry(max_sessions=4, idle_ttl=config.session_idle_ttl, config=config).sweep_interval_seconds() == 0.05


# ------------------------------------------------------------------- the sweep

def test_sweep_expired_returns_and_disposes_only_the_stale_ones():
    config = ServerConfig(session_idle_ttl=3600)
    registry = SessionRegistry(max_sessions=4, idle_ttl=1.0, config=config)
    stale = registry.create("client-a")
    fresh = registry.create("client-b")
    stale.last_access = time.time() - 30

    removed = registry.sweep_expired("idle_timeout")
    assert [s.session_id for s in removed] == [stale.session_id]
    assert stale.evict_reason == "idle_timeout", "the state must carry why it was released"
    assert registry.get(fresh.session_id) is fresh


def test_capacity_eviction_records_why_and_prefers_ephemeral():
    config = ServerConfig(session_idle_ttl=3600)
    registry = SessionRegistry(max_sessions=2, idle_ttl=3600, config=config)
    seen = []
    registry.on_evict = lambda state: seen.append((state.client_id, state.evict_reason))

    browser = registry.create("browser")
    script_a = registry.create("script", ephemeral=True)
    script_b = registry.create("script2", ephemeral=True)

    assert seen == [("script", "capacity")], "the unpinned session goes first"
    assert registry.get(browser.session_id) is browser
    assert registry.get(script_a.session_id) is None
    assert registry.get(script_b.session_id) is script_b


def test_ephemeral_sessions_expire_early_even_with_a_long_ttl():
    config = ServerConfig(session_idle_ttl=3600)
    registry = SessionRegistry(max_sessions=4, idle_ttl=3600, config=config)
    script = registry.create("script", ephemeral=True)
    browser = registry.create("browser")
    script.last_access = time.time() - (EPHEMERAL_SESSION_TTL + 1)

    removed = registry.sweep_expired("idle_timeout")
    assert [s.session_id for s in removed] == [script.session_id]
    assert browser.is_stale(3600) is False


async def test_feeds_are_closed_when_a_session_is_disposed():
    config = ServerConfig(session_idle_ttl=3600)
    registry = SessionRegistry(max_sessions=2, idle_ttl=3600, config=config)
    state = registry.create("browser")
    socket = RecordingSocket()
    feed = ClientFeed(socket).start()
    state.feeds.add(feed)

    await state.close_feeds(final={"type": "session_released", "reason": "idle_timeout"}, code=4408)
    assert socket.sent == [{"type": "session_released", "reason": "idle_timeout"}]
    assert socket.closed == 4408
    assert state.feeds == set()

    # A second close is a no-op rather than an error (eviction and a disconnect can
    # race for the same socket).
    assert await state.close_feeds() == 0


def test_the_reaper_task_reclaims_a_quiet_browser_with_a_live_socket():
    """
    End-to-end through the real app: a tab that connects and then says nothing is
    released by the background reaper, and its socket is told why.
    """
    config = ServerConfig(host="127.0.0.1", session_idle_ttl=0.2, session_sweep_interval=0.05)
    app = create_app(config)
    with TestClient(app) as client:
        # The session cookie comes from an HTTP response (a WebSocket handshake
        # cannot set one), which is also how a real tab gets its session.
        client.get("/api/agents")
        with client.websocket_connect("/ws") as ws:
            init = ws.receive_json()
            assert init["type"] == "init"
            state = app.state.registry._states[client.cookies.get("mmm_session")]
            assert state is not None
            assert len(state.feeds) == 1

            # No further HTTP request - that is the point. The reaper must notice.
            deadline = time.monotonic() + 5
            while app.state.registry._states and time.monotonic() < deadline:
                time.sleep(0.05)

            assert len(app.state.registry) == 0, "the idle session was never reclaimed"

            # The tab is told *why* it lost its mesh, then the socket closes.
            frame = ws.receive_json()
            assert frame["type"] == "session_released"
            assert frame["reason"] == "idle_timeout"
            assert "Reload" in frame["detail"]

        # And the next request is served by a fresh session, not a half-dead one.
        assert client.get("/api/agents").status_code == 200
        assert client.get("/api/history").json() == []


def test_the_reaper_is_stopped_with_the_app():
    app = create_app(ServerConfig(host="127.0.0.1", session_sweep_interval=0.05))
    with TestClient(app):
        task = app.state.reaper
        assert task is not None and not task.done()
    assert task.done(), "the reaper must not outlive the server"


def test_no_reaper_when_the_ttl_is_disabled():
    app = create_app(ServerConfig(host="127.0.0.1", session_idle_ttl=0))
    with TestClient(app):
        assert app.state.reaper is None
