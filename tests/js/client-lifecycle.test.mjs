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
 * Run: npm install && node --test tests/js/
 */
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";
import { JSDOM } from "jsdom";

const here = dirname(fileURLToPath(import.meta.url));
const repoRoot = resolve(here, "..", "..");
const staticDir = join(repoRoot, "machinelearningmachine", "server", "static");

const read = (p) => readFileSync(p, "utf8");

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
    pretendToBeVisual: true,
    // The client is installed before the parse so that the page's *own*
    // DOMContentLoaded runs its init exactly once. Dispatching the event by hand
    // after construction initialised every tab twice (two sockets, two toasts)
    // and made the assertions lie.
    beforeParse(win) {
      installStubs(win, calls, responder);
      win.eval(read(join(staticDir, "vendor", "marked", "marked.min.js")));
      win.eval(read(join(staticDir, "vendor", "dompurify", "purify.min.js")));
      win.eval(read(join(staticDir, "markdown.js")));
      win.eval(read(join(staticDir, "app.js")));
    },
  });
  const win = dom.window;

  for (let i = 0; i < 12; i++) {
    await new Promise((r) => win.setTimeout(r, 0));
  }
  return { win, dom, calls, socket: () => calls.sockets[calls.sockets.length - 1] };
}

/** Everything app.js touches that jsdom does not provide. */
function installStubs(win, calls, responder) {
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
      setTimeout(() => this.onopen && this.onopen({}), 0);
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
      { agent_id: "claude", name: "Claude 3.5", role: "Critique", provider: "MockLLMProvider", provider_kind: "simulated", color: "#d97706", avatar: "🔮", system_prompt: "y" },
    ],
    history: [],
    limits: { max_messages_client: 500 },
    flags: { url_reader_enabled: false, run_timeout_seconds: 180 },
  });
  for (let i = 0; i < 6; i++) await new Promise((r) => win.setTimeout(r, 0));

  assert.match(body(win, "agentCountBadge"), /2 Modules/);
  assert.match(body(win, "moduleList"), /GitHub Copilot/);
  assert.match(body(win, "moduleList"), /Claude 3\.5/);
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

  assert.equal(calls.sockets.length, socketsAtStart,
    "reconnecting would silently hand the tab a different, empty session");
  assert.match(body(win, "connectionStatus"), /Session released/i);
  const toasts = [...win.document.querySelectorAll("#toastContainer .toast")].map((t) => t.textContent);
  assert.ok(toasts.some((t) => /released after 6 hours idle/i.test(t)),
    `the server's explanation is shown verbatim: ${JSON.stringify(toasts)}`);
});

test("an ordinary disconnect still reconnects - the released case is the exception", async () => {
  const { calls, socket } = await loadClient(() => ({ status: 200, body: [] }));
  socket().close();
  for (let i = 0; i < 3; i++) await new Promise((r) => globalThis.setTimeout(r, 1200));
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
      id: "m1", sender_id: "gpt", sender_name: "GPT-4o", recipient_id: "*", topic: "general",
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
