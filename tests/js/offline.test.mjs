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
 * The rest of the file is the unreachable-server state machine: the banner, the
 * controls that go grey with a reason, and the recovery that must happen without
 * a reload.
 *
 * Run: npm install && node --test tests/js/*.test.mjs
 */
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import test, { afterEach } from "node:test";
import { JSDOM, VirtualConsole } from "jsdom";

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
async function loadClient({ serviceWorker = "absent", fetchFails = false, fetchStatus = 200, sessions = null } = {}) {
  const html = read(join(staticDir, "index.html"))
    .replace(/<script[^>]*\ssrc=[^>]*><\/script>/gi, "")
    .replace(/<link[^>]*>/gi, "");
  //: `serverGone` lets a test kill the server mid-flight, which is how the page
  //: ever has session rows on screen while the server is unreachable.
  const calls = {
    registered: [],
    fetch: [],
    sockets: 0,
    all: [], // every FakeSocket, in order, so a test can count what is still open
    lastSocket: null,
    //: One switch for "nothing answers": set at load, and flipped by a test that
    //: needs the server to come back. (It used to be a captured parameter, so a test
    //: could not bring the server back at all.)
    serverGone: fetchFails,
    jsdomErrors: [], // jsdom reports every navigation as an error, and a reload IS one
  };
  // jsdom's default console prints "Not implemented: navigation" to the Node
  // console - noise a test cannot assert on. Captured instead, and forwarded for
  // everything that is not a jsdom limitation.
  const virtualConsole = new VirtualConsole();
  virtualConsole.on("jsdomError", (err) => calls.jsdomErrors.push(String((err && err.message) || err)));
  virtualConsole.sendTo(console, { omitJSDOMErrors: true });

  const dom = new JSDOM(html, {
    url: "http://127.0.0.1:8000/",
    runScripts: "outside-only",
    virtualConsole,
    beforeParse(win) {
      installTimers(win);
      win.fetch = async (url) => {
        calls.fetch.push(String(url));
        //: A test can hold one request open (and resolve it later) to observe what
        //: a handler does when the server dies while its request is in flight.
        if (calls.exportGate && String(url).includes("/api/export")) await calls.exportGate;
        if (calls.serverGone) {
          // Exactly what a browser throws when nothing is listening on the port.
          throw new TypeError("Failed to fetch");
        }
        if (sessions && String(url).includes("/api/sessions")) {
          return {
            ok: true,
            status: 200,
            json: async () => ({ sessions }),
            text: async () => "{}",
            headers: new win.Headers({ "Content-Type": "application/json" }),
          };
        }
        return {
          ok: fetchStatus >= 200 && fetchStatus < 300,
          status: fetchStatus,
          json: async () => ({ detail: "the server answered" }),
          text: async () => "{}",
          headers: new win.Headers({ "Content-Type": "application/json" }),
        };
      };
      class FakeSocket {
        // A real WebSocket carries these on the constructor, and code that asks
        // "is this socket open?" uses them - a fake without them silently answers
        // every such question with `false`.
        static CONNECTING = 0;
        static OPEN = 1;
        static CLOSING = 2;
        static CLOSED = 3;
        constructor(url) {
          this.url = url;
          // CONNECTING, like a real socket: code that asks "can I use this socket
          // as it is?" gets the same answer as in a browser. (Saying OPEN here hid
          // a real recovery bug for a whole task - see the short-outage test.)
          this.readyState = 0;
          calls.sockets += 1;
          calls.all.push(this);
          calls.lastSocket = this;
          // A socket only opens if something is listening - otherwise onopen here
          // would tell the page the server is up while every request says it is not.
          win.setTimeout(() => {
            if (calls.serverGone) {
              this.readyState = 3;
              this.onclose && this.onclose({ code: 1006 });
            } else {
              this.readyState = 1;
              this.onopen && this.onopen({});
            }
          }, 0);
        }
        send() {}
        close() {
          this.readyState = 3;
          // A real socket tells the page it closed - that event is what puts the
          // page into "reconnecting" or "released", so the double must raise it.
          this.onclose && this.onclose({ code: 1000 });
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

      // theme.js, in the order the page really uses it (<head>, before the deferred
      // scripts). It cannot simply be eval'd here: jsdom calls beforeParse with
      // `document.documentElement === null`, and theme.js applies the theme to that
      // element - which never happens in a browser, where a <script> in <head> runs
      // after <html> exists. So it is loaded at DOMContentLoaded instead, and the
      // mount app.js would have done is done here, because app.js's own init ran
      // before the module existed.
      win.document.addEventListener("DOMContentLoaded", () => {
        win.eval(read(join(staticDir, "theme.js")));
        win.MLMTheme.mount({});
      });
    },
  });
  opened.push(dom);
  const win = dom.window;
  for (let i = 0; i < 12; i++) await win.__nextTick();
  return { win, calls, socket: () => calls.lastSocket };
}

function installTimers(win) {
  const realSetTimeout = win.setTimeout.bind(win);
  const realClearTimeout = win.clearTimeout.bind(win);
  const pending = new Map();
  const rafTimers = new Set(); // animation frames: the redraw loop, not page logic
  let nextId = 1;
  win.setTimeout = (fn, ms = 0, ...args) => {
    const id = nextId++;
    const realId = realSetTimeout(() => {
      if (pending.delete(id) && typeof fn === "function") fn(...args);
    }, ms);
    pending.set(id, { realId, fn, ms });
    return id;
  };
  win.clearTimeout = (id) => {
    const record = pending.get(Number(id));
    if (record) {
      pending.delete(Number(id));
      realClearTimeout(record.realId);
    }
  };
  /**
   * The harness's own "go around the event loop once", on a real timer *outside*
   * the page's queue. Using win.setTimeout for this (as the helpers first did) put
   * the harness's 0ms timers into the same map the tests advance by hand, so "run
   * the next timer the page scheduled" could pick up the harness's own tick instead
   * - and a whole step silently did nothing.
   */
  win.__nextTick = () => new Promise((resolve) => realSetTimeout(resolve, 0));
  win.setInterval = () => nextId++;
  win.clearInterval = () => {};
  win.requestAnimationFrame = (fn) => {
    const id = win.setTimeout(() => fn(Date.now()), 16);
    rafTimers.add(id);
    return id;
  };
  win.cancelAnimationFrame = (id) => win.clearTimeout(id);
  win.__cancelAllTimers = () => {
    for (const record of pending.values()) realClearTimeout(record.realId);
    pending.clear();
  };
  /**
   * Run whatever the page has queued, *now*, instead of waiting out its delays.
   *
   * Real time would otherwise rule these tests: the offline probe backs off to
   * ten seconds between attempts and the socket retry budget takes ten tries, so
   * "the server comes back later" would cost minutes of wall clock. This runs the
   * callbacks in queue order and returns how many ran, so a test can loop it and
   * let the page's promises settle in between. The limit is what keeps a page
   * that reschedules itself forever from hanging a test.
   */
  win.__runPendingTimers = (limit = 50) => {
    // Snapshot first, then run: a timer scheduled *by* a callback belongs to the
    // future, not to this round. Draining the live map instead would let one call
    // collapse a chain of retries - and "the retry timer was still pending when the
    // server came back" is precisely the timing that has to be testable.
    // Fired by *delay*, like real timers, not in the order the page happened to
    // schedule them - the two differ exactly where it matters (the 1s offline probe
    // is scheduled after the 1.5s socket retry, and the browser gives the race to the
    // probe). Animation frames sort last: the canvas redraw queues one every 16ms, so
    // otherwise "the next timer" is almost always a redraw and a step-by-step test
    // never reaches the event it is stepping towards.
    const due = [...pending.entries()]
      .sort(([ia, a], [ib, b]) => (rafTimers.has(ia) ? 1 : 0) - (rafTimers.has(ib) ? 1 : 0) || a.ms - b.ms)
      .slice(0, limit);
    let ran = 0;
    for (const [id, record] of due) {
      if (!pending.delete(id)) continue;
      realClearTimeout(record.realId);
      ran += 1;
      if (typeof record.fn === "function") record.fn();
    }
    return ran;
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
/** Let the page's own async work settle. */
const settleFrames = async (win, rounds = 10) => {
  for (let i = 0; i < rounds; i++) await win.__nextTick();
};

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
  // Property *and* callable: a browser can expose the object without a usable
  // register(), and assuming otherwise throws inside DOMContentLoaded - which
  // kills every handler after it, not just the registration.
  assert.match(
    app,
    /navigator\s*\.\s*serviceWorker\s*&&[\s\S]{0,80}register/,
    "guarded by a feature check that also verifies register() is callable",
  );
  assert.match(app, /register\(\s*["']\/sw\.js["']/, "registered from the root");
  // The refusal path must be handled in-place: no toast, no rethrow.
  assert.match(app, /register\(\s*["']\/sw\.js["'][\s\S]{0,200}catch\s*\(/, "the rejection is caught where it happens");
});

/*
 * The unreachable-server state.
 *
 * The signal is deliberately *not* `navigator.onLine`: a local server can be down
 * while the browser is perfectly online, which is the normal case for this app.
 * What matters is whether the server answers - so boot probes it, and the state
 * is entered only when the network itself fails (never on a 4xx from a server that
 * is clearly there, and never on the "session released" frame, which has its own
 * meaning and its own pill).
 */

/** The banner, if the page has one, with what the user can see in it. */
const banner = (win) => win.document.getElementById("offlineBanner");
const bannerText = (win) => (banner(win) ? banner(win).textContent : "");
const bannerVisible = (win) => {
  const el = banner(win);
  if (!el) return false;
  if (el.hidden) return false;
  return !el.classList.contains("hidden");
};

test("offline: a server that rejects the boot probe puts the page in the unreachable state", async () => {
  const { win } = await loadClient({ fetchFails: true });

  assert.ok(banner(win), "the page must carry an offline banner element");
  assert.ok(bannerVisible(win), "and show it when the server does not answer");
  assert.equal(banner(win).getAttribute("role"), "status", "it is information, not an alert");
  assert.notEqual(win.document.activeElement, banner(win), "and it must never steal focus");
  assert.match(bannerText(win), /server/i, `it should name the missing thing: ${bannerText(win)}`);
  assert.match(bannerText(win), /launch/i, "and how to get it back (the launcher)");
});

test("offline: a server that answers leaves the banner out of the way", async () => {
  const { win } = await loadClient();

  assert.ok(banner(win), "the element exists so the state is testable");
  assert.equal(bannerVisible(win), false, "but stays hidden while the server answers");
});

test("offline: a refusal from a reachable server is not 'unreachable'", async () => {
  // 401/403/409 mean the server is *there* - the page has a different problem,
  // and telling the user to start the server would be a lie.
  const { win } = await loadClient({ fetchStatus: 401 });

  assert.equal(bannerVisible(win), false, `a 401 is not a missing server: ${bannerText(win)}`);
  const panel = win.document.getElementById("authPanel");
  assert.ok(panel, "the token panel exists");
  assert.equal(panel.classList.contains("hidden"), false, "a refusal opens the token prompt - that is its job");
});

test("offline: a released session is not 'unreachable' either", async () => {
  const { win, socket } = await loadClient();
  socket().emit({ type: "session_released", reason: "idle", detail: "released after 6 hours idle" });
  socket().close();  // the server closes the connection after saying this
  await settleFrames(win);

  assert.equal(bannerVisible(win), false, "the released state has its own pill and its own meaning");
  const status = win.document.getElementById("connectionStatus");
  assert.match(status.textContent, /released/i, "and it is still the one telling that story");
});

test("offline: the socket reconnecting is not 'unreachable' while the server answers", async () => {
  const { win, socket } = await loadClient();
  socket().close();  // an ordinary drop: the client reconnects, the server is fine

  // Read the pill now: the reconnect is scheduled, and letting the clock run would
  // replace this state with the next successful connection.
  assert.match(win.document.getElementById("connectionStatus").textContent, /reconnect/i);
  await settleFrames(win);
  assert.equal(bannerVisible(win), false, "a reconnect is not a missing server");
});

test("offline: a request path that legitimately fails (a stop with no run) does not raise the banner", async () => {
  // /api/runs/<id>/cancel 404s when the run already ended. That is an answer.
  const { win } = await loadClient({ fetchStatus: 404 });
  win.document.getElementById("btnStop").click();
  await settleFrames(win);

  assert.equal(bannerVisible(win), false, `a 404 is an answer, not an outage: ${bannerText(win)}`);
});

/*
 * What stops working when the server is not there - and what must not.
 *
 * The split is deliberate and it is the whole point of this task: a control that
 * needs the server is disabled *with a reason*, and everything the page can do by
 * itself (theme, the prompt box, the preset library, read-aloud, help, search)
 * keeps working. Getting this wrong in either direction is user-visible: an
 * enabled Execute button that cannot run, or a greyed-out theme toggle because the
 * user's server is off.
 *
 * The set is declared in the markup with data-requires-server, so this file can
 * lock it: the drift lock at the bottom fails if a control is added to one side of
 * the line without a decision.
 */

/** The ids the markup marks as needing the server (the drift-locked contract). */
function markedServerControls() {
  const markup = read(join(staticDir, "index.html"));
  const ids = [];
  for (const match of markup.matchAll(/<([a-z]+)([^>]*?)data-requires-server([^>]*?)>/g)) {
    const id = /id="([^"]+)"/.exec(match[2] + match[3]);
    if (id) ids.push(id[1]);
  }
  return ids.sort();
}

/** Controls that must keep working with the server down - all local. */
const LOCAL_CONTROLS = [
  "btnThemeToggle", // theme is a class on <html>
  "btnCopyPrompt",
  "btnClearPrompt",
  "inputPrompt",
  "btnReadPrompt",
  "btnHelp",
  "btnPresetManager",
  "btnSavePreset",
  "btnPresetImport",
  "btnPresetExportAll",
  "searchMessages",
];

const disabledIds = (win) =>
  [...win.document.querySelectorAll("button, input, textarea, select")]
    .filter((el) => el.disabled)
    .map((el) => el.id)
    .filter(Boolean)
    .sort();

test("offline: markup marks exactly the controls that need the server", () => {
  /*
   * Every control whose handler reaches the server, and nothing else. Audited
   * against the request sites rather than by eye - app.js's `apiFetch` call sites
   * are all either background refreshes the page does for itself, or a user action
   * on one of these controls:
   *
   *   run / stop / clear / confirm-clear .......... /api/run, /api/runs/<id>/cancel, /api/clear
   *   read-url ..................................... /api/read/url
   *   sessions list / save / load / delete ......... /api/sessions*
   *   add module / save providers / clear providers  /api/agents, /api/config
   *   export markdown / json ....................... /api/export/*
   *   unlock (the token submit) .................... /api/auth/login
   *
   * The three exports-and-clear group was missing until that audit: they looked
   * local (an export is just a download, clearing is just forgetting keys) while
   * both went through the server. Offline, they were enabled buttons that answered
   * with "Failed to export markdown".
   */
  assert.deepEqual(markedServerControls(), [
    "btnClear",
    "btnClearProviders",
    "btnConfirmClear",
    "btnExportJson",
    "btnExportMd",
    "btnNewAgent",
    "btnReadUrl",
    "btnRegisterAgent",
    "btnRun",
    "btnSaveProviders",
    "btnSaveSession",
    "btnSessions",
    "btnStop",
    "btnUnlock",
  ], "the server-bound set is a decision, not an accident - update this list deliberately");
});

test("offline: going offline disables exactly the marked controls, and nothing else", async () => {
  // Compared as a *delta* against the same page with the server up: some controls
  // are disabled for their own reasons (Export-all with an empty preset library,
  // the voice picker with no voices installed), and blaming those on the network
  // would be wrong in the other direction.
  const up = await loadClient();
  const down = await loadClient({ fetchFails: true });

  // 1. Every server-bound control is disabled while offline - including the ones
  //    that happened to be disabled already (Stop is idle-disabled with a server
  //    up, and stays so), which is why this is stated per control and not as a set
  //    difference.
  const down_disabled = disabledIds(down.win);
  for (const id of markedServerControls()) {
    assert.ok(down_disabled.includes(id), `${id} needs the server and must be disabled without it`);
  }
  // 2. Nothing outside that set is newly disabled by going offline.
  const newlyDisabled = down_disabled.filter((id) => !disabledIds(up.win).includes(id));
  assert.deepEqual(
    newlyDisabled.filter((id) => !markedServerControls().includes(id)),
    [],
    "the offline state may only disable controls the markup marks as needing the server",
  );
  // 3. And nothing is enabled by losing the server.
  const newlyEnabled = disabledIds(up.win).filter((id) => !down_disabled.includes(id));
  assert.deepEqual(newlyEnabled, [], "being offline must not enable anything");
});

test("offline: everything local keeps working while the server is down", async () => {
  const up = await loadClient();
  const { win } = await loadClient({ fetchFails: true });

  for (const id of LOCAL_CONTROLS) {
    const el = win.document.getElementById(id);
    assert.ok(el, `${id} should exist`);
    assert.equal(
      el.disabled,
      up.win.document.getElementById(id).disabled,
      `${id} does not need the server: losing it must change nothing about this control`,
    );
  }
  // And they still do something: the theme toggle flips the class on <html>.
  const before = win.document.documentElement.classList.contains("dark");
  win.document.getElementById("btnThemeToggle").click();
  assert.notEqual(win.document.documentElement.classList.contains("dark"), before, "the theme still switches");
});

test("offline: a disabled control says why, and points at the banner", async () => {
  const { win } = await loadClient({ fetchFails: true });

  const run = win.document.getElementById("btnRun");
  assert.equal(run.disabled, true);
  assert.match(run.getAttribute("aria-describedby") || "", /offlineBanner/, "screen readers reach the reason");
  assert.match(run.getAttribute("title") || "", /server/i, "and a hover explains it too");
});

test("offline: recovery gives the controls back, and run-state wins over ours", async () => {
  // The subtle case: btnStop is disabled while idle *anyway*. Recovery must not
  // blanket-enable it - it has to go back to whatever the run state says.
  const { win, socket } = await loadClient({ fetchFails: true });
  assert.equal(win.document.getElementById("btnStop").disabled, true);

  socket().onopen({});  // the server is back
  await settleFrames(win);

  assert.equal(bannerVisible(win), false, "the banner clears");
  assert.equal(win.document.getElementById("btnStop").disabled, true, "Stop is idle-disabled, not offline-disabled");
  assert.equal(win.document.getElementById("btnRun").disabled, false, "and Execute is usable again");
  assert.equal(win.document.getElementById("btnSessions").disabled, false);
  assert.equal(win.document.getElementById("btnRun").getAttribute("aria-describedby"), null, "the reason is gone too");
});

test("offline: session rows already on screen are disabled when the server goes away", async () => {
  // The real sequence: the user opens the Sessions panel while the server is up
  // (rows render, each is a server read), and the server then stops.
  const { win, calls, socket } = await loadClient({
    sessions: [{ id: "20260923-101010.000000-ab12cd", name: "earlier chat", saved_at: 1790000000, message_count: 4 }],
  });
  win.document.getElementById("btnSessions").click();
  await settleFrames(win);
  const rows = [...win.document.querySelectorAll("#sessionsList button")];
  assert.ok(rows.length > 0, "the panel has rows to begin with");

  calls.serverGone = true;
  socket().onclose({ code: 1006 });  // the server went away
  await settleFrames(win);

  assert.ok(bannerVisible(win), "the page noticed");
  for (const button of rows) {
    assert.notEqual(button.dataset.requiresServer, undefined, "a session row is a server read");
    assert.equal(button.disabled, true, `the row's ${button.getAttribute("aria-label")} must be disabled`);
  }
});

test("offline: the prompt box is never disabled - the user's typing is theirs", async () => {
  const { win } = await loadClient({ fetchFails: true });

  const box = win.document.getElementById("inputPrompt");
  assert.equal(box.disabled, false);
  assert.equal(box.readOnly, false);
  box.value = "a prompt I want to keep";
  assert.equal(box.value, "a prompt I want to keep", "and nothing rewrites it");
});

/*
 * Getting back on its feet.
 *
 * This is what the whole exercise is for: an outage that ends without the user's
 * help, and without the one thing that would cost them their work - a reload. The
 * socket's retry loop is the fast path while it lasts, but it gives up (it has
 * to, or a page left open overnight would dial forever); what must not give up is
 * the page. So the page keeps asking the server whether it is back, on a widening
 * delay, for as long as the outage lasts, and when the answer finally comes it
 * clears the banner, hands the controls back and re-opens the socket - in place,
 * with whatever the user has typed still in the prompt box.
 */

/** Run the page's queued work whatever its delay, and let its promises settle. */
async function tick(win, rounds = 6) {
  for (let i = 0; i < rounds; i++) {
    win.__runPendingTimers(200);
    await settleFrames(win, 3);
  }
}

/** jsdom's word for "something tried to navigate the page". */
const navigationErrors = (calls) => calls.jsdomErrors.filter((message) => /navigation/i.test(message));
const probes = (calls) => calls.fetch.filter((url) => String(url).includes("/api/status")).length;

test("offline: the page brings itself back when the server returns - no reload, prompt intact", async () => {
  const { win, calls, socket } = await loadClient();
  const box = win.document.getElementById("inputPrompt");
  box.value = "a half-written prompt I do not want to lose";

  calls.serverGone = true; // the server stops...
  socket().close(); // ...and the socket dies with it, readyState and all
  await tick(win, 14); // long enough that the socket's own retry budget is spent
  assert.ok(bannerVisible(win), "inside the outage the page says so");
  assert.equal(win.document.getElementById("btnRun").disabled, true);

  calls.serverGone = false; // and later - a minute, an hour, the user relaunches it
  await tick(win, 8);

  assert.equal(bannerVisible(win), false, "the banner clears by itself");
  assert.equal(win.document.getElementById("btnRun").disabled, false, "the controls come back");
  assert.equal(
    box.value,
    "a half-written prompt I do not want to lose",
    "and the user's typing is still there - this is why nothing reloads",
  );
  // A reload cannot be observed in the DOM - it would be a different page - so
  // this is caught at the door: jsdom reports *every* navigation as a jsdomError,
  // reload included, and the detector itself is pinned by the test below.
  assert.deepEqual(navigationErrors(calls), [], `nothing reloaded or navigated the page: ${calls.jsdomErrors.join(" | ")}`);
  assert.ok(calls.sockets >= 2, `the socket is re-dialled for the returned server (made ${calls.sockets})`);
  assert.match(win.document.getElementById("connectionStatus").textContent, /live mesh/i, "and the page is live again");
});

test("offline: it never tells the user to refresh, and it never gives up while the server is gone", async () => {
  const { win, calls, socket } = await loadClient();
  calls.serverGone = true;
  socket().close();

  await tick(win, 14); // the socket's retry budget is spent inside this window
  assert.ok(bannerVisible(win), "still down, and still saying so");
  assert.deepEqual(
    toasts(win).filter((text) => /refresh|reload/i.test(text)),
    [],
    `no "refresh the page" dead end: ${toasts(win).join(" | ")}`,
  );
  assert.doesNotMatch(
    win.document.getElementById("connectionStatus").textContent,
    /refresh/i,
    "and the status line does not ask for one either",
  );

  // The point: the page is still asking. A "give up" state means a server that
  // comes back ten minutes later is never noticed, and the banner becomes a lie.
  const before = probes(calls);
  await tick(win, 6);
  const after = probes(calls);
  assert.ok(after > before, `it keeps probing the server (${before} -> ${after})`);
  assert.ok(after - before <= 20, `at a sane rate, not in a spin (${after - before} probes in six rounds)`);

  calls.serverGone = false;
  await tick(win, 8);
  assert.equal(bannerVisible(win), false, "and an outage this long still ends by itself");
  assert.equal(win.document.getElementById("btnRun").disabled, false);
});

test("offline: the navigation detector the recovery test relies on actually fires", async () => {
  // The test above says "nothing reloaded the page", and that is only worth
  // anything if the detector can see a reload at all. jsdom refuses to let a test
  // replace location.reload ("Cannot redefine property"), so the net is jsdom's own
  // report of an attempted navigation - this proves it is a net, not a no-op.
  const { win, calls } = await loadClient();
  assert.deepEqual(navigationErrors(calls), [], "quiet to begin with");

  win.location.reload();
  await settleFrames(win, 3);

  assert.ok(navigationErrors(calls).length >= 1, `a reload must be visible here: ${calls.jsdomErrors.join(" | ")}`);
});

test("offline: recovery opens one socket, not a storm of them", async () => {
  const { win, calls, socket } = await loadClient();
  calls.serverGone = true;
  socket().close();
  await tick(win, 14);
  calls.serverGone = false;
  await tick(win, 6);

  assert.equal(bannerVisible(win), false, "the outage is over before sockets are counted");
  const live = calls.all.filter((s) => s.readyState === 1);
  assert.equal(live.length, 1, `exactly one live socket, got ${live.length} (of ${calls.all.length} ever opened)`);
  const opened = calls.all.length;
  await tick(win, 6);
  assert.equal(
    calls.all.length,
    opened,
    "and a settled page opens no more: the two recovery paths (socket retry, probe) do not fight",
  );
});

test("offline: a short outage leaves exactly one socket, not a leaked one behind", async () => {
  /*
   * The bug this pins: the socket's retry loop schedules its next attempt with a
   * bare setTimeout and nothing cancelled it. Recovery *before* that timer fires -
   * the ordinary case for a short outage, because the probe comes back at 1s while
   * the retry waits 1.5s - re-opens the socket by hand, and then the stale retry
   * opens a second one. The hand-opened socket is never closed: two live sockets,
   * and every message arriving twice.
   *
   * Advancing one timer at a time (rather than draining everything, then settling)
   * is what makes that timeline reachable here at all.
   */
  const { win, calls, socket } = await loadClient();
  const step = async () => {
    win.__runPendingTimers(1); // the next timer in delay order, whatever its delay
    await settleFrames(win, 3);
  };

  calls.serverGone = true;
  socket().close();
  await settleFrames(win, 3); // the page enters the outage and schedules both loops
  assert.ok(bannerVisible(win), "the page is in the outage state");

  calls.serverGone = false; // the server comes back before the first retry fires
  for (let i = 0; i < 4; i++) await step();

  const live = () => calls.all.filter((s) => s.readyState === 1);
  assert.equal(bannerVisible(win), false, "the outage is over");
  assert.equal(live().length, 1, `one live socket, got ${live().length}`);
  const opened = calls.all.length;
  for (let i = 0; i < 6; i++) await step();
  assert.equal(calls.all.length, opened, `no socket opens after recovery (${opened} -> ${calls.all.length})`);
  assert.equal(live().length, 1, "and the one on screen is the live one");
});


test("offline: a server that is not there does not open the token prompt", async () => {
  /*
   * The token prompt is the answer to "the server refused you", not to "the server
   * is not there" - and it used to open on both. A network failure asked the user
   * for a token they could not spend, on a dashboard that may not even need one
   * (loopback runs with auth off), and nothing ever closed the panel again: after
   * the server came back, the page sat there saying "Cannot reach the server - is it
   * still running?" over a working connection. The banner says the missing-server
   * story now, with the controls that come back with it, and this keeps the two
   * apart.
   */
  const { win, socket, calls } = await loadClient({ fetchFails: true });
  assert.ok(bannerVisible(win), "the banner is the one saying the server is gone");
  const panel = win.document.getElementById("authPanel");
  assert.ok(panel);
  assert.equal(panel.classList.contains("hidden"), true, "and the token prompt stays out of it");

  calls.serverGone = false; // the server is back; the page recovers on its own
  await tick(win, 8);
  assert.equal(bannerVisible(win), false, "recovered");
  assert.equal(panel.classList.contains("hidden"), true, "still no token prompt to dismiss");
  assert.match(panel.textContent, /token/i, "the panel is still the token panel for when a 401 does happen");
});


test("offline: a control that comes back from its own operation stays off while the server is gone", async () => {
  /*
   * A hole the marked set alone does not close: several handlers disable their own
   * button while the request is in flight and re-enable it in a `finally`. When the
   * server dies *during* that request, the button used to be switched back on by the
   * failure - leaving an enabled export button on an offline page, pointing at a
   * server that was no longer there.
   */
  const { win, calls, socket } = await loadClient();
  // The export needs a transcript, and the transcript arrives over the socket.
  socket().emit({
    type: "init",
    agents: [],
    history: [{ id: "m1", role: "user", content: "hello", sender: "You" }],
    authenticated: true,
  });
  await settleFrames(win, 4);

  let release;
  calls.exportGate = new Promise((resolve) => {
    release = resolve;
  });
  const exportBtn = win.document.getElementById("btnExportMd");
  exportBtn.click(); // in flight, so the button is disabled by its own handler
  await settleFrames(win, 3);
  assert.equal(exportBtn.disabled, true, "the export disables its button while it runs");

  calls.serverGone = true;
  socket().close();
  await tick(win, 3);
  assert.ok(bannerVisible(win), "the server dies mid-export");

  release(); // the request comes back as a network failure
  await settleFrames(win, 8);
  assert.equal(exportBtn.disabled, true, "and the failure must not switch it back on while the server is away");
  assert.match(exportBtn.getAttribute("title") || "", /server/i, "it still says why");

  calls.serverGone = false;
  await tick(win, 8);
  assert.equal(bannerVisible(win), false, "the page recovers");
  assert.equal(exportBtn.disabled, false, "and the button is usable again");
});
