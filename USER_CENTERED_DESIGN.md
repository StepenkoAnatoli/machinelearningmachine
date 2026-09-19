# User-Centered Engineering & Design Decisions

This document explains the engineering and design decisions made to maximize real user value in MachineLearningMachine.

## Core Principle

> **Maximize real user value, not merely technical correctness or implementation speed.**
> Treat every solution as both an engineering and design problem.

We audited the system from a real user's perspective: a developer who wants to quickly prototype multi-agent workflows, see realistic code examples, and understand how agents collaborate — without wrestling with setup, confusing errors, or insecure defaults.

---

## 🔍 Audit Findings: What Was Hurting Users?

### 1. Security Issues (Trust Erosion)
- **CORS misconfiguration**: `allow_origins=["*"]` + `allow_credentials=True` is rejected by browsers — causes silent failures
- **XSS via markdown**: `marked.parse` allows raw HTML, LLM output could inject scripts
- **No input validation**: Custom agent IDs could be `../../../etc/passwd`, prompts could be 1MB causing DoS
- **Unbounded history**: Message bus grows forever → memory leak, crashes long sessions

**User impact**: Users lose trust when things break silently or feel insecure.

### 2. Usability Issues (Friction & Frustration)
- **alert()/confirm()**: Jarring, blocks UI, not accessible, can't be styled, no screen reader support
- **No feedback**: Button says "Execute" but no progress, user clicks twice → double execution
- **Small prompt box**: 2 rows for complex tasks, no character count, no hint about limits
- **No search**: 100 messages → impossible to find relevant one
- **Canvas always animating**: Wastes battery, 60fps even when idle, doesn't pause when tab hidden
- **Hardcoded 0.4s sleeps**: Makes every dialogue feel sluggish (4 turns = 1.6s wasted)

**User impact**: Users feel the tool is slow, unpolished, and doesn't respect their time or device.

### 3. Accessibility Issues (Exclusion)
- **No keyboard navigation**: Can't tab through modules, modals don't trap focus
- **No ARIA**: Screen readers can't understand topology graph or message feed
- **No ESC to close**: Modals require clicking X, frustrating for keyboard users
- **No skip link**: Keyboard users must tab through entire header

**User impact**: Excludes users with disabilities, violates WCAG.

### 4. Engineering Issues (Reliability)
- **Global shared mesh**: All users share same history → confusing in multi-user scenarios
- **Mock provider too generic**: Same code for "cache" vs "auth" → not useful, user can't copy-paste
- **CLI poor errors**: Stack trace for "agent not found" instead of "Available: arena-ai, copilot..."
- **No resource limits**: 20+ custom agents, no max prompt length

**User impact**: Users get confused, can't get value from mock mode, hit invisible limits.

---

## ✅ Solutions Implemented

### A. Security & Reliability (Must-Fix for Trust)

**1. Fixed CORS**
```python
# Before: insecure and broken
allow_origins=["*"], allow_credentials=True

# Then: a wildcard with credentials is a cross-site read of a local service.
# Now: origins are operator-configured (--allow-origin, or the
# MACHINELEARNINGMACHINE_ALLOW_ORIGINS env var for containers),
# the default allows none beyond same-origin, and mutating requests with a
# non-JSON body are refused so a cross-origin form cannot write state.
allow_origins=normalize_origins(config.allow_origins), allow_credentials=False
```
- **Why**: Browsers reject wildcard + credentials, and `*` on a loopback service is
  an invitation for any page in the user's browser to talk to it.
- **User value**: No mysterious CORS errors, and no cross-origin page can write to a
  service that holds the user's provider keys.

