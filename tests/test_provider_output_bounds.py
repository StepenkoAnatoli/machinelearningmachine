"""
Long provider answers must complete the run, and say what was cut.

Found while testing the mesh against real-sized output rather than the simulator's:
``Message.content`` is bounded at 50 000 characters and ``BaseAgent`` refused any
injected prompt over 10 000, so the first answer a real model writes that is longer
than a screenful broke the dialogue. Measured on the pre-fix code:

    len=10500  -> ValueError: Prompt too long (max 10000 chars)   (p2p, pipeline, hub)
    len=51000  -> pydantic ValidationError, transcript empty, and the model's own
                  text echoed back into the HTTP 400 "detail"

Both are limits the *server* chose, so the server has to absorb them: shrink the
input to its context budget, clamp the output to the protocol bound, and leave a
record in the transcript of what happened.
"""

import asyncio

import pytest
from fastapi.testclient import TestClient

from machinelearningmachine.agents.base import MAX_INJECTED_PROMPT, fit_context
from machinelearningmachine.agents.providers import BaseLLMProvider
from machinelearningmachine.mesh import AgentMesh
from machinelearningmachine.protocol.message import MAX_CONTENT_LENGTH, clamp_content
from machinelearningmachine.server.app import create_app
from machinelearningmachine.server.config import ServerConfig


class SizedProvider(BaseLLMProvider):
    """A 'live' provider whose answer is exactly ``size`` characters long."""

    is_simulated = False
    label = "Sized"
    fallback_to_mock = True

    def __init__(self, size: int, marker: str = "MODEL-ANSWER"):
        self.size = size
        self.marker = marker
        self.prompts = []

    async def generate(self, system_prompt, messages, agent_role, agent_name, task_context=None):
        self.prompts.append(task_context or "")
        filler = self.marker + ":" + ("a" * max(0, self.size - len(self.marker) - 1))
        return (filler + "b" * self.size)[: self.size]


# ------------------------------------------------------------------ clamp_content

def test_clamp_content_leaves_short_text_untouched():
    text, dropped = clamp_content("hello", limit=100)
    assert text == "hello" and dropped == 0


def test_clamp_content_bounds_and_reports_what_it_cut():
    text, dropped = clamp_content("x" * 60_000)
    assert len(text) <= MAX_CONTENT_LENGTH
    assert dropped > 0
    assert "Truncated" in text
    assert "60000" in text, "the notice must say how big the real answer was"


# ------------------------------------------------------------------ fit_context

def test_fit_context_keeps_head_and_tail():
    prompt = "HEAD " + ("z" * 30_000) + " TAIL"
    fitted, dropped = fit_context(prompt, limit=4_000)
    assert len(fitted) <= 4_000
    assert fitted.startswith("HEAD ")
    assert fitted.endswith("TAIL")
    assert "trimmed to fit the context budget" in fitted
    assert dropped == len(prompt) - len(fitted)


def test_fit_context_is_a_no_op_within_budget():
    assert fit_context("short", limit=100) == ("short", 0)


# ------------------------------------------------------------------ mesh level

@pytest.mark.parametrize("size", [1_000, 9_500, 10_500, 49_000, 51_000, 200_000])
async def test_p2p_completes_for_any_answer_size(size):
    mesh = AgentMesh()
    provider = SizedProvider(size)
    for agent in mesh.agents.values():
        agent.provider = provider
    transcript = await mesh.talk_p2p("arena-ai", "copilot", "build a rate limiter", turns=4)
    assert len(transcript) == 4, "a long answer must not cost the user the remaining turns"
    for message in transcript:
        assert len(message.content) <= MAX_CONTENT_LENGTH


@pytest.mark.parametrize("topology", ["pipeline", "debate", "hub"])
@pytest.mark.parametrize("size", [15_000, 60_000])
async def test_every_topology_survives_long_answers(topology, size):
    """The bug lived in the shared agent path, so the fix must hold everywhere."""
    mesh = AgentMesh()
    for agent in mesh.agents.values():
        agent.provider = SizedProvider(size)
    if topology == "pipeline":
        transcript = await mesh.run_pipeline("build a rate limiter")
    elif topology == "debate":
        transcript = await mesh.run_debate("build a rate limiter")
    else:
        transcript = await mesh.run_hub_and_spoke("build a rate limiter")
    assert len(transcript) >= 4


async def test_truncation_is_recorded_in_metadata_and_text():
    mesh = AgentMesh()
    for agent in mesh.agents.values():
        agent.provider = SizedProvider(MAX_CONTENT_LENGTH + 5_000)
    transcript = await mesh.talk_p2p("arena-ai", "copilot", "build a rate limiter", turns=2)
    meta = transcript[0].metadata
    assert meta["content_truncated"] > 0
    assert "Truncated" in transcript[0].content
    # Provenance is unchanged: it is still a live-provider answer, just cut short.
    assert meta["simulated"] is False


