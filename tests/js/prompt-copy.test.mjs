/*
 * Copy-last-prompt, under jsdom.
 *
 * Why this exists: a prompt is the expensive thing a user writes, and the
 * dashboard is good at losing it. Nothing echoes it into the transcript (only
 * module messages are rendered), running a dialogue does not clear the box, but
 * the scenario presets overwrite it, dictation appends to it, and Clear wipes it.
 * So "what did I actually ask?" is a question the page could not answer after
 * the fact - and the text is wanted back most often right after a run failed,
 * was refused, or queued.
 *
 * The button therefore copies the last prompt *executed* (recorded at commit
 * time, so refusals are recoverable too), falling back to the current textarea
 * content when nothing has been run yet, and refuses to fail silently when the
 * clipboard is out of reach - which is the normal case for this dashboard, since
 * `navigator.clipboard` does not exist over the plain http:// most people reach
 * it on.
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
const HTML = read(join(staticDir, "index.html"));
const APP_JS = read(join(staticDir, "app.js"));

/** A prompt with the awkward parts: newlines, quotes, non-ASCII, an emoji. */
const AWKWARD_PROMPT = 'Explain consistent hashing\r\n  - "with a diagram"\r\n  - in 中文, with 🚀 labels';

const opened = [];

/**
 * A window running the real index.html + app.js against doubles, like
 * client-lifecycle.test.mjs - trimmed to what these tests need.
 *
 * `clipboard` is how the page will see the browser: "api" (a working async
 * clipboard), "denied" (the API exists but rejects - a non-secure context or a
 * refused permission), or "absent" (plain http:// on a browser without it).
 */
async function loadClient({ clipboard = "api", vendor = false } = {}) {
  const html = HTML.replace(/<script[^>]*\ssrc=[^>]*><\/script>/gi, "").replace(/<link[^>]*>/gi, "");
  const calls = { fetch: [], clipboard: [], sockets: [] };

  const dom = new JSDOM(html, {
    url: "http://127.0.0.1:8000/",
    runScripts: "outside-only",
    beforeParse(win) {
      installTimers(win);
      win.fetch = async (url, options) => {
        calls.fetch.push({ url: String(url), options: options || {} });
        return {
          ok: true,
          status: 200,
          json: async () => ({ status: "ok" }),
          text: async () => "{}",
          headers: new win.Headers({ "Content-Type": "application/json" }),
        };
      };
      class FakeSocket {
        constructor(url) {
          this.url = url;
          this.readyState = 1;
          calls.sockets.push(this);
          // Deliver onopen asynchronously, like a browser handing over later.
          win.setTimeout(() => this.onopen && this.onopen({}), 0);
        }
        send() {}
        close() {
          this.readyState = 3;
          this.onclose && this.onclose({ code: 1000 });
        }
        emit(payload) {
          this.onmessage && this.onmessage({ data: JSON.stringify(payload) });
        }
      }
      win.WebSocket = FakeSocket;
      const ctxStub = new Proxy(
        {},
        {
          get: (target, prop) => {
            if (prop === "canvas") return { width: 300, height: 150 };
            if (prop === "measureText") return () => ({ width: 10 });
            if (prop in target) return target[prop];
            return () => undefined;
          },
          set: (target, prop, value) => {
            target[prop] = value;
            return true;
          },
        },
      );
      win.HTMLCanvasElement.prototype.getContext = () => ctxStub;

      if (clipboard === "api") {
        Object.defineProperty(win.navigator, "clipboard", {
          configurable: true,
          value: {
            writeText: async (text) => {
              calls.clipboard.push(text);
            },
          },
        });
      } else if (clipboard === "denied") {
        Object.defineProperty(win.navigator, "clipboard", {
          configurable: true,
          value: {
            writeText: async (text) => {
              calls.clipboard.push(text);
              throw new Error("NotAllowedError: the clipboard is not available in this context");
            },
          },
        });
      }
      // "absent": jsdom's default - no navigator.clipboard at all.

      // The code-block tests need fenced markdown to become a real <pre><code>
      // with a copy button; the others read textContent and stay cheap.
      if (vendor) {
        win.eval(read(join(staticDir, "vendor", "marked", "marked.min.js")));
        win.eval(read(join(staticDir, "vendor", "dompurify", "purify.min.js")));
      }
      win.eval(read(join(staticDir, "markdown.js")));
      win.eval(read(join(staticDir, "app.js")));
    },
  });
  opened.push(dom);
  const win = dom.window;
  for (let i = 0; i < 12; i++) await new Promise((r) => win.setTimeout(r, 0));
  return { win, calls, socket: () => calls.sockets[calls.sockets.length - 1] };
}