**2. XSS Protection**
```javascript
// Before: direct marked.parse → XSS possible
parsedContent = marked.parse(msg.content)

// Intermediate (insufficient): regex "sanitizing" - bypassed by unquoted
// handlers, <svg>/<math>/<template>/<noscript> subtrees and parser quirks.
// After: mark + DOMPurify allowlist, rendered as DOM nodes (static/markdown.js)
const clean = DOMPurify.sanitize(marked.parse(text), { ...ALLOWLIST, RETURN_DOM: true });
container.replaceChildren(...clean.body.childNodes);
```
- **Why**: LLM output, saved-session JSON and custom-agent fields are all untrusted
  input. A denylist of regexes cannot express "only these tags, only these
  attributes"; an allowlist plus a real sanitizer can.
- **User value**: pasting hostile markup into a transcript renders as text instead of
  executing, and there are no inline handlers left to review.

**3. Input Validation with Helpful Messages**
```python
@field_validator("agent_id")
def validate_agent_id(cls, v):
    if v in RESERVED_AGENT_IDS:
        raise ValueError(f"Agent ID '{v}' is reserved")
    if not AGENT_ID_PATTERN.match(v):
        raise ValueError("Agent ID must be lowercase alphanumeric with dashes, e.g. 'my-agent'")
```
- **Why**: Fail fast with clear guidance, not cryptic 500 error
- **User value**: Knows exactly what to fix, e.g. "must be lowercase" vs "validation error"

**4. Bounded History & Memory**
```python
MAX_HISTORY = 1000
MAX_MEMORY = 100  # per agent

# Prune oldest when exceeding
if len(self._history) > self._max_history:
    self._history = self._history[-self._max_history:]
```
- **Why**: Prevents memory leak, keeps app responsive
- **User value**: Long sessions don't crash, predictable performance.

**5. Rate Limiting**
```python
RUN_COOLDOWN_SECONDS = 1.0
if now - last < RUN_COOLDOWN_SECONDS:
    raise HTTPException(429, "Please wait 1s between runs")
```
- **Why**: Prevents accidental double-clicks from spamming
- **User value**: No duplicate executions, clear feedback.

### B. UX Improvements (Reduce Friction)

**1. Toast System Replaces alert()/confirm()**
```javascript
// Before: blocking, inaccessible
alert("Please enter a prompt")
if (confirm("Clear all?")) { ... }

// After: non-blocking, accessible, styled
showToast("Please enter a task prompt", "warning")
openModal(clearConfirmModal) // Custom modal with focus trap
```
- **Why**: alert() is deprecated UX, blocks JS thread, no screen reader support
- **User value**: Pleasant, non-jarring feedback, can continue working, accessible.

**2. Modal Accessibility**
```javascript
function trapFocus(modal) {
    // Trap Tab inside modal, return focus on close
    // ESC to close, click backdrop to close
}
```
- **Why**: WCAG requires focus management, keyboard users need ESC
- **User value**: Can use entire app via keyboard, screen readers announce modal.

**3. Character Count & Auto-Resize**
```javascript
function updateCharCount() {
    charCountEl.textContent = `${len} / 5000`
    if (len > 4500) charCountEl.classList.add("warning")
    inputPrompt.style.height = Math.min(scrollHeight, 200) + "px"
}
// Ctrl+Enter to run
inputPrompt.addEventListener("keydown", e => {
    if ((e.ctrlKey || e.metaKey) && e.key === "Enter") btnRun.click()
})
```
- **Why**: Users need to know limits before hitting error, want quick keyboard flow
- **User value**: No surprise "too long" error, faster workflow, prompt box grows with content.

**4. Search & Filter**
```javascript
searchInput.addEventListener("input", e => {
    searchFilter = e.target.value.trim()
    renderAllMessages() // Filters by content, sender, type
})
```
- **Why**: 100+ messages → needle in haystack
- **User value**: Can find "rate limiter" messages instantly, shows "3 / 50 messages".

**5. Performance Optimizations**
```javascript
// Before: 60fps always, even when idle
requestAnimationFrame(drawCanvas) // runs forever

// After: throttled to 30fps, pauses when tab hidden, only redraws when needed
const DRAW_THROTTLE = 1000/30
if (timestamp - lastDrawTime < DRAW_THROTTLE && !needsRedraw) return
document.addEventListener("visibilitychange", () => {
    if (document.hidden) cancelAnimationFrame(id)
})
```
- **Why**: Battery life, CPU usage, respects user's device
- **User value**: Laptop doesn't heat up, tab in background doesn't waste resources.

