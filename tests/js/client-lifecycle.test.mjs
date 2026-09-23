/*
 * The dashboard's own survival behaviour, under jsdom.
 *
 * Everything here is a client-side response to a server event that used to be
 * invisible or wrong:
 *
 *   stream_gap        - the server dropped frames for this tab; the transcript on
 *                       screen is now known-stale, so re-pull it instead of letting
 *                       the user read a conversation with holes in it.
 *   session_released  - the mesh behind this tab was reaped. The tab used to
 *                       reconnect and land in a *fresh, empty* session while still
 *                       showing the old messages: same URL, different everything.
 *                       It now says so and stops reconnecting.
 *   run 409           - a second run in the same browser session is refused. The
 *                       refusal has to reach the user, not the console.
 *   truncated_count   - a reply was cut at the message limit; the exported
 *                       transcript is missing its tail and the user should know.
 *
 * Run: npm install && node --test tests/js/*.test.mjs
 */
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import test, { afterEach } from "node:test";
import { JSDOM } from "jsdom";

const here = dirname(fileURLToPath(import.meta.url));
const repoRoot = resolve(here, "..", "..");
const staticDir = join(repoRoot, "machinelearningmachine", "server", "static");

const read = (p) => readFileSync(p, "utf8");

/**
 * Every window this file opens, closed after each test.
 *
 * Each window's timers are cancelled before it is closed.
 *
 * Two reasons. (1) Speed: app.js schedules a 12-second toast dismissal and a
 * multi-second WebSocket backoff, and node will not exit while a handle is open -
 * the suite took 17 s of wall clock for 1.5 s of work. (2) Correctness: a window
 * left running leaks its timers into the next test. `window.close()` alone did not
 * stop them under jsdom 24, so the registry below owns them explicitly, and the two
 * timing-sensitive tests *drive* time instead of waiting for it.
 */
const opened = [];
let windowsOpened = 0;
let windowsClosed = 0;

/** A fetch/WebSocket double that records every call the client makes. */
async function loadClient(responder) {
  // The page's own <script src> and <link> tags are removed: this test supplies
  // those libraries by eval'ing the same files, and leaving the tags in place makes
  // jsdom queue resource loads that delay the parse it fires DOMContentLoaded on.
  const html = read(join(staticDir, "index.html"))
    .replace(/<script[^>]*\ssrc=[^>]*><\/script>/gi, "")
    .replace(/<link[^>]*>/gi, "");

  const calls = { fetch: [], sockets: [] };
  const dom = new JSDOM(html, {
    url: "http://127.0.0.1:8000/",
    runScripts: "outside-only",
    // The client is installed before the parse so that the page's *own*
    // DOMContentLoaded runs its init exactly once. Dispatching the event by hand
    // after construction initialised every tab twice (two sockets, two toasts)
    // and made the assertions lie.
    beforeParse(win) {
      installStubs(win, calls, responder);
      // Deliberately *not* loading marked/DOMPurify here: markdown.js degrades to
      // escaping when they are missing (asserted in sanitize.test.mjs), and these
      // tests read textContent, so the vendor libraries would only add ~1.5s per
      // window of eval. What must be real is index.html and app.js.
      win.eval(read(join(staticDir, "markdown.js")));
      win.eval(read(join(staticDir, "app.js")));
    },
  });
  // Registration is what makes teardown possible; a window that is never pushed
  // here stays open with its toasts and backoff timers, and the suite quietly
  // becomes 13 seconds of waiting for a process that has nothing left to test.
  opened.push(dom);
  windowsOpened++;
  const win = dom.window;

  for (let i = 0; i < 12; i++) {
    await new Promise((r) => win.setTimeout(r, 0));
  }
  return { win, dom, calls, socket: () => calls.sockets[calls.sockets.length - 1] };
}

afterEach(() => {
  while (opened.length) {
    const win = opened.pop().window;
    try {
      win.__cancelAllTimers();
    } catch {
      /* already closed */
    }
    try {
      win.close();
    } catch {
      /* already closed */
    }
    windowsClosed++;
  }
});

/**
 * jsdom does not give us a way to un-schedule what app.js scheduled, so the
 * window's timer functions are wrapped: every pending callback is tracked by id,
 * can be run on demand (`__flushTimers`), and is cancelled on teardown
 * (`__cancelAllTimers`).
 */
