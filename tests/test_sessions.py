"""Tests for saved-session persistence and the web page reader."""

import time

import pytest
from fastapi.testclient import TestClient

import machinelearningmachine.server.app as appmod
from machinelearningmachine import sessions as session_store
from machinelearningmachine.server.app import app


@pytest.fixture(autouse=True)
def sessions_dir(tmp_path, monkeypatch):
    """Point session storage at a throwaway directory for every test."""
    monkeypatch.setenv(session_store.SESSIONS_ENV_VAR, str(tmp_path / "sessions"))


def _sample_messages(n=2):
    msgs = []
    for i in range(n):
        msgs.append({
            "id": f"msg-{i}",
            "sender_id": "copilot",
            "sender_name": "Copilot",
            "recipient_id": "arena-ai",
            "recipient_name": "Arena AI",
            "topic": "general",
            "message_type": "proposal" if i % 2 == 0 else "critique",
            "content": f"Message number {i} with some content.",
            "artifacts": {},
            "metadata": {},
            "timestamp": time.time() + i,
        })
    return msgs


def test_store_roundtrip():
    agents = [{
        "agent_id": "unit-agent",
        "name": "Unit Agent",
        "role": "Tester",
        "system_prompt": "You test things.",
        "color": "#123456",
        "avatar": "X",
    }]
    messages = _sample_messages(3)

    meta = session_store.save_session("Unit Test Session", agents, messages)
    assert meta["id"]
    assert meta["name"] == "Unit Test Session"
    assert meta["message_count"] == 3

    listed = session_store.list_sessions()
    assert any(s["id"] == meta["id"] and s["message_count"] == 3 for s in listed)

    data = session_store.get_session(meta["id"])
    assert data is not None
    assert data["name"] == "Unit Test Session"
    assert data["agents"][0]["agent_id"] == "unit-agent"
    assert len(data["messages"]) == 3

    assert session_store.delete_session(meta["id"]) is True
    assert session_store.get_session(meta["id"]) is None
    assert session_store.delete_session(meta["id"]) is False


def test_store_name_sanitization():
    meta = session_store.save_session('bad/name: with*chars?<>\n\nnewline', [], [])
    assert "/" not in meta["name"]
    assert ":" not in meta["name"]
    assert "\n" not in meta["name"]
    assert "*" not in meta["name"]
    session_store.delete_session(meta["id"])


def test_store_rejects_path_traversal():
    assert session_store.get_session("../../etc/passwd") is None
    assert session_store.get_session("..") is None
    assert session_store.delete_session("../../etc/passwd") is False


def test_store_pruning_keeps_max_sessions():
    session_store.save_session("seed", [], _sample_messages(1))
    for i in range(session_store.MAX_SESSIONS + 5):
        session_store.save_session(f"prune {i}", [], _sample_messages(1))
    listed = session_store.list_sessions()
    # Storage is bounded at MAX_SESSIONS, and everything listed is readable.
    assert len(listed) <= session_store.MAX_SESSIONS
    for s in listed:
        assert session_store.get_session(s["id"]) is not None


def test_server_sessions_roundtrip(monkeypatch):
    client = TestClient(app)
    # Tests run fast - lift the 1s run cooldown so consecutive dialogues work.
    monkeypatch.setattr(appmod, "RUN_COOLDOWN_SECONDS", 0.0)

    # A custom agent + a short dialogue to save.
    client.post("/api/agents", json={
        "agent_id": "session-agent-a",
        "name": "Session A",
        "role": "Session Tester",
        "system_prompt": "You verify sessions are saved correctly.",
    })
    run = client.post("/api/run", json={
        "topology": "p2p",
        "prompt": "Create a retry decorator in Python",
        "from_agent": "session-agent-a",
        "to_agent": "copilot",
        "turns": 2,
    })
    assert run.status_code == 200, run.text

    # Save the current state.
    save = client.post("/api/sessions", json={"name": "Saved A"})
    assert save.status_code == 200, save.text
    sid = save.json()["session"]["id"]

    listed = client.get("/api/sessions").json()["sessions"]
    assert any(s["id"] == sid and s["name"] == "Saved A" for s in listed)

    # Single-session endpoint works.
    detail = client.get(f"/api/sessions/{sid}")
    assert detail.status_code == 200
    assert len(detail.json()["messages"]) == 2

    # Change the live state completely...
    assert client.post("/api/clear").status_code == 200
    assert client.get("/api/history").json() == []
    client.post("/api/agents", json={
        "agent_id": "session-agent-b",
        "name": "Session B",
        "role": "Second tester",
        "system_prompt": "You are the second session tester.",
    })
    other = client.post("/api/run", json={
        "topology": "p2p",
        "prompt": "Design a queue with backpressure",
        "from_agent": "session-agent-b",
        "to_agent": "claude",
        "turns": 2,
    })
    assert other.status_code == 200, other.text

    # ...then load the saved session back: conversation and custom agent return.
    load = client.post("/api/sessions/load", json={"session_id": sid})
    assert load.status_code == 200, load.text
    assert load.json()["messages"] == 2

    history = client.get("/api/history").json()
    assert len(history) == 2
    assert history[0]["content"] == run.json()["messages"][0]["content"]

    agent_ids = [a["agent_id"] for a in client.get("/api/agents").json()]
    assert "session-agent-a" in agent_ids

    # Unknown / malformed sessions are rejected.
    assert client.post("/api/sessions/load", json={"session_id": "nope-1234"}).status_code == 404
    assert client.delete(f"/api/sessions/{sid}").status_code == 200
    assert client.post("/api/sessions/load", json={"session_id": sid}).status_code == 404
    assert client.get(f"/api/sessions/{sid}").status_code == 404