/**
 * Track the window's timers so teardown can cancel what app.js scheduled (the
 * 12-second toast dismissal, the reconnect backoff) instead of keeping node alive
 * for it. Unlike client-lifecycle's version these fire for real - the tests here
 * await them to settle the click's promises.
 */
function installTimers(win) {
  const realSetTimeout = win.setTimeout.bind(win);
  const realClearTimeout = win.clearTimeout.bind(win);
  const pending = new Map();
  let nextId = 1;

  win.setTimeout = (fn, ms = 0, ...args) => {
    const id = nextId++;
    const realId = realSetTimeout(() => {
      if (pending.delete(id) && typeof fn === "function") fn(...args);
    }, ms);
    pending.set(id, { realId });
    return id;
  };
  win.clearTimeout = (id) => {
    const record = pending.get(Number(id));
    if (record) {
      pending.delete(Number(id));
      realClearTimeout(record.realId);
    }
  };
  win.setInterval = () => nextId++;
  win.clearInterval = () => {};
  win.requestAnimationFrame = (fn) => win.setTimeout(() => fn(Date.now()), 16);
  win.cancelAnimationFrame = (id) => win.clearTimeout(id);
  win.__cancelAllTimers = () => {
    for (const record of pending.values()) realClearTimeout(record.realId);
    pending.clear();
  };
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
  }
});

/** Let the click's promises settle before asserting. */
const settle = async (win, rounds = 10) => {
  for (let i = 0; i < rounds; i++) await new Promise((r) => win.setTimeout(r, 0));
};

const toasts = (win) => [...win.document.querySelectorAll("#toastContainer .toast")].map((t) => t.textContent);
const hasToast = (win, re) => toasts(win).some((t) => re.test(t));
const setPrompt = (win, text) => {
  win.document.getElementById("inputPrompt").value = text;
};
const clickCopy = async (win) => {
  win.document.getElementById("btnCopyPrompt").click();
  await settle(win);
};
const selectedText = (win) => {
  const area = win.document.getElementById("inputPrompt");
  return area.value.slice(area.selectionStart, area.selectionEnd);
};
const runCalls = (calls) => calls.fetch.filter((c) => String(c.url).includes("/api/run"));

test("prompt copy: the button is in the prompt row, labelled as what it copies", () => {
  const start = HTML.indexOf('id="btnCopyPrompt"');
  assert.notEqual(start, -1, "index.html must carry a copy-prompt button");
  const tag = HTML.slice(HTML.lastIndexOf("<button", start), HTML.indexOf("</button>", start));
  assert.match(tag, /type="button"/, "a button that copies must not submit anything");
  assert.match(tag, />\s*Copy\s*$/, `it carries a visible label: ${tag}`);
  assert.match(tag, /aria-label="[^"]*prompt[^"]*"/i, "its accessible name says *prompt*, not just 'copy'");
  assert.match(tag, /title="[^"]*prompt[^"]*"/i, "and it explains itself on hover");
  // Same row as Clear, on the label line - not adrift in the toolbar.
  const row = HTML.slice(0, start).lastIndexOf('class="flex items-center gap-2"');
  const clear = HTML.lastIndexOf('id="btnClearPrompt"');
  assert.ok(row > 0 && clear > row, "it lives beside Clear in the prompt label row");
});

test("prompt copy: before anything has run it copies what is in the box", async () => {
  const { win, calls } = await loadClient();
  setPrompt(win, "Design a token bucket rate limiter");
  await clickCopy(win);

  assert.deepEqual(calls.clipboard, ["Design a token bucket rate limiter"]);
  assert.ok(hasToast(win, /current prompt copied/i), `the toast names the source: ${JSON.stringify(toasts(win))}`);
  assert.equal(runCalls(calls).length, 0, "copying a prompt is not running one");
});