function installTimers(win) {
  const realSetTimeout = win.setTimeout.bind(win);
  const realClearTimeout = win.clearTimeout.bind(win);
  const pending = new Map();
  let nextId = 1;

  win.setTimeout = (fn, ms = 0, ...args) => {
    const record = { fn, args, ms, kind: "timeout" };
    const id = nextId++;
    record.id = id;
    record.realId = realSetTimeout(() => {
      if (pending.delete(id) && typeof fn === "function") fn(...args);
    }, ms);
    pending.set(id, record);
    return id;
  };
  win.clearTimeout = (id) => {
    const record = pending.get(Number(id));
    if (record) {
      pending.delete(record.id);
      realClearTimeout(record.realId);
    }
  };
  // app.js arms one interval - the Chrome speech-synthesis nudge - and only when
  // `speechSynthesis` exists, which jsdom does not implement. So no interval is
  // ever actually scheduled here; registering them anyway means that if a future
  // client feature starts using one, teardown still cancels it instead of leaking a
  // repeating timer into the next test (and into the exit of the process).
  win.setInterval = (fn, ms = 0, ...args) => {
    const id = nextId++;
    pending.set(id, { fn, args, ms, kind: "interval" });
    return id;
  };
  win.clearInterval = (id) => pending.delete(Number(id));

  // jsdom can animate, but only by running a real 16 ms requestAnimationFrame
  // loop per window - one repeating timer per tab that survives the test and keeps
  // node alive at exit. The drawing is not under test here, so a rAF callback is
  // made an ordinary tracked timeout: it runs when the test advances the clock, and
  // is cancelled with everything else afterwards.
  // 16 ms, like a real frame: app.js's draw loop re-arms itself, and a 0 ms
  // requestAnimationFrame would turn that into a hot loop.
  win.requestAnimationFrame = (fn) => win.setTimeout(() => fn(Date.now()), 16);
  win.cancelAnimationFrame = (id) => win.clearTimeout(id);

  // Run every callback scheduled no further out than `horizonMs`, as if the clock
  // had advanced. Returns how many ran, so a test can assert it actually did.
  win.__flushTimers = (horizonMs = Infinity) => {
    const due = [...pending.values()].filter((record) => record.ms <= horizonMs);
    for (const record of due) {
      if (record.kind === "interval") continue;  // intervals stay armed
      pending.delete(record.id);
      realClearTimeout(record.realId);
      if (typeof record.fn === "function") record.fn(...record.args);
    }
    return due.filter((record) => record.kind === "timeout").length;
  };
  win.__pendingTimers = () => pending.size;
  win.__cancelAllTimers = () => {
    for (const record of pending.values()) {
      if (record.kind === "timeout") realClearTimeout(record.realId);
    }
    pending.clear();
  };
}

/** Everything app.js touches that jsdom does not provide. */
function installStubs(win, calls, responder) {
  installTimers(win);
  win.fetch = async (url, options) => {
    const record = { url: String(url), options: options || {} };
    calls.fetch.push(record);
    const reply = responder(url, options || {});
    return {
      ok: reply.status >= 200 && reply.status < 300,
      status: reply.status,
      json: async () => reply.body,
      text: async () => (typeof reply.body === "string" ? reply.body : JSON.stringify(reply.body)),
      headers: new win.Headers({ "Content-Type": "application/json" }),
    };
  };

  class FakeSocket {
    constructor(url) {
      this.url = url;
      this.sent = [];
      this.readyState = 1;
      calls.sockets.push(this);
      // Deliver asynchronously: the client attaches its handlers right after the
      // constructor returns, exactly as a browser hands over to onopen later.
      // Scheduled on the window so teardown can cancel it like everything else.
      win.setTimeout(() => this.onopen && this.onopen({}), 0);
    }

    send(data) {
      this.sent.push(data);
    }

    close() {
      this.readyState = 3;
      this.onclose && this.onclose({ code: 1000 });
    }

    emit(payload) {
      this.onmessage && this.onmessage({ data: JSON.stringify(payload) });
    }
  }
  win.WebSocket = FakeSocket;

  // Canvas animation is not what these tests are about; jsdom has no 2D context.
  // A permissive stub keeps the drawing code from throwing on whichever call it
  // makes next - the tests below assert on the DOM, never on the canvas.
  const ctxState = {};
  const ctxStub = new Proxy(ctxState, {
    get(target, prop) {
      if (prop === "canvas") return { width: 300, height: 150 };
      if (prop === "measureText") return () => ({ width: 10 });
      if (prop === "createLinearGradient" || prop === "createRadialGradient") {
        return () => ({ addColorStop() {} });
      }
      if (prop === "getImageData") return () => ({ data: new Uint8ClampedArray(4) });
      if (prop in target) return target[prop];
      return () => undefined;
    },
    set(target, prop, value) {
      target[prop] = value;
      return true;
    },
  });
  win.HTMLCanvasElement.prototype.getContext = () => ctxStub;

  // Load the client the way the page does: vendor libraries, then app.js, then the
  // event app.js waits for.
}

