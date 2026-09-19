"""
D20: a second run queues instead of being refused.

Reproduced before the fix: two concurrent POSTs to /api/run on one session
returned [200, 409] - the loser was refused with "already in progress" even
though the session could have waited its turn. The queue is per-session and
per-process (like the run lock and the outboxes): bounded by --max-queued,
positioned (1-indexed) in the 202 response, 429 with Retry-After when full,
and --max-queued 0 restores the 409 refusal.
"""

import asyncio

import httpx
from fastapi.testclient import TestClient

from machinelearningmachine.agents.providers import BaseLLMProvider
from machinelearningmachine.server.app import create_app
from machinelearningmachine.server.config import ServerConfig


class PacedProvider(BaseLLMProvider):
    is_simulated = False
    label = "Paced"
    fallback_to_mock = True

    def __init__(self, delay: float = 0.2):
        self.delay = delay

    async def generate(self, system_prompt, messages, agent_role, agent_name, task_context=None):
        await asyncio.sleep(self.delay)
        # Full context (not truncated): the pipeline's second step answers from the
        # first step's output, so truncation would erase the run's own token.
        token = task_context or ""
        return f"{agent_name} answering [{token}]"


def _state(app, client):
    return app.state.registry._states[client.cookies.get("mmm_session")]


def _use_paced(state, delay=0.2):
    provider = PacedProvider(delay)
    for agent in state.mesh.agents.values():
        agent.provider = provider
    return provider


def test_second_run_queues_with_position_in_response():
    app = create_app(ServerConfig(host="127.0.0.1"))
    client = TestClient(app)
    client.get("/api/agents")
    state = _state(app, client)
    _use_paced(state, delay=0.4)

    async def both():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test", cookies=dict(client.cookies)
        ) as http:
            payload = {
                "topology": "pipeline",
                "prompt": "design a cache layer",
                "agent_ids": ["copilot", "gpt"],
            }
            first, second = await asyncio.gather(
                http.post("/api/run", json=payload),
                http.post("/api/run", json=payload),
            )
            return first, second

    first, second = asyncio.run(both())
    codes = sorted([first.status_code, second.status_code])
    assert codes == [200, 202], f"expected one immediate run and one queued, got {codes}"
    queued = second if second.status_code == 202 else first
    body = queued.json()
    assert body["status"] == "queued"
    assert body["queue_position"] == 1
    assert body["run_id"]


def test_queue_full_returns_429_with_retry_after():
    app = create_app(ServerConfig(host="127.0.0.1", max_queued=1))
    client = TestClient(app)
    client.get("/api/agents")
    state = _state(app, client)
    _use_paced(state, delay=0.5)

    async def three():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test", cookies=dict(client.cookies)
        ) as http:
            payload = {
                "topology": "pipeline",
                "prompt": "design a cache layer",
                "agent_ids": ["copilot", "gpt"],
            }
            return await asyncio.gather(
                http.post("/api/run", json=payload),
                http.post("/api/run", json=payload),
                http.post("/api/run", json=payload),
            )

    responses = asyncio.run(three())
    codes = sorted(r.status_code for r in responses)
    assert codes == [200, 202, 429], f"expected immediate+queued+full, got {codes}"
    full = next(r for r in responses if r.status_code == 429)
    assert "Retry-After" in full.headers
    assert "queue" in full.json()["detail"].lower()


def test_max_queued_zero_restores_refusal():
    app = create_app(ServerConfig(host="127.0.0.1", max_queued=0))
    client = TestClient(app)
    client.get("/api/agents")
    state = _state(app, client)
    _use_paced(state, delay=0.4)

    async def both():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test", cookies=dict(client.cookies)
        ) as http:
            payload = {
                "topology": "pipeline",
                "prompt": "design a cache layer",
                "agent_ids": ["copilot", "gpt"],
            }
            return await asyncio.gather(
                http.post("/api/run", json=payload),
                http.post("/api/run", json=payload),
            )

    first, second = asyncio.run(both())
    codes = sorted([first.status_code, second.status_code])
    assert codes == [200, 409], f"--max-queued 0 must refuse as before, got {codes}"


def test_queued_runs_execute_in_order_without_interleaving():
    async def scenario():
        app = create_app(ServerConfig(host="127.0.0.1", max_queued=5))
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
            await http.get("/api/agents")
            state = app.state.registry._states[http.cookies.get("mmm_session")]
            _use_paced(state, delay=0.05)
            first_payload = {
                "topology": "pipeline",
                "prompt": "alpha-run-token please",
                "agent_ids": ["copilot", "gpt"],
            }
            second_payload = {
                "topology": "pipeline",
                "prompt": "bravo-run-token please",
                "agent_ids": ["copilot", "gpt"],
            }
            first, second = await asyncio.gather(
                http.post("/api/run", json=first_payload),
                http.post("/api/run", json=second_payload),
            )
            assert sorted([first.status_code, second.status_code]) == [200, 202]
            # The queued run executes in the background after the immediate one.
            history = []
            for _ in range(100):
                await asyncio.sleep(0.05)
                history = (await http.get("/api/history")).json()
                contents = " ".join(m["content"] for m in history)
                if len(history) >= 4 and "alpha-run-token" in contents and "bravo-run-token" in contents:
                    break
            else:
                raise AssertionError("queued run never reached the transcript")
            # FIFO without interleave: all of one run's messages precede the other's.
            # (The second pipeline step answers from the first step's output, so it
            # carries "alpha"/"bravo" rather than the full prompt token.)
            kinds = [
                ("alpha" if "alpha" in m["content"] else "bravo")
                for m in history
                if ("alpha" in m["content"] or "bravo" in m["content"])
            ]
            assert kinds == sorted(kinds) or kinds == sorted(kinds, reverse=True), kinds
            assert len(kinds) == 4

    asyncio.run(scenario())


