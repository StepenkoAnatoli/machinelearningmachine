/*
 * The service worker contract.
 *
 * sw.js is the one file in this repository that runs *outside* the page, in a
 * context no other test can reach: it has no DOM, no app.js, and it is loaded by
 * the browser long after every other test has finished. So it is evaluated here
 * in a small worker-shaped sandbox (node:vm) - synthetic `caches`, synthetic
 * events, the real `Request`/`Response` - and made to answer questions a browser
 * would ask it.
 *
 * Two things this file is really guarding:
 *
 *   1. **The precache list is the shell.** It is drift-locked against the files
 *      that actually ship: add a file to static/ and forget the worker, and this
 *      goes red; list a file that is not shipped, and it goes red too.
 *   2. **Nothing else is cached, ever.** /api/ and /ws must pass straight through:
 *      caching a transcript would put a user's conversation in a browser cache on
 *      disk, which is exactly the line scope A drew.
 *
 * Run: npm install && node --test tests/js/*.test.mjs
 */
import assert from "node:assert/strict";
import { existsSync, readFileSync, readdirSync } from "node:fs";
import { dirname, join, resolve, sep } from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";
import vm from "node:vm";

const here = dirname(fileURLToPath(import.meta.url));
const repoRoot = resolve(here, "..", "..");
const staticDir = join(repoRoot, "machinelearningmachine", "server", "static");
const ORIGIN = "http://127.0.0.1:8000";

const read = (path) => readFileSync(path, "utf8");

/** Read the worker, or fail a test with something a human can act on. */
function workerSource() {
  const path = join(staticDir, "sw.js");
  assert.ok(existsSync(path), "static/sw.js must exist - the offline shell is only as real as this file");
  return read(path);
}

/*
 * The shell, exactly as the server serves it.
 *
 * Most files live behind the /static mount, but three are served from root routes
 * (index.html at "/", the manifest and the favicon) - a precache list that gets
 * that mapping wrong caches URLs nothing will ever request.
 */
const EXCLUDED_FILES = new Set([
  // Browser-managed: a worker that precaches itself is an update-loop classic.
  "sw.js",
  // Build metadata, never fetched by the page.
  "vendor/MANIFEST.json",
]);
const EXCLUDED_PREFIXES = [
  // Legal text: shipped in the wheel, never requested at runtime.
  "vendor/licenses/",
];

function shellUrls() {
  const urls = [];
  const walk = (dir) => {
    for (const entry of readdirSync(dir, { withFileTypes: true })) {
      const full = join(dir, entry.name);
      const rel = full.slice(staticDir.length + 1).split(sep).join("/");
      if (entry.isDirectory()) {
        walk(full);
        continue;
      }
      if (EXCLUDED_FILES.has(rel) || EXCLUDED_PREFIXES.some((prefix) => rel.startsWith(prefix))) {
        continue;
      }
      if (rel === "index.html") {
        urls.push("/");
      } else if (rel === "manifest.webmanifest" || rel === "favicon.ico") {
        urls.push(`/${rel}`);
      } else {
        urls.push(`/static/${rel}`);
      }
    }
  };
  walk(staticDir);
  return urls.sort();
}

function responseFor(url, body = `body of ${url}`) {
  return new Response(body, { status: 200, headers: { "Content-Type": "text/plain" } });
}

/**
 * Load the worker the way a browser would, into a worker-shaped sandbox.
 *
 *   network: "ok"     - fetch resolves
 *            "down"   - fetch rejects (the server is not running)
 *   cached:  URLs pre-filled in the cache, as a previous install would have left it
 */