const okResponder = () => ({ status: 200, body: {} });

const body = (win, id) => win.document.getElementById(id)?.textContent || "";

test("one socket per tab, and the roster arrives on it", async () => {
  const { win, calls, socket } = await loadClient(() => ({ status: 200, body: [] }));
  assert.equal(calls.sockets.length, 1, "a tab opens exactly one socket (boot must not double-initialise)");
  assert.match(socket().url, /^ws:\/\/127\.0\.0\.1:8000\/ws$/, "the socket is same-origin, not a configured URL");

  // The roster comes from the init frame, so a fresh tab is populated without a
  // second round trip - and without trusting anything but this session's state.
  socket().emit({
    type: "init",
    authenticated: true,
    agents: [
      { agent_id: "copilot", name: "GitHub Copilot", role: "Code", provider: "MockLLMProvider", provider_kind: "simulated", color: "#06b6d4", avatar: "🐙", system_prompt: "x" },
      { agent_id: "claude", name: "Claude Fable 5.1", role: "Critique", provider: "MockLLMProvider", provider_kind: "simulated", color: "#d97706", avatar: "🔮", system_prompt: "y" },
    ],
    history: [],
    limits: { max_messages_client: 500 },
    flags: { url_reader_enabled: false, run_timeout_seconds: 180 },
  });
  for (let i = 0; i < 6; i++) await new Promise((r) => win.setTimeout(r, 0));

  assert.match(body(win, "agentCountBadge"), /2 Modules/);
  assert.match(body(win, "moduleList"), /GitHub Copilot/);
  assert.match(body(win, "moduleList"), /Claude Fable 5\.1/);
  assert.ok(!calls.fetch.some((c) => String(c.url).startsWith("/api/agents")),
    "the boot path must not need a second fetch for what init already carried");
});

test("a dropped frame makes the tab re-pull the transcript instead of trusting itself", async () => {
  const seen = [];
  const { win, calls, socket } = await loadClient((url) => {
    seen.push(String(url));
    if (String(url).includes("/api/history")) {
      return {
        status: 200,
        body: [
          {
            id: "m1", sender_id: "copilot", sender_name: "GitHub Copilot", recipient_id: "*",
            topic: "general", message_type: "proposal", content: "the full conversation, refetched",
            artifacts: {}, metadata: { simulated: true }, timestamp: 1700000000,
          },
        ],
      };
    }
    return okResponder();
  });

  const before = calls.fetch.length;
  socket().emit({ type: "stream_gap", dropped: 12 });
  for (let i = 0; i < 8; i++) await new Promise((r) => win.setTimeout(r, 0));

  const refetch = calls.fetch.slice(before).find((c) => String(c.url).startsWith("/api/history"));
  assert.ok(refetch, "a gap notice must trigger a history refetch");
  assert.match(body(win, "messagesContainer"), /the full conversation, refetched/,
    "the refetched transcript reaches the screen");
  const toasts = [...win.document.querySelectorAll("#toastContainer .toast")].map((t) => t.textContent);
  assert.ok(toasts.some((t) => /skipped|not keeping up/i.test(t) && /12/.test(t)),
    `the user is told frames were dropped: ${JSON.stringify(toasts)}`);
});

