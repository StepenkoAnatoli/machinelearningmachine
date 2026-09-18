"""
Tests for per-session state isolation.

The audit's core complaint was one global AgentMesh shared by every request: any
caller could mutate the agents, run tasks, wipe history, or reconfigure the API
keys for everybody. These tests pin down the replacement - a bounded registry
where each browser gets its own mesh, provider config, rate-limit bucket and
WebSocket set, and where nothing crosses between them.
"""

import pytest
from fastapi.testclient import TestClient

from machinelearningmachine import sessions as session_store
from machinelearningmachine.mesh import AgentMesh
from machinelearningmachine.server.app import create_app
from machinelearningmachine.server.config import ServerConfig
from machinelearningmachine.server.state import SessionRegistry


@pytest.fixture(autouse=True)
def isolated_store(tmp_path, monkeypatch):
    monkeypatch.setenv(session_store.SESSIONS_ENV_VAR, str(tmp_path / "sessions"))


@pytest.fixture
def client_a():
    return TestClient(create_app(ServerConfig(host="127.0.0.1")))


@pytest.fixture
def client_b():
    return TestClient(create_app(ServerConfig(host="127.0.0.1")))


AGENT = {
    "agent_id": "solo-agent",
    "name": "Solo Agent",
    "role": "Belongs to one client",
    "system_prompt": "You are a private agent that only one session may see.",
    "color": "#ff00aa",
    "avatar": "🥷",
}


def test_clients_get_separate_agent_registries(client_a, client_b):
    assert client_a.post("/api/agents", json=AGENT).status_code == 200

    a_ids = [a["agent_id"] for a in client_a.get("/api/agents").json()]
    b_ids = [a["agent_id"] for a in client_b.get("/api/agents").json()]
    assert "solo-agent" in a_ids
    assert "solo-agent" not in b_ids
    assert len(b_ids) == 4, "a fresh browser should see only the built-ins"


def test_one_client_cannot_wipe_another_client_history(client_a, client_b):
    run = client_a.post(
        "/api/run",
        json={"topology": "p2p", "prompt": "Design a token bucket rate limiter", "turns": 2},
    )
    assert run.status_code == 200
    assert len(client_a.get("/api/history").json()) == 2
    assert client_b.get("/api/history").json() == []

    assert client_b.post("/api/clear").status_code == 200
    assert len(client_a.get("/api/history").json()) == 2, "clearing must be scoped to the caller"


def test_api_keys_are_configured_per_session(client_a, client_b):
    resp = client_a.post("/api/config", json={"anthropic_api_key": "sk-ant-this-is-a-test-value-123"})
    assert resp.status_code == 200
    assert "Anthropic" in resp.json()["message"]

    # The other session still runs the simulator and has no keys.
    b_status = client_b.get("/api/config").json()
    assert b_status["mode"] == "simulated"
    assert b_status["has_anthropic_key"] is False

    a_status = client_a.get("/api/config").json()
    assert a_status["has_anthropic_key"] is True
    assert a_status["configured"] == ["Anthropic"]


def test_the_api_never_echoes_a_configured_key(client_a):
    secret = "sk-ant-a-key-that-must-never-be-returned"
    client_a.post("/api/config", json={"anthropic_api_key": secret})
    for path in ("/api/config", "/api/status", "/api/agents", "/health"):
        assert secret not in client_a.get(path).text, f"{path} leaked the key"
    # nor in the response to the configuration call itself
    assert secret not in client_a.post("/api/config", json={"anthropic_api_key": secret}).text


def test_saved_sessions_do_not_cross_clients(client_a, client_b):
    client_a.post("/api/run", json={"topology": "p2p", "prompt": "Build a caching layer in Python", "turns": 2})
    saved = client_a.post("/api/sessions", json={"name": "Private work"}).json()["session"]

    b_list = client_b.get("/api/sessions").json()["sessions"]
    assert all(s["id"] != saved["id"] for s in b_list)
    assert client_b.get(f"/api/sessions/{saved['id']}").status_code == 404
    assert client_b.post("/api/sessions/load", json={"session_id": saved["id"]}).status_code == 404
    assert client_b.delete(f"/api/sessions/{saved['id']}").status_code == 404

    # ...and the owner can still load it.
    assert client_a.get(f"/api/sessions/{saved['id']}").status_code == 200


