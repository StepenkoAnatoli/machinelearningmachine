# Implementation Plan — W3: Offline PWA (Installable Shell & Honest Unreachable State)

- **Date:** 2026-09-23
- **Spec:** [`docs/superpowers/specs/2026-09-23--offline-pwa-design.md`](../specs/2026-09-23--offline-pwa-design.md) (approved)
- **Branch:** `arena/01a0cd46-machinelearningmachine` (all work and commits stay here)
- **Phases:** P1 Installable → P2 Service worker → P3 Unreachable state → P4 Honesty & verification (spec §8)

## Constraints & invariants (every task)

1. **The CSP header is unchanged, byte for byte.** No new directive, no relaxation. `tests/` already pins it; this feature must pass without touching it.
2. **`PUBLIC_PATHS` grows by exactly two entries** (`/sw.js`, `/manifest.webmanifest`). `/api/*` stays authenticated. `/favicon.ico` is already listed — it stops being a dangling entry.
3. **Zero new dependencies.** `package.json`, `package-lock.json`, `requirements*.txt`, `pyproject.toml` must not change. PNG/ICO generation uses stdlib `zlib` + `struct` only.
4. **Scope A is a promise, not a preference:** no API response, transcript, session or `/api/` URL ever enters a cache. A test proves it (§7.2 negative lock).
5. **Network-first with no artificial timeout** for shell assets — a slow-but-alive server always wins over a cached copy.
6. **No inline scripts/styles** (`tests/test_frontend_security.py`): worker registration lives in `app.js`.
7. **Every new colour utility needs a light-theme rule**, or the W2 drift locks (`tests/js/theme.test.mjs`) go red. The offline banner is new markup with new tokens — plan for it, don't discover it.
8. **No behaviour change when the worker cannot register** (plain-`http://` LAN address): the dashboard must be identical to today.
9. One commit per task; TDD: **RED → GREEN → REFACTOR → commit**. Report one line per commit; wait for **go**.

## Commands (verified against repo tooling)

| What | Command |
|---|---|
| JS tests (jsdom + node) | `npm run test:js` *(setup once: `npm ci`)* |
| Python tests | `.venv/bin/python -m pytest -q -ra --maxfail=25` |
| Lint | `.venv/bin/python -m ruff check machinelearningmachine tests scripts examples` |
| E2E | `.venv/bin/python -m machinelearningmachine serve --port 8799 &` then `.venv/bin/python scripts/e2e_server_check.py --base http://127.0.0.1:8799` |
| Vendor rebuild check | `.venv/bin/python scripts/build_vendor.py` → `git status --short .../vendor/` empty |
| Safety audit | `npm run audit:js` |

**Known tooling constraint (this environment):** `node_modules/` and `.venv/` are dropped between sessions — run `npm ci` and rebuild the venv before the first test run.

---

## P1 — Installable (no caching, no worker)

### Task 1 — Icon source + generator + committed binaries (RED)
**Files:** `scripts/make_icons.py` (new), `machinelearningmachine/server/static/icons/icon.svg` (new), `…/icons/icon-192.png`, `…/icons/icon-512.png`, `…/icons/apple-touch-icon.png` (180), `…/favicon.ico` (all new, committed binaries), `tests/test_pwa.py` (new)
**RED:** `tests/test_pwa.py` asserts: the four binaries exist; their real pixel dimensions (parsed from the PNG IHDR / ICO header, no Pillow) are 192×192, 512×512, 180×180, 32×32; and **regenerating from `icon.svg` via `scripts/make_icons.py` is byte-identical** to what is committed. Must fail (nothing exists).
**GREEN:** hand-author `icon.svg` (rounded-square indigo→purple→pink gradient + white lightning bolt, matching the header mark), implement the generator (stdlib `zlib`/`struct`, PNG chunks + ICO container), run it, commit the outputs.
**REFACTOR:** the SVG is the single source of truth — the generator parses the SVG's own colours/dimensions rather than duplicating them in Python.
**Verify:** the byte-identical test passes twice in a row (idempotent), and after `git clean`-style regeneration.
**Commit:** `feat(pwa): app icons from one SVG source, generated with no dependencies (P1)`

