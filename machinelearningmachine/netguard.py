"""
Guarded outbound HTTP requests - the SSRF boundary for the dashboard.

The "read a web page aloud" feature fetches a URL that the *user* typed into
the browser. Fetching it with a plain ``requests.get()`` turns the server into
an arbitrary network client: an attacker (or any page that can talk to a
running dashboard) could aim it at ``127.0.0.1``, a container network, or the
cloud metadata service at ``169.254.169.254`` and read the response back.

Everything in this module exists to make that impossible-ish:

* only ``http``/``https`` and ports 80/443 are allowed,
* the hostname is resolved *before* connecting and every answer is checked
  against loopback / private / link-local / unique-local / multicast /
  reserved / "this-network" ranges (IPv4 and IPv6, including IPv4-mapped),
* redirects are never followed implicitly - each hop is validated again,
* response bodies are capped by bytes, not buffered without limit,
* credentials, cookies and proxy settings are never sent,
* an operator allowlist (``MACHINELEARNINGMACHINE_URL_ALLOWLIST``, alias
  ``MODULE_MESH_URL_ALLOWLIST``) can replace "block the
  bad addresses" with "allow only the good ones", which is the stronger rule.

Residual risk, stated plainly: validation and connection are two separate
steps, so a hostile DNS server with a very short TTL can still rebind between
them. Anything that must be safe against a determined attacker should fetch
from a network namespace with its own egress policy - see ``SECURITY.md``.
"""

from __future__ import annotations

import ipaddress
import re
import socket
from dataclasses import dataclass, field
from typing import Callable, Dict, Iterable, List, Optional, Sequence, Tuple
from urllib.parse import urljoin, urlparse, urlunparse

from . import env

# ---------------------------------------------------------------------------
# Policy constants (imported by the server and by tests)
# ---------------------------------------------------------------------------

ALLOWED_SCHEMES: Tuple[str, ...] = ("http", "https")
ALLOWED_PORTS: Tuple[int, ...] = (80, 443)
MAX_REDIRECTS = 3
#: Refuse to buffer more than this for a single page read.
MAX_FETCH_BYTES = 1_000_000
#: Refuse to even look at bodies smaller than this being text/plain-ish only.
ALLOWED_CONTENT_TYPES: Tuple[str, ...] = (
    "text/html",
    "application/xhtml+xml",
    "text/plain",
    "text/markdown",
    "application/json",
)
CONNECT_TIMEOUT = 5.0
READ_TIMEOUT = 15.0

#: Environment variable operators can use to replace the blocklist with an
#: explicit allowlist of hostnames: ``MODULE_MESH_URL_ALLOWLIST=example.com,blog.example``
#: Both ``MACHINELEARNINGMACHINE_URL_ALLOWLIST`` and the short alias are read;
#: see :mod:`machinelearningmachine.env`. The constant keeps the legacy spelling
#: because it is what the docs and SECURITY.md name.
URL_ALLOWLIST_ENV_VAR = "MODULE_MESH_URL_ALLOWLIST"
URL_ALLOWLIST_SUFFIX = "URL_ALLOWLIST"

#: Extra networks ``ipaddress`` does not classify as private that must not be
#: reachable either (``0.0.0.0/8`` "this network", benchmark range, TEST-NETs).
EXTRA_BLOCKED_NETWORKS: Tuple[str, ...] = (
    "0.0.0.0/8",
    "192.0.0.0/24",       # IETF protocol assignments
    "192.0.2.0/24",       # TEST-NET-1
    "198.18.0.0/15",      # benchmarking
    "198.51.100.0/24",    # TEST-NET-2
    "203.0.113.0/24",     # TEST-NET-3
    "240.0.0.0/4",        # reserved for future use
    "100.64.0.0/10",      # CGNAT - often reaches internal services
    "fc00::/7",           # IPv6 unique local
    "2001:db8::/32",      # documentation
    "3fff::/20",          # IPv6 documentation
    "5f00::/16",          # IPv6 segment routing (internal-ish)
)

_BLOCKED_NETS_V4 = tuple(ipaddress.ip_network(n) for n in EXTRA_BLOCKED_NETWORKS if ":" not in n)
_BLOCKED_NETS_V6 = tuple(ipaddress.ip_network(n) for n in EXTRA_BLOCKED_NETWORKS if ":" in n)

_LOCAL_HOSTNAMES = {"localhost"}