test("prompt copy: after a run it copies the prompt that was run, not the box", async () => {
  const { win, calls } = await loadClient();
  setPrompt(win, "Implement a thread-safe rate limiter");
  win.document.getElementById("btnRun").click();
  await settle(win);
  assert.equal(runCalls(calls).length, 1, "the click really did commit a run");

  // The user clears the box afterwards - the usual reason to want the text back.
  win.document.getElementById("btnClearPrompt").click();
  await clickCopy(win);

  assert.deepEqual(calls.clipboard, ["Implement a thread-safe rate limiter"]);
  assert.ok(hasToast(win, /last run prompt copied/i), `the toast names the source: ${JSON.stringify(toasts(win))}`);
});

test("prompt copy: a half-written draft does not shadow the last run", async () => {
  const { win, calls } = await loadClient();
  setPrompt(win, "Implement a thread-safe rate limiter");
  win.document.getElementById("btnRun").click();
  await settle(win);

  setPrompt(win, "now make the seconds configurable");
  await clickCopy(win);

  assert.deepEqual(calls.clipboard, ["Implement a thread-safe rate limiter"],
    "the box holds the next thought, not what was run");
});

test("prompt copy: only Execute records - presets and dictation are not runs", async () => {
  const { win, calls } = await loadClient();
  const preset = win.document.querySelector(".preset-btn[data-prompt]");
  assert.ok(preset, "the scenario presets are in the markup");
  preset.click();
  await settle(win);

  await clickCopy(win);

  assert.deepEqual(calls.clipboard, [preset.dataset.prompt], "a loaded preset is copied as current text");
  assert.ok(hasToast(win, /current prompt copied/i), `not as a run: ${JSON.stringify(toasts(win))}`);
  assert.equal(runCalls(calls).length, 0);
});

test("prompt copy: a prompt the validation refused is not remembered as run", async () => {
  const { win, calls } = await loadClient();
  setPrompt(win, "hi"); // under the 5-character floor
  win.document.getElementById("btnRun").click();
  await settle(win);
  assert.equal(runCalls(calls).length, 0, "the run never left the tab");

  setPrompt(win, "");
  await clickCopy(win);

  assert.deepEqual(calls.clipboard, [], "there is no 'last run prompt' to copy");
  assert.ok(hasToast(win, /no prompt to copy yet/i), `and it says so: ${JSON.stringify(toasts(win))}`);
});

test("prompt copy: unicode and newlines survive the trip byte for byte", async () => {
  const { win, calls } = await loadClient();
  setPrompt(win, AWKWARD_PROMPT);
  win.document.getElementById("btnRun").click();
  await settle(win);
  win.document.getElementById("btnClearPrompt").click();

  await clickCopy(win);

  // A textarea normalises CRLF to LF on the way in (per HTML's value sanitisation),
  // so the committed prompt is the LF text - and that is what must come back out.
  assert.deepEqual(calls.clipboard, [AWKWARD_PROMPT.replace(/\r\n/g, "\n")],
    "the clipboard gets exactly the characters that were committed");
});