test("a released session stops reconnecting and says why", async () => {
  const { win, calls, socket } = await loadClient(() => ({ status: 200, body: [] }));
  const socketsAtStart = calls.sockets.length;

  socket().emit({
    type: "session_released",
    reason: "idle_timeout",
    detail: "This browser session was released after 6 hours idle. Reload the page to start a new one.",
  });
  for (let i = 0; i < 4; i++) await new Promise((r) => win.setTimeout(r, 0));
  socket().close();
  for (let i = 0; i < 6; i++) await new Promise((r) => win.setTimeout(r, 0));
  // Stronger than "no reconnect yet": run every pending timer, including an
  // hour's worth of backoff, and still nothing may open a new socket.
  win.__flushTimers(3_600_000);

  assert.equal(calls.sockets.length, socketsAtStart,
    "reconnecting would silently hand the tab a different, empty session");
  assert.match(body(win, "connectionStatus"), /Session released/i);
  const toasts = [...win.document.querySelectorAll("#toastContainer .toast")].map((t) => t.textContent);
  assert.ok(toasts.some((t) => /released after 6 hours idle/i.test(t)),
    `the server's explanation is shown verbatim: ${JSON.stringify(toasts)}`);
});

test("an ordinary disconnect still reconnects - the released case is the exception", async () => {
  const { win, calls, socket } = await loadClient(() => ({ status: 200, body: [] }));
  socket().close();
  // app.js backs off (1.5s, 2.25s, 3.4s…), so waiting would take seconds of real
  // time per attempt. Advancing the window's clock proves the retry is scheduled
  // *and* fires, without the suite depending on how fast the machine is.
  const fired = win.__flushTimers(60_000);
  assert.ok(fired > 0, "the reconnect was scheduled");
  assert.ok(calls.sockets.length > 1, "a dropped socket must be retried; only a released session stops");
});

test("a refused concurrent run is reported to the user, not the console", async () => {
  const { win, calls } = await loadClient((url, options) => {
    if (String(url).includes("/api/run")) {
      return {
        status: 409,
        body: { detail: "A run is already in progress in this browser session (run-3). Wait for it to finish." },
      };
    }
    return okResponder();
  });

  win.document.getElementById("inputPrompt").value = "Design a token bucket rate limiter";
  win.document.getElementById("btnRun").click();
  for (let i = 0; i < 10; i++) await new Promise((r) => win.setTimeout(r, 0));

  const runCall = calls.fetch.find((c) => String(c.url).includes("/api/run"));
  assert.ok(runCall, "clicking Execute posts a run");
  assert.equal(runCall.options.method, "POST");
  const toasts = [...win.document.querySelectorAll("#toastContainer .toast")].map((t) => t.textContent);
  assert.ok(toasts.some((t) => /already in progress/i.test(t)),
    `the refusal reason is surfaced: ${JSON.stringify(toasts)}`);
  const btn = win.document.getElementById("btnRun");
  assert.equal(btn.disabled, false, "a refused run must not leave the button stuck busy");
});

test("a trimmed reply is announced when the run completes", async () => {
  const { win, socket } = await loadClient(() => ({ status: 200, body: [] }));
  socket().emit({ type: "run_completed", run_id: "run-1", truncated_count: 2, simulated_count: 0 });
  for (let i = 0; i < 6; i++) await new Promise((r) => win.setTimeout(r, 0));

  const toasts = [...win.document.querySelectorAll("#toastContainer .toast")].map((t) => t.textContent);
  assert.ok(toasts.some((t) => /2 replies were longer than the 50,000-character message limit/i.test(t)),
    `a shortened transcript is labelled as one: ${JSON.stringify(toasts)}`);
});

test("an injected toast message is rendered as text, never as markup", async () => {
  const { win, socket } = await loadClient(() => ({ status: 200, body: [] }));
  socket().emit({
    type: "session_released",
    reason: "capacity",
    detail: '<img src=x onerror="window.__pwned = 1">released',
  });
  for (let i = 0; i < 4; i++) await new Promise((r) => win.setTimeout(r, 0));

  assert.equal(win.__pwned, undefined, "a server-supplied detail string cannot execute");
  const toasts = [...win.document.querySelectorAll("#toastContainer .toast")].map((t) => t.textContent);
  assert.ok(toasts.some((t) => t.includes('<img src=x')), "it is shown as literal text instead");
  assert.equal(win.document.querySelectorAll("#toastContainer img").length, 0);
});

