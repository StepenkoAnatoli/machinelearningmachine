"""
One run at a time per browser session, and a transcript that belongs to somebody.

Reproduced before the fix: two concurrent ``run_pipeline`` calls on one session
produced

    history: arena-ai, arena-ai, claude, claude, copilot, copilot, gpt, gpt

i.e. neither run's transcript - and each agent's memory had picked up the other
run's messages, which with a live provider means model answers conditioned on a
different task. The exported session and the saved transcript inherit both.

The rules the endpoint now keeps:
* validation happens before the rate-limit stamp is spent;
* a second concurrent run is refused (409), not queued and not interleaved;
* every run event carries the run's id, so a browser can tell its own run from
  another tab's.
"""

import asyncio

import httpx
import pytest
from fastapi.testclient import TestClient

from machinelearningmachine.agents.providers import BaseLLMProvider
from machinelearningmachine.server.app import create_app
from machinelearningmachine.server.config import ServerConfig


class PacedProvider(BaseLLMProvider):
    """Answers after a delay, tagged with the prompt it was given."""

    is_simulated = False
    label = "Paced"
    fallback_to_mock = True

    def __init__(self, delay: float = 0.2):
        self.delay = delay

    async def generate(self, system_prompt, messages, agent_role, agent_name, task_context=None):
        await asyncio.sleep(self.delay)
        token = (task_context or "").split("\n")[-1][:24]
        return f"{agent_name} answering [{token}]"


@pytest.fixture
def paced_app():
    app = create_app(ServerConfig(host="127.0.0.1"))
    return app


def _state(app, client):
    return app.state.registry._states[client.cookies.get("mmm_session")]


def _use_paced_provider(state, delay=0.2):
    provider = PacedProvider(delay)
    for agent in state.mesh.agents.values():
        agent.provider = provider
    return provider


# --------------------------------------------------------------------- 409 path

def test_second_run_is_refused_while_one_is_in_progress(paced_app):
    client = TestClient(paced_app)
    client.get("/api/agents")
    state = _state(paced_app, client)
    _use_paced_provider(state, delay=0.4)

    async def both():
        transport = httpx.ASGITransport(app=paced_app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test",
                                     cookies=dict(client.cookies)) as http:
            payload = {"topology": "pipeline", "prompt": "design a cache layer", "agent_ids": ["copilot", "gpt"]}
            first, second = await asyncio.gather(
                http.post("/api/run", json=payload),
                http.post("/api/run", json=payload),
            )
            return first, second

    first, second = asyncio.run(both())
    codes = sorted([first.status_code, second.status_code])
    assert codes == [200, 409], f"expected one accepted run and one refusal, got {codes}"
    refusal = second if second.status_code == 409 else first
    assert "already in progress" in refusal.json()["detail"]
    assert "Retry-After" in refusal.headers


def test_a_refused_run_leaves_the_transcript_alone(paced_app):
    client = TestClient(paced_app)
    client.get("/api/agents")
    state = _state(paced_app, client)
    _use_paced_provider(state, delay=0.3)

    before = len(state.mesh.get_history())

    async def both():
        transport = httpx.ASGITransport(app=paced_app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test",
                                     cookies=dict(client.cookies)) as http:
            payload = {"topology": "pipeline", "prompt": "design a cache layer", "agent_ids": ["copilot", "gpt"]}
            await asyncio.gather(
                http.post("/api/run", json=payload),
                http.post("/api/run", json=payload),
            )

    asyncio.run(both())
    history = state.mesh.get_history()
    assert len(history) == before + 2, "only the accepted run may write to the transcript"
    prompts = {m.content for m in history[before:]}
    assert len(prompts) == 2, "the two messages must come from one run, not one from each"


def test_run_lock_is_released_after_a_failure(paced_app):
    client = TestClient(paced_app)
    client.get("/api/agents")
    state = _state(paced_app, client)

    class Broken(BaseLLMProvider):
        is_simulated = False
        label = "Broken"
        fallback_to_mock = False

        async def generate(self, **kwargs):
            raise RuntimeError("provider blew up")

    for agent in state.mesh.agents.values():
        agent.provider = Broken()
    assert client.post("/api/run", json={"topology": "p2p", "prompt": "design a cache"}).status_code == 500
    assert state.run_lock.locked() is False, "a failed run must not leave the session locked"

    _use_paced_provider(state, delay=0.0)
    state.last_run_time = 0.0
    assert client.post("/api/run", json={"topology": "p2p", "prompt": "design a cache"}).status_code == 200