def test_cancel_works_on_a_queued_run():
    async def scenario():
        app = create_app(ServerConfig(host="127.0.0.1", max_queued=5))
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
            await http.get("/api/agents")
            state = app.state.registry._states[http.cookies.get("mmm_session")]
            entered, release = asyncio.Event(), asyncio.Event()

            class HeldProvider(BaseLLMProvider):
                calls = 0

                async def generate(self, **kwargs):
                    type(self).calls += 1
                    if type(self).calls == 1:
                        entered.set()
                        await release.wait()
                    return "held reply"

            for agent in state.mesh.agents.values():
                agent.provider = HeldProvider()
            first_task = asyncio.create_task(
                http.post(
                    "/api/run",
                    json={"topology": "pipeline", "prompt": "Design a cache layer", "agent_ids": ["gpt"]},
                )
            )
            await asyncio.wait_for(entered.wait(), 2)
            queued = await http.post(
                "/api/run",
                json={"topology": "pipeline", "prompt": "Design another cache", "agent_ids": ["gpt"]},
            )
            assert queued.status_code == 202
            queued_id = queued.json()["run_id"]
            cancel = await http.post(f"/api/runs/{queued_id}/cancel")
            assert cancel.status_code == 200
            assert cancel.json()["run_id"] == queued_id
            release.set()
            first = await first_task
            assert first.status_code == 200
            await asyncio.sleep(0.2)
            history = (await http.get("/api/history")).json()
            assert all("Design another cache" not in m["content"] for m in history)
            assert (await http.post(f"/api/runs/{queued_id}/cancel")).status_code == 404

    asyncio.run(scenario())


def test_queue_is_per_session_like_the_run_lock():
    async def scenario():
        app = create_app(ServerConfig(host="127.0.0.1", max_queued=5))
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as alice, httpx.AsyncClient(
            transport=transport, base_url="http://test"
        ) as bob:
            await alice.get("/api/agents")
            await bob.get("/api/agents")
            alice_state = app.state.registry._states[alice.cookies.get("mmm_session")]
            entered, release = asyncio.Event(), asyncio.Event()

            class HeldProvider(BaseLLMProvider):
                async def generate(self, **kwargs):
                    entered.set()
                    await release.wait()
                    return "held reply"

            for agent in alice_state.mesh.agents.values():
                agent.provider = HeldProvider()
            alice_task = asyncio.create_task(
                alice.post("/api/run", json={"topology": "p2p", "prompt": "Design a cache layer", "turns": 2})
            )
            await asyncio.wait_for(entered.wait(), 2)
            try:
                # Bob's session has its own lock and queue: nothing queued, nothing refused.
                bob_run = await bob.post(
                    "/api/run", json={"topology": "p2p", "prompt": "Design a cache layer", "turns": 2}
                )
                assert bob_run.status_code == 200
                assert bob_run.json()["status"] == "completed"
            finally:
                release.set()
                assert (await alice_task).status_code == 200

    asyncio.run(scenario())


def test_queued_frame_names_the_active_run_it_waits_behind():
    # D22: a tab that missed run_started (a gap ate it) learns the active run
    # from run_queued - without the id it goes busy with nothing to attribute
    # the later completion to, and wedges busy forever.
    async def scenario():
        app = create_app(ServerConfig(host="127.0.0.1", max_queued=5))
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
            await http.get("/api/agents")
            state = app.state.registry._states[http.cookies.get("mmm_session")]
            entered, release = asyncio.Event(), asyncio.Event()

            class HeldProvider(BaseLLMProvider):
                async def generate(self, **kwargs):
                    entered.set()
                    await release.wait()
                    return "held reply"

            for agent in state.mesh.agents.values():
                agent.provider = HeldProvider()
            frames = []
            original_publish = state.publish

            def spy(payload):
                frames.append(payload)
                return original_publish(payload)

            state.publish = spy
            first_task = asyncio.create_task(
                http.post("/api/run", json={
                    "topology": "pipeline", "prompt": "Design a cache layer", "agent_ids": ["gpt"],
                })
            )
            await asyncio.wait_for(entered.wait(), 2)
            active_id = state.active_run_id
            try:
                queued = await http.post("/api/run", json={
                    "topology": "pipeline", "prompt": "Design another cache", "agent_ids": ["gpt"],
                })
                assert queued.status_code == 202
                queued_id = queued.json()["run_id"]
                announced = [f for f in frames if f.get("type") == "run_queued" and f.get("run_id") == queued_id]
                assert len(announced) == 1, f"expected one run_queued for {queued_id}"
                assert announced[0]["active_run_id"] == active_id
            finally:
                release.set()
                assert (await first_task).status_code == 200

    asyncio.run(scenario())
