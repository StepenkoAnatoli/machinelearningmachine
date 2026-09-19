# Production Hardening: real providers, real browsers, real time

Research → requirements → architecture → implementation → evidence, for the
milestone that came after the security audit.

The audit before this one made the dashboard *safe to run*. This milestone asked a
different question: **does it still work when the traffic is real?** Two assumptions
ran through the whole codebase:

1. answers are small (the simulator produces ~1–2 KB of text), and
2. exactly one browser drives one mesh, one run at a time, and it is always
   listening.

Neither is true the moment you configure a real model and leave a tab open. The original findings were reproduced against commit `9cffbd3`; D18 was
reproduced against the parent of the cancellation change before being fixed, and each fix has a regression test named after it.

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
| **D17** | A CI step that only worked on the maintainer's Node | The jsdom step already existed on main as `node --test tests/js/sanitize.test.mjs`; this milestone widened it to a glob and initially ran `node --test "tests/js/*.test.mjs"`. Node >= 21 expands that pattern itself; the workflow's node 20 does not, so node tried to open a file literally named `tests/js/*.test.mjs` and the job died 15 seconds in - green locally, red on the runner. Found by CI, not by review. Narrowing back to one file would have hidden the red while leaving the second suite unexecuted forever | Medium (a job that can never pass is worse than a missing one: it trains people to ignore red) |

| **D18** | Runs cannot be stopped by the user | The cancellation regression received HTTP 404 for an active run in every topology; the browser regression found no Stop control | Medium (unwanted provider turns continue until completion or timeout) |

| **D19** | Reconnect snapshots omit run state | Server snapshot regression raised `KeyError: active_run_id`; browser regressions left Stop disabled during an active run and Execute busy after a missed completion | Medium (run controls cannot recover on reconnect) |

| **D20** | A second run is refused instead of waiting | Two concurrent `POST /api/run` in one session answered `200` and `409`; the browser regression found no queue position, no way to cancel a waiting run, and a dashboard that cleared another tab's run state when its own request was refused | Medium (a second tab/user must retry by hand; the refusal path can desynchronise the dashboard buttons) |

Reproduction scripts were written first, and each one became a test under `tests/`
(the end-to-end one became `scripts/e2e_server_check.py`, which CI runs against the
installed wheel). The numbers above - lengths, timings, captured
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

- **F14** Stop an active run at the next agent boundary, retain arrived replies and a cancellation notice, and scope cancellation to the requesting browser/run. (D18)
- **F15** A second run in a busy session waits its turn in a bounded per-session FIFO
  (202 with a 1-indexed position, `429` + `Retry-After` when full, `409` only when
  `--max-queued 0`), runs in arrival order without interleaving, is cancellable
  while queued, and is attributable to its tab in the UI. (D20)

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

**B. Per-session `asyncio.Lock` + a bounded FIFO, rather than refusal.**
*Alternatives:* refuse the second run while the first holds the lock (the original
choice: simple, but every retry is by hand and two tabs of one browser race for the
lock); shard the mesh per run (breaks "agents have memory" semantics, which is the
whole point of the demo). *Chosen (D20):* the lock still serialises execution, but a
request that arrives while the lock is held (or while entries wait) is appended to a
per-session in-memory queue bounded by `--max-queued` (default 5, `0` restores the
original refusal) and answered `202` with its 1-indexed position; a full queue answers
`429` + `Retry-After`. The pump is a lock-handoff chain (each finished entry schedules
the next before releasing), so at most one run executes per session and queued turns
cannot interleave with the active one. *Trade-off:* a queued run may wait through up to
`max_queued` full executions with no per-entry wait timeout, and the queue dies with
the process like the lock - mitigated by the bound, the visible position, and Stop
working on a queued run.

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

Every evidence cell is a test that was run and passed, not a plan. All offline.