test("a clamped reply is badged where it hangs, not only in the toast", async () => {
  const { win, socket } = await loadClient(() => ({ status: 200, body: [] }));
  socket().emit({ type: "init", authenticated: true, agents: [], history: [], limits: {}, flags: {} });
  socket().emit({
    type: "new_message",
    run_id: "run-1",
    message: {
      id: "m1", sender_id: "gpt", sender_name: "GPT-6 Astra", recipient_id: "*", topic: "general",
      message_type: "answer",
      content: "here is the implementation\n\n> ✂️ **Truncated:** 20000 of 70000 characters dropped.",
      artifacts: {}, metadata: { simulated: true, content_truncated: 20000 }, timestamp: 1700000000,
    },
  });
  for (let i = 0; i < 8; i++) await new Promise((r) => win.setTimeout(r, 0));

  const badges = [...win.document.querySelectorAll("#messagesContainer .prov-clipped")];
  assert.equal(badges.length, 1, "the clipped message carries exactly one badge");
  assert.match(badges[0].textContent, /clipped/i);
  assert.match(badges[0].title, /20000 characters/, "and says how much is missing");
});

test("a degraded reply's badge says how long the retries waited", async () => {
  /*
   * D28's browser half. The server now records what a retry cost in seconds
   * (`metadata.provider_waited`, and the same number inside `provider_error`).
   * A number only in metadata is the D16 mistake - written by the agent,
   * rendered by nobody - so this asserts it reaches the badge the user hovers.
   */
  const { win, socket } = await loadClient(() => ({ status: 200, body: [] }));
  socket().emit({ type: "init", authenticated: true, agents: [], history: [], limits: {}, flags: {} });
  socket().emit({
    type: "new_message",
    run_id: "run-1",
    message: {
      id: "m1", sender_id: "gpt", sender_name: "GPT-6 Astra", recipient_id: "*", topic: "general",
      message_type: "answer",
      content: "> \u26a0\ufe0f **OpenAI failed** (rate limited). The reply below is the simulator talking.",
      artifacts: {},
      metadata: {
        simulated: true,
        provider: "OpenAI -> simulator",
        provider_error: "the API answered HTTP 429 (after 3 attempts, 4s spent waiting)",
        provider_status_code: 429,
        provider_attempts: 3,
        provider_waited: 4,
      },
      timestamp: 1700000000,
    },
  });
  for (let i = 0; i < 8; i++) await new Promise((r) => win.setTimeout(r, 0));

  const badges = [...win.document.querySelectorAll("#messagesContainer .prov-degraded")];
  assert.equal(badges.length, 1, "the degraded message carries exactly one badge");
  assert.match(badges[0].title, /after 3 attempts/, "the badge names the effort");
  assert.match(badges[0].title, /4s spent waiting/, "and the time it cost, not only the count");
});

test("Stop targets the active run and is disabled while idle", async () => {
  const { win, calls, socket } = await loadClient(okResponder);
  const stop = win.document.getElementById("btnStop");
  assert.ok(stop, "the dashboard offers Stop");
  assert.equal(stop.disabled, true);
  socket().emit({ type: "run_started", run_id: "run-18", topology: "pipeline" });
  assert.equal(stop.disabled, false);
  stop.click();
  for (let i = 0; i < 6; i++) await new Promise(r => win.setTimeout(r, 0));
  assert.ok(calls.fetch.some(c => c.url === "/api/runs/run-18/cancel" && c.options.method === "POST"));
  socket().emit({ type: "run_cancelled", run_id: "run-18" });
  assert.equal(stop.disabled, true);
  assert.equal(win.document.getElementById("btnRun").disabled, false);
  assert.match(body(win, "toastContainer"), /cancelled/i);
});

test("reconnecting during a run restores Stop from the snapshot", async () => {
  const { win, socket } = await loadClient(okResponder);
  socket().emit({ type: "init", agents: [], history: [], active_run_id: "run-19", cancel_requested: false });
  assert.equal(win.document.getElementById("btnStop").disabled, false);
  assert.equal(win.document.getElementById("btnRun").disabled, true);
  socket().emit({ type: "init", agents: [], history: [], active_run_id: "run-19", cancel_requested: true });
  assert.equal(win.document.getElementById("btnStop").disabled, true);
});

test("reconnecting after a missed completion clears stale busy state", async () => {
  const { win, socket } = await loadClient(okResponder);
  socket().emit({ type: "run_started", run_id: "run-19", topology: "pipeline" });
  socket().emit({ type: "init", agents: [], history: [], active_run_id: null, cancel_requested: false });
  assert.equal(win.document.getElementById("btnStop").disabled, true);
  assert.equal(win.document.getElementById("btnRun").disabled, false);
});