**6. Faster Execution**
```python
# Before: 0.4s * 4 turns = 1.6s wasted
await asyncio.sleep(0.4)

# After: 0.15s, configurable, --no-delay for tests
inter_turn_delay = 0.15
```
- **Why**: 0.4s is arbitrary, feels sluggish, slows tests
- **User value**: Feels snappy, tests run in 2.4s vs 7.7s.

### C. Mock Provider: Real User Value Without API Keys

**Before**: Generic code for all domains
```python
# Same for "cache" and "auth"
class ModulePayload: ...
class CoreServiceEngine: ...
```

**After**: Domain detection + tailored, copy-paste-ready code
```python
DOMAIN_PATTERNS = {
    "rate_limiter": ["rate limit", "token bucket"],
    "cache": ["cache", "ttl", "lru"],
    "auth": ["jwt", "oauth"],
}

def _detect_domain(prompt):
    for domain, keywords in patterns.items():
        if any(kw in prompt.lower() for kw in keywords):
            return domain

# Then generate specific code:
if domain == "rate_limiter":
    code = "class TokenBucketRateLimiter: ..." # thread-safe, with example
elif domain == "cache":
    code = "class TTLCache: ..." # OrderedDict, LRU, with example
```

**Why**: Most users try mock mode first. If it generates irrelevant code, they think tool is broken.

**User value**:
- Prompt "Build rate limiter" → gets actual `TokenBucketRateLimiter` with `allow()` method, thread-safe, docstring with usage example
- Prompt "JWT auth" → gets `JWTAuth` with `create_token()` / `verify_token()`
- Each includes pytest that actually tests the domain (thread safety for rate limiter, TTL expiry for cache)
- Can copy-paste and run immediately

### D. CLI Improvements

**Before**:
```bash
# No validation, stack trace on error
python -m machinelearningmachine.cli run --topology p2p --agent-a arena-ai --agent-b arena-ai --prompt "hi"
# → ValueError traceback
```

**After**:
```bash
# Friendly validation, helpful suggestions
❌ Error: Cannot start dialogue with same agent. Choose two different agents.

💡 Tip: Run with --help for usage examples

# More options
--agent-ids copilot,claude,gpt  # for pipeline/debate
--turns 2                       # configurable
--no-delay                      # faster for testing
--export markdown

# Progress indicators
============================================================
🤖 MachineLearningMachine - P2P Topology
============================================================
📝 Task: Build a rate limiter...
🔧 Agents: arena-ai <-> copilot
============================================================

💬 Initiating direct dialogue: arena-ai <---> copilot (4 turns)
✅ Dialogue completed - 4 messages
```

**User value**: Knows what went wrong and how to fix, sees progress, can customize.

---

## 📊 Impact Metrics

| Area | Before | After | User Benefit |
|------|--------|-------|--------------|
| **Security** | CORS broken, XSS possible, no validation, unauthenticated and bound to all interfaces, SSRF in the page reader | Real sanitizer + CSP, no CDN, per-session auth/isolation, SSRF-guarded reader (off by default), validated input | Trust that matches what the code actually enforces |
| **Performance** | 0.4s delay × 4 = 1.6s wasted, 60fps always | 0.15s delay, canvas redraws only when the mesh changes | Feels snappy, saves battery |
| **Accessibility** | No keyboard, no ARIA, alert() | Focus trap, ARIA, toast, skip link, ESC | Inclusive, keyboard usable |
| **Mock Quality** | Generic code for all prompts, claiming approval | Domain-specific draft, explicitly labelled simulated/unverified | Real value without API keys, without pretending to be verification |
| **Error Handling** | Stack traces | Friendly messages + suggestions | Knows how to fix |
| **UX Polish** | Small prompt box, no search, no feedback | Auto-resize, char count, search, toast | Feels professional |

