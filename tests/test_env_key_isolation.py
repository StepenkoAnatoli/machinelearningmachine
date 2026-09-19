"""
The dashboard must never spend a credential it was not given.

Reproduced before the fix, with ``OPENAI_API_KEY=sk-OPERATOR-SECRET-...`` exported
in the server's environment and *no* key sent by the client::

    POST /api/config {"openai_base_url": "http://127.0.0.1:18099/v1"}
    POST /api/run    {"topology": "p2p", ...}

    captured on the wire:  Authorization: Bearer sk-OPERATOR-SECRET-...

``OpenAIProvider.__init__`` fell back to ``os.environ`` whenever the caller handed
it an empty key, so any browser that could reach ``/api/config`` could move the
operator's key to an endpoint of its choosing (the shape check on the key was
skipped for "local" URLs, and ``{"verify": true}`` sent it eagerly). That is why
SECURITY.md could claim "``openai_base_url`` is operator-supplied" - with a
key-reading fallback, it was not.

The rule now: only an explicit, operator-side call (the library API, ``module-mesh
run --live``) may read a key from the environment. Anything built for a browser
session uses exactly what that session supplied, or nothing.
"""

import asyncio
import json
import threading
import time

import pytest
from aiohttp import web
from fastapi.testclient import TestClient

from machinelearningmachine.agents.providers import AnthropicProvider, OpenAIProvider
from machinelearningmachine.server.app import create_app
from machinelearningmachine.server.config import ServerConfig

ENV_KEY = "sk-ope…3456"
ENV_ANTHROPIC_KEY = "sk-ant-OPERATOR-SECRET-0123456789"


class FakeProviderServer:
    """
    A stand-in for an "OpenAI-compatible" endpoint on an address a browser chose.

    Listens on a free port on loopback and records every header it is sent, which
    is the only way to assert what actually left the machine rather than what the
    server intended to send.
    """

    def __init__(self):
        self.port = None
        self.requests = []
        self.response = {"choices": [{"message": {"content": "answer from the fake endpoint"}}]}
        self._loop = None
        self._thread = None
        self._ready = threading.Event()
        self._stop = None

    async def _handle(self, request):
        self.requests.append({
            "path": request.path,
            "headers": dict(request.headers),
            "body": await request.text(),
        })
        return web.json_response(self.response)

    async def _handle_models(self, request):
        self.requests.append({"path": request.path, "headers": dict(request.headers), "body": ""})
        return web.json_response({"data": [{"id": "gpt-6-astra"}]})

    def __enter__(self):
        async def run():
            app = web.Application()
            app.router.add_post("/v1/chat/completions", self._handle)
            app.router.add_get("/v1/models", self._handle_models)
            runner = web.AppRunner(app)
            await runner.setup()
            site = web.TCPSite(runner, "127.0.0.1", 0)
            await site.start()
            self.port = runner.addresses[0][1]
            # A future rather than a long sleep: __exit__ resolves it, the
            # coroutine returns normally, and no "coroutine ignored GeneratorExit"
            # warning is left behind to hide a real one.
            self._stop = asyncio.get_running_loop().create_future()
            self._ready.set()
            await self._stop
            await runner.cleanup()

        def thread_main():
            self._loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self._loop)
            try:
                self._loop.run_until_complete(run())
            except (asyncio.CancelledError, RuntimeError):
                pass
            finally:
                self._loop.close()

        self._thread = threading.Thread(target=thread_main, daemon=True)
        self._thread.start()
        assert self._ready.wait(10), "the fake provider endpoint did not start"
        return self

    def __exit__(self, *exc):
        if getattr(self, "_stop", None) is not None and self._loop is not None:
            self._loop.call_soon_threadsafe(
                lambda: None if self._stop.done() else self._stop.set_result(None)
            )
            self._thread.join(5)
        return False

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}/v1"

    def wait_for_traffic(self, seconds: float = 1.0) -> None:
        deadline = time.monotonic() + seconds
        while not self.requests and time.monotonic() < deadline:
            time.sleep(0.01)
        time.sleep(0.05)  # let any follow-up request land too

    def auth_headers(self):
        return [r["headers"].get("Authorization") for r in self.requests]


@pytest.fixture(autouse=True)
def operator_keys(monkeypatch):
    """The environment of the *machine running the server*, not of any browser."""
    monkeypatch.setenv("OPENAI_API_KEY", ENV_KEY)
    monkeypatch.setenv("ANTHROPIC_API_KEY", ENV_ANTHROPIC_KEY)