test("a queued run shows its position and Stop targets the queued run", async () => {
  const { win, calls, socket } = await loadClient((url) => {
    if (String(url).includes("/api/run")) {
      return {
        status: 202,
        body: {
          status: "queued",
          run_id: "run-7",
          queue_position: 2,
          max_queued: 5,
          detail: "Queued at position 2 behind the active run (run-6).",
        },
      };
    }
    return okResponder();
  });

  win.document.getElementById("inputPrompt").value = "Design a token bucket rate limiter";
  win.document.getElementById("btnRun").click();
  for (let i = 0; i < 10; i++) await new Promise((r) => win.setTimeout(r, 0));

  const toasts = [...win.document.querySelectorAll("#toastContainer .toast")].map((t) => t.textContent);
  assert.ok(toasts.some((t) => /queued/i.test(t) && /position 2/.test(t) && /run-7/.test(t)),
    `the queue position is user-visible: ${JSON.stringify(toasts)}`);
  assert.equal(win.document.getElementById("btnRun").disabled, true,
    "a queued run keeps the button busy until its own completion frame");
  const stop = win.document.getElementById("btnStop");
  assert.equal(stop.disabled, false, "Stop is offered for the queued run");
  stop.click();
  for (let i = 0; i < 6; i++) await new Promise((r) => win.setTimeout(r, 0));
  assert.ok(calls.fetch.some((c) => c.url === "/api/runs/run-7/cancel" && c.options.method === "POST"),
    "Stop targets the queued run, not the active one");

  // The queued run starts (its own run_started) and then completes: only its own
  // frames release this tab, not another tab's.
  socket().emit({ type: "run_completed", run_id: "run-6" });
  for (let i = 0; i < 4; i++) await new Promise((r) => win.setTimeout(r, 0));
  assert.equal(win.document.getElementById("btnRun").disabled, true,
    "another tab's completion must not release this tab's queued run");
  socket().emit({ type: "run_started", run_id: "run-7", topology: "pipeline" });
  socket().emit({ type: "run_completed", run_id: "run-7", simulated_count: 0, truncated_count: 0 });
  for (let i = 0; i < 6; i++) await new Promise((r) => win.setTimeout(r, 0));
  assert.equal(win.document.getElementById("btnRun").disabled, false);
  assert.equal(win.document.getElementById("btnStop").disabled, true);
});

test("a full queue is reported as retryable, not as a failure", async () => {
  const { win } = await loadClient((url) => {
    if (String(url).includes("/api/run")) {
      return { status: 429, body: { detail: "Run queue is full (5 waiting, max 5). Wait for a run to finish and try again." } };
    }
    return okResponder();
  });
  win.document.getElementById("inputPrompt").value = "Design a token bucket rate limiter";
  win.document.getElementById("btnRun").click();
  for (let i = 0; i < 10; i++) await new Promise((r) => win.setTimeout(r, 0));
  const toasts = [...win.document.querySelectorAll("#toastContainer .toast")].map((t) => t.textContent);
  assert.ok(toasts.some((t) => /queue is full/i.test(t)),
    `the queue-full reason is surfaced: ${JSON.stringify(toasts)}`);
  assert.equal(win.document.getElementById("btnRun").disabled, false,
    "a refused queue-full run must not leave the button stuck busy");
});

test("a tab that missed run_started learns the active run from run_queued", async () => {
  // D22: run_started can be the frame a gap eats. The tab then goes busy on
  // run_queued with no id to attribute the later completion to - unless the
  // frame names the run it waits behind.
  const { win, calls, socket } = await loadClient(okResponder);
  socket().emit({
    type: "run_queued", run_id: "run-2", queue_position: 1, queue_depth: 1,
    topology: "pipeline", active_run_id: "run-1",
  });
  assert.equal(win.document.getElementById("btnRun").disabled, true);
  const stop = win.document.getElementById("btnStop");
  assert.equal(stop.disabled, false, "Stop targets the adopted active run");
  stop.click();
  for (let i = 0; i < 6; i++) await new Promise((r) => win.setTimeout(r, 0));
  assert.ok(calls.fetch.some((c) => c.url === "/api/runs/run-1/cancel" && c.options.method === "POST"),
    "the adopted id - not the queued one - is what Stop offers");
  // The queued run is cancelled before starting; then the active run finishes.
  // Neither may wedge this tab busy.
  socket().emit({ type: "run_cancelled", run_id: "run-2", queued: true });
  socket().emit({ type: "run_completed", run_id: "run-1", simulated_count: 0, truncated_count: 0 });
  for (let i = 0; i < 6; i++) await new Promise((r) => win.setTimeout(r, 0));
  assert.equal(win.document.getElementById("btnRun").disabled, false);
  assert.equal(win.document.getElementById("btnStop").disabled, true);
});