---

## 🎯 Design Decisions & Trade-offs

### 1. Why not full session isolation?
**Considered**: Per-user mesh with session IDs, cookies
**Chosen**: Global mesh with clear documentation + bounded history
**Why**: Full isolation requires auth, DB, complexity. For demo/OSS tool, bounded global state is simpler and works for single-user. Added note in code: "Global mesh shared across requests - suitable for single-user/demo. For multi-user prod, add session management."

**User value**: Keeps tool simple to run (`pip install -e .` and go), no DB setup.

### 2. Why toast instead of more sophisticated notification system?
**Considered**: Using a library like notistack, or building queue with actions
**Chosen**: Simple custom toast with 4 types, auto-dismiss, accessible
**Why**: No extra dependencies, 50 lines of code, does 90% of what user needs. Avoids bundle bloat.

**User value**: Fast load, no extra JS, still gets clear feedback.

### 3. DOMPurify for XSS - the decision reversed (2026-09)
**Originally chosen**: regex stripping + marked with raw HTML disabled, because
"DOMPurify adds 10KB and needs an extra CDN".
**Reverted to**: DOMPurify 3.4.15 + Marked 12.0.2, **vendored** (no CDN) and
checksummed in `static/vendor/MANIFEST.json`.
**Why the original reasoning failed**:
- "no raw HTML" is not enough on its own once markup is re-parsed by a browser,
  and the regex layer gave a false sense of a boundary; a security review found
  trivial bypasses (`<svg onload=...>`, unquoted handlers, malformed tags).
- The 10KB argument is moot when nothing is fetched remotely anyway: the whole
  vendor bundle (Tailwind build, FontAwesome, marked, DOMPurify, highlight.js)
  is served from the app itself, so the page is offline-capable and CSP-clean.
**User value**: the transcript is the one place arbitrary text from models and
saved files lands, and it is now rendered through an audited sanitizer whose
bypass corpus is an executable test (`tests/js/sanitize.test.mjs`).

A note that came out of the vendoring work itself: the DOMPurify release first
pinned here (3.1.6) turned out to carry 20 open advisories, several of them
sanitizer bypasses. `npm audit --audit-level=high` now runs in CI next to
`pip-audit`, and the vendored files are rebuilt and hash-checked there, so a
pinned-but-vulnerable asset fails the build instead of shipping quietly.

### 4. Why keep mock provider instead of forcing API keys?
**Considered**: Remove mock, require OpenAI key for "real" experience
**Chosen**: Improve mock to be genuinely useful
**Why**: Many users won't have API keys, or want to try offline. Good mock with domain detection provides real value and teaches concepts.

**User value**: Zero-config works, can learn and prototype without spending money.

---

## 🧪 Validation

All 21 original tests still pass:
```
21 passed in 2.39s (was 7.71s - faster due to reduced delays)
```

Additional manual validation:
- Empty prompt → 422 with helpful message (not 500)
- Same agent P2P → 400 "Cannot start dialogue with same agent"
- Invalid agent ID → 422 "must be lowercase alphanumeric..."
- Search filters messages in real-time
- Toast appears for success/error, auto-dismisses
- Modal traps focus, ESC closes, returns focus
- Canvas pauses when tab hidden (checked via console)
- Rate limiter prompt → gets TokenBucket code, not generic
- JWT prompt → gets JWTAuth code

---

## 🚀 Round 2: Zero-Friction Install, Sessions, and Reading Aloud

The second user-centered pass focused on three things users repeatedly hit: **getting the app running without reading docs**, **not losing work**, and **hearing the output** (accessibility + convenience).

