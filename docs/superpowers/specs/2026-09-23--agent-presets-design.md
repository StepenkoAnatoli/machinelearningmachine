# W1 — Agent Presets & Preset Library — Design

- **Date:** 2026-09-23
- **Workstream:** W1 of a four-workstream program (W1 → W2 → W4 → W3)
- **Classification:** **Architectural** (upgraded from Bounded when scope grew from "built-in templates" to a preset-management subsystem; the ratchet is one-way)
- **Path:** full process — clarifying questions, approaches, sectioned design (all four sections approved), this spec
- **Status:** design approved in sections; awaiting spec review before implementation planning

---

## 1. Context & Problem

`USER_CENTERED_DESIGN.md` lists "Agent presets" as future work: *"'Security Auditor', 'DB Expert' templates for the **Add Module** form (the existing presets fill in a scenario, not a module)."*

What exists today:

- **Add Custom Module modal** (`#agentModal` in `static/index.html`, submit handler in `static/app.js` ≈ line 1983) with six fields — Module ID (slug), Display Name, Domain Role, System Prompt, Color, Emoji Avatar — submitted to `POST /api/agents` and validated server-side by `AddAgentRequest` (`machinelearningmachine/server/app.py:156`, constants at `app.py:73–82`).
- **Scenario presets** (`.preset-btn` chips) that fill the *prompt box* — the wrong target, per the future-list note.
- **Saved Sessions panel** — the house pattern for list rows, icon buttons, empty states, and safe DOM construction (`createElement` + `dataset`/`setAttribute`, never interpolated attributes; `textContent` labels).

This design adds the missing kind: **module presets** — curated built-in templates plus a personal, saveable, file-portable library that prefills the Add Custom Module form.

## 2. Locked Decisions (from the design dialogue)

| Decision | Choice | Why |
|---|---|---|
| Scope | Built-in gallery **+** user-savable presets **+** import/export | Explicitly chosen over built-ins-only |
| Purpose | **Personal kits**; built-ins cover quick-start/teaching; files cover handoff | A "team sharing platform" would fight the project's "no third-party origins, genuinely offline" ethos |
| Storage | **Browser `localStorage`**, per browser profile | The server stays ignorant of presets; export/import is the durability + portability story |
| Export/import granularity | **Both, uniform format**: single-preset file and whole-library file; importer auto-detects | One schema, one code path; covers "share one module" and "back up everything" |
| Architecture | **Approach A**: self-contained `static/presets.js` classic script (the `markdown.js` pattern) | See §3 |

## 3. Approaches Considered

| | Approach | Verdict |
|---|---|---|
| **A** | **Self-contained `presets.js`** (classic script): DOM-free core (schema, validation, store, codec) + thin UI layer mounted into the existing modal; `app.js` gains a ~15-line seam. Tested in `tests/js/` under the established jsdom `win.eval(read(...))` pattern. | **Chosen.** Follows the `markdown.js` precedent; testable without the full `app.js` harness; honors "server stays ignorant of presets"; CSP `default-src 'self'` untouched (same-origin script); import/export is FileReader/Blob only — no network. |
| B | Inline everything in `app.js` | Rejected: grows the 2.5k-line monolith by hundreds of lines; pure logic trapped in a classic script forces every preset test through the full `client-lifecycle` jsdom harness. |
| C | Server-authoritative validation via `POST /api/presets/validate` | Rejected: breaks the locked "server stays ignorant" constraint; adds API surface inheriting this repo's auth/rate-limit obligations; async round-trips for a client concept. Parity is achieved instead by drift-lock tests (§7.3). |

## 4. Architecture, Components & Data Flow

```
        ┌──────────────┐   getFormData()/fillForm()   ┌───────────────────────────┐
        │   app.js     │◄───────── seam (~15 lines) ──►│  presets.js  (new file)   │
        │ modal life-  │                              │  ├─ core: schema, validate,│
        │ cycle, toast │                              │  │   store, single/lib codec│
        └──────────────┘                              │  └─ ui: chips + manager +  │
                                                      │      import/export wiring │
                                                      └──────┬──────────┬─────────┘
                                                        localStorage   FileReader/Blob
                                                    mlm.agentPresets.v1   (no network)
```