test("a run that starts before its 202 arrives is adopted as active, not queued", async () => {
  // D23: HTTP and the socket are separate connections - run_started can beat the
  // 202. Adopting the stale queued identity would route the later completion to
  // the wrong branch and wedge the tab busy.
  let resolveRun;
  const runBody = new Promise((r) => { resolveRun = r; });
  const { win, socket } = await loadClient((url) => {
    if (String(url).includes("/api/run")) return { status: 202, body: runBody };
    return okResponder();
  });
  win.document.getElementById("inputPrompt").value = "Design a token bucket rate limiter";
  win.document.getElementById("btnRun").click();
  for (let i = 0; i < 6; i++) await new Promise((r) => win.setTimeout(r, 0));
  socket().emit({ type: "run_started", run_id: "run-7", topology: "pipeline" });
  resolveRun({ status: "queued", run_id: "run-7", queue_position: 1, max_queued: 5 });
  for (let i = 0; i < 10; i++) await new Promise((r) => win.setTimeout(r, 0));

  assert.equal(win.document.getElementById("btnRun").disabled, true,
    "still busy: the run is active, its 202 just arrived late");
  const toasts = [...win.document.querySelectorAll("#toastContainer .toast")].map((t) => t.textContent);
  assert.ok(!toasts.some((t) => /queued at position/i.test(t)),
    `no stale queue position may be shown for a running run: ${JSON.stringify(toasts)}`);
  socket().emit({ type: "run_completed", run_id: "run-7", simulated_count: 0, truncated_count: 0 });
  for (let i = 0; i < 6; i++) await new Promise((r) => win.setTimeout(r, 0));
  assert.equal(win.document.getElementById("btnRun").disabled, false);
  assert.equal(win.document.getElementById("btnStop").disabled, true);
});

test("a run cancelled before its 202 arrives releases the tab instead of wedging it", async () => {
  // D23: another tab can cancel this tab's queued run inside the HTTP window.
  // The completion frame finds no queued identity yet and stays silent - so the
  // late 202 must reconcile instead of adopting a dead run.
  let resolveRun;
  const runBody = new Promise((r) => { resolveRun = r; });
  const { win, socket } = await loadClient((url) => {
    if (String(url).includes("/api/run")) return { status: 202, body: runBody };
    return okResponder();
  });
  win.document.getElementById("inputPrompt").value = "Design a token bucket rate limiter";
  win.document.getElementById("btnRun").click();
  for (let i = 0; i < 6; i++) await new Promise((r) => win.setTimeout(r, 0));
  socket().emit({ type: "run_cancelled", run_id: "run-7", queued: true });
  resolveRun({ status: "queued", run_id: "run-7", queue_position: 1, max_queued: 5 });
  for (let i = 0; i < 10; i++) await new Promise((r) => win.setTimeout(r, 0));

  assert.equal(win.document.getElementById("btnRun").disabled, false,
    "a 202 for an already-cancelled run must not wedge the button busy");
  assert.equal(win.document.getElementById("btnStop").disabled, true);
  const toasts = [...win.document.querySelectorAll("#toastContainer .toast")].map((t) => t.textContent);
  assert.ok(toasts.some((t) => /cancelled/i.test(t)),
    `the tab is told its run was cancelled: ${JSON.stringify(toasts)}`);
});

