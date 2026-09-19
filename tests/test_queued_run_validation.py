"""
D21: a run that is invalid must fail the same way whether the session is idle or busy.

Reproduced before the fix: ``{"topology": "pipeline", "agent_ids": []}`` answered
400 on an idle session (the mesh rejects an empty roster) but 202 - and then ran
the *default* roster - on a busy one (the queue stored ``[]`` as ``None``). Same
split for hub runs with an unknown hub agent or the hub listed as its own spoke:
400 now versus 202-then-``run_error``. Validation that does not need the lock
belongs before the busy check, next to the existing p2p agent checks, so the busy
path and the idle path refuse with the same status and the same words.
"""

import asyncio

import httpx
import pytest

from machinelearningmachine.agents.providers import BaseLLMProvider
from machinelearningmachine.server.app import create_app
from machinelearningmachine.server.config import ServerConfig


def _hold_first_run(state):
    """Hold the session's lock with a run that waits until released."""
    entered, release = asyncio.Event(), asyncio.Event()

    class HeldProvider(BaseLLMProvider):
        async def generate(self, **kwargs):
            entered.set()
            await release.wait()
            return "held reply"

    for agent in state.mesh.agents.values():
        agent.provider = HeldProvider()
    return entered, release


def _states(app, *clients):
    return [app.state.registry._states[c.cookies.get("mmm_session")] for c in clients]


@pytest.mark.parametrize("topology,detail", [
    ("pipeline", "At least one agent ID required for pipeline"),
    ("debate", "At least one agent required for debate"),
    ("hub", "At least one spoke agent required"),
])
def test_empty_agent_ids_rejected_when_busy_like_when_idle(topology, detail):
    async def scenario():
        app = create_app(ServerConfig(host="127.0.0.1", max_queued=5))
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as busy, \
                httpx.AsyncClient(transport=transport, base_url="http://test") as idle:
            await busy.get("/api/agents")
            await idle.get("/api/agents")
            busy_state = _states(app, busy)[0]
            entered, release = _hold_first_run(busy_state)
            first_task = asyncio.create_task(
                busy.post("/api/run", json={
                    "topology": "pipeline", "prompt": "Design a cache layer", "agent_ids": ["gpt"],
                })
            )
            await asyncio.wait_for(entered.wait(), 2)
            try:
                invalid = {"topology": topology, "prompt": "Design a cache layer", "agent_ids": []}
                refused_busy = await busy.post("/api/run", json=invalid)
                refused_idle = await idle.post("/api/run", json=invalid)
                assert refused_busy.status_code == 400, refused_busy.text[:200]
                assert refused_idle.status_code == 400, refused_idle.text[:200]
                assert refused_busy.json()["detail"] == detail
                assert refused_busy.json()["detail"] == refused_idle.json()["detail"]
            finally:
                release.set()
                assert (await first_task).status_code == 200

    asyncio.run(scenario())


def test_hub_with_unknown_hub_agent_rejected_when_busy_like_when_idle():
    async def scenario():
        app = create_app(ServerConfig(host="127.0.0.1", max_queued=5))
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as busy, \
                httpx.AsyncClient(transport=transport, base_url="http://test") as idle:
            await busy.get("/api/agents")
            await idle.get("/api/agents")
            busy_state = _states(app, busy)[0]
            entered, release = _hold_first_run(busy_state)
            first_task = asyncio.create_task(
                busy.post("/api/run", json={
                    "topology": "pipeline", "prompt": "Design a cache layer", "agent_ids": ["gpt"],
                })
            )
            await asyncio.wait_for(entered.wait(), 2)
            try:
                invalid = {
                    "topology": "hub", "prompt": "Design a cache layer",
                    "from_agent": "ghost-agent", "agent_ids": ["gpt"],
                }
                refused_busy = await busy.post("/api/run", json=invalid)
                refused_idle = await idle.post("/api/run", json=invalid)
                assert refused_busy.status_code == 400, refused_busy.text[:200]
                assert refused_idle.status_code == 400, refused_idle.text[:200]
                assert refused_busy.json()["detail"] == refused_idle.json()["detail"]
                assert "ghost-agent" in refused_busy.json()["detail"]
            finally:
                release.set()
                assert (await first_task).status_code == 200

    asyncio.run(scenario())


def test_hub_with_hub_as_own_spoke_rejected_when_busy_like_when_idle():
    async def scenario():
        app = create_app(ServerConfig(host="127.0.0.1", max_queued=5))
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as busy, \
                httpx.AsyncClient(transport=transport, base_url="http://test") as idle:
            await busy.get("/api/agents")
            await idle.get("/api/agents")
            busy_state = _states(app, busy)[0]
            entered, release = _hold_first_run(busy_state)
            first_task = asyncio.create_task(
                busy.post("/api/run", json={
                    "topology": "pipeline", "prompt": "Design a cache layer", "agent_ids": ["gpt"],
                })
            )
            await asyncio.wait_for(entered.wait(), 2)
            try:
                invalid = {
                    "topology": "hub", "prompt": "Design a cache layer",
                    "from_agent": "copilot", "agent_ids": ["copilot", "gpt"],
                }
                refused_busy = await busy.post("/api/run", json=invalid)
                refused_idle = await idle.post("/api/run", json=invalid)
                assert refused_busy.status_code == 400, refused_busy.text[:200]
                assert refused_idle.status_code == 400, refused_idle.text[:200]
                assert refused_busy.json()["detail"] == "Hub agent cannot also be a spoke"
                assert refused_busy.json()["detail"] == refused_idle.json()["detail"]
            finally:
                release.set()
                assert (await first_task).status_code == 200

    asyncio.run(scenario())