### Task 2 — Manifest + routes + public paths (RED)
**Files:** `machinelearningmachine/server/static/manifest.webmanifest` (new), `machinelearningmachine/server/app.py`, `tests/test_pwa.py`
**RED:** pytest: `GET /manifest.webmanifest` → 200, `application/manifest+json`, valid JSON, `name`/`short_name`/`start_url: "/"`/`display: "standalone"`/`theme_color`/`background_color`; every icon entry's `sizes` matches the real PNG dimensions; `GET /favicon.ico` → 200 with an image content type; both the manifest and `/favicon.ico` are reachable **with `--auth-token` set** (public-path regression); `GET /sw.js` is *not* required yet (P2).
**GREEN:** add `@app.get("/manifest.webmanifest")` and `@app.get("/favicon.ico")` beside `serve_index()`, returning the files with explicit media types; extend `PUBLIC_PATHS` with `"/manifest.webmanifest"`; write the manifest pointing at the Task-1 icons.
**Verify:** `pytest tests/test_pwa.py tests/test_server.py tests/test_packaging.py`; CSP header unchanged (existing security test).
**Commit:** `feat(pwa): web app manifest, real favicon, public install routes (P1)`

### Task 3 — Markup wiring + packaging/docs honesty (RED)
**Files:** `machinelearningmachine/server/static/index.html`, `tests/test_packaging.py`, `tests/test_docs_are_accurate.py`, `README.md`
**RED:** `tests/test_packaging.py` gains the new required assets (`icons/icon-192.png`, `icons/icon-512.png`, `manifest.webmanifest`, `sw.js` marked as P2-pending → added in Task 6 instead); docs test fails on the new trees/counts.
**GREEN:** `<link rel="manifest" href="/manifest.webmanifest">`, `<link rel="apple-touch-icon" href="/static/icons/apple-touch-icon.png">`, favicon link → `/favicon.ico` (drop the data-URI); README trees + a **reworded** offline claim that distinguishes "no third-party assets" from "installs as an app; the shell opens when the server is down (localhost/HTTPS only — a plain LAN `http://` address gets no service worker)".
**Verify:** `pytest tests/test_docs_are_accurate.py tests/test_packaging.py tests/test_frontend_security.py`.
**Commit:** `docs(pwa): manifest wired into the head, docs stop overclaiming offline (P1)`

> **P1 checkpoint for the user:** install prompt works on `http://127.0.0.1:<port>` (Chrome/Edge), icon renders in the tab and the install dialog. Nothing about caching or offline yet.

---

## P2 — Service worker (the shell survives)

### Task 4 — `sw.js` contract (RED)
**Files:** `tests/js/service-worker.test.mjs` (new), `machinelearningmachine/server/static/sw.js` (new)
**RED:** a node test that loads `sw.js` under a stubbed worker global (`caches`, `self.addEventListener`, `fetch`/`Request`/`Response` from undici) and asserts: `install` calls `cache.addAll(<list>, {cache: "reload"})` + `skipWaiting`; the list **equals** the shipped shell set minus the three documented exclusions (`vendor/licenses/*`, `vendor/MANIFEST.json`, `sw.js`) — the drift lock; `activate` deletes non-current caches + `clients.claim()`; the fetch handler ignores non-GET requests and `/api/` + `/ws`; navigations fall back to `caches.match("/")`; **negative locks:** no `cache.put` anywhere, no `/api/` string inside the caching path, exactly one cache name.
**GREEN:** implement `sw.js` (network-first, cache-fallback; `mlm-shell-v1`; three exclusions commented with their reasons).
**REFACTOR:** the precache list is one literal array at the top, the only place a new asset needs adding.
**Commit:** `feat(pwa): shell service worker, network-first with a drift-locked precache (P2)`