test("a terminal record from an earlier session must not reconcile a new 202", async () => {
  // D25: run ids restart at run-1 in every session, but the terminal record
  // outlives the session: after a release without reload, a 202 can collide with
  // a dead run's record. Only frames inside this request's window may reconcile.
  const { win, calls, socket } = await loadClient((url) => {
    if (String(url).includes("/api/run")) {
      return { status: 202, body: { status: "queued", run_id: "run-2", queue_position: 1, max_queued: 5 } };
    }
    return okResponder();
  });
  let now = 1000;
  win.Date.now = () => now;
  // An earlier session ran run-2 to completion, then died (released, no reload).
  socket().emit({ type: "run_started", run_id: "run-2", topology: "pipeline" });
  socket().emit({ type: "run_completed", run_id: "run-2", simulated_count: 0, truncated_count: 0 });
  for (let i = 0; i < 6; i++) await new Promise((r) => win.setTimeout(r, 0));
  assert.equal(win.document.getElementById("btnRun").disabled, false);
  // Much later, a new session queues a run that reuses the id.
  now = 2000;
  win.document.getElementById("inputPrompt").value = "Design a token bucket rate limiter";
  win.document.getElementById("btnRun").click();
  for (let i = 0; i < 10; i++) await new Promise((r) => win.setTimeout(r, 0));

  assert.equal(win.document.getElementById("btnRun").disabled, true,
    "a stale record from a dead session must not release a genuinely queued run");
  const toasts = [...win.document.querySelectorAll("#toastContainer .toast")].map((t) => t.textContent);
  assert.ok(toasts.some((t) => /queued at position 1/i.test(t)),
    `the live run takes the queued identity: ${JSON.stringify(toasts)}`);
  const stop = win.document.getElementById("btnStop");
  assert.equal(stop.disabled, false);
  stop.click();
  for (let i = 0; i < 6; i++) await new Promise((r) => win.setTimeout(r, 0));
  assert.ok(calls.fetch.some((c) => c.url === "/api/runs/run-2/cancel" && c.options.method === "POST"),
    "Stop targets the live queued run");
});

/*
 * A guard on the harness itself, not on the client.
 *
 * `loadClient` registers every window it opens so that `afterEach` can cancel its
 * timers and close it. When that registration was once dropped, all eight tests
 * still passed - and the file quietly went from 2 seconds to 13, because a live
 * jsdom window keeps its 12-second toast timers pending and node waits for them at
 * exit. Failing fast here keeps "the suite is slow" from being the only symptom.
 */
test("no window outlives its test", () => {
  assert.ok(windowsOpened > 1, "this file is supposed to exercise several tabs");
  assert.equal(windowsClosed, windowsOpened,
    "an unclosed jsdom window keeps its timers alive after the last assertion");
});

test("the Saved Sessions panel actually renders the sessions the server lists", async () => {
  /*
   * This was *broken in production* while every test passed. The render built each
   * row's delete button and then ran
   *
   *     delBtn.querySelector("i").insertAdjacentElement("afterend", document.createTextNode(" "))
   *
   * insertAdjacentElement's second argument must be an Element, and a text node is
   * not one - so it threw a TypeError in every browser, the row was never appended,
   * and the panel showed "Could not list saved sessions" instead of the user's saved
   * conversations. The throw happened inside the render's own try/catch, which is
   * why nothing surfaced anywhere.
   *
   * Nothing covered the *rendering* of a session row: the API was tested, the
   * offline behaviour was tested, the row-building was not.
   */
  const { win } = await loadClient((url) => {
    if (String(url).includes("/api/sessions")) {
      return {
        status: 200,
        body: {
          sessions: [
            { id: "20260923-101010.000000-ab12cd", name: "earlier chat", saved_at: 1790000000, message_count: 4 },
            { id: "20260923-111111.000000-cd34ef", name: "long one", saved_at: 1790000600, message_count: 12, trimmed: true },
          ],
        },
      };
    }
    return okResponder();
  });

  win.document.getElementById("btnSessions").click();
  for (let i = 0; i < 10; i++) await new Promise((r) => win.setTimeout(r, 0));

  const rows = [...win.document.querySelectorAll("#sessionsList .session-row")];
  assert.equal(rows.length, 2, "one row per saved session");
  assert.match(rows[0].textContent, /earlier chat/);
  assert.match(rows[0].textContent, /4 messages/);
  assert.match(rows[1].textContent, /12 messages/);
  assert.match(rows[1].textContent, /oldest dropped to fit the size limit/i, "a trimmed transcript says so");
  // Each row offers the two things a saved session needs: load it, or delete it.
  assert.equal(rows[0].querySelectorAll("button").length, 2);
  assert.ok(rows[0].querySelector(".session-load"), "a load button");
  assert.ok(rows[0].querySelector(".session-del"), "a delete button");
});
