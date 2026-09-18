"""
Tests for the bind policy and the access-token gate.

The rule the project commits to: loopback by default, and listening on any other
interface requires an explicit acknowledgement plus a shared token that every API
call and WebSocket handshake must satisfy.
"""

import pytest
from fastapi.testclient import TestClient

from machinelearningmachine.server.app import create_app
from machinelearningmachine.server.config import (
    AUTH_TOKEN_ENV_VAR,
    ServerConfig,
    is_loopback_host,
    normalize_origins,
    validate_bind_policy,
)

TOKEN = "a-sufficiently-long-random-token-value"


@pytest.fixture
def guarded_client():
    return TestClient(create_app(ServerConfig(auth_token=TOKEN, host="0.0.0.0", allow_public=True)))


@pytest.fixture
def local_client():
    return TestClient(create_app(ServerConfig(host="127.0.0.1")))


# ------------------------------------------------------------------ the policy

@pytest.mark.parametrize("host", ["127.0.0.1", "localhost", "::1", "127.0.0.53"])
def test_loopback_hosts_are_allowed_without_anything(host):
    assert is_loopback_host(host) is True
    assert validate_bind_policy(host, auth_token=None) == []


@pytest.mark.parametrize("host", ["0.0.0.0", "192.168.1.20", "10.0.0.5", "0.0.0.0", "203.0.113.9"])
def test_public_binds_are_refused_by_default(host):
    assert is_loopback_host(host) is False
    errors = validate_bind_policy(host, auth_token=None)
    assert errors, "binding outside loopback must be refused"
    assert "--allow-public" in errors[0]


def test_public_bind_needs_both_acknowledgement_and_token():
    assert validate_bind_policy("0.0.0.0", allow_public=True, auth_token=None)  # token missing
    assert validate_bind_policy("0.0.0.0", allow_public=False, auth_token=TOKEN)  # ack missing
    assert validate_bind_policy("0.0.0.0", allow_public=True, auth_token=TOKEN) == []


def test_short_tokens_are_rejected():
    errors = validate_bind_policy("0.0.0.0", allow_public=True, auth_token="abc123")
    assert any("too short" in e for e in errors)


def test_token_may_come_from_the_environment(monkeypatch):
    monkeypatch.setenv(AUTH_TOKEN_ENV_VAR, TOKEN)
    assert validate_bind_policy("0.0.0.0", allow_public=True, auth_token=None) == []


def test_wildcard_origins_are_dropped():
    assert normalize_origins(["*", "https://app.example/"]) == ("https://app.example",)
    assert normalize_origins(None) == ()


def test_cookie_security_follows_the_bind():
    assert ServerConfig(host="127.0.0.1").cookies_should_be_secure() is False
    assert ServerConfig(host="0.0.0.0", allow_public=True).cookies_should_be_secure() is True


# --------------------------------------------------------------- enforcement

def test_api_requires_the_token(local_client, guarded_client):
    assert local_client.get("/api/agents").status_code == 200          # loopback: no auth
    assert guarded_client.get("/api/agents").status_code == 401         # public: auth first
    assert guarded_client.get("/api/history").status_code == 401
    assert guarded_client.get("/api/sessions").status_code == 401
    assert guarded_client.post("/api/clear").status_code == 401
    assert guarded_client.post("/api/run", json={"topology": "p2p", "prompt": "Design a queue"}).status_code == 401


def test_index_and_health_stay_reachable_for_the_login_flow(guarded_client):
    assert guarded_client.get("/").status_code == 200
    assert guarded_client.get("/static/app.js").status_code == 200
    health = guarded_client.get("/health")
    assert health.status_code == 200
    body = health.json()
    assert body["auth_required"] is True
    # /health is unauthenticated, so it must not leak operational detail.
    assert "messages" not in body and "agents" not in body


def test_login_sets_a_session_that_unlocks_the_api(guarded_client):
    assert guarded_client.post("/api/auth/login", json={"token": "wrong-token-value"}).status_code == 401
    ok = guarded_client.post("/api/auth/login", json={"token": TOKEN})
    assert ok.status_code == 200
    assert ok.json()["authenticated"] is True
    assert guarded_client.get("/api/agents").status_code == 200
    # state is per-session, so a second client is still locked out
    other = TestClient(create_app(ServerConfig(auth_token=TOKEN, host="0.0.0.0", allow_public=True)))
    assert other.get("/api/agents").status_code == 401


def test_repeated_wrong_tokens_get_rate_limited():
    client = TestClient(create_app(ServerConfig(auth_token=TOKEN, host="0.0.0.0", allow_public=True)))
    statuses = [client.post("/api/auth/login", json={"token": f"guess-{i}"}).status_code for i in range(7)]
    assert statuses[:5] == [401] * 5
    assert 429 in statuses, "brute forcing the token must be slowed down"


def test_bearer_header_authorises_api_clients(guarded_client):
    guarded_client.headers["Authorization"] = f"Bearer {TOKEN}"
    assert guarded_client.get("/api/agents").status_code == 200
    assert guarded_client.get("/api/status").status_code == 200


def test_bearer_header_with_wrong_token_still_401(guarded_client):
    guarded_client.headers["Authorization"] = "Bearer not-the-token-at-all"
    assert guarded_client.get("/api/agents").status_code == 401


