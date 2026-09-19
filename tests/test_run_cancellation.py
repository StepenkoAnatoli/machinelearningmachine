"""D18: cancellation retains arrived replies and is scoped to the browser/run."""
import asyncio

import httpx
import pytest

from machinelearningmachine.agents.providers import BaseLLMProvider
from machinelearningmachine.server.app import create_app


@pytest.mark.parametrize("topology, stop_at", [
    ("p2p", 1), ("p2p", 4), ("pipeline", 1), ("pipeline", 4),
    ("debate", 1), ("debate", 4), ("hub", 1), ("hub", 5),
])
def test_cancel_at_agent_boundary(topology, stop_at):
    async def scenario():
        app = create_app()
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client, \
                httpx.AsyncClient(transport=transport, base_url="http://test") as other:
            await client.get("/api/agents")
            state = app.state.registry._states[client.cookies.get("mmm_session")]
            entered, release = asyncio.Event(), asyncio.Event()

            class HeldProvider(BaseLLMProvider):
                calls = 0

                async def generate(self, **kwargs):
                    self.calls += 1
                    if self.calls == stop_at:
                        entered.set()
                        await release.wait()
                    return "arrived reply"

            provider = HeldProvider()
            for agent in state.mesh.agents.values():
                agent.provider = provider
            task = asyncio.create_task(client.post("/api/run", json={
                "topology": topology, "prompt": "Design a cache layer",
            }))
            await asyncio.wait_for(entered.wait(), 2)
            run_id = state.active_run_id
            try:
                assert (await other.post(f"/api/runs/{run_id}/cancel")).status_code == 404
                assert (await client.post("/api/runs/stale/cancel")).status_code == 404
                for _ in range(2):
                    response = await client.post(f"/api/runs/{run_id}/cancel")
                    assert response.status_code == 200
                    assert response.json()["status"] == "cancelling"
            finally:
                release.set()
                result = await task
            assert result.json()["status"] == "cancelled"
            assert provider.calls == stop_at
            history = (await client.get("/api/history")).json()
            assert sum(m["content"] == "arrived reply" for m in history) == stop_at
            assert history[-1]["metadata"]["run_id"] == run_id
            assert "cancelled" in history[-1]["content"].lower()
            assert not state.run_lock.locked()
            assert (await client.post(f"/api/runs/{run_id}/cancel")).status_code == 404
            state.last_run_time = 0
            result = await client.post("/api/run", json={
                "topology": "pipeline", "prompt": "Design another cache", "agent_ids": ["gpt"],
            })
            assert result.json()["status"] == "completed"
    asyncio.run(scenario())