function loadWorker({ network = "ok", cached = [] } = {}) {
  const handlers = new Map();
  const calls = { addAll: [], opened: [], matched: [], deleted: [], skipWaiting: 0, claim: 0, fetched: [], puts: 0 };
  const store = new Map(cached.map((url) => [url, responseFor(url)]));

  const makeCache = () => ({
    async addAll(urls, options) {
      calls.addAll.push({ urls: [...urls], options });
      for (const url of urls) store.set(url, responseFor(url));
    },
    async match(request) {
      const key = typeof request === "string" ? request : new URL(request.url).pathname;
      calls.matched.push(key);
      return store.get(key);
    },
    async put() {
      calls.puts += 1;
    },
  });

  const cache = makeCache();
  const caches = {
    async open(name) {
      calls.opened.push(name);
      return cache;
    },
    async match(request) {
      const key = typeof request === "string" ? request : new URL(request.url).pathname;
      calls.matched.push(key);
      return store.get(key);
    },
    async keys() {
      return ["mlm-shell-v9-an-old-one", nameFromSource()];
    },
    async delete(name) {
      calls.deleted.push(name);
      return true;
    },
  };

  const self = {
    location: { origin: ORIGIN, href: `${ORIGIN}/sw.js` },
    addEventListener(type, handler) {
      if (!handlers.has(type)) handlers.set(type, []);
      handlers.get(type).push(handler);
    },
    skipWaiting() {
      calls.skipWaiting += 1;
      return Promise.resolve();
    },
    clients: {
      claim() {
        calls.claim += 1;
        return Promise.resolve();
      },
    },
  };

  const context = vm.createContext({
    self,
    caches,
    console,
    URL,
    Request,
    Response,
    fetch: async (request) => {
      const url = typeof request === "string" ? request : new URL(request.url).pathname;
      calls.fetched.push(url);
      if (network === "down") {
        throw new TypeError("Failed to fetch");
      }
      return responseFor(url);
    },
  });
  vm.runInContext(workerSource(), context, { filename: "static/sw.js" });

  /** Fire an event and settle everything it scheduled. */
  const dispatch = async (type, init = {}) => {
    const list = handlers.get(type) || [];
    let responded = null;
    let waited = null;
    const event = {
      ...init,
      respondWith(promise) {
        responded = promise;
      },
      waitUntil(promise) {
        waited = promise;
      },
    };
    for (const handler of list) {
      handler(event);
    }
    if (waited) await waited;
    if (responded) {
      try {
        return { response: await responded, rejected: false };
      } catch (error) {
        return { response: null, rejected: true, error };
      }
    }
    return { response: null, rejected: false, respondedWithout: true };
  };

  return { handlers, calls, store, dispatch, self };
}

/** The cache name the worker declares, for the "old caches" assertions. */
function nameFromSource() {
  const match = workerSource().match(/CACHE_NAME\s*=\s*["']([^"']+)["']/);
  assert.ok(match, "sw.js must declare CACHE_NAME so the cache can be versioned");
  return match[1];
}

const request = (path, init = {}) => new Request(`${ORIGIN}${path}`, init);

/** A navigation: same-origin GET that asks for HTML. */
const navigation = (path = "/") => new Request(`${ORIGIN}${path}`, { headers: { Accept: "text/html,application/xhtml+xml" } });

// --------------------------------------------------------------------------- #
// the shell list, locked to what ships
// --------------------------------------------------------------------------- #
test("service worker: the precache list is exactly the shipped shell", () => {
  const { calls, dispatch } = loadWorker();
  return dispatch("install").then(() => {
    assert.equal(calls.addAll.length, 1, "install must cache the shell exactly once");
    const cached = [...calls.addAll[0].urls].sort();
    assert.deepEqual(
      cached,
      shellUrls(),
      "the worker's precache list and the files that ship have drifted apart",
    );
  });
});

test("service worker: the worker does not cache itself, the licences, or the build manifest", () => {
  const { calls, dispatch } = loadWorker();
  return dispatch("install").then(() => {
    const cached = calls.addAll[0].urls;
    assert.ok(!cached.includes("/static/sw.js"), "a worker that precaches itself cannot update itself");
    assert.ok(!cached.some((url) => url.includes("/licenses/")), "licence text is never fetched at runtime");
    assert.ok(!cached.includes("/static/vendor/MANIFEST.json"), "build metadata is not a page asset");
    assert.ok(cached.includes("/"), "the shell itself is cached at the URL users actually open");
    assert.ok(cached.includes("/manifest.webmanifest") && cached.includes("/favicon.ico"), "install assets are root paths");
  });
});

test("service worker: install asks the network to bypass HTTP cache, then takes over", () => {
  const { calls, dispatch } = loadWorker();
  return dispatch("install").then(() => {
    // Compared field by field, not deeply: the options object was built inside the
    // worker's own realm, and a strict deepEqual would be comparing prototypes.
    assert.equal(calls.addAll[0].options.cache, "reload", "a stale shell file must not be precached");
    assert.equal(calls.skipWaiting, 1, "a new worker should not sit waiting while the old one serves");
  });
});

test("service worker: activate deletes every other cache and claims the pages", () => {
  const { calls, dispatch } = loadWorker();
  return dispatch("activate").then(() => {
    assert.deepEqual(calls.deleted, ["mlm-shell-v9-an-old-one"], "old shells must not linger on disk");
    assert.equal(calls.claim, 1, "the new worker should control open pages without a reload");
  });
});