### One-Click Launch (no terminal required)
- **Problem**: The documented install was `git clone` → `python -m venv` → `pip install -e .` → `python -m ...`. Five steps, several of which fail confusingly (PEP 668, wrong interpreter, `ModuleNotFoundError: pydantic`) for non-developers.
- **Solution**: Three tiny launcher files at the repo root — `launch-windows.bat`, `launch-macos.command`, `launch-linux.sh`. **Double-click the one for your OS.** Each one:
  1. Detects an installed Python and gives a plain-English install hint if missing.
  2. Creates a private `.venv` the first time (isolated, no PEP 668 clashes).
  3. Installs dependencies once, marked by a `.deps_installed` flag so later launches are instant.
  4. Starts the dashboard on `127.0.0.1:8000` and auto-opens the browser.
- **Design choices**: everything happens inside the project folder (`.venv`) so nothing global is touched; failure paths always say what to do next; closing the window stops the app.

### Saved Sessions (work you can come back to)
- **Problem**: History lived only in memory — closing the browser lost the whole conversation.
- **Solution**: `sessions.py` + a *Sessions* panel in the dashboard. One click saves the current modules and full transcript as JSON in `~/.module_mesh/sessions` (user home, never in the repo). Load restores agents *and* messages; delete removes. Bounded (50 kept, oldest pruned), filename-safe, path-traversal-proof, corrupt-file tolerant.
- **Why home dir, not the repo**: sessions are personal, change constantly, and would otherwise pollute `git status`.

### Read-Aloud Studio (the machine reads what you write)
- **Problem**: Output is all visual; users want to *hear* their prompt, the replies, documents, or web pages — and keyboard/mic input is friendlier than typing for some tasks.
- **Solution** (100% local, zero extra dependencies):
  - **Web Speech API** TTS in the dashboard: read the prompt, any single message (per-message speaker button), the whole conversation, or with auto-read on, every reply as it arrives. Voice + speed pickers, test-voice button, settings persisted in `localStorage`. Long texts are chunked to dodge the Chrome long-utterance cutoff.
  - **Other reading skills**: paste any text, open a local file (txt/md/csv/json/log/code, ≤1 MB), or paste a web-page URL — the server fetches it (`/api/read/url`), strips scripts/styles/nav to readable text, shows it, and reads it aloud.
  - **Dictation**: 🎙️ button transcribes speech into the prompt (browser speech recognition, graceful hide when unsupported).
  - **CLI parity**: `--speak` reads a dialogue with the OS voice (`say` on macOS, SAPI on Windows, `espeak-ng` on Linux) — no install required where the OS ships a voice.

**User impact**: install is one double-click; conversations survive restarts; and the system can now *talk* as well as show.

---

## 🛡️ Round 3: the promises the code had stopped keeping

The audit standard used in rounds 1 and 2 - "does this make the user's job easier,
safer, more pleasant?" - was applied to the project's own claims. Seventeen findings
came back, and every one of them was a case of documentation, a test, or a user-facing
promise that had quietly detached from the code. The full record (reproductions,
reasoning, rejected options) is [PRODUCTION_HARDENING.md](PRODUCTION_HARDENING.md).

The ones users feel:

