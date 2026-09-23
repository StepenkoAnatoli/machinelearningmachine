# Implementation Plan — W1: Agent Presets & Preset Library

- **Date:** 2026-09-23
- **Spec:** [`docs/superpowers/specs/2026-09-23--agent-presets-design.md`](../specs/2026-09-23--agent-presets-design.md) (approved)
- **Branch:** `arena/01a0cd46-machinelearningmachine` (all work and commits stay here)
- **Phases:** P1 Gallery → P2 Personal kits → P3 Files & honesty (spec §8)

## Constraints & invariants (every task)

1. **No server behavior changes.** The only Python file added is the P3 parity test. `test_server.py`, isolation suites, and `/api/*` routes are untouched.
2. **Zero new dependencies.** `package.json` / `requirements*.txt` must not change. `npm install` only materializes the existing lockfile (jsdom 24.1.0 is already pinned).
3. **No network.** Import/export is FileReader / Blob only.
4. **`textContent` discipline** for every preset string; `color` reaches `style` only after the hex gate (spec §5.3); DOM building follows the Saved Sessions `makeButton` pattern (`dataset`/`setAttribute`, never interpolated attributes).
5. **Mirror table = server contract** (spec §5.1): `agent_id` effective rule 3–50 / `^[a-z0-9][a-z0-9-_]{1,48}[a-z0-9]$` / reserved `{system, broadcast, all, *, api, admin, root}` / `strip().lower()`.
6. One commit per task; TDD: **RED → GREEN → REFACTOR → commit**.

## Commands (verified against repo tooling)

| What | Command |
|---|---|
| JS tests (jsdom) | `npm run test:js` *(setup once: `npm install`)* |
| Python tests | `python -m pytest -q -ra --maxfail=25` |
| Lint | `ruff check machinelearningmachine tests scripts examples` |
| E2E | start server on a free port, then `python scripts/e2e_server_check.py --base http://127.0.0.1:<port>` |
| Setup once | `npm install` · `pip install -r requirements.txt -r requirements-dev.txt` |

---

## P1 — Gallery

### Task 1 — Mirror-table validation core (RED)
**Files:** `tests/js/presets.test.mjs` (new), `machinelearningmachine/server/static/presets.js` (new)
**RED:** write `validatePreset` boundary tests per spec §7.1 (every limit ±1: `name` 100/101, `role` 200/201, `system_prompt` 10/2000/2001, `label` 60/61, `avatar` 8/9, `color` `#abc`/`#AABBCC`/junk, `agent_id` 2-char ✗ / 3-char ✓ / reserved ✗ / uppercase-normalized, whitespace-only fields). Expose `MLMPresets.LIMITS` and `MLMPresets.validatePreset`. Run `npm run test:js` — must fail.
**GREEN:** implement DOM-free core in `static/presets.js` (`window.MLMPresets = { LIMITS, validatePreset, … }`). Tests pass.
**REFACTOR:** limits table as data (single object feeding all checks).
**Commit:** `feat(presets): mirror-table validation core (P1)`

### Task 2 — Built-in roster + uniqueLabel (RED)
**Files:** `tests/js/presets.test.mjs`
**RED:** tests for `MLMPresets.BUILTINS` — exactly the 5 spec entries (labels + `agent_id` slugs pinned in spec §4.2), each passing `validatePreset`; `uniqueLabel` manual family (`X` → `X (2)` → `X (3)`… against a full label set; empty/clean labels kept).
**GREEN:** implement `BUILTINS` with drafted copy (roster names/slugs fixed; prompt/role copy drafted here) and `uniqueLabel`.
**⚠️ CHECKPOINT (user):** present the 5 built-in templates' copy for a quick human look — spec says copy is user-reviewed before P1 lands.
**Commit:** `feat(presets): built-in roster + uniqueLabel (P1)`

### Task 3 — Chips + fillForm seam (RED)
**Files:** `tests/js/presets.test.mjs` (UI section), `static/presets.js`, `static/index.html`, `static/app.js`
**RED:** jsdom UI tests: 5 chips render in `#presetChips` (`role="group"`), chip click calls `fillForm` with that preset and toasts `Loaded preset: <label>`, chips never submit the form.
**GREEN:** implement `mount({ getFormData, fillForm, toast })` (P1 wires `fillForm` + `toast`), chips row markup in `#agentModal`, script tag (defer, before `app.js`), and the `app.js` seam (~15 lines, `payload` shape `{agent_id, name, role, system_prompt, color, avatar}` per spec §4.1).
**Verify:** `npm run test:js` · `python -m pytest -q tests/test_frontend_security.py tests/test_packaging.py -q` (static glob already ships `presets.js`).
**Commit:** `feat(presets): built-in chips fill the Add Module form (P1)`

## P2 — Personal kits

### Task 4 — Store core (RED)
**Files:** `tests/js/presets.test.mjs`
**RED:** `store` tests per spec §4.3/§7.1: load empty → `[]`; save/load round-trip; `setItem` throws → old library intact; bounds on **compact** serialization (8 KB/preset, 256 KB library, 50 presets) enforced on save; `localStorage` unavailable → in-memory + one-time toast text; library schema `mlm-agent-preset-lib/1` / key `mlm.agentPresets.v1` exact.
**GREEN:** implement store in the DOM-free core.
**Commit:** `feat(presets): localStorage library store with bounds (P2)`