async def test_long_peer_answer_does_not_reach_the_next_agent_verbatim():
    """Context is a budget, so peer text is trimmed rather than rejected."""
    mesh = AgentMesh()
    provider = SizedProvider(40_000)
    for agent in mesh.agents.values():
        agent.provider = provider
    await mesh.talk_p2p("arena-ai", "copilot", "build a rate limiter", turns=4)
    for sent in provider.prompts:
        assert len(sent) <= MAX_INJECTED_PROMPT + 200  # the topology adds a header line


# ------------------------------------------------------------------ export & API

async def test_exported_markdown_shows_the_truncation():
    mesh = AgentMesh()
    for agent in mesh.agents.values():
        agent.provider = SizedProvider(MAX_CONTENT_LENGTH + 9_000)
    await mesh.talk_p2p("arena-ai", "copilot", "build a rate limiter", turns=2)
    markdown = mesh.export_markdown()
    assert "Truncated" in markdown, "an exported transcript must not read as complete"


@pytest.fixture
def long_answer_client():
    app = create_app(ServerConfig(host="127.0.0.1"))
    client = TestClient(app)
    client.get("/api/agents")  # allocate this browser's session
    state = app.state.registry._states[client.cookies.get("mmm_session")]
    provider = SizedProvider(MAX_CONTENT_LENGTH + 3_000, marker="SECRET-PROMPT-TEXT")
    for agent in state.mesh.agents.values():
        agent.provider = provider
    return client, state


def test_api_run_succeeds_and_reports_truncation(long_answer_client):
    client, state = long_answer_client
    response = client.post(
        "/api/run",
        json={"topology": "p2p", "prompt": "build a rate limiter", "turns": 4},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert len(body["messages"]) == 4
    assert body["truncated_messages"] == 4
    assert all(len(m["content"]) <= MAX_CONTENT_LENGTH for m in body["messages"])
    assert state.last_run_warnings == []


def test_safe_reason_never_echoes_a_payload():
    """
    ``str(exc)`` for a pydantic ValidationError interpolates the offending value,
    which here would be the model answer - and that string used to be returned to
    the browser *and* broadcast to every open socket of the session.
    """
    from machinelearningmachine.server.app import _safe_reason

    class ValidationError(ValueError):  # only the class name matters to the policy
        pass

    leaky = ValidationError(
        "1 validation error for Message\ncontent\n  String should have at most 50000 "
        "characters [type=string_too_long, input_value='SECRET-PROMPT-TEXT" + "x" * 4000 + "']"
    )
    reason = _safe_reason(leaky)
    assert "SECRET-PROMPT-TEXT" not in reason
    assert "could not be assembled into a valid message" in reason

    # An ordinary validation message is user-facing and stays verbatim, capped.
    assert _safe_reason(ValueError("Prompt too short")) == "Prompt too short"
    assert len(_safe_reason(ValueError("q" * 5_000))) <= 300


def test_history_and_saved_transcript_stay_loadable_after_truncation(long_answer_client):
    client, _state = long_answer_client
    assert client.post("/api/run", json={"topology": "pipeline", "prompt": "build a rate limiter"}).status_code == 200
    exported = client.get("/api/export/json")
    assert exported.status_code == 200
    saved = client.post("/api/sessions", json={"name": "long answers"})
    assert saved.status_code == 200, saved.text
    loaded = client.post("/api/sessions/load", json={"session_id": saved.json()["session"]["id"]})
    assert loaded.status_code == 200
    assert loaded.json()["messages"] == 4


def test_run_timeout_reports_504_and_frees_the_session():
    """A provider that never answers must not own the session forever."""

    class HangingProvider(BaseLLMProvider):
        is_simulated = False
        label = "Hanging"
        fallback_to_mock = True

        async def generate(self, **kwargs):
            await asyncio.sleep(30)
            return "never"

    app = create_app(ServerConfig(host="127.0.0.1", run_timeout=0.2))
    client = TestClient(app)
    client.get("/api/agents")
    state = app.state.registry._states[client.cookies.get("mmm_session")]
    for agent in state.mesh.agents.values():
        agent.provider = HangingProvider()

    response = client.post("/api/run", json={"topology": "p2p", "prompt": "build a rate limiter"})
    assert response.status_code == 504
    assert "run-timeout" in response.json()["detail"] or "never answered" in response.json()["detail"]
    # The session is usable again: the lock came back, and nothing is "busy".
    assert state.run_lock.locked() is False
    assert state.active_run_id is None