| What a user would have experienced | What actually happened | What it is now |
| --- | --- | --- |
| A run fails with a 500 after a provider answers with a long code block | `Message` rejects content past 50,000 characters, so the *answer* was the thing that broke the run - and the transcript was lost | Answers are clamped with an in-text "Truncated" notice, a `clipped` badge, a toast, and a count in the run result (`truncated_messages`). A 200,000-character answer now saves and reloads fine |
| Two tabs open, garbled transcript, agents replying to the wrong conversation | Concurrent runs shared one mesh and one history; both runs interleaved and both poisoned provider memory | One run per browser session. A second request is refused with a `409` that names the run in flight |
| "My session expires" in the docs, sessions that never expired | The idle TTL was only checked when a request arrived, so a quiet tab held a mesh (and its keys) until the process restarted or the LRU evicted it | A background sweeper expires sessions, closes their sockets with a `session_released` frame, and the client says "Session released - Reload" instead of silently landing in a new empty session |
| A phone tab on a bad network stalls everyone's run | The bus awaited each socket write: one unread TCP buffer blocked the producer, the run, and every other tab | Each connection gets a bounded outbox (128 frames, drop-oldest). A stalled tab loses frames, never the run; and when it does, it is told (`stream_gap`) and re-pulls the transcript |
| My dashboard run used the API key from my shell | `OpenAIProvider()` falls back to `OPENAI_API_KEY`, and `/api/config` built providers without a key - so an ambient key rode along on a session that never supplied one (and, with no key anywhere, the literal string `None` was sent as `Authorization`) | Dashboard providers are constructed with `allow_env_key=False`; a keyless session sends no auth header at all. The only opt-out of the simulator in the terminal is `run --live`, which prints endpoint, model and key source first |
| Typing a base URL of `http://169.254.169.254/` in Settings | `/api/read/url` had an SSRF policy; the provider base URL had none, and `{"verify": true}` was a status-code oracle | `netguard.validate_provider_target` guards it, with local backends still allowed on a loopback bind and an explicit `--allow-insecure-provider-urls` opt-in |
| My saved transcript disappeared, or a session list took 154 ms | Saves truncated the file before writing (a crash left an unreadable file, silently skipped by the listing) and listing re-parsed every transcript on disk | Atomic writes through a temp file, a metadata header with the message count, stale-temp pruning, and a "trimmed" flag when a transcript had to be shortened to fit |
| `--no-delay` did nothing for debate and hub | The CLI built those two topologies by hand and the mesh methods had no delay parameter | The delay is an argument on all four; measured 0.45 s → 0.00 s |
| A typo cost me a second of waiting | The run cooldown was stamped before validation, so a rejected 400 request still spent the session's rate-limit budget | Validation first, cooldown only for runs that actually start (and `Retry-After` on both refusals) |

**The user-facing principle that came out of this**: an error the user can act on is a
feature. Every refusal in this codebase now names the thing it is refusing (the run id,
the seconds to wait, the limit that was hit, the flag that would change it), and no
failure is reported as a bare 500.

### Validation (round 3)

```
$ pytest -q
365 passed in 21.9s
$ node --test tests/js/*.test.mjs
# pass 23
$ python scripts/e2e_server_check.py --base http://127.0.0.1:8799
ALL E2E CHECKS PASSED (33 checks, real uvicorn + real WebSocket, two browser sessions)
$ python scripts/bench_sessions.py               # 50 transcripts, 73.9 MB on disk
  list_sessions (header index) :    1.3 ms   -> 50 rows
  read + parse every file     :  213.4 ms
  ratio                           : 167.5x
```

---

## 🔮 Future User-Centered Improvements (Not Yet Done)

1. **Mid-run cancellation**: a run is bounded by `--run-timeout`, not by a stop button;
   and a second run is refused rather than queued. Queueing is the natural next step.
2. **Pagination / virtual scrolling**: for 1000+ messages the transcript array is capped
   and the feed says so, but there is no windowed scroll.
3. **Dark/light toggle**: currently dark only, some users prefer light.
4. **Copy prompt button**: quick duplicate of the last prompt.
5. **Agent presets**: "Security Auditor", "DB Expert" templates.
6. **Offline support**: service worker for PWA.
7. **Per-run meshes** if multi-run-per-session is ever wanted, instead of the run lock.

These are noted but not implemented to keep scope focused on highest user value fixes.
(An earlier version of this list proposed *session isolation* and *export
`Content-Disposition`* as future work; both shipped since, so they have been removed
rather than left as stale claims.)

---

## 💡 Key Takeaway

User-centered engineering is not about adding features — it's about removing friction, building trust, and respecting the user's time, device, and abilities.

Every change here answers: **"Does this make the user's job easier, safer, or more pleasant?"**

- Security fix → trust
- Toast vs alert → pleasant
- Search → practical
- Keyboard nav → inclusive
- Domain-specific mock → genuinely useful
- Faster delays → respects time
- Battery optimization → respects device

That's the bar we aimed for.