### Task 5 — Registration wiring (RED)
**Files:** `tests/js/offline.test.mjs` (new), `static/app.js`, `app.py`, `tests/test_pwa.py`
**RED:** jsdom: registration is attempted only when `navigator.serviceWorker` exists; it registers **`/sw.js` with scope `/`**; a rejected registration is swallowed (no toast, no throw, dashboard unaffected); with no `serviceWorker` at all (today's jsdom default) nothing happens. pytest: `GET /sw.js` → 200, `text/javascript`, `Cache-Control: no-cache`, reachable **with `--auth-token`**; `PUBLIC_PATHS` contains it.
**GREEN:** the `navigator.serviceWorker.register("/sw.js")` call in `app.js` behind a feature check + swallow, the `/sw.js` route, the `PUBLIC_PATHS` entry.
**Verify:** `npm run test:js` · `pytest tests/test_pwa.py` · manual: server up → DevTools shows the worker active; stop server → reload → shell still renders.
**Commit:** `feat(pwa): register the shell worker, serve /sw.js from the root (P2)`

> **P2 checkpoint for the user:** open the dashboard, stop the server, reload — the page loads (P3's banner and disabling arrive next).

---

## P3 — Unreachable state (honest, and it recovers by itself)

### Task 6 — Unreachability detection + banner (RED)
**Files:** `tests/js/offline.test.mjs`, `static/index.html`, `static/app.js`, `static/style.css`
**RED:** jsdom with a rejecting fetch double: a `#offlineBanner` becomes visible with `role="status"` (not `alert`, never focused), naming the launcher; with a succeeding double it stays hidden; a **4xx from a reachable server is not "unreachable"**; the socket's `session_released` frame does **not** enter the offline state (released stays authoritative); boot-time probe failure enters the state.
**GREEN:** `#offlineBanner` markup (new tokens → **light-theme rules in the same commit**), the boot probe, the `reachable`/`unreachable` state machine, banner show/hide.
**Verify:** `npm run test:js` (theme drift locks included) · light + dark screenshots by eye.
**Commit:** `feat(pwa): say when the server is unreachable instead of failing silently (P3)`

### Task 7 — Disable what cannot work, keep what can (RED)
**Files:** `tests/js/offline.test.mjs`, `static/index.html`, `static/app.js`, `static/style.css`
**RED:** with the state `unreachable`: every control marked `data-requires-server` is `disabled` with a `title`/`aria-describedby` reason, the documented server-bound set is exactly the disabled set (drift lock against a literal list in the test), and the local set (theme toggle, copy prompt, prompt box, preset library controls, TTS, help, search) is **not** disabled; restoring the state re-enables all of them.
**GREEN:** `data-requires-server` markers on the documented controls (including dynamically-built session-row buttons), one `setServerReachable(bool)` that toggles `disabled` from that attribute set.
**Commit:** `feat(pwa): disable server-bound controls while unreachable, with a reason (P3)`

### Task 8 — Automatic recovery (RED)
**Files:** `tests/js/offline.test.mjs`, `static/app.js`
**RED:** while unreachable the probe re-runs on the documented backoff schedule (drive the harness clock and count attempts: 1s → 2s → 5s → 10s, capped); the first success clears the banner, re-enables controls, calls `refreshMeshState()`, reconnects the socket, and **preserves the text in the prompt box**; no reload is simulated; the socket's own reconnect backoff is not multiplied by the probe's.
**GREEN:** the probe loop + the recovery handoff.
**REFACTOR:** one `probeServer()` used by boot and by the loop.
**Commit:** `feat(pwa): reconnect on our own when the server comes back (P3)`

> **P3 checkpoint for the user:** the end-to-end story — stop the server, reload (shell + banner + disabled set), restart the server (banner clears, controls return, prompt text intact).

---

## P4 — Honesty & verification

### Task 9 — Docs truth + full sweep (RED)
**Files:** `README.md`, `USER_CENTERED_DESIGN.md`, `tests/test_docs_are_accurate.py`, `tests/test_packaging.py`
**RED:** docs test fails: README trees must list `sw.js`, `manifest.webmanifest`, the icons and the two new test files; counts must match the real totals; the "Genuinely offline UI" bullet must state the secure-context limit and the port-is-part-of-the-origin limit; `USER_CENTERED_DESIGN.md`'s future list drops *Offline support* into the removal note (the repo's rule: a shipped item may not stay listed as future work).
**GREEN:** the edits above.
**Then:** full sweep — pytest (all), `npm run test:js`, ruff, `npm run audit:js`, e2e on a live server, `scripts/build_vendor.py` → vendor tree byte-identical, `git diff <branch-point> -- '*/package*.json' requirements*.txt pyproject.toml` empty, CI green on PR #42.
**Commit:** `docs(pwa): ship the offline story — README, trees, counts, future list (P4)`

---

## Task loop & checkpoints

For each task: write the failing test → run it and **see it fail for the right reason** → implement → tests green → refactor → full relevant suite → commit → **one-line report** → wait for the user's **go** before the next task.

Checkpoints where the user should look at the running app, not just the tests: **after P1 Task 3** (install prompt + icon), **after P2 Task 5** (shell survives a stopped server), **after P3 Task 8** (the full offline → recovery story).

## Definition of done (maps to spec §9)

1. Installable from `http://127.0.0.1:<port>` with a real 192/512 icon; `/favicon.ico` serves a real file.
2. With the server stopped, opening the dashboard shows the dashboard: banner, disabled server-bound controls, working local controls.
3. Starting the server clears the banner and re-enables everything with no reload and no lost prompt text.
4. No API response, transcript or `/api/` URL ever enters a cache (test-proven).
5. Docs state the secure-context and origin/port limits; the offline bullet is reworded; nothing claims offline *dialogue*.
6. CSP byte-identical · zero new dependencies · dark mode untouched · light mode covered by the W2 drift locks · all suites green · CI green on PR #42.
