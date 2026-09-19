"""
Who is allowed to choose the address the mesh dials out to.

SECURITY.md said ``POST /api/read/url`` "is the one endpoint that talks to a
user-supplied address", and that ``openai_base_url`` was operator-supplied. The
second claim depended on a detail that no longer holds: ``/api/config`` takes an
arbitrary base URL, and ``/api/run`` POSTs the conversation there - so on a bind
another machine can reach, a token holder could aim the server at
``169.254.169.254``, at CGNAT space, or at an internal port, and use
``{"verify": true}`` as a clean ``GET /models`` oracle for which ports are open.

The policy applied here is deliberately asymmetric, because the trust
relationship is: on loopback the operator *is* the caller (Ollama on 127.0.0.1 is
the documented setup, so nothing may break it); once the port is reachable from
elsewhere, a browser-supplied address is a *user* input and gets the same
treatment as the page reader's URL.
"""

import pytest
from fastapi.testclient import TestClient

from machinelearningmachine import netguard
from machinelearningmachine.server.app import _guard_provider_url, create_app
from machinelearningmachine.server.config import ServerConfig

TOKEN = "a-sufficiently-long-random-token-value"


def _resolver(mapping):
    return lambda host: list(mapping.get(host, []))


# ------------------------------------------------------------ netguard policy

@pytest.mark.parametrize("url,reason", [
    ("http://127.0.0.1:11434/v1", "loopback"),
    ("http://169.254.169.254/latest/meta-data/", "cloud metadata"),
    ("http://10.0.0.7:8000/v1", "private"),
    ("http://100.64.0.1:8080/v1", "CGNAT"),
    ("http://[::1]:11434/v1", "IPv6 loopback"),
    ("http://192.0.0.1/v1", "IETF reserved"),
])
def test_private_targets_are_refused_for_a_browser(url, reason):
    with pytest.raises(netguard.UnsafeURL) as exc:
        netguard.validate_provider_target(url, allow_private=False, resolver=_resolver({}))
    assert "private or local" in exc.value.reason, reason


def test_public_targets_are_accepted_on_any_bind():
    target = netguard.validate_provider_target(
        "https://api.openai.com/v1", allow_private=False,
        resolver=_resolver({"api.openai.com": ["104.18.0.1"]}),
    )
    assert target.port == 443 and target.host == "api.openai.com"


def test_local_backends_are_fine_when_the_caller_is_the_operator():
    target = netguard.validate_provider_target("http://localhost:11434/v1", allow_private=True)
    assert target.url == "http://localhost:11434/v1"


def test_any_port_is_allowed_because_ollama_and_vllm_do_not_live_on_80():
    target = netguard.validate_provider_target(
        "http://gpu.example:8000/v1", allow_private=False,
        resolver=_resolver({"gpu.example": ["93.184.216.34"]}),
    )
    assert target.port == 8000


@pytest.mark.parametrize("url,needle", [
    ("ftp://host/v1", "http:// or https://"),
    ("http://:8080/v1", "hostname"),
    ("http://user:pass@host/v1", "credentials"),
    ("http://host:abc/v1", "invalid port"),
    ("   ", "enter a base URL"),
    ("https://unresolvable.example/v1", "does not resolve"),
])
def test_malformed_urls_are_rejected_with_a_plain_reason(url, needle):
    with pytest.raises(netguard.UnsafeURL) as exc:
        netguard.validate_provider_target(url, allow_private=False, resolver=_resolver({}))
    assert needle.lower() in exc.value.reason.lower()


def test_a_too_long_url_is_rejected():
    with pytest.raises(netguard.UnsafeURL):
        netguard.validate_provider_target("https://h.example/" + "p" * 600, allow_private=True)


def test_decimal_host_forms_are_caught_by_the_resolver_not_the_string():
    """``http://0x7f000001/`` is loopback to glibc, and to the connect() call."""
    with pytest.raises(netguard.UnsafeURL):
        netguard.validate_provider_target(
            "http://0x7f000001:11434/v1", allow_private=False,
            resolver=_resolver({"0x7f000001": ["127.0.0.1"]}),
        )


def test_operator_allowlist_opens_one_named_host():
    target = netguard.validate_provider_target(
        "http://ollama.internal:11434/v1", allow_private=False,
        resolver=_resolver({"ollama.internal": ["10.0.0.9"]}),
        extra_allowed_hosts=("ollama.internal",),
    )
    assert target.port == 11434


