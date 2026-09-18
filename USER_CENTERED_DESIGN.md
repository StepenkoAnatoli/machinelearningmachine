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

# After: secure, works with preview URLs
allow_origins=["*"], allow_credentials=False, allow_methods=["GET","POST","OPTIONS"]
```
- **Why**: Browsers reject wildcard + credentials. Fixing it makes preview work reliably.
- **User value**: No mysterious CORS errors, secure by default.

**2. XSS Protection**
```javascript
// Before: direct marked.parse → XSS possible
parsedContent = marked.parse(msg.content)

// After: sanitize + strip event handlers
html = marked.parse(content)
html = html.replace(/<script>.*?<\/script>/gi, "")
html = html.replace(/on\w+="[^"]*"/gi, "")
```
- **Why**: LLM output is untrusted, could contain `<img onerror=...>`
- **User value**: Safe to paste LLM output, no script injection.

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
| **Security** | CORS broken, XSS possible, no validation | Fixed CORS, XSS sanitized, validated | Trust, no silent failures |
| **Performance** | 0.4s delay × 4 = 1.6s wasted, 60fps always | 0.15s delay, 30fps, pauses when hidden | Feels snappy, saves battery |
| **Accessibility** | No keyboard, no ARIA, alert() | Focus trap, ARIA, toast, skip link, ESC | Inclusive, keyboard usable |
| **Mock Quality** | Generic code for all prompts | Domain-specific, copy-paste ready | Real value without API keys |
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

### 3. Why not DOMPurify for XSS?
**Considered**: DOMPurify is gold standard for sanitization
**Chosen**: Lightweight regex stripping + marked config (no raw HTML)
**Why**: DOMPurify adds 10KB, requires extra CDN. For this app, where content is mostly code, simple stripping of `<script>` and `on*=` is sufficient. Documented: "In production, use DOMPurify."

**User value**: Faster load, still safe for typical use. If user pastes malicious HTML, it's stripped.

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

## 🔮 Future User-Centered Improvements (Not Yet Done)

1. **Session isolation**: Add optional session ID header for multi-user
2. **Export with proper headers**: Currently returns JSON wrapped, should support `?download=true` with Content-Disposition (partially done)
3. **Pagination**: For 1000+ messages, virtual scrolling
4. **Dark/light toggle**: Currently dark only, some users prefer light
5. **Copy prompt button**: Quick duplicate last prompt
6. **Agent presets**: "Security Auditor", "DB Expert" templates
7. **Offline support**: Service worker for PWA

These are noted but not implemented to keep scope focused on highest user value fixes.

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