def test_logout_locks_the_session_again(guarded_client):
    guarded_client.post("/api/auth/login", json={"token": TOKEN})
    assert guarded_client.get("/api/agents").status_code == 200
    guarded_client.post("/api/auth/logout", json={})
    assert guarded_client.get("/api/agents").status_code == 401


# ------------------------------------------------------------------ websockets

def test_websocket_requires_auth(guarded_client, local_client):
    with local_client.websocket_connect("/ws") as ws:            # loopback: fine
        assert ws.receive_json()["type"] == "init"

    with pytest.raises(Exception):                                 # public: refused pre-handshake
        with guarded_client.websocket_connect("/ws") as ws:
            ws.receive_json()


def test_websocket_works_after_login(guarded_client):
    guarded_client.post("/api/auth/login", json={"token": TOKEN})
    with guarded_client.websocket_connect("/ws") as ws:
        init = ws.receive_json()
        assert init["type"] == "init"
        assert init["authenticated"] is True


# ------------------------------------------------------------------- cookies

def test_session_cookies_are_httponly_and_same_site_lax(guarded_client):
    resp = guarded_client.get("/api/auth/status")
    cookies = resp.headers.get_list("set-cookie")
    joined = " ".join(cookies).lower()
    assert "httponly" in joined
    assert "samesite=lax" in joined
    assert "samesite=none" not in joined


def test_token_never_appears_in_responses_or_health(guarded_client):
    for path in ("/health", "/api/auth/status", "/", "/openapi.json"):
        body = guarded_client.get(path).text
        assert TOKEN not in body


def test_check_token_uses_constant_time_comparison():
    cfg = ServerConfig(auth_token=TOKEN)
    assert cfg.check_token(TOKEN) is True
    assert cfg.check_token(TOKEN[:-1]) is False
    assert cfg.check_token(None) is False
    # No token configured -> loopback demo mode, everything passes.
    assert ServerConfig().check_token(None) is True


# ------------------------------------------- no free sessions for drive-by traffic

def test_anonymous_traffic_cannot_allocate_sessions():
    """
    Each session owns a mesh, a transcript and listeners, and the registry is
    bounded - so unauthenticated requests must not be able to create them and
    evict real users' state.
    """
    # One app, many cookie jars - the way a real server sees many browsers.
    app = create_app(ServerConfig(auth_token=TOKEN, host="0.0.0.0", allow_public=True))
    anon = TestClient(app)
    for _ in range(12):
        assert anon.get("/api/status").status_code == 401
        assert anon.post("/api/agents", json={"agent_id": "x"}).status_code == 401

    authed = TestClient(app)
    assert authed.post("/api/auth/login", json={"token": TOKEN}).status_code == 200
    status = authed.get("/api/status").json()
    # 12 anonymous calls + this sign-in: only the session that proved the token exists.
    assert status["live_sessions"] <= 1, status


def test_failed_signins_lock_the_client_out_even_with_fresh_cookies():
    """The counter belongs to the client, not to a cookie jar that can be dropped."""
    app = create_app(ServerConfig(auth_token=TOKEN, host="0.0.0.0", allow_public=True))
    for _ in range(5):
        client = TestClient(app)                      # brand-new cookie jar each time
        assert client.post("/api/auth/login", json={"token": "wrong-token-value"}).status_code == 401

    sixth = TestClient(app)
    resp = sixth.post("/api/auth/login", json={"token": TOKEN})
    assert resp.status_code == 429, resp.text
    assert "try again" in resp.json()["detail"].lower()
    # ...and the page itself still loads, so the user can wait it out and retry.
    assert sixth.get("/").status_code == 200


def test_successful_login_clears_the_failure_counter():
    app = create_app(ServerConfig(auth_token=TOKEN, host="0.0.0.0", allow_public=True))
    client = TestClient(app)
    client.post("/api/auth/login", json={"token": "nope-nope-nope-nope"})
    client.post("/api/auth/login", json={"token": "nope-nope-nope-nope"})
    assert client.post("/api/auth/login", json={"token": TOKEN}).status_code == 200
    # A different browser on the same host is unaffected by our reset.
    fresh = TestClient(app)
    assert fresh.post("/api/auth/login", json={"token": "bad-once-only"}).status_code == 401


def test_logout_state_survives_a_missing_session(guarded_client):
    """Signing out with no session at all must not become a 500."""
    assert guarded_client.post("/api/auth/logout").status_code == 200


def test_login_throttle_is_bounded():
    from machinelearningmachine.server.state import LoginThrottle

    throttle = LoginThrottle(max_failures=2, lockout_seconds=60.0, max_keys=32)
    now = 1000.0
    for i in range(100):
        throttle.record_failure(f"client-{i}", now=now)
    assert throttle.size() <= 32, "the throttle itself must not grow without bound"
    # A locked key stays locked for the window, and the counter expires afterwards.
    throttle.record_failure("noisy", now=now)
    throttle.record_failure("noisy", now=now)
    assert throttle.locked_for("noisy", now=now + 1) > 0
    assert throttle.locked_for("noisy", now=now + 120) == 0.0
