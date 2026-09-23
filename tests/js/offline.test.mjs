/*
 * Service-worker *registration*, from the page's side.
 *
 * sw.js itself is pinned by service-worker.test.mjs; this file is about the three
 * lines in app.js that ask the browser for a worker, and about the ways that must
 * not break anything:
 *
 *   - the worker exists  -> it is registered, at the root scope, once
 *   - it is refused      -> the dashboard carries on exactly as before (a LAN
 *                           http:// address has no secure context, and this is
 *                           the normal case for this app, not an error)
 *   - it does not exist  -> nothing is attempted at all (old browsers, jsdom)
 *
 * The unreachable-server state machine (the banner, the disabled controls, the
 * automatic recovery) is the next task and lands in this same file.
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

const read = (path) => readFileSync(path, "utf8");

const opened = [];

/**
 * A window running the real index.html + app.js.
 *
 * `serviceWorker` is how the page will see the browser, which is the whole point
 * of this file: "ok" (registers), "reject" (exists but refuses - no secure
 * context), or "absent" (jsdom's default, and a plain-http LAN address in real
 * life).
 */
async function loadClient({ serviceWorker = "absent" } = {}) {
  const html = read(join(staticDir, "index.html"))
    .replace(/<script[^>]*\ssrc=[^>]*><\/script>/gi, "")
    .replace(/<link[^>]*>/gi, "");
  const calls = { registered: [], fetch: [], sockets: 0 };

  const dom = new JSDOM(html, {
    url: "http://127.0.0.1:8000/",
    runScripts: "outside-only",
    beforeParse(win) {
      installTimers(win);
      win.fetch = async (url) => {
        calls.fetch.push(String(url));
        return {
          ok: true,
          status: 200,
          json: async () => ({}),
          text: async () => "{}",
          headers: new win.Headers({ "Content-Type": "application/json" }),
        };
      };
      class FakeSocket {
        constructor(url) {
          this.url = url;
          this.readyState = 1;
          calls.sockets += 1;
          win.setTimeout(() => this.onopen && this.onopen({}), 0);
        }
        send() {}
        close() {
          this.readyState = 3;
        }
        emit(payload) {
          this.onmessage && this.onmessage({ data: JSON.stringify(payload) });
        }
      }
      win.WebSocket = FakeSocket;
      win.HTMLCanvasElement.prototype.getContext = () =>
        new Proxy(
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

      if (serviceWorker !== "absent") {
        Object.defineProperty(win.navigator, "serviceWorker", {
          configurable: true,
          value: {
            register(url, options) {
              calls.registered.push({ url, options });
              if (serviceWorker === "reject") {
                return Promise.reject(new Error("Failed to register a ServiceWorker: the origin is not secure"));
              }
              return Promise.resolve({ scope: new URL(url, win.location.href).pathname });
            },
          },
        });
      }

      win.eval(read(join(staticDir, "markdown.js")));
      win.eval(read(join(staticDir, "app.js")));
    },
  });
  opened.push(dom);
  const win = dom.window;
  for (let i = 0; i < 12; i++) await new Promise((r) => win.setTimeout(r, 0));
  return { win, calls };
}

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

const toasts = (win) => [...win.document.querySelectorAll("#toastContainer .toast")].map((t) => t.textContent);
/** Toasts about the offline machinery - not the fake socket's "connected" one. */
const offlineToasts = (win) => toasts(win).filter((text) => /worker|offline|unreachable/i.test(text));

test("offline: the worker is registered at the root scope", async () => {
  const { calls } = await loadClient({ serviceWorker: "ok" });

  assert.equal(calls.registered.length, 1, "exactly one registration, or the page is asking twice");
  assert.equal(calls.registered[0].url, "/sw.js", "a worker under /static/ could never control the page");
  const scope = calls.registered[0].options && calls.registered[0].options.scope;
  assert.ok(scope === undefined || scope === "/", `the worker must cover the whole origin, got scope=${scope}`);
});

test("offline: a refused registration is not an error the user has to see", async () => {
  const { win, calls } = await loadClient({ serviceWorker: "reject" });

  assert.equal(calls.registered.length, 1, "the attempt is made");
  assert.deepEqual(offlineToasts(win), [], "and its refusal produces no toast, no banner, no noise");
  // The dashboard must be exactly as alive as it is without a worker.
  assert.equal(win.document.querySelectorAll("#messagesContainer").length, 1);
  assert.ok(win.document.getElementById("btnRun"), "the page still rendered");
  assert.ok(calls.fetch.some((url) => url.includes("/api/")), "and it still talks to the server");
});

test("offline: without the API nothing is attempted", async () => {
  const { win, calls } = await loadClient({ serviceWorker: "absent" });

  assert.equal(win.navigator.serviceWorker, undefined, "this is the plain-http LAN case, and jsdom's default");
  assert.deepEqual(calls.registered, [], "no registration attempt");
  assert.deepEqual(offlineToasts(win), [], "and nothing said about it");
});

test("offline: registration happens once, and never blocks the dashboard", async () => {
  const { win, calls } = await loadClient({ serviceWorker: "ok" });

  assert.equal(calls.registered.length, 1);
  assert.ok(calls.sockets >= 1, "the WebSocket connected regardless of the worker");
  const runButton = win.document.getElementById("btnRun");
  assert.equal(runButton.disabled, false, "a worker must never gate the UI");
});

test("offline: app.js registers the worker and nothing else does", () => {
  const app = read(join(staticDir, "app.js"));
  const registrations = app.match(/serviceWorker\s*\.\s*register\s*\(/g) || [];
  assert.equal(registrations.length, 1, "one call site, behind one feature check");
  assert.match(app, /["']serviceWorker["']\s+in\s+navigator/, "guarded by a feature check, not assumed");
  assert.match(app, /register\(\s*["']\/sw\.js["']/, "registered from the root");
  // The refusal path must be handled in-place: no toast, no rethrow.
  assert.match(app, /register\(\s*["']\/sw\.js["'][\s\S]{0,200}catch\s*\(/, "the rejection is caught where it happens");
});
