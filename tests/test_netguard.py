"""
Tests for the SSRF boundary (machinelearningmachine.netguard).

Everything here is offline: the resolver and the transport are injected, so the
tests assert on the *policy* (what may be contacted) rather than on the network.
"""

import pytest

from machinelearningmachine import netguard
from machinelearningmachine.netguard import FetchError, RawResponse, UnsafeURL, guarded_fetch, is_blocked_ip

PUBLIC_IP = "93.184.216.34"


def resolver_for(*ips):
    return lambda host: list(ips)


# ---------------------------------------------------------------- blocking rules

@pytest.mark.parametrize("url", [
    "http://127.0.0.1/admin",
    "http://127.0.0.2:80/x",           # whole 127/8 is loopback
    "http://localhost/x",
    "http://[::1]/x",
    "http://[::ffff:127.0.0.1]/x",      # IPv4-mapped IPv6
    "http://169.254.169.254/latest/meta-data/",   # AWS/GCP metadata (link-local)
    "http://10.0.0.7/x",
    "http://172.16.5.4/x",
    "http://192.168.1.1/x",
    "http://100.64.0.1/x",              # CGNAT
    "http://0.0.0.0/x",
    "http://224.0.0.1/x",               # multicast
    "http://[fd00::1]/x",               # IPv6 unique local
    "http://[fe80::1]/x",               # link local
])
def test_blocks_addresses_that_point_back_at_the_host(url):
    # Even if DNS claims a public answer, a private *literal* must stay blocked.
    with pytest.raises(UnsafeURL):
        netguard.validate_target(url, resolver=resolver_for(PUBLIC_IP))


def test_blocks_names_that_resolve_to_internal_addresses():
    # Provider metadata hostnames resolve into link-local, so the IP rule covers
    # them - which also means they cannot be whitelisted by name.
    for name in ("internal.corp", "metadata.google.internal"):
        with pytest.raises(UnsafeURL) as exc:
            netguard.validate_target(f"http://{name}/x", resolver=resolver_for("169.254.169.254"))
        assert "private or local network" in exc.value.reason


def test_blocks_a_name_resolving_to_mixed_public_and_private():
    # One public answer does not license the private one (happy-eyeballs style attacks).
    with pytest.raises(UnsafeURL):
        netguard.validate_target("http://both.example/x", resolver=resolver_for(PUBLIC_IP, "127.0.0.1"))


@pytest.mark.parametrize("url", [
    "file:///etc/passwd",
    "gopher://127.0.0.1:11211/x",
    "http://127.0.0.1:6379/",          # scheme ok, port not
    "http://example.com:8080/x",
    "https://example.com:4444/x",
    "http://user:pass@example.com/",
    "",
    "not a url",
])
def test_blocks_non_http_schemes_and_ports(url):
    with pytest.raises(UnsafeURL):
        netguard.validate_target(url, resolver=resolver_for(PUBLIC_IP))


def test_allows_an_ordinary_public_page():
    target = netguard.validate_target("https://example.com/a/b?x=1", resolver=resolver_for(PUBLIC_IP))
    assert target.host == "example.com"
    assert target.port == 443
    assert "x=1" in target.url


def test_hex_and_decimal_ip_encodings_are_caught_by_the_resolver():
    # glibc maps these to 127.0.0.1; the policy checks resolution, not spelling.
    with pytest.raises(UnsafeURL):
        netguard.validate_target("http://0x7f000001/x", resolver=resolver_for("127.0.0.1"))
    with pytest.raises(UnsafeURL):
        netguard.validate_target("http://2130706433/x", resolver=resolver_for("127.0.0.1"))


@pytest.mark.parametrize("ip", ["127.0.0.1", "::1", "10.1.1.1", "169.254.1.1", "255.255.255.255", "0.0.0.0"])
def test_is_blocked_ip_covers_local_ranges(ip):
    assert is_blocked_ip(ip) is True


@pytest.mark.parametrize("ip", [PUBLIC_IP, "1.1.1.1", "8.8.8.8", "2606:2800:220:1:26ff:ff00:3ed:c71d"])
def test_public_addresses_are_not_blocked(ip):
    assert is_blocked_ip(ip) is False


def test_unparseable_address_is_treated_as_unsafe():
    assert is_blocked_ip("not-an-ip") is True


def test_operator_allowlist_overrides_the_blocklist_mode(monkeypatch):
    monkeypatch.setenv(netguard.URL_ALLOWLIST_ENV_VAR, "docs.example.com")
    with pytest.raises(UnsafeURL) as exc:
        netguard.validate_target("https://other.example/x", resolver=resolver_for(PUBLIC_IP))
    assert "allowlist" in exc.value.reason
    ok = netguard.validate_target("https://docs.example.com/x", resolver=resolver_for(PUBLIC_IP))
    assert ok.host == "docs.example.com"
    # An allowlisted name still cannot resolve to localhost.
    with pytest.raises(UnsafeURL):
        netguard.validate_target("https://docs.example.com/x", resolver=resolver_for("127.0.0.1"))