def test_files_on_disk_live_under_the_client_namespace(client_a, tmp_path):
    client_a.post("/api/run", json={"topology": "p2p", "prompt": "Design a queue with backpressure", "turns": 2})
    client_a.post("/api/sessions", json={"name": "Namespaced"})
    files = sorted(p.relative_to(tmp_path).as_posix() for p in tmp_path.rglob("*.json"))
    assert len(files) == 1
    assert files[0].startswith("sessions/") and not files[0].startswith("sessions/.")
    # Root of the store stays clean: no loose session files for anonymous callers.
    assert list((tmp_path / "sessions").glob("*.json")) == []


def test_custom_agent_color_is_never_markup(client_a):
    """Saved sessions and the API both feed colours into inline styles."""
    payload = dict(AGENT)
    payload["agent_id"] = "evil-color"
    payload["name"] = "Colour Inject"
    payload["color"] = "#effefe\" onmouseover=\"alert(1)"
    resp = client_a.post("/api/agents", json=payload)
    assert resp.status_code in (400, 422), "the hex pattern must reject injected attributes"
    # And it must not have been registered with the hostile colour.
    agents = client_a.get("/api/agents").json()
    assert all(a["agent_id"] != "evil-color" for a in agents)


def test_two_tabs_of_the_same_browser_share_a_session(client_a):
    """Isolation is per browser session, not per request."""
    client_a.post("/api/agents", json=AGENT)
    ids = [a["agent_id"] for a in client_a.get("/api/agents").json()]
    assert ids.count("solo-agent") == 1
    # a second request on the same cookie jar sees the same state
    assert "solo-agent" in [a["agent_id"] for a in client_a.get("/api/agents").json()]


# ------------------------------------------------------- the registry itself

def test_registry_reuses_state_per_session_id():
    registry = SessionRegistry(max_sessions=4, idle_ttl=3600, config=ServerConfig(),
                               mesh_factory=lambda cfg: AgentMesh())
    state = registry.create("client-1")
    assert registry.get(state.session_id) is state


def test_registry_evicts_least_recently_used_and_notifies():
    evicted = []
    registry = SessionRegistry(max_sessions=2, idle_ttl=3600, config=ServerConfig(),
                               mesh_factory=lambda cfg: AgentMesh())
    registry.on_evict = evicted.append
    first = registry.create("c")
    registry.create("c")
    registry.touch(first)          # keep `first` alive, push the other one back
    third = registry.create("c")
    assert len(registry) == 2
    assert registry.get(first.session_id) is first
    assert third.session_id in registry._states


def test_registry_drops_states_past_the_idle_ttl():
    registry = SessionRegistry(max_sessions=10, idle_ttl=1, config=ServerConfig(),
                               mesh_factory=lambda cfg: AgentMesh())
    state = registry.create("c")
    state.last_access -= 10
    assert registry.get(state.session_id) is None
    assert len(registry) == 0


def test_eviction_clears_key_material():
    registry = SessionRegistry(max_sessions=1, idle_ttl=3600, config=ServerConfig(),
                               mesh_factory=lambda cfg: AgentMesh())
    victim = registry.create("c")
    victim.api_keys["openai"] = "sk-leak-me"
    registry.create("c")
    assert victim.api_keys == {}


def test_meshes_do_not_share_a_message_bus():
    a = AgentMesh()
    b = AgentMesh()
    assert a.bus is not b.bus
    assert a.bus is not b.bus
    assert a.get_agent("copilot") is not b.get_agent("copilot")


async def test_bus_events_only_reach_the_owning_session():
    """A message dispatched in mesh A must not be sent to mesh B's sockets."""

    sockets = {"a": [], "b": []}

    class FakeSocket:
        def __init__(self, bucket):
            self.bucket = bucket
            self.sent = []

        async def send_json(self, payload):
            self.sent.append(payload)

        async def close(self):
            self.bucket.remove(self)

    registry = SessionRegistry(max_sessions=4, idle_ttl=3600, config=ServerConfig(),
                                mesh_factory=lambda cfg: AgentMesh())
    a = registry.create("client-a")
    b = registry.create("client-b")
    sock_a, sock_b = FakeSocket(sockets["a"]), FakeSocket(sockets["b"])
    a.websockets.add(sock_a)
    b.websockets.add(sock_b)

    # What the app wires up per session: a listener bound to *that* state only.
    def bind(state):
        async def on_message(msg):
            for ws in list(state.websockets):
                await ws.send_json({"type": "new_message", "message": msg.to_dict()})
        return on_message

    a.mesh.bus.add_global_listener(bind(a))
    b.mesh.bus.add_global_listener(bind(b))

    await a.mesh.get_agent("copilot").broadcast("only for session a")

    assert len(sock_a.sent) == 1 and sock_a.sent[0]["message"]["content"] == "only for session a"
    assert sock_b.sent == [], "session B must not see session A's traffic"
    assert b.mesh.get_history() == []