def test_save_session_requires_history():
    # State is per browser session, so a brand-new client starts with no history.
    client = TestClient(app)
    resp = client.post("/api/sessions", json={"name": "Empty"})
    assert resp.status_code == 400
    assert "nothing to save" in resp.json()["detail"]


def test_page_text_extraction_strips_html():
    raw = (
        "<html><head><title>Hello &amp; welcome</title>"
        "<script>evil()</script><style>body{}</style></head>"
        "<body><h1>Head</h1><nav>hidden nav</nav>"
        "<p>Para one.</p><p>Para two.</p><ul><li>Item A</li></ul>"
        "<footer>footer junk</footer></body></html>"
    )
    out = appmod.page_text_from_body(raw, "text/html; charset=utf-8")
    assert out["title"] == "Hello & welcome"
    assert "Head" in out["text"]
    assert "Para one." in out["text"]
    assert "- Item A" in out["text"]
    assert "hidden nav" not in out["text"]
    assert "footer junk" not in out["text"]
    assert "evil()" not in out["text"]
    assert "body{}" not in out["text"]


def test_read_url_endpoint_validates():
    client = TestClient(app)
    resp = client.post("/api/read/url", json={"url": "ftp://nope.example"})
    assert resp.status_code == 422


def test_read_url_is_disabled_by_default():
    """The fetcher is opt-in: a default server must not expose it at all."""
    client = TestClient(app)
    resp = client.post("/api/read/url", json={"url": "https://example.com"})
    assert resp.status_code == 404
    assert "enable-url-reader" in resp.json()["detail"]


def test_saved_sessions_are_namespaced_per_client(tmp_path):
    """Two browsers must not see, load, or delete each other's saved files."""
    session_store.save_session("Alice only", [], _sample_messages(2), namespace="client-a")
    bob = session_store.save_session("Bob only", [], _sample_messages(1), namespace="client-b")

    listed = session_store.list_sessions("client-a")
    assert [s["name"] for s in listed] == ["Alice only"]
    assert session_store.get_session(bob["id"], "client-a") is None
    assert session_store.delete_session(bob["id"], "client-a") is False

    # A hostile namespace can only ever collapse to "no namespace".
    assert session_store.sanitize_namespace("../../etc") is None
    assert session_store.sessions_dir("../../etc") == session_store.sessions_dir(None)


def test_tts_engine_reporting():
    from machinelearningmachine import tts

    engine = tts.available_engine()
    # Whatever OS this runs on, the helper must never raise.
    assert engine is None or isinstance(engine, str)
    assert tts.speak("") is False  # empty text is a no-op, not an error


def test_a_failed_save_tells_the_operator_why_without_leaking_the_path(monkeypatch, tmp_path):
    """
    Disk-full and "some generic 500" are not the same product. The reason reaches
    the caller; the server's directory does not.
    """
    from machinelearningmachine import sessions as store

    client = TestClient(app)
    client.post("/api/run", json={"topology": "pipeline", "prompt": "Design a rate limiter"})

    def exploding_replace(src, dst):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(store.os, "replace", exploding_replace)
    resp = client.post("/api/sessions", json={"name": "on a full disk"})
    assert resp.status_code == 500
    detail = resp.json()["detail"]
    assert "No space left on device" in detail, detail
    assert str(tmp_path) not in detail and "/sessions" not in detail, detail