# ------------------------------------------------------------- cooldown fairness

def test_an_invalid_request_does_not_spend_the_cooldown(paced_app):
    """The stamp used to be taken before validation, so a typo cost a second."""
    client = TestClient(paced_app)
    client.get("/api/agents")
    state = _state(paced_app, client)

    rejected = client.post("/api/run", json={"topology": "p2p", "from_agent": "ghost", "prompt": "design a cache"})
    assert rejected.status_code == 400
    assert state.last_run_time == 0.0, "a request that never ran must not start a cooldown"

    ok = client.post("/api/run", json={"topology": "p2p", "prompt": "design a cache layer", "turns": 2})
    assert ok.status_code == 200, "the next legitimate run must not be throttled by a rejected one"


def test_cooldown_applies_between_two_accepted_runs(paced_app):
    client = TestClient(paced_app)
    client.get("/api/agents")
    _state(paced_app, client)
    first = client.post("/api/run", json={"topology": "p2p", "prompt": "design a cache layer", "turns": 2})
    assert first.status_code == 200
    second = client.post("/api/run", json={"topology": "p2p", "prompt": "design a cache layer", "turns": 2})
    assert second.status_code == 429
    assert "Retry-After" in second.headers


# --------------------------------------------------------------- run attribution

def _drain(feed):
    """Frames a feed has queued but not yet written (pump intentionally not started)."""
    out = []
    while True:
        try:
            item = feed._queue.get_nowait()
        except asyncio.QueueEmpty:
            break
        if item is not None:
            out.append(item)
        feed._queue.task_done()
    return out


def test_run_events_carry_a_run_id_and_messages_inherit_it(paced_app):
    """
    Two tabs share one session, so "a run finished" is only meaningful next to
    *which* run. The id in the response and in every frame of that run is what lets
    a browser ignore another tab's completion instead of unsticking its own UI.
    """
    client = TestClient(paced_app)
    client.get("/api/agents")
    state = _state(paced_app, client)
    _use_paced_provider(state, delay=0.0)

    from machinelearningmachine.server.feed import ClientFeed

    class SilentSocket:
        async def send_json(self, payload):  # pragma: no cover - never pumped
            raise AssertionError("the feed must not be pumped in this test")

        async def close(self, code=1000):
            pass

    feed = ClientFeed(SilentSocket())
    state.feeds.add(feed)

    response = client.post("/api/run", json={"topology": "p2p", "prompt": "design a cache layer", "turns": 2})
    assert response.status_code == 200
    run_id = response.json()["run_id"]
    assert run_id

    frames = _drain(feed)
    by_type = {f["type"]: f for f in frames}
    assert by_type["run_started"]["run_id"] == run_id
    assert by_type["run_completed"]["run_id"] == run_id
    messages = [f for f in frames if f["type"] == "new_message"]
    assert messages and all(f["run_id"] == run_id for f in messages)
    assert by_type["run_started"]["prompt"] == "design a cache layer"
    feed.abort()


def test_prompt_is_not_broadcast_in_full(paced_app):
    client = TestClient(paced_app)
    client.get("/api/agents")
    state = _state(paced_app, client)
    _use_paced_provider(state, delay=0.0)

    from machinelearningmachine.server.feed import ClientFeed

    class SilentSocket:
        async def send_json(self, payload):  # pragma: no cover
            raise AssertionError("not pumped")

        async def close(self, code=1000):
            pass

    feed = ClientFeed(SilentSocket())
    state.feeds.add(feed)
    long_prompt = "design a cache " + ("z" * 900)
    assert client.post("/api/run", json={"topology": "p2p", "prompt": long_prompt, "turns": 2}).status_code == 200
    started = next(f for f in _drain(feed) if f["type"] == "run_started")
    assert len(started["prompt"]) <= 200, "the feed carries a preview, not the user's text"
    feed.abort()