test("service worker: exactly one cache name, and it is versioned", () => {
  const source = workerSource();
  const literals = [...source.matchAll(/["']mlm-shell[^"']*["']/g)].map((match) => match[0].slice(1, -1));
  assert.deepEqual([...new Set(literals)], ["mlm-shell-v1"], "one cache name, versioned - not a family of them");
  assert.equal(/cache\.put\s*\(/.test(source), false, "nothing may be written to the cache outside install");
});

// --------------------------------------------------------------------------- #
// what the worker must keep its hands off
// --------------------------------------------------------------------------- #
test("service worker: API calls are never intercepted", async () => {
  const { calls, dispatch } = loadWorker({ network: "down" });
  const result = await dispatch("fetch", { request: request("/api/status") });
  assert.equal(result.respondedWithout, true, "/api/ must pass straight through to the network");
  assert.deepEqual(calls.matched, [], "and must never reach for a cache");
  assert.equal(calls.fetched.length, 0, "the worker does not even re-issue it");
});

test("service worker: the WebSocket handshake is never intercepted", async () => {
  const { dispatch } = loadWorker({ network: "down" });
  for (const path of ["/ws", "/ws/", "/api/runs/1/cancel"]) {
    const result = await dispatch("fetch", { request: request(path, { method: path.includes("cancel") ? "POST" : "GET" }) });
    assert.equal(result.respondedWithout, true, `${path} must pass through`);
  }
});

test("service worker: writes and other origins are left alone", async () => {
  const { dispatch } = loadWorker({ network: "down" });
  const post = await dispatch("fetch", { request: request("/static/app.js", { method: "POST" }) });
  assert.equal(post.respondedWithout, true, "a POST is never served from a GET cache");

  const external = await dispatch("fetch", { request: new Request("https://example.com/thing.js") });
  assert.equal(external.respondedWithout, true, "the worker has no business answering for other origins");
});

// --------------------------------------------------------------------------- #
// what it does serve
// --------------------------------------------------------------------------- #
test("service worker: with the server down, opening the dashboard serves the cached shell", async () => {
  const { dispatch } = loadWorker({ network: "down", cached: ["/"] });
  const result = await dispatch("fetch", { request: navigation("/") });
  assert.ok(result.response, "a navigation to the app must resolve to something");
  assert.equal(result.response.status, 200);
  assert.match(await result.response.text(), /body of \//);
});

test("service worker: a shell asset survives the server being down", async () => {
  const { dispatch } = loadWorker({ network: "down", cached: ["/static/app.js"] });
  const result = await dispatch("fetch", { request: request("/static/app.js") });
  assert.ok(result.response, "app.js must come from the cache");
  assert.match(await result.response.text(), /body of \/static\/app\.js/);
});

test("service worker: while the server is up, the network wins - the shell is never served stale", async () => {
  const { calls, dispatch } = loadWorker({ network: "ok", cached: ["/static/app.js"] });
  const result = await dispatch("fetch", { request: request("/static/app.js") });
  assert.ok(result.response);
  assert.deepEqual(calls.fetched, ["/static/app.js"], "the network attempt comes first");
  assert.deepEqual(calls.matched, [], "and with the network up the cache is not consulted at all");
});

test("service worker: a navigation to a deep link falls back to the shell, not to nothing", async () => {
  // No client-side routing exists today, but a bookmarked path must not become a
  // blank screen just because the server is down.
  const { dispatch } = loadWorker({ network: "down", cached: ["/"] });
  const result = await dispatch("fetch", { request: navigation("/some/deep/link") });
  assert.ok(result.response, "an unknown path still resolves");
  assert.match(await result.response.text(), /body of \//, "to the cached shell");
});

test("service worker: with no network and no cache it fails honestly", async () => {
  const { dispatch } = loadWorker({ network: "down" });
  const result = await dispatch("fetch", { request: request("/static/theme.js") });
  if (result.rejected) {
    return; // rejecting is honest too: the browser shows its own network error
  }
  assert.ok(result.response, "if it resolves, it must be an error response");
  assert.equal(result.response.type, "error", "an invented stub page would be worse than an error");
  assert.equal(result.response.status, 0);
});

test("service worker: no /api/ URL is ever precached", () => {
  const { calls, dispatch } = loadWorker();
  return dispatch("install").then(() => {
    const leaked = calls.addAll[0].urls.filter((url) => url.startsWith("/api/"));
    assert.deepEqual(leaked, [], "scope A promised no server content in a browser cache");
  });
});

test("service worker: it registers handlers for install, activate and fetch - and nothing else", () => {
  const { handlers } = loadWorker();
  assert.deepEqual([...handlers.keys()].sort(), ["activate", "fetch", "install"]);
  for (const type of ["install", "activate", "fetch"]) {
    assert.equal(handlers.get(type).length, 1, `${type} must have exactly one handler`);
  }
});