def test_ambient_url_allowlist_is_honoured(monkeypatch):
    monkeypatch.setenv("MACHINELEARNINGMACHINE_URL_ALLOWLIST", "inference.lan")
    target = netguard.validate_provider_target(
        "http://a.inference.lan:8000/v1", allow_private=False,
        resolver=_resolver({"a.inference.lan": ["10.1.1.1"]}),
    )
    assert target.host == "a.inference.lan"
    # ...and only that host: an unrelated private address stays blocked.
    with pytest.raises(netguard.UnsafeURL):
        netguard.validate_provider_target(
            "http://10.1.1.2:8000/v1", allow_private=False, resolver=_resolver({})
        )


def test_no_ambient_allowlist_means_no_bypass(monkeypatch):
    """
    Regression guard: ``allowed_by_operator`` returns True when nothing is
    configured (it means "fall back to the blocklist"), which must not be read as
    "every host was explicitly allowed".
    """
    monkeypatch.delenv("MACHINELEARNINGMACHINE_URL_ALLOWLIST", raising=False)
    monkeypatch.delenv("MODULE_MESH_URL_ALLOWLIST", raising=False)
    with pytest.raises(netguard.UnsafeURL):
        netguard.validate_provider_target(
            "http://10.0.0.7:8000/v1", allow_private=False, resolver=_resolver({})
        )


# ------------------------------------------------------------------ the endpoint

def _app(**kwargs):
    return create_app(ServerConfig(**kwargs))


def test_loopback_bind_allows_a_local_backend():
    app = _app(host="127.0.0.1")
    with TestClient(app) as client:
        response = client.post("/api/config", json={"openai_base_url": "http://localhost:11434/v1"})
        assert response.status_code == 200, response.text
        assert client.get("/api/config").json()["mode"] == "live"


def test_public_bind_refuses_a_local_backend_for_a_browser():
    app = _app(host="0.0.0.0", allow_public=True, auth_token=TOKEN)
    with TestClient(app) as client:
        client.post("/api/auth/login", json={"token": TOKEN})
        response = client.post("/api/config", json={"openai_base_url": "http://127.0.0.1:11434/v1"})
        assert response.status_code == 400
        assert "server-side request forgery" in response.json()["detail"]
        # Nothing was configured by the refused request.
        assert client.get("/api/config").json()["mode"] == "simulated"


def test_public_bind_refuses_metadata_addresses():
    app = _app(host="0.0.0.0", allow_public=True, auth_token=TOKEN)
    with TestClient(app) as client:
        client.post("/api/auth/login", json={"token": TOKEN})
        for url in ("http://169.254.169.254/latest/meta-data/", "http://100.64.0.1:8080/v1"):
            response = client.post("/api/config", json={"openai_base_url": url})
            assert response.status_code == 400, url


def test_public_bind_still_allows_a_real_provider_endpoint():
    app = _app(host="0.0.0.0", allow_public=True, auth_token=TOKEN)
    with TestClient(app) as client:
        client.post("/api/auth/login", json={"token": TOKEN})
        response = client.post(
            "/api/config",
            json={"openai_api_key": "sk-" + "k" * 30, "openai_base_url": "https://api.openai.com/v1"},
        )
        assert response.status_code == 200, response.text


def test_operator_can_opt_the_local_backend_back_in():
    app = _app(host="0.0.0.0", allow_public=True, auth_token=TOKEN, allow_insecure_provider_urls=True)
    with TestClient(app) as client:
        client.post("/api/auth/login", json={"token": TOKEN})
        response = client.post("/api/config", json={"openai_base_url": "http://localhost:11434/v1"})
        assert response.status_code == 200, response.text


def test_guard_helper_is_a_no_op_on_loopback():
    _guard_provider_url(ServerConfig(host="127.0.0.1"), "http://127.0.0.1:9/v1")


def test_a_base_url_change_cannot_reach_a_cleared_key():
    """Configuring a URL must not resurrect a key the session cleared."""
    app = _app(host="127.0.0.1")
    with TestClient(app) as client:
        client.post("/api/config", json={"openai_api_key": "sk-" + "a" * 30})
        client.post("/api/config", json={"clear": True})
        assert client.get("/api/config").json()["has_openai_key"] is False
        state = app.state.registry._states[client.cookies.get("mmm_session")]
        assert state.api_keys == {}
        client.post("/api/config", json={"openai_base_url": "http://localhost:11434/v1"})
        assert state.api_keys == {}