| Req | Implementation | Evidence |
|---|---|---|
| F1 | `protocol/message.py: clamp_content`, `agents/base.py` clamp + `fit_context` for injected prompts, `server/app.py: _safe_reason` | `tests/test_provider_output_bounds.py` - 1 k…200 k chars across all four topologies, export wording, `/api/run`'s `truncated_messages`, and `_safe_reason` refusing to echo a payload |
| F2 | `server/state.py: run_lock` + `active_run_id`, `server/app.py` 409-with-reason, `run_id` on `run_started`/`new_message`/`run_completed` | `tests/test_run_serialization.py` - loser gets 409, transcript untouched, `run_id` consistent across frames, lock freed on error |
| F3 | `server/feed.py: ClientFeed` (bounded outbox, drop-oldest, `stream_gap`, send timeout), `SessionState.publish`, `finally:` detach in the WS handler | `tests/test_ws_backpressure.py` - a stalled socket cannot delay the producer, 40 publishes → `dropped >= 30` with the newest frames intact, `/api/run` completes on a saturated feed |
| F4 | `server/app.py` lifespan sweeper → `SessionRegistry.sweep_expired(reason)`, `on_evict` → `close_feeds(final=…, code=4408)`, `session_released` frame | `tests/test_session_lifecycle.py` - a quiet tab with a live socket is reclaimed with no further request, its next request is a *new* session, capacity eviction prefers ephemeral, sweeper stops with the app |
| F5 | `sessions.py: _write_atomic`, metadata header (`message_count`, `trimmed`), prefix-only listing, stale-`.tmp` prune, `new_session_id` carries sub-second precision | `tests/test_session_storage.py` - a failed `os.replace` leaves the previous file byte-identical with no temp litter, listing reads ≤ 4 KiB per file, oversized transcripts are trimmed *and flagged*, prune order is timestamp-driven |
| F6 | `providers.py: post_for_json` retry loop, `max_attempts`/`retry_backoff`/`timeout`, `ProviderError.attempts`, `metadata.provider_attempts` | `tests/test_provider_retry.py` - 429/503 retried up to the budget, 401 not retried, an unparseable 200 costs one request, the wait scales with the attempt, and the transcript records the effort |
| F7 | `providers.py: allow_env_key`, `server/app.py` builds providers with `allow_env_key=False`, auth headers only when a key exists | `tests/test_env_key_isolation.py` - a real aiohttp capture server asserts the headers it *received*: a keyless session sends none, an operator key is never attached to a session's request, and clearing a key cannot resurrect it from the environment |
| F8 | `netguard.validate_provider_target`, `server/app.py: _guard_provider_url`, `--allow-insecure-provider-urls`, `ServerConfig.allow_insecure_provider_urls` | `tests/test_provider_url_policy.py` - CGNAT/`192.0.0.1`/IPv6-mapped/decimal-host matrices, loopback bind keeps Ollama on any port, public bind refuses and changes nothing (mode stays `simulated`), operator allowlist honoured |
| F9 | `cli.py: run --live {openai,anthropic,both}`, `--base-url`, `--provider-timeout`, honest pre-flight banner, exit 1 without a key | `tests/test_cli_live.py` - which agents get wired per mode, nothing dials out without `--live`, the key is never echoed, flag > env > default for `--run-timeout`/`--max-sessions`/`--session-ttl`, and `SESSION_TTL=5m` stops startup instead of being ignored |
| F10 | `cli.py`/`mesh.py: _delay_kwargs` on all four topologies | `tests/test_cli_live.py` (parametrised over p2p/pipeline/debate/hub) + measured 0.45 s → 0.00 s |
| F11 | validation before the cooldown stamp, `Retry-After` on 409/429 | `tests/test_run_serialization.py` - `state.last_run_time` unchanged by a 400 |
| F12 | `server/app.py: _safe_reason` for every failure string that reaches a client | `tests/test_provider_output_bounds.py` (unit cases) |
| N1/N2 | stdlib-only core, `asyncio.wait_for` on every await that can block | CI matrix (3.10/3.11/3.12) + `test_import_without_optional_deps` |
| N3 | README + SECURITY.md rewritten in the same change; `serve` knobs readable from the environment | `tests/test_docs_are_accurate.py` + `tests/test_cli_live.py` |
| N4 | `--run-timeout` (default 180 s) → 504 with a plain-language reason, `run_error` frame first | `tests/test_run_serialization.py` |
| D16 | `.prov-clipped` badge in `app.js` + `style.css` | `tests/js/client-lifecycle.test.mjs` |

