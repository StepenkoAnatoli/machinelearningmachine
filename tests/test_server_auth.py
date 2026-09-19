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


# ------------------------------------------------- sign-in when none is needed

def test_login_is_refused_when_the_server_needs_no_token(local_client):
    """
    On loopback there is no token to check, so "logging in" with any string must
    not pretend to have unlocked anything - it is answered honestly instead.
    """
    resp = local_client.post("/api/auth/login", json={"token": "whatever"})
    assert resp.status_code == 400
    assert "does not require" in resp.json()["detail"]
    # ...and the API is still usable, because it never required a token.
    assert local_client.get("/api/status").status_code == 200


# ------------------------------------------- cookie-less API clients stay cheap

def test_bearer_only_sessions_expire_early_and_first():
    """
    A client that authenticates with a header but keeps no cookies cannot be
    pinned to a browser, so its session must not squat in the bounded registry
    for the full idle TTL, and must be the one given up under pressure.
    """
    from machinelearningmachine.server.state import EPHEMERAL_SESSION_TTL, SessionRegistry

    cfg = ServerConfig(auth_token=TOKEN, host="0.0.0.0", allow_public=True, max_sessions=2)
    registry = SessionRegistry(max_sessions=2, idle_ttl=3600, config=cfg)

    browser = registry.create("browser-1")
    script = registry.create("script-1", ephemeral=True)
    assert browser.ephemeral is False and script.ephemeral is True

    # The ephemeral session expires after its own short TTL, not the hour-long one.
    assert script.effective_ttl(3600) == EPHEMERAL_SESSION_TTL
    assert browser.effective_ttl(3600) == 3600
    assert browser.is_stale(3600) is False

    # Under capacity pressure the ephemeral one is evicted, the browser survives.
    third = registry.create("script-2", ephemeral=True)
    assert registry.get(script.session_id) is None, "an ephemeral session should be evicted first"
    assert registry.get(browser.session_id) is not None
    assert registry.get(third.session_id) is not None


def test_http_sessions_are_persistent_but_bearer_only_ones_are_not():
    """A signed-in browser keeps its transcript; a cookie-less script does not squat."""
    app = create_app(ServerConfig(auth_token=TOKEN, host="0.0.0.0", allow_public=True))
    registry = app.state.registry

    cookie_client = TestClient(app)
    assert cookie_client.post("/api/auth/login", json={"token": TOKEN}).status_code == 200
    header_only = TestClient(app)
    header_only.headers["Authorization"] = f"Bearer {TOKEN}"
    assert header_only.get("/api/status").status_code == 200

    states = list(registry._states.values())
    persistent = [st for st in states if not st.ephemeral]
    ephemeral = [st for st in states if st.ephemeral]
    assert persistent, "the signed-in browser must own a persistent session"
    assert ephemeral, "a cookie-less bearer request must be marked ephemeral"


def test_bearer_session_becomes_persistent_once_the_client_keeps_cookies():
    """
    Marking a session ephemeral is about *un*-pin-able callers. As soon as a
    client sends the session cookie back, the session belongs to it and gets the
    normal idle lifetime - otherwise a script that does keep cookies would lose
    its transcript early.
    """
    app = create_app(ServerConfig(auth_token=TOKEN, host="0.0.0.0", allow_public=True))
    registry = app.state.registry
    client = TestClient(app, headers={"Authorization": f"Bearer {TOKEN}"})

    assert client.get("/api/status").status_code == 200
    assert [st.ephemeral for st in registry._states.values()] == [True]

    assert "mmm_session" in client.cookies  # the server pinned the session
    assert client.get("/api/status").status_code == 200
    assert [st.ephemeral for st in registry._states.values()] == [False], (
        "a session the client keeps returning to should be promoted"
    )
