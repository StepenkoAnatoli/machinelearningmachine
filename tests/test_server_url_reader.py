"""
Tests for the web page reader as exposed over HTTP.

The IP/scheme/redirect policy itself is covered exhaustively in
``tests/test_netguard.py``. These tests cover the *endpoint*: that the feature is
gated, that policy decisions reach the client as honest errors instead of
tracebacks, that nothing sensitive leaks into responses, that the byte cap is
actually passed to the fetcher, and that the cooldown is per session.

No network access happens here - ``guarded_fetch`` is replaced with a stub.
"""

import pytest
from fastapi.testclient import TestClient

from machinelearningmachine import netguard
from machinelearningmachine.server.app import MAX_READ_BYTES, create_app
from machinelearningmachine.server.config import ServerConfig


def _result(url="https://example.com/docs", *, text="<h1>Retries</h1><p>Use tenacity.</p>", truncated=False):
    return netguard.FetchResult(
        content=text.encode("utf-8"),
        content_type="text/html; charset=utf-8",
        final_url=url,
        requested_url=url,
        status_code=200,
        truncated=truncated,
        redirects=0,
        addresses=("93.184.216.34",),
    )


@pytest.fixture
def reader_client(monkeypatch):
    """A client with the reader enabled and the fetcher stubbed out."""
    calls = []

    def fake_fetch(url, *args, **kwargs):
        calls.append({"url": url, "kwargs": kwargs})
        return _result(url=url) if fake_fetch.result is None else fake_fetch.result

    fake_fetch.result = None
    monkeypatch.setattr(netguard, "guarded_fetch", fake_fetch)
    client = TestClient(create_app(ServerConfig(host="127.0.0.1", enable_url_reader=True)))
    client.fetch_calls = calls
    return client


def _post_read(client, url="https://example.com/docs"):
    return client.post("/api/read/url", json={"url": url})


def test_reader_returns_extracted_text_not_html(reader_client):
    resp = _post_read(reader_client)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "ok"
    assert "Retries" in body["text"]
    assert "<h1>" not in body["text"], "raw markup must not be forwarded to the client"
    assert body["url"] == "https://example.com/docs"


def test_fetcher_is_called_with_the_byte_cap(reader_client):
    _post_read(reader_client)
    call = reader_client.fetch_calls[0]
    assert call["url"] == "https://example.com/docs"
    assert call["kwargs"].get("max_bytes") == MAX_READ_BYTES


def test_truncation_is_reported_to_the_client(reader_client, monkeypatch):
    monkeypatch.setattr(
        netguard, "guarded_fetch",
        lambda url, *a, **k: _result(url=url, text="<p>cut off</p>", truncated=True),
    )
    body = _post_read(reader_client).json()
    assert body.get("truncated") is True, "clients must know the page was only partially read"


def test_unsafe_url_becomes_a_plain_reason(reader_client, monkeypatch):
    def blocked(url, *a, **k):
        raise netguard.UnsafeURL("that address is loopback", detail=str(url))

    monkeypatch.setattr(netguard, "guarded_fetch", blocked)
    resp = _post_read(reader_client, url="http://127.0.0.1:8080/admin")
    assert resp.status_code == 400
    assert "loopback" in resp.json()["detail"]


def test_fetch_failure_reason_is_forwarded(reader_client, monkeypatch):
    def fails(url, *a, **k):
        raise netguard.FetchError("the server replied with 404", status_code=404)

    monkeypatch.setattr(netguard, "guarded_fetch", fails)
    resp = _post_read(reader_client)
    assert resp.status_code == 400
    assert "404" in resp.json()["detail"]


def test_unexpected_error_does_not_leak_the_target_host(reader_client, monkeypatch):
    def explodes(url, *a, **k):
        raise RuntimeError("connection to db.internal.corp:5432 refused")

    monkeypatch.setattr(netguard, "guarded_fetch", explodes)
    resp = _post_read(reader_client)
    assert resp.status_code == 500
    detail = resp.json()["detail"]
    assert "db.internal.corp" not in detail and "5432" not in detail, detail


def test_page_without_text_is_rejected(reader_client, monkeypatch):
    monkeypatch.setattr(
        netguard, "guarded_fetch",
        lambda url, *a, **k: _result(url=url, text="   \n  \n "),
    )
    assert _post_read(reader_client).status_code == 422


def test_reads_are_rate_limited_per_session(reader_client):
    assert _post_read(reader_client).status_code == 200
    second = _post_read(reader_client)
    assert second.status_code == 429
    assert "wait" in second.json()["detail"].lower()


def test_cooldown_is_not_shared_between_sessions(reader_client):
    """One browser hammering the reader must not lock the others out."""
    assert _post_read(reader_client).status_code == 200

    other = TestClient(create_app(ServerConfig(host="127.0.0.1", enable_url_reader=True)))
    assert _post_read(other).status_code == 200, "a second session was punished for the first one's request"

    assert _post_read(reader_client).status_code == 429, "the first session should still be cooling down"


def test_reader_disabled_is_404_not_403():
    """Absent, not 'forbidden': a disabled feature should not advertise itself."""
    client = TestClient(create_app(ServerConfig(host="127.0.0.1")))
    assert client.post("/api/read/url", json={"url": "https://example.com"}).status_code == 404


def test_invalid_url_shape_is_rejected_before_the_fetcher(reader_client):
    resp = _post_read(reader_client, url="ftp://example.com/pub")
    assert resp.status_code in (400, 422)
    assert reader_client.fetch_calls == [], "validation must happen before any fetch"
