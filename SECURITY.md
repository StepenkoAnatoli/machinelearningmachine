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
| `--allow-public` without `--auth-token` (or `MACHINELEARNINGMACHINE_AUTH_TOKEN`) | **Refused at startup.** |
| `--allow-public --auth-token <token>` | Starts; every `/api/*` route and the `/ws` handshake requires the token; `<16` character tokens are rejected. |

The token is compared with `hmac.compare_digest`, is never written to logs, is
never echoed by any endpoint, and is only ever sent by the browser as an
HttpOnly, `SameSite=Lax` cookie after the dashboard's sign-in form posts it.
Five wrong attempts lock that client out for 60 seconds. The counter is keyed by the
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
- its own WebSocket set (a run in one browser never streams into another),
- its own rate-limit bucket and login-failure counter.

Saved transcripts on disk are namespaced by a persistent client id
(`~/.module_mesh/sessions/<client-id>/<session>.json`), so one browser cannot
list, load, or delete another browser's files. Idle sessions are released after
`--session-ttl` minutes (default 360) and at most `--max-sessions` (default 32)
live meshes are kept; eviction drops the key material with the state.

Limits: state lives in process memory, so a restart logs everyone out and drops
in-memory keys; a client that deletes its cookies gets a fresh (empty) session
rather than an escape hatch into someone else's.

Two consequences worth knowing:

- On a token-protected server, **unauthenticated requests allocate no session at
  all** (a drive-by visitor cannot fill the bounded registry and evict other
  people's state); the session is created when the token is proven.
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
- `openai_base_url` is operator-supplied and gets sent your key. Treat it like a
  shell command: only point it at an endpoint you control, and remember that
  local backends (`http://localhost:11434/v1`) are deliberately allowed.
- Provider calls send the conversation text to that endpoint. Do not paste
  secrets or personal data into prompts if a live provider is configured.

## 4. Outbound requests (SSRF)

`POST /api/read/url` is the one endpoint that talks to a user-supplied address,
so it is **off unless you pass `--enable-url-reader`**, and even then it is
mediated by `machinelearningmachine/netguard.py`:

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

Operators who want a hard boundary instead of a blocklist can set
`MODULE_MESH_URL_ALLOWLIST=docs.example.com,example.org`, which replaces "block
the bad addresses" with "allow only these hosts".

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
test: `node --test tests/js/sanitize.test.mjs` (jsdom, 13 tests).

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

1. No per-user authorisation beyond the shared token; no roles, no audit log.
2. Rate limiting is a per-session cooldown (1s/run, 2s/page read) plus a login
   lockout keyed per client/address. It is not a quota: a fresh cookie jar gets a
   fresh cooldown bucket (the *login* counter does survive discarding cookies).
3. In-memory state: one process restart loses transcripts and keys by design.
4. Transcripts are stored **unencrypted** on disk under `~/.module_mesh/sessions`
   — do not save secrets in prompts if that machine is shared.
5. TLS is not handled by this app: terminate it in a proxy you control.
6. The DNS-rebinding window in §4 is documented, not closed.
7. `uvicorn` single-worker assumption: the session registry is per-process. Do
   not run multiple workers behind a round-robin load balancer without sticky
   sessions; state will appear to reset.

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