### 4.1 Components

1. **`static/presets.js`** *(new; classic script, loaded as a sync `<script>` immediately before `app.js` — both are end-of-body; a `defer` tag in `<head>` would execute **after** `app.js`'s sync script and the seam would find no `MLMPresets`)* — exposes one global: `window.MLMPresets`, with `mount({ getFormData, fillForm, toast })` and the core surface (incl. `LIMITS`) for tests. The core is DOM-free so jsdom tests can exercise it directly. `app.js` keeps owning modal lifecycle, focus trap, and toasts; `presets.js` never reimplements them.
2. **`static/index.html`** — one `<script>` tag; inside the existing `#agentModal`: chips row `#presetChips`, saved-presets manager `#presetManager`, a hidden `<input type="file">`, and the import/export controls (layout in §6).
3. **`static/app.js`** — the ~15-line seam only. The submit handler already assembles `payload = {agent_id, name, role, system_prompt, color, avatar}` (≈ line 1984); `getFormData()` reuses that exact shape, `fillForm()` writes the same six fields. **Register behavior is unchanged.**
4. **`tests/js/presets.test.mjs`** *(new)* — jsdom tests in the established pattern (`node --test tests/js/*.test.mjs`).
5. **Docs touch (P3 only)** — README bullet; retire the future-list entry in `USER_CENTERED_DESIGN.md` using that document's own honesty rule ("remove shipped items rather than leave stale claims"); `test_docs_are_accurate.py` stays green.

### 4.2 Data model — `Preset` (schema `mlm-agent-preset/1`)

```json
{
  "schema": "mlm-agent-preset/1",
  "label": "Security Auditor kit",
  "agent_id": "security-auditor",
  "name": "Security Auditor",
  "role": "Application Security Reviewer",
  "system_prompt": "You are a security…",
  "color": "#0ea5e9",
  "avatar": "🛡️"
}
```

- `label` (1–60 chars, not whitespace-only) is the only field beyond the six form fields. "Save as preset" defaults it to Display Name; rename edits `label` only.
- `agent_id` is stored already normalized (`strip().lower()` per the server contract) so chips fill ids Register accepts unchanged.

**Initial built-in roster (5):** Security Auditor (`security-auditor`), DB Expert (`db-expert`), Performance Engineer (`perf-engineer`), QA/Test Engineer (`qa-engineer`), Technical Writer (`tech-writer`). Names, count, and slugs are fixed here; the exact field copy is drafted during P1 and user-reviewed before P1 lands.

### 4.3 Storage

- **One key:** `mlm.agentPresets.v1` = `{"schema": "mlm-agent-preset-lib/1", "presets": [Preset, …]}` — one atomic `setItem`, simple quota math.
- **Built-ins are code, not data:** the curated gallery lives in `presets.js` and is merged at render as un-deletable, un-editable entries; user presets can never overwrite them (label collisions suffix per §5, gate 6). Wiping `localStorage` cannot lose what ships with the app.
- **Bounds** (all measured on **compact** JSON serialization): ≤ 50 user presets; ≤ 8 KB per preset; ≤ 256 KB whole library. Since 50 × 8 KB can exceed 256 KB, the library total is re-checked on every save/import — the tighter bound always wins. A library exported at cap pretty-prints to well under the 512 KB import gate, so round-trips can't fail on size.
- **Degradation:** if `localStorage` throws (some private windows), presets work in-memory for the session with a one-time toast: *"Presets won't persist in this browser session."* Success criterion #2 holds wherever storage is actually available.

### 4.4 Data flow

- **Chip click** → `fillForm(preset)` → user tweaks → **Register** (unchanged `POST /api/agents`).
- **Save as preset** → `getFormData()` → validate → library save.
- **Import/export** → local file I/O only (FileReader / Blob + anchor download). No fetches, ever.

## 5. Validation, Import Safety, File Format & Errors

### 5.1 Mirror table (server is the contract)

`AddAgentRequest` (`server/app.py:156`) is authoritative. Presets validate to **server acceptance**, not the HTML attributes — the HTML pattern `^[a-z0-9][a-z0-9-_]{0,48}[a-z0-9]$` and the client-side check (`app.js` ≈ 1993) accept 2-char ids that the server pattern rejects (known drift, out of scope to fix here); the server pattern's `^[a-z0-9]$` single-char alternative is unreachable due to `min_length=2`. Effective rules:

| Field | Rule |
|---|---|
| `agent_id` | `strip().lower()`; 3–50 chars; `^[a-z0-9][a-z0-9-_]{1,48}[a-z0-9]$`; not in reserved set `{system, broadcast, all, *, api, admin, root}` |
| `name` | 1–100 chars; not whitespace-only |
| `role` | 1–200 chars; not whitespace-only |
| `system_prompt` | 10–2000 chars; not whitespace-only |
| `color` | `^#[0-9a-fA-F]{6}$` |
| `avatar` | ≤ 8 chars |
| `label` *(ours)* | 1–60 chars; not whitespace-only |

This table is the meeting point for the drift-lock tests (§7.3).

### 5.2 Two validation layers

- **Save path:** Save-as-preset validates against the mirror table before anything enters the library. Nothing bad gets stored.
- **Import path:** every byte is hostile. Ordered gates:

1. **Size** — file > 512 KB → *"That file is too large to be a preset file."*
2. **Parse** — `JSON.parse` in try/catch → *"That file isn't valid JSON."* on throw.
3. **Shape** — must self-describe: `schema: "mlm-agent-preset/1"` (single preset object) or `schema: "mlm-agent-preset-lib/1"` with a `presets` array (library). Unknown/missing schema → *"That file was made for a different version of this app."* Single-vs-library is auto-detected by shape. Library entries beyond 100 at the shape gate → *"That file lists more presets than this app supports."*
4. **Flat by construction** — the format has no nesting to traverse: the validator reads only top-level, known fields, type-checks each as string before length checks, **whitelist-copies** known fields into fresh objects, ignores unknown keys, and never spreads / `Object.assign`s parsed objects into anything. This is also why `__proto__` / `constructor` / `prototype` payloads have no path to pollution (locked by tests, §7.1).
5. **Per-preset field validation** against the mirror table. Policy: **accept valid entries, skip bad ones, report honestly** — *"Imported 3 of 5 — 2 skipped."* with `console.warn` details. One corrupt entry cannot spoil a 20-preset kit.
6. **Merge** — imported entries become user presets (built-ins are unreachable by import). Label collisions run through one shared `uniqueLabel()` helper against the full current label set (built-ins + user): the import family tries ` (imported)`, ` (imported 2)`, ` (imported 3)`…; the manual-save family (§6.2) tries ` (2)`, ` (3)`…. `agent_id` may repeat across presets (they are templates; uniqueness is enforced at Register time). Fill to the 50-preset cap, then report the remainder as skipped.
7. **Atomic commit** — build the complete new library array in memory → one `localStorage.setItem`. Quota/serialization failure leaves the old library untouched and shows a plain toast.

### 5.3 Rendering rule (XSS posture)

Preset strings reach the DOM via **`textContent` only** — never `innerHTML`. `color` touches `style` properties only after the hex gate. Row/button construction follows the Saved Sessions `makeButton` discipline exactly (`dataset`/`setAttribute`, never interpolated attributes; `aria-label`s built with quoted names). Imported files are data, never markup.

### 5.4 File format (uniform, human-diffable)

- **Single preset file** = the Preset object, pretty-printed (self-describing via its own `schema`).
- **Library file** = `{"schema": "mlm-agent-preset-lib/1", "presets": [Preset, …]}` — every entry keeps its own `schema`, so a split-out single file stays valid.
- 2-space indent + trailing newline. No numbers, no nesting beyond the library array.
- Export names: `preset-<label-slug>.json` and `agent-presets-YYYY-MM-DD.json`. Slug: lowercase, every run of non-`[a-z0-9]` becomes one `-`, trimmed, capped at 40 chars, fallback `preset` if empty.
- File picker: `accept=".json,application/json"` — content is validated regardless of name/extension.

### 5.5 Error voice

Toasts only (existing `showToast`), plain language + what to do next, never raw parser output or stack traces. Every failure is non-destructive. Save-as-preset on an empty/invalid form names the first offending field (*"System Prompt needs at least 10 characters."*).

## 6. UI/UX Flow

### 6.1 Layout (inside the existing `#agentModal`, above the form)

```
┌─ Add Custom Module ───────────────────────────── ✕ ─┐
│ Module presets                                        │
│ [🛡️ Security Auditor] [🗄️ DB Expert] [⚡️ Perf Eng] …  [＋ Save as preset] │
│ ▾ Saved presets (2)              [⬆ Import] [⬇ All]  │
│   🧩 My DBA kit · DBA Architect…    [Load][✎][🗑][⬇]  │
│   🔍 Sec review kit · AppSec…       [Load][✎][🗑][⬇]  │
│ ───────────────────────────────────────────────────── │
│ Module ID (slug) *   ← six fields completely unchanged │
│ …                                                   │
│                                        [Cancel] [Register] │
└──────────────────────────────────────────────────────┘
```

### 6.2 Flows

1. **Template → Register (success criterion #1):** open modal → click a chip → six fields fill + toast *"Loaded preset: Security Auditor"* (voice matches the existing *"Loaded preset: …"* scenario toasts) → tweak or not → **Register**. Chips only fill; they never submit. Register stays explicit.
2. **Save form as preset:** `[＋ Save as preset]` sits at the end of the chips row (always visible — saving is the core of the "personal kits" purpose). It validates via `getFormData()`, defaults `label` to Display Name, saves instantly with toast *"Saved 'Security Auditor' to your presets."* Colliding labels get ` (2)`, ` (3)`… until free (manual-save family; imports use the ` (imported)` family — §5, gate 6).
3. **Manage (collapsed by default; badge shows count):** a disclosure button (`aria-expanded`) — open when you want your library, closed so the common flow stays short. Rows mirror the Saved Sessions row pattern: info block (`avatar + label`, meta line `name · role` truncated) + actions **[Load]** (text, indigo; fills the form and toasts *"Loaded preset: …"* exactly like chips), **[✎ rename]**, **[🗑 delete]**, **[⬇ export]** (icon buttons). Delete is one-click, like session rows. Rename is inline: the label swaps to a small `<input>` (Enter/blur commits, Esc cancels) — a commit only lands when the new label passes the mirror table, otherwise the old label stands and a toast names the rule. No `prompt()`, no new modal.
4. **Import / Export:** `[⬆ Import]` and `[⬇ All]` sit on the disclosure row itself — visible whether the manager is open or closed (rarer actions stay out of the chip row); `[⬇ All]` is disabled while the library is empty. Per-preset `[⬇]` lives on each row. All local file I/O per §5; summary toast on completion.

### 6.3 Empty states & a11y

- Chips always render (built-ins are code). Manager open + empty → *"No saved presets yet — fill the form and hit 'Save as preset'."*
- Chips `role="group"` labeled "Module presets"; manager a real disclosure button; rows `role="listitem"`; every icon button an `aria-label` quoting the preset name (sessions pattern); rename input labeled and focus-managed (focus in on open, focus back to the rename button on commit/cancel); focus trap remains owned by `app.js`; reduced-motion handled by existing styles.

### 6.4 Deliberate non-goals (YAGNI)

Drag-reorder, categories/tags, search (≤ 50 presets), editing/deleting built-ins, one-click-register chips, share-URLs, cloud sync.

## 7. Testing

All in existing patterns; **zero new dependencies** (jsdom + `node --test` already used by `tests/js/`).

### 7.1 `tests/js/presets.test.mjs` — core (DOM-free, `win.eval(read(...))` pattern)

- **Mirror-table boundaries:** every limit ±1 (name 100/101, prompt 10/2000/2001, slug shapes incl. 2-char vs 3-char, reserved IDs, whitespace-only, `color` case, avatar 8/9, label 60/61).
- **Round-trip losslessness** (success criterion #2): preset → library export → import → deep-equal.
- **Shape/version gates:** single vs library auto-detect; missing/unknown `schema` message; > 100 entries rejected.
- **Hostile imports:** `__proto__`/`constructor`/`prototype` keys (**assert `Object.prototype` stays clean**), non-string types in string fields, unknown keys ignored, > 512 KB files, non-JSON, huge arrays (shape-gate cap), nested junk the flat reader never walks; label-collision suffixing ` (imported)`, ` (imported 2)`; 50-cap fill with honest skip counts.
- **Store safety:** `setItem` throws → old library intact; per-preset 8 KB / library 256 KB bounds enforced on save and import-merge; in-memory degradation when `localStorage` is unavailable.
- **Codec hygiene:** pretty-print + trailing newline; export filename slugging (unicode labels → safe names, empty → `preset`).

### 7.2 UI-layer tests (jsdom, `client-lifecycle.test.mjs` style)

Chip → `fillForm` seam; Save-as-preset (incl. label default + ` (2)` suffix); rename Enter/blur/Esc; delete row; built-ins expose no delete affordance; import summary toast text (*"Imported 3 of 5 — 2 skipped."*); **XSS regressions** — label/name/role/prompt carrying `<img onerror>…`, `<script>`, attribute-breakout payloads render inert (`textContent` discipline asserted), junk `color` strings never reach `style`.

### 7.3 Drift-lock parity (the `test_docs_are_accurate.py` spirit: this spec's mirror table is the meeting point)

- A **Python test** (`tests/test_preset_contract.py`, new) asserts `AddAgentRequest`'s limits, pattern, and reserved set equal the table.
- A **JS test** asserts `MLMPresets.LIMITS` (the table exposed by `presets.js`) equals the table.

Server and client cannot drift without a red test.

### 7.4 Whole suites stay green

`test_frontend_security.py`, `test_server.py`, `tests/js/*`, `scripts/e2e_server_check.py`. No npm/pip changes → audits unaffected.

## 8. Phasing (inside W1 — each phase independently shippable, separate commits)

| Phase | Delivers | Tests |
|---|---|---|
| **P1 — Gallery** | `presets.js` core subset + chips + built-in templates + `fillForm` seam | core validation + chip-fill UI tests |
| **P2 — Personal kits** | store + Save-as-preset + manager (load/rename/delete) | store safety + manager UI tests |
| **P3 — Files & honesty** | import/export (single + library) + docs | hostile-file suite + round-trip + parity + docs accuracy |

Implementation will be planned with TDD per phase (tests first, in this order).

## 9. Success Criteria & Traceability

| # | Criterion | Locked by |
|---|---|---|
| 1 | Add-Modal → registered "Security Auditor" in **one chip click + Register** | UI chip test (P1) |
| 2 | Saved presets survive reload; export → import on another machine/browser is **lossless** | round-trip test (P3) |
| 3 | A hostile or corrupt import can never inject markup, exceed form limits, or corrupt the library — plain-language errors, no state damage | hostile-import suite + XSS regressions (P3) |
| 4 | Runs, transcripts, and server-side session isolation are untouched | non-goal; `test_server.py` & isolation suites unchanged and green |

## 10. Feature-Level Non-Goals (reasoned)

- **Server awareness of presets** — locked constraint; keeps `/api/*` surface, auth, and rate limits out of the feature.
- **Editing/deleting built-ins** — they are code, not data.
- **Categories / search / reorder** — ceremony at ≤ 50 presets.
- **Session-scoped presets** — presets are **browser-profile** personal kits on purpose; per-session isolation semantics stay untouched.
- **Sync / telemetry** — none, ever (project ethos).

## 11. Program Context

This spec covers **W1 only**. The approved program order is **W1 → W2 (dark/light toggle, Bounded) → W4 (copy-prompt button, Bounded) → W3 (offline PWA, Architectural, own spec)**. W5 (per-run meshes) was considered and excluded (YAGNI; the run lock is a deliberate isolation/security decision).