def test_dashboard_supplied_base_url_never_picks_up_the_environment_key():
    with FakeProviderServer() as fake:
        app = create_app(ServerConfig(host="127.0.0.1"))
        with TestClient(app) as client:
            configured = client.post(
                "/api/config",
                json={"openai_base_url": fake.base_url, "openai_model": "gpt-5.6-terra"},
            )
            assert configured.status_code == 200, configured.text
            run = client.post(
                "/api/run",
                json={"topology": "p2p", "prompt": "design a cache layer", "turns": 2},
            )
            assert run.status_code == 200, run.text
            fake.wait_for_traffic()

        assert fake.requests, "the endpoint a browser chose should have been called"
        for header in fake.auth_headers():
            assert ENV_KEY not in (header or ""), "the operator's ambient key left the machine"
        # The run is labelled for what it was: an answer from that endpoint, sent
        # with no credential at all - not a silently substituted simulation.
        requests = [r for r in fake.requests if r["path"].endswith("/chat/completions")]
        assert requests
        request_body = json.loads(requests[0]["body"])
        assert request_body["model"] == "gpt-5.6-terra"
        assert request_body["reasoning_effort"] == "medium", "current GPT models use reasoning effort"
        messages = run.json()["messages"]
        assert any("answer from the fake endpoint" in m["content"] for m in messages)
        assert all(not m["metadata"].get("simulated") for m in messages if "fake endpoint" in m["content"])


def test_config_endpoint_reports_no_key_when_only_a_url_was_given():
    with FakeProviderServer() as fake:
        app = create_app(ServerConfig(host="127.0.0.1"))
        with TestClient(app) as client:
            client.post("/api/config", json={"openai_base_url": fake.base_url})
            status = client.get("/api/config").json()
        assert status["has_openai_key"] is False, "a base URL is not a credential"
        assert status["mode"] == "live"
        assert ENV_KEY not in client.get("/api/config").text


def test_verification_also_sends_no_ambient_key():
    """``{"verify": true}`` was the cleanest exfiltration path: one GET, header included."""
    with FakeProviderServer() as fake:
        app = create_app(ServerConfig(host="127.0.0.1"))
        with TestClient(app) as client:
            response = client.post("/api/config", json={"openai_base_url": fake.base_url, "verify": True})
            assert response.status_code == 200, response.text
            fake.wait_for_traffic()
        paths = [r["path"] for r in fake.requests]
        assert "/v1/models" in paths, "verification should have been attempted"
        for req in fake.requests:
            assert ENV_KEY not in str(req["headers"])


def test_a_key_the_session_supplied_is_used_for_that_session():
    with FakeProviderServer() as fake:
        app = create_app(ServerConfig(host="127.0.0.1"))
        mine = "sk-" + "u" * 24
        with TestClient(app) as client:
            client.post("/api/config", json={"openai_api_key": mine, "openai_base_url": fake.base_url})
            assert client.get("/api/config").json()["has_openai_key"] is True
            client.post("/api/run", json={"topology": "p2p", "prompt": "design a cache layer", "turns": 2})
            fake.wait_for_traffic()

            # A second browser on the same server never inherits the first one's key.
            other = TestClient(app)
            other.post("/api/config", json={"openai_base_url": fake.base_url})
            other.post("/api/run", json={"topology": "p2p", "prompt": "design a cache layer", "turns": 2})
            fake.wait_for_traffic()

        headers = [h for h in fake.auth_headers() if h]
        assert f"Bearer {mine}" in headers, "an explicitly configured key must still work"
        assert all(ENV_KEY not in h for h in headers), "no session may send the ambient key"
        assert len(headers) == len([h for h in headers if h == f"Bearer {mine}"]), (
            f"the keyless session must send no Authorization header at all: {headers}"
        )


# ------------------------------------------------------------------ provider API

def test_library_callers_may_still_use_the_environment(monkeypatch):
    """``allow_env_key`` defaults to True: the documented library/CLI path is intact."""
    monkeypatch.setenv("OPENAI_API_KEY", ENV_KEY)
    assert OpenAIProvider().api_key == ENV_KEY
    assert AnthropicProvider().api_key == ENV_ANTHROPIC_KEY


def test_dashboard_style_providers_do_not(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", ENV_KEY)
    monkeypatch.setenv("ANTHROPIC_API_KEY", ENV_ANTHROPIC_KEY)
    assert OpenAIProvider(api_key="", base_url="http://localhost:11434/v1", allow_env_key=False).api_key == ""
    assert AnthropicProvider(api_key="", allow_env_key=False).api_key == ""


def test_keys_are_trimmed_and_never_stored_in_provider_config():
    app = create_app(ServerConfig(host="127.0.0.1"))
    with TestClient(app) as client:
        secret = "sk-" + "z" * 30
        client.post("/api/config", json={"openai_api_key": secret})
        state = app.state.registry._states[client.cookies.get("mmm_session")]
        assert state.api_keys["openai"] == secret
        assert secret not in json.dumps(state.provider_config)