---

## 5. Testing: what was run, and what it caught

Three layers, deliberately: unit (pure functions), in-process HTTP (`httpx.ASGITransport`
/ `TestClient`), and a **real uvicorn process driven over real HTTP and a real
WebSocket** (`scripts/e2e_server_check.py`, 33 checks, two browser sessions, run by CI
against the installed wheel). Plus jsdom for the client's own behaviour, since the
server-side tests cannot prove a browser does anything with the frames it is sent.

| Result | |
|---|---|
| CI (GitHub) | green on `3.10 / 3.11 / 3.12`, vendor integrity, `npm audit`, `pip-audit`, secret scan, and the wheel-served end-to-end pass |
| `pytest -q` | **357 passed** (was 220 at `9cffbd3`), 0 failures, 2 warnings (both starlette/httpx deprecations) |
| `node --test tests/js/*.test.mjs` | **24 pass** (15 sanitizer + 9 client lifecycle), on node 20 in CI and node 22 locally |
| `ruff check machinelearningmachine tests scripts examples` | clean (examples were broken at baseline and are now in CI's scope) |
| `scripts/e2e_server_check.py` | **33/33 PASS** against a live server |
| packaged wheel | built, installed into a clean venv, served, and the e2e pass run against it (the new CI step) |

Where the browser cases actually run: the **frontend** job is what enforces them in
CI. `pytest -q` runs them too, so one command is enough on a development machine, but
that gate skips where `node_modules/jsdom` is absent - as it is in the three Python
jobs - rather than pretending to have covered them.

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
   `test_pruning_keeps_the_newest_session_even_within_one_second` - nine saves inside
   one second, five survivors, and the missing ones were not the oldest.
4. **`float(flag or default)` turned `--provider-timeout 0` into 60**, i.e. the value
   the user typed was silently replaced by the default - caught by the parametrised
   rejection test I wrote *for* that flag.
5. **"2 replys were longer than…"** - the pluralisation in the truncation toast, caught
   by a jsdom assertion on user-visible text.
6. **CI caught a defect in this milestone's own tooling** (D17): the new jsdom step used a
   quoted glob node 20 cannot expand - green locally, red on the runner in 15 s. Fixed by
   letting the shell expand it, plus a test that refuses both the single-file form and the
   quoted form, because a step that only works on the author's machine is a claim, not a check.
7. **A test harness that double-initialised the client.** Manually dispatching
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

- **Run-control recovery requires connectivity (D19).** Reconnection restores the active
  run and pending-stop state, but an offline tab cannot deliver a cancellation request.

- **Cancellation is cooperative (D18).** Stop does not abort an in-flight provider request
  or its retry/backoff. It prevents the next agent call; the current reply is kept.
  A provider error or run timeout can still terminate a pending stop as an error.
  The existing timeout remains the bound; no upstream billing cancellation is promised.

- **The run queue is per-process, bounded in count but not in wait (D20).** Queued
  entries live in the session object like the lock and the outboxes, so a restart
  drops them and there is no queue across `--workers > 1`. `--max-queued` bounds how
  many wait (default 5), but nothing bounds how long one waits: a short prompt behind
  five long live-provider runs may wait through all of them. `--run-timeout` bounds
  each execution, not the wait. A `202` response carries no messages; clients learn
  the outcome from the feed (`run_queued`/`run_started`/completion, all stamped with
  the run id) or by re-reading `/api/history`.

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
- **The browser tests depend on the runner's Node version** (D17). They are executed
  by the shell-expanded file list, which works on node 18/20/22. Two tests pin the form
  rather than trusting it: `test_ci_runs_every_jsdom_suite` for CI and `npm run test:js`,
  and `test_the_documented_way_to_run_the_browser_tests_works` for every *copyable*
  instruction in the docs and in the suites' own header comments - one of which had been
  telling people to run `node --test tests/js/`, a command that has never worked. Prose
  that quotes a broken form to explain it is deliberately exempt: the fix is a truer
  document, not a blunter lint. There is no Node-version matrix - if a future suite needs
  node >= 21 APIs, the workflow has to say so.
- **The jsdom client tests stub `fetch` and `WebSocket`.** They prove the client reacts
  correctly to frames; the real socket is covered by the e2e script, and browser-specific
  rendering (canvas, Web Speech) is not asserted anywhere.
- **Every jsdom window must be registered so `afterEach` can close it.** Not for tidiness:
  jsdom leaves a closed window's pending `setTimeout`s armed, so a single live window holding
  app.js's 12-second toast dismissal keeps node's event loop open and the *file* reports 13 s
  for 1 s of work. When that registration was dropped during a refactor, all eight tests still
  passed - only the wall clock moved - so `client-lifecycle.test.mjs` now asserts
  `windowsClosed == windowsOpened`, and 24 cases run in under 2 s instead of 23 in 17.

## 7. What this milestone deliberately did *not* do

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
| D1 output bounds | F1 | `protocol/message.py`, `agents/base.py`, `server/app.py:_safe_reason` | `test_provider_output_bounds.py` |
| D2 concurrent runs | F2 | `server/state.py:run_lock`, `server/app.py:/api/run` | `test_run_serialization.py`, e2e check "concurrency in the same tab" |
| D3 fan-out on dispatch path | F3 | `server/feed.py`, `server/state.py:publish`, WS handler `finally` | `test_ws_backpressure.py`, e2e "no frame was dropped on a healthy connection" |
| D4 dead sweeper | F4 | `server/app.py` lifespan task, `state.sweep_expired`, `close_feeds` | `test_session_lifecycle.py` |
| D5 listing parsed everything | F5 | `sessions.py:_head_meta`, stored `message_count` | `test_session_storage.py` - `test_listing_reads_only_the_header` caps what a listing may read per file |
| D6 non-atomic save | F5, F12 | `sessions.py:_write_atomic`, temp-file prune | `test_session_storage.py` (failed-write and stale-temp cases), `test_sessions.py` (endpoint surfaces the reason) |
| D7 retryable never used | F6 | `agents/providers.py:post_for_json` | `test_provider_retry.py` |
| D8 ambient key on the wire | F7 | `providers.py:allow_env_key`, `server/app.py` provider construction, header suppression | `test_env_key_isolation.py` - a capture server asserts the headers it actually received |
| D9 unguarded provider URL | F8 | `netguard.validate_provider_target`, `server/app.py:_guard_provider_url`, `--allow-insecure-provider-urls` | `test_provider_url_policy.py` |
| D10 cooldown spent by 400 | F11 | `server/app.py` ordering + `Retry-After` | `test_run_serialization.py` (2 cases) |
| D11 `run --live` missing | F9 | `cli.py:_apply_live_providers`, banner, exit 1 | `test_cli_live.py` |
| D12 `--no-delay`, unlinted examples | F10 | `mesh.py:_delay_kwargs`, `cli.py`, CI lint scope | `test_cli_live.py` (parametrised), `ruff check examples` in CI |
| D13 four stale prose claims | F13, N3 | README/SECURITY/USER_CENTERED_DESIGN rewritten in this change | `test_docs_are_accurate.py` |
| D14 trim loop could not terminate | F5 | `sessions.py` bounded loop + explicit refusal | `test_session_storage.py::test_a_single_huge_message_does_not_loop_forever` |
| D15 prune order tie | F5 | `sessions.py:_prune_sort_key`, sub-second `new_session_id` | `test_session_storage.py` (2 prune cases) |
| D16 badge claimed but absent | F12, F1 | `static/app.js`, `static/style.css` | `tests/js/client-lifecycle.test.mjs` ("clamped reply is badged") |
| D17 CI step assumed node ≥ 21 | N3 | `.github/workflows/ci.yml` (shell-expanded glob), every copyable command in the docs | `test_ci_runs_every_jsdom_suite`, `test_the_documented_way_to_run_the_browser_tests_works` |

| D18 no Stop control | F14 | `run_control.py`, `agents/base.py`, `server/state.py`, `server/app.py`, dashboard Stop | `test_run_cancellation.py`, `tests/js/client-lifecycle.test.mjs` (Stop lifecycle) |

| D19 reconnect loses run controls | F14 | `server/app.py` WebSocket snapshot, `static/app.js` init handling | `test_reconnecting_socket_receives_active_run_and_stop_state`, jsdom reconnect-during-run and missed-completion regressions |

| D20 second run refused | F15 | `server/config.py:MAX_QUEUED_SUFFIX`, `server/state.py:run_queue`, `cli.py:--max-queued`, `server/app.py` queue branch + pump + queued-aware cancel, `static/app.js` queued lifecycle | `test_run_queue.py` (202+position, 429+Retry-After, 0→409, FIFO order, queued cancel, per-scope isolation), `test_cli_live.py` max_queued resolution, jsdom queued-position + queue-full cases, e2e "concurrency in the same tab queues instead of refusing" |

Requirements **N1/N2/N4** (no new runtime dependency; every await bounded; runs bounded by
`--run-timeout`) are cross-cutting: they are the reason the fixes above are implemented as
a bounded per-connection outbox and an `asyncio.wait_for` rather than a queue, a worker
pool, or a new dependency.

---

## 9. Validation status

**Done and verified locally, at the commit this milestone produced:**

- `pytest -q` → 357 passed; `node --test tests/js/*.test.mjs` → 24 passed;
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
  `--no-delay`), and no benchmark script to re-measure them: a ratio like 167x moves
  with the filesystem and the page cache, so the listing's real guarantee is the
  deterministic one `test_listing_reads_only_the_header` makes - a listing reads at
  most `META_HEAD_BYTES` of any file, however large the transcript.