### Task 5 — Save as preset (RED)
**Files:** `tests/js/presets.test.mjs` (UI), `static/presets.js`, `static/index.html`, `static/app.js`
**RED:** Save validates via `getFormData`; invalid form → toast naming the first offending field, nothing saved; `label` defaults to `name`; collision → ` (2)` family; success toast `Saved '<label>' to your presets.`; `[＋ Save as preset]` chip-row button present.
**GREEN:** implement flow; extend the seam with `getFormData`.
**Commit:** `feat(presets): save form as preset (P2)`

### Task 6 — Manager UI (RED)
**Files:** `tests/js/presets.test.mjs` (UI), `static/presets.js`, `static/index.html`
**RED:** disclosure with `aria-expanded` + badge count, collapsed default; rows in sessions `makeButton` pattern (`role="listitem"`, quoted `aria-label`s); **[Load]** fills + toasts `Loaded preset: …`; rename inline (Enter/blur commit, Esc cancel, invalid label → old label stands + toast); delete one-click removes row; built-ins never render as manager rows and core `rename`/`delete` refuse `BUILTINS`; empty state copy exact (spec §6.3).
**GREEN:** implement `#presetManager` UI.
**Commit:** `feat(presets): saved-presets manager (load/rename/delete) (P2)`

## P3 — Files & honesty

### Task 7 — Export codec (RED)
**Files:** `tests/js/presets.test.mjs`
**RED:** single-file export = pretty-printed preset (self-describing `schema`), library export = `mlm-agent-preset-lib/1` wrapper with per-entry `schema`; 2-space indent + trailing newline; filenames `preset-<slug>.json` (slug: lowercase, non-`[a-z0-9]` runs → `-`, trim, ≤40 chars, fallback `preset`) and `agent-presets-YYYY-MM-DD.json`; `[⬇ All]` disabled when empty; per-row `[⬇]`.
**GREEN:** implement export (Blob + anchor; no network).
**Commit:** `feat(presets): export single + library files (P3)`

### Task 8 — Import pipeline (RED)
**Files:** `tests/js/presets.test.mjs`
**RED:** hostile suite per spec §5.2/§7.1 — gates 1–7 in order: >512 KB; non-JSON; missing/unknown `schema` message exact; single-vs-library autodetect; >100 entries rejected; `__proto__`/`constructor`/`prototype` payloads (**assert `Object.prototype` stays clean**); non-string types ignored per field; unknown keys dropped (whitelist-copy); per-preset skip + summary toast `Imported 3 of 5 — 2 skipped.`; ` (imported)` / ` (imported 2)` collision family; 50-cap fill with skip report; `setItem` failure → old library intact; nested junk never walked.
**GREEN:** implement gates 1–7 exactly as specified.
**Commit:** `feat(presets): hardened import pipeline (P3)`

### Task 9 — Drift-lock parity (RED)
**Files:** `tests/js/preset-contract.test.mjs` (new), `tests/test_preset_contract.py` (new)
**RED→GREEN:** each test restates the spec §5.1 table literally and asserts the implementation equals it — JS against `MLMPresets.LIMITS` + `BUILTINS` ids; Python against `AddAgentRequest` field bounds, `AGENT_ID_PATTERN` (effective 3–50 reading documented), and `RESERVED_AGENT_IDS`. Any server/client drift turns one of them red.
**Commit:** `test(presets): drift-lock parity between client, server and spec (P3)`

### Task 10 — Docs honesty + full verification (RED)
**Files:** `README.md`, `USER_CENTERED_DESIGN.md`
**RED (docs-accuracy):** run `python -m pytest -q tests/test_docs_are_accurate.py` before and after; the future-list still names presets until this task flips it.
**GREEN:** README "Practical & Pleasant" gains one accurate bullet (module presets: built-ins, saved kits, file import/export — local-only); `USER_CENTERED_DESIGN.md` future-list retires the presets entry using its own "remove shipped items, don't leave stale claims" rule.
**Full verification:** `ruff check machinelearningmachine tests scripts examples` · `python -m pytest -q -ra --maxfail=25` · `npm run test:js` · `npm run audit:js` · e2e against a local server (`scripts/e2e_server_check.py`) · confirm `git diff --stat` touches no dependency manifests.
**Commit:** `docs: ship agent presets — README + future-list honesty (P3)`

---

## Task loop & checkpoints

Execute tasks **in order**. After every task: run that task's test command, commit, and report (one line: what landed, tests green). Pause points are already embedded:

1. **Task 2 checkpoint** — built-in template copy review (user eyes required by the spec).
2. **End of each phase (P1/P2/P3)** — phase is independently shippable; user may stop or redirect with working software at every boundary.

## Definition of done (maps to spec §9)

| # | Criterion | Verified by |
|---|---|---|
| 1 | One chip click + Register → "Security Auditor" | Task 3 UI tests |
| 2 | Reload survives; export→import lossless | Tasks 4 + 7 round-trip tests |
| 3 | Hostile import can't inject, exceed limits, or corrupt | Task 8 suite + Task 3/6/7 XSS regressions |
| 4 | Runs/transcripts/session isolation untouched | Task 10 full suites (isolation suites green, zero server diffs) |

Plus: all suites green, no dependency-manifest changes, docs accurate.