class UnsafeURL(ValueError):
    """A URL that must not be fetched. ``reason`` is safe to show to a user."""

    def __init__(self, reason: str, *, detail: str = "") -> None:
        super().__init__(reason if not detail else f"{reason} ({detail})")
        self.reason = reason
        self.detail = detail


class FetchError(RuntimeError):
    """The guarded fetch failed. ``reason`` is safe to show to a user."""

    def __init__(self, reason: str, *, status_code: Optional[int] = None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.status_code = status_code


# ---------------------------------------------------------------------------
# Address / URL validation
# ---------------------------------------------------------------------------

def operator_allowlist() -> Optional[Tuple[str, ...]]:
    """Hostnames the operator explicitly permitted, or ``None`` for "use the
    IP blocklist" mode."""
    raw = env.get(URL_ALLOWLIST_SUFFIX)
    if not raw:
        return None
    hosts = tuple(
        h.strip().lower().lstrip(".") for h in re.split(r"[,\s]+", raw) if h.strip()
    )
    return hosts or None


def is_blocked_ip(address: str) -> bool:
    """True when ``address`` points at the machine itself or its network."""
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return True  # unparseable -> treat as unsafe

    if ip.version == 6:
        # "::ffff:127.0.0.1" and friends must not slip past the v4 rules.
        mapped = getattr(ip, "ipv4_mapped", None)
        if mapped is not None:
            ip = mapped
        else:
            # 6to4 / Teredo can also smuggle v4 loopback or private addresses.
            for attr in ("sixtofour", "teredo"):
                inner = getattr(ip, attr, None)
                if inner is None:
                    continue
                if getattr(inner, "ipv4_mapped", None) is not None:
                    inner = inner.ipv4_mapped
                if inner.version == 4 and _is_unsafe_v4(inner):
                    return True
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast:
                return True
            if ip.is_reserved or ip.is_unspecified:
                return True
            return any(ip in net for net in _BLOCKED_NETS_V6)

    return _is_unsafe_v4(ip)


def _is_unsafe_v4(ip: "ipaddress.IPv4Address") -> bool:
    if (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    ):
        return True
    return any(ip in net for net in _BLOCKED_NETS_V4)


def allowed_by_operator(host: str) -> bool:
    allowlist = operator_allowlist()
    if allowlist is None:
        return True
    host = host.lower().strip(".")
    return any(host == entry or host.endswith("." + entry) for entry in allowlist)


def default_resolver(host: str) -> List[str]:
    """Return every address ``host`` currently resolves to."""
    try:
        infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise UnsafeURL(
            "Could not find that address - check the URL for typos.",
            detail=str(exc),
        ) from exc
    # ``str()`` is not decoration: getaddrinfo's address tuple is a 2-tuple for
    # IPv4 and a 3-tuple for IPv6, so a checker that cannot narrow the union sees
    # ``str | int`` here. Slot 0 is the address text in both, and this function is
    # annotated as returning addresses - so say so instead of leaving it inferred.
    return sorted({str(info[4][0]) for info in infos})


def check_url(url: str, *, resolver: Optional[Callable[[str], Sequence[str]]] = None) -> str:
    """
    Validate a user-supplied URL and return the final validated URL string.

    Raises :class:`UnsafeURL` for anything that must not be fetched.
    """
    return validate_target(url, resolver=resolver).url


def validate_target(url: str, *, resolver: Optional[Callable[[str], Sequence[str]]] = None) -> "Target":
    """Parse + validate ``url``; returns the :class:`Target` to fetch."""
    raw = (url or "").strip()
    if not raw:
        raise UnsafeURL("Please enter a web address first.")
    if len(raw) > 2000:
        raise UnsafeURL("That web address is too long (max 2000 characters).")

    parsed = urlparse(raw)
    scheme = (parsed.scheme or "").lower()
    if scheme not in ALLOWED_SCHEMES:
        raise UnsafeURL("Only http:// and https:// links can be read aloud.")
    if not parsed.hostname:
        raise UnsafeURL("That web address has no hostname - it cannot be read.")
    if parsed.username or parsed.password:
        raise UnsafeURL("Web addresses with embedded credentials are not allowed.")

    host = parsed.hostname.lower().strip(".")
    # A literal private/loopback address is reported as such *before* the port
    # check: "http://127.0.0.1:8080/" is a server-side request forgery attempt,
    # and telling the user "only ports 80/443" would describe the lesser problem.
    try:
        literal = ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        literal = None
    if literal is not None and is_blocked_ip(str(literal)):
        raise UnsafeURL(
            "That address points at a private or local network, which the page "
            "reader refuses to contact (server-side request forgery protection)."
        )

    try:
        port = parsed.port
    except ValueError as exc:  # e.g. "http://host:abc/"
        raise UnsafeURL("That web address has an invalid port.", detail=str(exc)) from exc
    if port is None:
        port = 443 if scheme == "https" else 80
    if port not in ALLOWED_PORTS:
        raise UnsafeURL(f"Only ports {', '.join(str(p) for p in ALLOWED_PORTS)} are allowed.")

    if not allowed_by_operator(host):
        raise UnsafeURL("That host is not in this server's allowlist of readable sites.")

    # A literal IP is checked directly *and* through the resolver (already partly
    # decided above for blocked literals), because glibc also accepts
    # 0x7f000001 / 2130706433 / 0177.0.0.1 as 127.0.0.1 and the HTTP client will
    # connect to whatever the resolver says. Names are resolved: "localhost" or
    # "internal.corp" are private addresses in disguise.
    addresses: List[str] = [str(literal)] if literal is not None else []
    if literal is None and host in _LOCAL_HOSTNAMES:
        raise UnsafeURL(
            "Fetching localhost is not allowed - the page reader can only reach public web sites."
        )

    resolve = resolver or default_resolver
    try:
        addresses.extend(resolve(host))
    except UnsafeURL:
        if literal is not None:
            pass  # the literal address was already enough to decide
        else:
            raise
    except Exception as exc:  # a resolver hook may raise anything
        if literal is None:
            raise UnsafeURL("Could not resolve that host.", detail=str(exc.__class__.__name__)) from exc

    if not addresses:
        raise UnsafeURL("That host does not resolve to an address.")

    blocked = [a for a in addresses if is_blocked_ip(a)]
    if blocked:
        raise UnsafeURL(
            "That address points at a private or local network, which the page "
            "reader refuses to contact (server-side request forgery protection)."
        )

    netloc = parsed.netloc.rsplit("@", 1)[-1]
    if port != (443 if scheme == "https" else 80):
        netloc = f"{host}:{port}"
    safe_url = urlunparse((scheme, netloc, parsed.path or "/", "", parsed.query, ""))
    return Target(url=safe_url, scheme=scheme, host=host, port=port, addresses=tuple(addresses))


def validate_provider_target(
    url: str,
    *,
    resolver: Optional[Callable[[str], Sequence[str]]] = None,
    allow_private: bool = False,
    extra_allowed_hosts: Sequence[str] = (),
) -> "Target":
    """
    Validate the base URL of an LLM provider endpoint.

    Different policy from the page reader, because it is a different job: an
    OpenAI-compatible backend legitimately runs on a port like ``11434`` and under a
    path like ``/v1``, and it is *usually* on this very machine. What must not be
    negotiable is who gets to choose the address:

    * ``allow_private=True`` - the caller is the operator (a loopback bind, or the
      library API). Local and private targets are fine; that is the whole point of
      Ollama and vLLM.
    * ``allow_private=False`` - the address arrived from a *browser*. Then the same
      server-side-request-forgery rules as the page reader apply: the host is
      resolved and every answer must be publicly routable, so the dashboard cannot
      be aimed at the cloud metadata service, at CGNAT, or at an internal port.

    ``extra_allowed_hosts`` is an operator allowlist of hostnames that bypasses the
    address rule (e.g. ``ollama.internal``); the ambient
    ``MACHINELEARNINGMACHINE_URL_ALLOWLIST`` is honoured as well.

    Raises :class:`UnsafeURL` with a reason that is safe to show to a user.
    """
    raw = (url or "").strip()
    if not raw:
        raise UnsafeURL("Please enter a base URL first.")
    if len(raw) > 500:
        raise UnsafeURL("That base URL is too long (max 500 characters).")

    parsed = urlparse(raw)
    scheme = (parsed.scheme or "").lower()
    if scheme not in ALLOWED_SCHEMES:
        raise UnsafeURL("A provider base URL must start with http:// or https://.")
    if not parsed.hostname:
        raise UnsafeURL("That base URL has no hostname - it cannot be reached.")
    if parsed.username or parsed.password:
        raise UnsafeURL("A provider base URL must not embed credentials.")

    host = parsed.hostname.lower().strip(".")
    try:
        port = parsed.port
    except ValueError as exc:  # e.g. "http://host:abc/"
        raise UnsafeURL("That base URL has an invalid port.", detail=str(exc)) from exc
    port = port if port is not None else (443 if scheme == "https" else 80)

    allowed_hosts = {h.strip().lower().lstrip(".") for h in extra_allowed_hosts if h and h.strip()}

    def _operator_listed(name: str) -> bool:
        """
        Did the *operator* name this host? (Not: is the host unblocked?)

        ``allowed_by_operator`` answers the second question and returns True when no
        allowlist is configured at all, so it cannot be used here: that would turn
        "the operator listed nothing" into "every address is a local backend".
        """
        if any(name == entry or name.endswith("." + entry) for entry in allowed_hosts):
            return True
        ambient = operator_allowlist()
        if ambient is None:
            return False
        return any(name == entry or name.endswith("." + entry) for entry in ambient)

    try:
        literal = ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        literal = None

    if literal is not None:
        addresses = [str(literal)]
    else:
        resolve = resolver or default_resolver
        try:
            addresses = sorted(set(resolve(host)))
        except UnsafeURL:
            raise
        except Exception as exc:  # a resolver hook may raise anything
            raise UnsafeURL(
                "Could not resolve that host - check the base URL.",
                detail=str(exc.__class__.__name__),
            ) from exc
        if host in _LOCAL_HOSTNAMES and not addresses:
            # "localhost" resolves to loopback on every normal machine. Where this
            # one has no resolver entry for it, treat the name as the address it
            # stands for: refusing here would break the documented Ollama setup,
            # and the block check below still decides whether it may be dialled.
            addresses = ["127.0.0.1"]

    if not addresses:
        raise UnsafeURL("That host does not resolve to an address.")

    if not allow_private and not _operator_listed(host):
        blocked = [a for a in addresses if is_blocked_ip(a)]
        if blocked:
            raise UnsafeURL(_PRIVATE_TARGET_REASON)

    netloc = parsed.netloc.rsplit("@", 1)[-1]
    safe_url = urlunparse((scheme, netloc, parsed.path or "", "", parsed.query, ""))
    return Target(
        url=safe_url.rstrip("/"), scheme=scheme, host=host, port=port,
        addresses=tuple(addresses),
    )


_PRIVATE_TARGET_REASON = (
    "That provider address points at a private or local network. On a server that "
    "is reachable from other machines, a browser is not allowed to choose such a "
    "target (server-side request forgery protection). Start the server with "
    "--allow-insecure-provider-urls if this machine's own backends should be usable "
    "from the dashboard."
)


@dataclass(frozen=True)
class Target:
    """A URL that passed validation, plus the addresses it resolved to."""

    url: str
    scheme: str
    host: str
    port: int
    addresses: Tuple[str, ...] = ()


# ---------------------------------------------------------------------------
# Transport
# ---------------------------------------------------------------------------

@dataclass
class RawResponse:
    status_code: int
    headers: Dict[str, str] = field(default_factory=dict)
    content: bytes = b""
    truncated: bool = False

    def header(self, name: str, default: str = "") -> str:
        for key, value in self.headers.items():
            if key.lower() == name.lower():
                return value
        return default


@dataclass
class FetchResult:
    """The outcome of a fully validated fetch."""

    content: bytes
    content_type: str
    final_url: str
    requested_url: str
    status_code: int
    truncated: bool = False
    redirects: int = 0
    addresses: Tuple[str, ...] = ()

    @property
    def text(self) -> str:
        return self.content.decode("utf-8", errors="replace")


Transport = Callable[[str, float, int], RawResponse]


def requests_transport(
    url: str, timeout: float, max_bytes: int, *, user_agent: Optional[str] = None
) -> RawResponse:
    """
    The default transport: fetch exactly ``url`` with no redirect following,
    no cookies, no credentials and no ambient proxy configuration.

    Everything about the *policy* lives in :func:`guarded_fetch`; this function
    only moves bytes and never decides where to go.
    """
    try:
        import requests
        from requests import exceptions as requests_exceptions
    except ImportError as exc:  # pragma: no cover - depends on environment
        raise FetchError("The 'requests' package is required to read web pages.") from exc

    ua = user_agent or "Mozilla/5.0 (compatible; MachineLearningMesh-Reader/2.0)"
    try:
        session = requests.Session()
        # Do not inherit HTTP_PROXY / ~/.netrc: a proxy would let the fetch hop
        # to an address we just decided was forbidden.
        session.trust_env = False
        session.proxies = {}
        session.max_redirects = 0
        try:
            resp = session.get(
                url,
                timeout=(CONNECT_TIMEOUT, timeout),
                stream=True,
                allow_redirects=False,
                headers={"User-Agent": ua, "Accept": "text/plain, text/html, application/json;q=0.8"},
            )
            chunks: List[bytes] = []
            downloaded = 0
            truncated = False
            for chunk in resp.iter_content(chunk_size=65536):
                if not chunk:
                    continue
                chunks.append(chunk)
                downloaded += len(chunk)
                if downloaded >= max_bytes:
                    truncated = True
                    break
            headers = {str(k): str(v) for k, v in resp.headers.items()}
            status = resp.status_code
        finally:
            session.close()
    except requests_exceptions.RequestException as exc:
        raise FetchError(_friendly_network_error(exc)) from exc

    return RawResponse(status_code=status, headers=headers, content=b"".join(chunks), truncated=truncated)


def _friendly_network_error(exc: Exception) -> str:
    text = str(exc)
    if "404" in text or "Not Found" in text:
        return "That page was not found (404) - double-check the link."
    if "403" in text:
        return "That site refused the request (403) - it may block automated readers."
    if "getaddrinfo" in text or "Name or service not known" in text:
        return "Could not find that address - check the URL for typos."
    if "timed out" in text.lower():
        return "That page took too long to load - try a different URL."
    return "Could not reach that page. Check the URL and your internet connection, then try again."


def content_type_allowed(content_type: str, allowed: Optional[Iterable[str]] = None) -> bool:
    """True when ``content_type`` is something safe to render as text."""
    allowed_tuple = tuple(allowed) if allowed is not None else ALLOWED_CONTENT_TYPES
    ctype = (content_type or "").split(";", 1)[0].strip().lower()
    if not ctype:
        return True  # missing header: the caller still only ever reads it as text
    return any(ctype == entry or ctype.startswith(entry) for entry in allowed_tuple)


def guarded_fetch(
    url: str,
    *,
    resolver: Optional[Callable[[str], Sequence[str]]] = None,
    transport: Optional[Transport] = None,
    max_bytes: int = MAX_FETCH_BYTES,
    max_redirects: int = MAX_REDIRECTS,
    read_timeout: float = READ_TIMEOUT,
    allowed_content_types: Optional[Iterable[str]] = None,
) -> FetchResult:
    """
    Fetch ``url`` and return its body, validating *every* hop first.

    ``resolver`` and ``transport`` are injectable so the SSRF policy can be
    unit-tested without touching the network.
    """
    if allowed_content_types is None:
        allowed_content_types = ALLOWED_CONTENT_TYPES
    do_fetch = transport or requests_transport
    current_url = url
    hops = 0
    last_target: Optional[Target] = None

    while True:
        target = validate_target(current_url, resolver=resolver)
        last_target = target
        raw = do_fetch(target.url, read_timeout, max_bytes)

        if raw.status_code in (301, 302, 303, 307, 308):
            location = raw.header("location")
            if not location:
                raise FetchError(
                    f"That page redirected without saying where to ({raw.status_code})."
                )
            hops += 1
            if hops > max_redirects:
                raise FetchError("That page redirected too many times - stopping to stay out of loops.")
            candidate = urljoin(target.url, location.strip())
            # The redirect target is validated exactly like the original URL:
            # a public page pointing at 169.254.169.254 must fail, not fetch.
            current_url = candidate
            continue

        if raw.status_code >= 400:
            raise FetchError(
                f"That page returned HTTP {raw.status_code} instead of content.",
                status_code=raw.status_code,
            )

        ctype = raw.header("content-type", "")
        if not content_type_allowed(ctype, allowed_content_types):
            raise FetchError(
                "That link is not a readable text or web page "
                f"(server said {ctype.split(';', 1)[0] or 'unknown type'})."
            )

        # Defence in depth: the cap is the transport's job, but a transport that
        # over-delivers must still not be able to hand back more than we allow.
        body = raw.content
        truncated = raw.truncated
        if max_bytes and len(body) > max_bytes:
            body = body[:max_bytes]
            truncated = True

        return FetchResult(
            content=body,
            truncated=truncated,
            content_type=ctype,
            final_url=target.url,
            requested_url=(url or "").strip(),
            status_code=raw.status_code,
            redirects=hops,
            addresses=last_target.addresses if last_target else (),
        )