# ------------------------------------------------------------------- fetching

class FakeTransport:
    """Records the URLs the policy actually allowed through."""

    def __init__(self, responses):
        self.responses = responses  # url -> RawResponse
        self.seen = []

    def __call__(self, url, timeout, max_bytes):
        self.seen.append(url)
        if url not in self.responses:
            raise AssertionError(f"transport was asked for an unexpected URL: {url}")
        resp = self.responses[url]
        if isinstance(resp, Exception):
            raise resp
        return resp


def html_resp(body=b"<html><body>hi</body></html>", status=200, extra=None):
    headers = {"content-type": "text/html; charset=utf-8"}
    headers.update(extra or {})
    return RawResponse(status_code=status, headers=headers, content=body)


def test_fetch_returns_public_page_body():
    transport = FakeTransport({"https://example.com/": html_resp(b"<html>hello</html>")})
    result = guarded_fetch("https://example.com/", resolver=resolver_for(PUBLIC_IP), transport=transport)
    assert result.text == "<html>hello</html>"
    assert result.status_code == 200


def test_fetch_follows_a_public_redirect_and_validates_it():
    transport = FakeTransport({
        "https://example.com/start": RawResponse(302, {"location": "https://other.example/end"}),
        "https://other.example/end": html_resp(b"done"),
    })
    result = guarded_fetch("https://example.com/start", resolver=resolver_for(PUBLIC_IP), transport=transport)
    assert result.redirects == 1
    assert result.text == "done"


def test_redirect_into_the_metadata_service_is_blocked():
    # The classic SSRF chain: public URL -> 302 -> 169.254.169.254.
    transport = FakeTransport({
        "https://example.com/start": RawResponse(302, {"location": "http://169.254.169.254/latest/meta-data/"}),
    })
    with pytest.raises(UnsafeURL):
        guarded_fetch("https://example.com/start", resolver=resolver_for(PUBLIC_IP), transport=transport)
    assert transport.seen == ["https://example.com/start"], "the internal hop must never be fetched"


def test_redirect_to_localhost_by_name_is_blocked():
    transport = FakeTransport({
        "https://example.com/start": RawResponse(301, {"location": "http://localhost:8000/api/agents"}),
    })
    with pytest.raises(UnsafeURL):
        guarded_fetch("https://example.com/start", resolver=resolver_for(PUBLIC_IP), transport=transport)
    assert transport.seen == ["https://example.com/start"]


def test_redirect_loop_is_stopped():
    responses = {
        f"https://loop{i}.example/": RawResponse(302, {"location": f"https://loop{i + 1}.example/"})
        for i in range(10)
    }
    transport = FakeTransport(responses)
    with pytest.raises(FetchError):
        guarded_fetch("https://loop0.example/", resolver=resolver_for(PUBLIC_IP), transport=transport, max_redirects=3)


def test_error_status_becomes_a_user_safe_fetch_error():
    transport = FakeTransport({"https://example.com/gone": RawResponse(404, {}, b"nope")})
    with pytest.raises(FetchError) as exc:
        guarded_fetch("https://example.com/gone", resolver=resolver_for(PUBLIC_IP), transport=transport)
    assert "404" in exc.value.reason


def test_binary_content_type_is_refused():
    transport = FakeTransport({
        "https://example.com/file": RawResponse(200, {"content-type": "application/pdf"}, b"%PDF-1.7")
    })
    with pytest.raises(FetchError) as exc:
        guarded_fetch("https://example.com/file", resolver=resolver_for(PUBLIC_IP), transport=transport)
    assert "not a readable text" in exc.value.reason


def test_oversized_body_is_flagged_as_truncated_not_buffered_forever():
    transport = FakeTransport({"https://example.com/big": RawResponse(200, {"content-type": "text/plain"}, b"x" * 10_000)})
    result = guarded_fetch("https://example.com/big", resolver=resolver_for(PUBLIC_IP), transport=transport, max_bytes=1000)
    assert result.truncated is True


def test_default_transport_is_deliberately_conservative():
    """The default transport must not re-introduce what the policy forbids."""
    import inspect

    src = inspect.getsource(netguard.requests_transport)
    assert "allow_redirects=False" in src   # redirects are re-validated, hop by hop
    assert "trust_env = False" in src       # no HTTP_PROXY bypassing the IP checks
    assert "session.proxies = {}" in src
    assert "stream=True" in src             # size cap applies before buffering
    assert "timeout=" in src


def test_fetch_never_sends_credentials():
    transport = FakeTransport({"https://example.com/": html_resp()})
    result = guarded_fetch("https://example.com/", resolver=resolver_for(PUBLIC_IP), transport=transport)
    assert result.addresses == (PUBLIC_IP,)