test("prompt copy: a denied clipboard hands the text back in the box instead of failing", async () => {
  const { win, calls } = await loadClient({ clipboard: "denied" });
  setPrompt(win, "Implement a thread-safe rate limiter");
  win.document.getElementById("btnRun").click();
  await settle(win);
  win.document.getElementById("btnClearPrompt").click();

  await clickCopy(win);

  const area = win.document.getElementById("inputPrompt");
  assert.equal(area.value, "Implement a thread-safe rate limiter", "the prompt is restored to the box");
  assert.equal(selectedText(win), "Implement a thread-safe rate limiter", "and selected, so Ctrl+C is one keystroke");
  assert.equal(win.document.activeElement, area, "and focused, so the keystroke goes to the right place");
  assert.ok(hasToast(win, /couldn't reach the clipboard/i), `the fallback is explained: ${JSON.stringify(toasts(win))}`);
  assert.ok(!hasToast(win, /copied/i), "it must not claim a copy that did not happen");
});

test("prompt copy: a browser without the clipboard API degrades the same way", async () => {
  const { win, calls } = await loadClient({ clipboard: "absent" });
  assert.equal(win.navigator.clipboard, undefined, "this is the plain-http:// case, where the API is undefined");

  setPrompt(win, "Explain CRDTs to a sceptical reviewer");
  await clickCopy(win);

  assert.deepEqual(calls.clipboard, [], "no API, no call");
  assert.equal(win.document.getElementById("inputPrompt").value, "Explain CRDTs to a sceptical reviewer");
  assert.equal(selectedText(win), "Explain CRDTs to a sceptical reviewer");
  assert.ok(hasToast(win, /couldn't reach the clipboard/i), `still explained, never thrown: ${JSON.stringify(toasts(win))}`);
});

test("prompt copy: with nothing run and an empty box it says so once, quietly", async () => {
  const { win, calls } = await loadClient();
  setPrompt(win, "");

  await clickCopy(win);

  assert.deepEqual(calls.clipboard, []);
  assert.ok(hasToast(win, /no prompt to copy yet/i), `one plain-language sentence: ${JSON.stringify(toasts(win))}`);
  assert.equal(toasts(win).filter((t) => /no prompt/i.test(t)).length, 1, "and only one");
});

test("prompt copy: the clipboard is touched from exactly one place in the client", () => {
  const writes = APP_JS.match(/\.writeText\s*\(/g) || [];
  assert.equal(writes.length, 1,
    "every copy goes through the one helper, so the absent-clipboard case cannot regress in a second call site");
  assert.match(APP_JS, /function copyTextToClipboard\s*\(/, "and that helper is named");
  assert.match(APP_JS, /async function copyTextToClipboard\s*\(|copyTextToClipboard\s*=\s*async/,
    "it must be async: writeText returns a promise");
});

/*
 * The code-block copy button (the `pre code` blocks inside replies) shared the
 * same flaw: it called `navigator.clipboard.writeText` directly, so over plain
 * http:// - where that API does not exist - every click answered "Failed to copy
 * code" and copied nothing. It has no textarea to fall back into, but it does
 * have a selection: select the code and say so.
 */
const CODE_REPLY = "Here is the implementation:\n\n```python\nimport threading\n\nclass RateLimiter:\n    pass\n```\n";

/** A reply carrying one fenced code block, as the server would deliver it. */
async function withCodeReply({ clipboard = "api" } = {}) {
  const { win, calls, socket } = await loadClient({ clipboard, vendor: true });
  socket().emit({ type: "init", authenticated: true, agents: [], history: [], limits: {}, flags: {} });
  socket().emit({
    type: "new_message",
    run_id: "run-1",
    message: {
      id: "m1",
      sender_id: "gpt",
      sender_name: "GPT-6 Astra",
      recipient_id: "*",
      topic: "general",
      message_type: "answer",
      content: CODE_REPLY,
      artifacts: {},
      metadata: {},
      timestamp: 1700000000,
    },
  });
  await settle(win, 12);
  const btn = win.document.querySelector("#messagesContainer .btn-copy-code");
  assert.ok(btn, "the reply rendered a code block with its copy button");
  return { win, calls, btn, code: win.document.querySelector("#messagesContainer pre code") };
}

test("code block: a working clipboard still copies the block and says so", async () => {
  const { win, calls, btn, code } = await withCodeReply();
  btn.click();
  await settle(win);

  assert.deepEqual(calls.clipboard, [code.innerText], "the block's text reaches the clipboard");
  assert.match(btn.textContent, /copied/i, "and the button reports it on itself");
  assert.ok(hasToast(win, /code copied to clipboard/i), JSON.stringify(toasts(win)));
});

test("code block: without a clipboard it selects the code instead of pretending to fail", async () => {
  const { win, btn, code, calls } = await withCodeReply({ clipboard: "absent" });
  assert.equal(win.navigator.clipboard, undefined, "plain http://, where the API is missing");

  btn.click();
  await settle(win);

  assert.deepEqual(calls.clipboard, [], "nothing was copied, and nothing claims otherwise");
  assert.match(String(win.getSelection()), /class RateLimiter/, "the code is selected, so Ctrl+C is one keystroke");
  assert.ok(hasToast(win, /couldn't reach the clipboard/i),
    `the reason is stated: ${JSON.stringify(toasts(win))}`);
  assert.ok(!hasToast(win, /^Failed to copy code$/i), "not the old dead-end error");
  assert.ok(!hasToast(win, /code copied to clipboard/i), "and never a false success");
});

test("code block: a denied clipboard degrades the same way", async () => {
  const { win, btn } = await withCodeReply({ clipboard: "denied" });

  btn.click();
  await settle(win);

  assert.match(String(win.getSelection()), /class RateLimiter/);
  assert.ok(hasToast(win, /couldn't reach the clipboard/i), JSON.stringify(toasts(win)));
});