- No cross-browser verification: the client behaviours are jsdom + code review, not
  Safari/Firefox runs.
- The GitHub Actions workflow changes are unexecuted here (this environment has no
  runner); the steps were reproduced by hand locally, which is how the `[web]`-extra
  mistake was caught before commit.

### D18 cancellation contract

`POST /api/runs/{run_id}/cancel` sets a fresh per-run `asyncio.Event` and returns
`status: cancelling`. Repeated requests while active are harmless; unknown, finished,
or another browser's run returns 404 through the normal authenticated session route.
The run task carries the event in a task-local context, checked before each agent
starts, so every topology shares the same boundary without mutable agent flags.
The original run response returns `status: cancelled`; the feed emits `run_cancelled`.
A system message in history records cancellation and survives saving/exporting along
with the replies already received. Stop is disabled while idle and while awaiting
acknowledgement/completion; it becomes available once `run_started` supplies the id.
Tests were written first: active cancellation returned 404 and the DOM lacked Stop.
Tests cover early and final-agent cancellation, repeat/stale requests, browser isolation,
retained history, lock release, and a subsequent successful run with a fresh event.

Local D18 validation: `pytest -q` reported **365 passed**;
`node --test tests/js/*.test.mjs` reported **25 passed**. The full lint scope
passed, and `scripts/e2e_server_check.py` reported `ALL E2E CHECKS PASSED` against
`serve --port 8799` on its unchanged loopback default. The server was stopped
following the check. No real-provider or cross-browser validation was performed.

### D19 final review

Failing tests first reproduced missing run state in the WebSocket snapshot and
stale controls on reconnect. The snapshot now includes the session-scoped active
run id and pending cancellation flag; init restores Stop and Execute accordingly.
The existing local-request guard remains active until its HTTP request settles.

D19 local validation: **366 Python tests passed**, **27 jsdom tests passed**;
full-scope Ruff and the loopback server e2e gate passed. The gate server was stopped.
