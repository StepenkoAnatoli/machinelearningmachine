# Security Policy

This is a **local engineering tool**, not a multi-tenant service. The security
model below is what the code actually enforces today; the "not guaranteed" list
is just as important.

- Supported version: `0.1.0` (`main`).
- Report vulnerabilities privately via GitHub security advisories:
  <https://github.com/StepenkoAnatoli/machinelearningmachine/security/advisories/new>.
  Do not open a public issue for an unfixed vulnerability.

---

## 1. How the server is exposed (and how it refuses to be)

| Situation | Behaviour |
| --- | --- |
| `module-mesh serve` (default) | Binds `127.0.0.1:8000`. No authentication. Reachable only from the machine it runs on. |
| `--host 0.0.0.0` / a LAN address without `--allow-public` | **Refused at startup** with an explanation (`machinelearningmachine/server/config.py:validate_bind_policy`). |
| `--allow-public` without `--auth-token` (or the `MACHINELEARNINGMACHINE_AUTH_TOKEN` / `MODULE_MESH_AUTH_TOKEN` env var) | **Refused at startup.** |
| `--allow-public --auth-token <token>` | Starts; every `/api/*` route and the `/ws` handshake requires the token; `<16` character tokens are rejected. |

The token is compared with `hmac.compare_digest`, is never written to logs, is
never echoed by any endpoint, and is only ever sent by the browser as an
HttpOnly, `SameSite=Lax` cookie after the dashboard's sign-in form posts it.
Five wrong attempts lock that client out for 60 seconds. On a loopback server,
where no token is configured, `POST /api/auth/login` answers 400 ("this server does
not require an access token") rather than accepting any string and implying a
protection that is not switched on. The counter is keyed by the
client cookie it *presented* (or its peer address when it presents none) rather than
by the session, so throwing cookies away does not reset it.

`/` and `/health` stay unauthenticated so the sign-in UI can load and uptime
checks can work; `/health` therefore reports only `status`, `version`,
`auth_required` and `url_reader_enabled`. Everything with operational detail
lives behind `/api/status`.

### What authentication does *not* give you

A single shared bearer token means **one trust domain, not per-user access
control**. Multiple browsers get separate meshes and separate saved-session
folders (see §2), but anyone holding the token can do everything the owner can:
register agents, run tasks, replace provider configuration, load and delete
that browser's saved sessions. There is no user database, no roles, no
per-user quota, and no rate limiting that survives a fresh cookie jar.

**Do not put this behind a public reverse proxy as a shared service.** If you
need that, you need authentication in front of it (OIDC/oauth2-proxy), a
per-user process or container, and an egress policy — that is a different
deployment model than this codebase is written for.

## 2. State isolation

There is no global mesh any more. `machinelearningmachine/server/state.py` keeps
a bounded LRU of `SessionState` objects, each owning:

- its own `AgentMesh` (agents, message bus, history),
- its own provider configuration and API keys,
- its own WebSocket set, each connection with a bounded outbox of its own
  (a run in one browser never streams into another, and one stalled tab cannot
  hold up the run or any other tab),
- its own rate-limit bucket and login-failure counter.

Saved transcripts on disk are namespaced by a persistent client id
(`~/.module_mesh/sessions/<client-id>/<session>.json`), so one browser cannot
list, load, or delete another browser's files. Idle sessions are released after
`--session-ttl` minutes (default 360) and at most `--max-sessions` (default 32)
live meshes are kept; eviction drops the key material with the state.

Those two limits are enforced by a sweeper that the server starts in its own
lifespan task (`SessionRegistry.sweep_expired`), not only when a request happens to
arrive: a tab that goes quiet for six hours is reclaimed even if nobody ever calls
it again, and the reclaim path closes its WebSocket feeds with code `4408` and a
`session_released` frame explaining what happened, so the client shows "Session
released - Reload" instead of reconnecting into a brand-new empty session that
looks like the old one. Capacity eviction prefers the ephemeral entries and closes
their sockets the same way. Sweep interval is a quarter of the idle TTL, floored at
5 s and capped at 60 s; it is disabled when the TTL is unset.

Limits: state lives in process memory, so a restart logs everyone out and drops
in-memory keys; a client that deletes its cookies gets a fresh (empty) session
rather than an escape hatch into someone else's.

Three consequences worth knowing:

- On a token-protected server, **unauthenticated requests allocate no session at
  all** (a drive-by visitor cannot fill the bounded registry and evict other
  people's state); the session is created when the token is proven.
- A caller that proves the token by header but keeps **no cookies** is marked
  *ephemeral*: it gets `EPHEMERAL_SESSION_TTL` (120 s) of idle life instead of the
  full TTL and is the first thing evicted when the registry is full, so a script
  cannot crowd real browsers out. It is promoted to a normal session as soon as
  the client starts sending the session cookie back.
- Because state is attached to the session cookie, an API client that sends
  `Authorization: Bearer` but keeps no cookies gets a **fresh, empty mesh per
  request**. Bearer-only clients that want a conversation must persist the
  `mmm_session` cookie from the first response (any HTTP client with a cookie jar
  does this automatically).

## 3. API keys

- Accepted only via `POST /api/config`, and in `run` mode from the environment.
- Held in a per-session dict in memory. Never persisted, never written to the
  saved-session JSON, never returned by `GET /api/config` (which reports
  `has_openai_key` / `has_anthropic_key` booleans only), never logged.
- Saving a key performs a **shape check only**. It does not prove the key works.
  `{"verify": true}` (the "Verify the OpenAI key now" checkbox) makes one
  `GET {base_url}/models` request to actually test it.
- **A key in your environment is never used by the dashboard.** Provider objects built
  for a browser session are constructed with `allow_env_key=False`, so an ambient
  `OPENAI_API_KEY` cannot be picked up by a run started from a dashboard you only meant
  to demo. `run --live openai` is the deliberate, terminal-only opt-in, and it prints
  the endpoint, model and key source before the first request.
- A session with no key sends **no** `Authorization`/`x-api-key` header at all (an
  earlier release sent the literal string `None`, which both broke anonymous local
  backends and was a header-shaped surprise on the wire).
- `openai_base_url` is operator-supplied and gets sent your key. Treat it like a
  shell command: only point it at an endpoint you control, and remember that
  local backends (`http://localhost:11434/v1`) are deliberately allowed *on a loopback
  bind* - see §4, because that URL is now checked against the outbound policy too.
- Provider calls send the conversation text to that endpoint. Do not paste
  secrets or personal data into prompts if a live provider is configured.
- A run is serialised per session (`409` while one is in flight) and bounded by
  `--run-timeout`, so a live provider cannot hold a session open forever or have two
  runs rewriting the same transcript at once.

## 4. Outbound requests (SSRF)

Two things in this project dial out to an address a caller chose: the page reader
(`POST /api/read/url`) and the provider base URL (`POST /api/config`'s
`openai_base_url` / `anthropic_base_url`, used by `{"verify": true}` and by every live
run). Both are mediated by `machinelearningmachine/netguard.py`.

The page reader is **off unless you pass `--enable-url-reader`**, and even then:

- schemes limited to `http`/`https`; ports limited to `80`/`443`;
- no credentials in the URL;
- the hostname is resolved first and **every** returned address must be
  non-loopback, non-private, non-link-local, non-multicast, non-reserved and
  non-"this network" (IPv4 and IPv6, including IPv4-mapped, 6to4 and Teredo
  forms, plus CGNAT `100.64.0.0/10`, TEST-NETs and `0.0.0.0/8`);
- redirects are never followed implicitly: each hop is re-parsed and
  re-validated, up to 3 hops, so `public-page → 302 → 169.254.169.254` fails;
- `requests` is used with `allow_redirects=False`, `trust_env=False` and no
  proxies, so neither a redirect nor `HTTP_PROXY` can route around the checks;
- response bodies are capped at 1 MB and only text-ish `Content-Type`s are
  accepted; the fetched text is never echoed back with headers.

The provider base URL is checked by the same code path
(`netguard.validate_provider_target`) with one deliberate difference: **any port is
allowed**, because Ollama, LM Studio and vLLM do not live on 80/443. On a loopback
bind the caller is the operator, so local and private addresses stay reachable - that
is the whole point of the setting. On a bind another machine can reach, private,
loopback and link-local targets are refused (400, with the reason) unless the operator
opts in with `--allow-insecure-provider-urls`, and an operator-set
`MACHINELEARNINGMACHINE_URL_ALLOWLIST` is honoured there too. Before this, a
token holder on a public bind could point the dashboard at
`http://169.254.169.254/latest/meta-data/` and read the status codes back - and
`{"verify": true}` made that an explicit probe with an oracle.

Operators who want a hard boundary instead of a blocklist can set
`MACHINELEARNINGMACHINE_URL_ALLOWLIST=docs.example.com,example.org` (alias
`MODULE_MESH_URL_ALLOWLIST`), which replaces "block the bad addresses" with
"allow only these hosts".

**Residual risk, in plain terms:** validation and connection are two steps, so
an attacker-controlled DNS server with a sub-second TTL can still rebind
between them, and any host that resolves to a public IP *and* routes to an
internal service (hairpin NAT, some IPv6 translators) is out of reach of this
logic. For anything internet-facing, fetch from a separate service inside a
network namespace with its own egress policy — an in-process IP check is a
mitigation, not a boundary.

Also note the corollary: with `--enable-url-reader`, anyone who can reach the
port can make this machine issue GET requests to public URLs. On loopback by
default that is your own browser's feature; with a token it is a token-holder's.

## 5. Rendering untrusted text (XSS)

Agent replies, custom agent fields and saved-session JSON are attacker-influenced
input. Rules the frontend follows (`static/markdown.js`):

- Marked output is sanitized by **DOMPurify** with an explicit tag/attribute
  allowlist. The old regex "sanitizer" (`<script>` removal, `on...=` stripping,
  `javascript:` filtering) is gone: it was bypassable by unquoted handlers,
  `<svg>`/`<math>` subtrees, malformed markup and parser quirks.
- Rendering produces DOM nodes (`RETURN_DOM_FRAGMENT`) inserted with
  `replaceChildren()`; user-controlled values go through `textContent`, never
  into an interpolated HTML string.
- `src`/`srcset`/`style` are not allowed at all, so rendered transcript content
  cannot fetch anything or leak through CSS.
- Colours and avatars are validated before they touch an inline style
  (`/^#[0-9a-fA-F]{6}$/`, short printable avatar) — both client-side and in
  `agents/base.py:safe_color/safe_avatar`, so a hand-edited session file cannot
  smuggle attributes either.
- The server sends `Content-Security-Policy: default-src 'self'; script-src
  'self'; style-src 'self' 'unsafe-inline'; object-src 'none'; base-uri 'self';
  frame-ancestors 'none'` plus `X-Content-Type-Options: nosniff`,
  `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer`.
- **There are no third-party origins in the page at all.** Tailwind (compiled),
  FontAwesome, Marked, DOMPurify and Highlight.js are vendored under
  `static/vendor/`, pinned and checksummed in `static/vendor/MANIFEST.json`;
  `npm ci && python3 scripts/build_vendor.py` regenerates them and CI fails if
  the tree drifts from the manifest. CI also runs `npm audit --audit-level=high`
  and `pip-audit`, because a pinned asset is also a pinned vulnerability: the
  first DOMPurify version vendored here (3.1.6) had 20 open advisories.

The XSS payload corpus that the regex filter used to miss is now an executable
test, and so is the client's reaction to a server that admits it lost data:
`node --test tests/js/*.test.mjs` (jsdom) covers the sanitizer payloads *and* the tab's
behaviour on `stream_gap` / `session_released` / a refused run, including the
invariant that a server-supplied detail string is rendered as text and can never
execute - which is why `/api/run`'s error text is passed through `_safe_reason`
before it reaches the client at all.

`'unsafe-inline'` in `style-src` is deliberate (agent chips set colours via the
CSSOM); `script-src` has no inline allowance, which is why `index.html` contains
no inline `<script>` and no `on*=` attributes.

## 6. Mock output vs. real output (this is a security property too)

The built-in simulator is deterministic template text: **no model is called, and
no code is compiled, executed, or tested.** It used to say things like
"production-ready" and "All assertions should PASS", which invited exactly the
wrong reaction — treating simulated approval as verification. Now:

- every simulated reply is prefixed with a notice and carries
  `metadata.simulated = true`;
- the dashboard shows a `simulated` / `live` / `provider failed → simulated`
  badge on each message plus a mode banner;
- exports get a provenance line naming the provider mode;
- a configured provider that **fails** raises `ProviderError`; by default the
  simulator answers *and says which provider failed* (message badge
  `provider failed → simulated`, agent status `degraded`, HTTP response carries
  `provider_warnings`). With `--strict-provider-errors` the run fails with 502
  instead. An error string is never returned as a normal answer.

Treat anything marked `simulated` as an unreviewed draft.

## 7. Known limitations (accept these before deploying)

- **One shared token, no roles.** Everyone who can present it can read and clear
  everyone else's saved sessions if they also hold that browser's cookie, and can
  change server-wide settings.
- **No TLS.** Cookies and the token travel in clear text. Terminate TLS in front of
  this server, or use an SSH tunnel.
- **Single process, in-memory state.** The bounded LRU, the per-connection outboxes
  and the rate-limit buckets live in one process: no scale-out, and a restart drops
  every session and every key.
- **No mid-run cancellation.** A run is bounded by `--run-timeout` (default 180 s),
  not by a cancel button; a second run in the same session is refused with `409`
  rather than queued. A provider that streams slowly can therefore occupy its
  session's single run slot for the whole timeout.
- **Frame loss is designed in, recovery is best-effort.** Each connection's outbox
  holds 128 frames and drops the oldest under pressure, then tells the client
  (`stream_gap`), which re-fetches the transcript from the server. A tab that is
  closed, or a run whose history has already left the bus buffer, keeps its hole.
- **Provider retries are per-request, not per-run.** A provider that answers 429/5xx
  is retried up to `max_attempts` times with jittered backoff; the transcript records
  how many attempts it took (`metadata.provider_attempts`) but there is no circuit
  breaker and no budget across a run.
- **SSRF mitigation is an in-process IP check.** It is a mitigation, not a boundary
  (see §4's residual-risk note). Same applies to provider base URLs.
- **The DNS-rebinding window in §4 is documented, not closed.** Validation and
  connection are separate steps; closing it needs a proxy or a network namespace.
- **Sessions are as private as the cookie.** Anything with the browser's cookie jar -
  a local process, an XSS bug in some other extension - is inside that session.
- **Nothing here is an audit.** No external penetration test has been performed.

## 8. Checklist for a non-local deployment

```bash
TOKEN="$(python -c 'import secrets; print(secrets.token_urlsafe(32))')"
MACHINELEARNINGMACHINE_AUTH_TOKEN="$TOKEN" \
  module-mesh serve --host 0.0.0.0 --allow-public --auth-token "$TOKEN" \
  # leave --enable-url-reader off unless the reader is genuinely needed
```

Then, at minimum: TLS in front of it; the token distributed like a password;
firewall the port to known IPs; a separate egress policy for outbound fetches;
and read §1's "what authentication does not give you" again.
