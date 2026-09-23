/*
 * Does the dashboard really open when the server is not running - and does it
 * really come back by itself when the server returns?
 *
 * The jsdom suites answer that with doubles: a fake socket, a fetch that throws on
 * command, a clock the test advances by hand. That is the right way to test the
 * state machine, and it is not the same thing as watching the real page talk to a
 * real server over a real socket. This script does the second one, which is why it
 * exists separately from `tests/js/offline.test.mjs`:
 *
 *   1. start a real server on a port this script has confirmed is free,
 *   2. load the real index.html + app.js in jsdom, with real fetch, real WebSocket
 *      and real timers (no fakes at all),
 *   3. kill the server, and check the page says so and greys out what cannot work,
 *   4. start it again, and check the page recovers on its own - with the prompt text
 *      intact and nothing having navigated or reloaded.
 *
 * It runs the outage twice, because the two lengths exercise different code paths:
 *
 *   - a *fast* restart (2s), where the offline probe notices the returning server
 *     while the socket's own retry is still pending - the race the page has to win
 *     without leaving a second socket open (this is the one that caught the leak);
 *   - a *longer* outage (default 8s), the ordinary "I relaunched it a moment later"
 *     case, where the socket's retry budget is being spent while the probe carries on.
 *
 * Run it by hand, like the e2e pass (it takes ~40s and stops a server on purpose):
 *
 *     npm ci                                   # jsdom
 *     node scripts/offline_recovery_check.mjs
 *     node scripts/offline_recovery_check.mjs --port 8901 --outage-seconds 30
 *
 * It is deliberately not part of CI: it kills and restarts a server process, and a
 * red build from a port race or a slow machine would not mean the product is broken.
 */
import { spawn } from "node:child_process";
import { createServer } from "node:net";
import { existsSync, readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const repoRoot = join(here, "..");
const staticDir = join(repoRoot, "machinelearningmachine", "server", "static");

const args = process.argv.slice(2);
const flag = (name, fallback) => {
  const i = args.indexOf(`--${name}`);
  return i === -1 ? fallback : args[i + 1];
};
const OUTAGE_SECONDS = Number(flag("outage-seconds", 20));
const RECOVERY_TIMEOUT_MS = 45000;
const PYTHON = flag("python", existsSync(join(repoRoot, ".venv", "bin", "python")) ? join(repoRoot, ".venv", "bin", "python") : "python3");

let JSDOM;
let VirtualConsole;
try {
  ({ JSDOM, VirtualConsole } = await import("jsdom"));
} catch (err) {
  console.error("jsdom is not installed - run `npm ci` first (it is a devDependency of this repo).");
  console.error(`(${err.message})`);
  process.exit(2);
}

const checks = [];
const check = (ok, what) => {
  checks.push({ ok, what });
  console.log(`${ok ? "  PASS" : "  FAIL"}  ${what}`);
};
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

/** True when nothing is listening: the trap this script fell into its first time. */
function portIsFree(port) {
  return new Promise((resolve) => {
    const probe = createServer();
    probe.once("error", () => resolve(false));
    probe.once("listening", () => probe.close(() => resolve(true)));
    probe.listen(port, "127.0.0.1");
  });
}

async function firstFreePort(start) {
  for (let port = start; port < start + 50; port++) {
    if (await portIsFree(port)) return port;
  }
  throw new Error(`no free port between ${start} and ${start + 49}`);
}

async function answers(port) {
  try {
    const resp = await fetch(`http://127.0.0.1:${port}/health`);
    return resp.ok;
  } catch {
    return false;
  }
}

async function waitFor(what, predicate, timeoutMs) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (await predicate()) return true;
    await sleep(250);
  }
  console.log(`  ....  timed out waiting for: ${what}`);
  return false;
}

let server = null;
async function startServer(port) {
  server = spawn(PYTHON, ["-m", "machinelearningmachine", "serve", "--host", "127.0.0.1", "--port", String(port), "--allow-public"], {
    cwd: repoRoot,
    stdio: ["ignore", "pipe", "pipe"],
  });
  let stderr = "";
  server.stderr.on("data", (chunk) => {
    stderr += String(chunk);
  });
  server.on("exit", (code) => {
    if (code) console.log(`  ....  server exited ${code}: ${stderr.trim().split("\n").slice(-1)[0] || "(no message)"}`);
  });
  const up = await waitFor(`the server on ${port} to answer /health`, () => answers(port), 25000);
  if (!up) {
    // A server that failed to bind looks exactly like one that was killed, unless
    // its own words are printed - and then the "offline" checks would all pass.
    console.error(stderr.trim() || "(the server printed nothing)");
    throw new Error(`the server never started on ${port}`);
  }
}

async function stopServer(port) {
  if (!server) return;
  const pid = server.pid;
  server.kill("SIGKILL");
  await new Promise((resolve) => server.once("exit", resolve));
  server = null;
  // Confirmed gone: if something else held the port, the checks below would be
  // measuring the wrong process entirely.
  const stillThere = await waitFor("the port to stop answering", async () => (await answers(port)) === false, 10000);
  if (!stillThere) throw new Error(`something is still answering on ${port} after killing pid ${pid}`);
}

const main = async () => {
  console.log(`\n== offline -> recovery, against a real server (${PYTHON}) ==`);
  const port = Number(flag("port", 0)) || (await firstFreePort(8800));
  if (!(await portIsFree(port))) {
    console.error(`port ${port} is already in use - pass --port with a free one.`);
    process.exit(2);
  }
  console.log(`  port ${port} (checked free)`);

  await startServer(port);

  const html = readFileSync(join(staticDir, "index.html"), "utf8")
    .replace(/<script[^>]*\ssrc=[^>]*><\/script>/gi, "")
    .replace(/<link[^>]*>/gi, "");
  const navigations = [];
  const virtualConsole = new VirtualConsole();
  virtualConsole.on("jsdomError", (err) => navigations.push(String((err && err.message) || err)));
  virtualConsole.sendTo(console, { omitJSDOMErrors: true });
  //: jsdom refuses to let a script replace location.reload, so "did it reload?" is
  //: answered by jsdom's own report of an attempted navigation.
  const dom = new JSDOM(html, {
    url: `http://127.0.0.1:${port}/`,
    runScripts: "outside-only",
    pretendToBeVisual: true,
    virtualConsole,
    beforeParse(win) {
      win.fetch = (url, opts) => fetch(new URL(String(url), win.location.href), opts);
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
      win.eval(readFileSync(join(staticDir, "markdown.js"), "utf8"));
      win.eval(readFileSync(join(staticDir, "app.js"), "utf8"));
    },
  });
  const win = dom.window;
  const $ = (id) => win.document.getElementById(id);
  const bannerUp = () => {
    const el = $("offlineBanner");
    return Boolean(el && !el.hidden && !el.classList.contains("hidden"));
  };
  const socketOpen = () => Boolean(win.__lastSocket && win.__lastSocket.readyState === 1);

  // Track every socket the page opens, so "exactly one" can be asserted live.
  win.__sockets = [];
  const RealSocket = win.WebSocket;
  win.WebSocket = class extends RealSocket {
    constructor(url) {
      super(url);
      win.__lastSocket = this;
      win.__sockets.push(this);
    }
  };

  const PROMPT = "a prompt typed before the outage";
  const liveSockets = () => win.__sockets.filter((s) => s.readyState === 1).length;

  const baseline = async (label) => {
    console.log(`\n-- ${label}: the server is up --`);
    check(await waitFor("the socket to open", () => socketOpen(), 20000), "the page connects to the real server");
    check(!bannerUp(), "no offline banner while the server answers");
    check($("btnRun").disabled === false, "Execute is enabled");
    $("inputPrompt").value = PROMPT;
  };

  const outage = async (seconds, label) => {
    console.log(`\n-- ${label}: killing the server --`);
    await stopServer(port);
    check(await waitFor("the banner to appear", () => bannerUp(), 20000), "the page notices on its own and shows the banner");
    check($("btnRun").disabled === true, "Execute is disabled, with the banner explaining why");
    check($("inputPrompt").value === PROMPT, "the half-written prompt is still there");
    check($("inputPrompt").disabled === false, "and the prompt box is not disabled - it does not need the server");
    const banner = $("offlineBanner").textContent.replace(/\s+/g, " ").trim();
    check(/server/i.test(banner), `the banner names the missing thing: "${banner.slice(0, 60)}…"`);
    console.log(`\n-- ${label}: staying offline for ${seconds}s --`);
    await sleep(seconds * 1000);
    check(!/refresh|reload/i.test($("connectionStatus").textContent), `no "refresh the page" dead end (${JSON.stringify($("connectionStatus").textContent.trim())})`);
    check(bannerUp(), "still offline, still saying so");
  };

  const recovery = async (label, quietSeconds) => {
    console.log(`\n-- ${label}: starting the server again --`);
    await startServer(port);
    check(await waitFor("the banner to clear", () => !bannerUp(), RECOVERY_TIMEOUT_MS), "the page recovers by itself, with no reload");
    check($("btnRun").disabled === false, "Execute is usable again");
    check($("inputPrompt").value === PROMPT, "the prompt text survived the whole outage");
    check(navigations.length === 0, `nothing navigated or reloaded the page${navigations.length ? `: ${navigations.join(" | ")}` : ""}`);
    check(await waitFor("the socket to open again", () => socketOpen(), 20000), "the socket is genuinely open again (not just a pill saying so)");
    // Quiet period longer than the socket's maximum retry delay: a retry timer left
    // over from the outage would fire inside this window and open a second socket.
    const opened = win.__sockets.length;
    await sleep(quietSeconds * 1000);
    check(liveSockets() === 1, `exactly one live socket, none leaked (${liveSockets()} live of ${win.__sockets.length} opened)`);
    check(win.__sockets.length === opened, `no socket opens after recovery (${opened} opened then, ${win.__sockets.length} now)`);
  };

  await baseline("cycle 1");
  await outage(2, "cycle 1 - fast restart");
  await recovery("cycle 1", 6);

  await baseline("cycle 2");
  await outage(Number(flag("outage-seconds", 8)), "cycle 2 - longer outage");
  await recovery("cycle 2", 6);

  await stopServer(port);

  const failed = checks.filter((c) => !c.ok);
  console.log(`\n${failed.length ? `${failed.length} CHECK(S) FAILED` : `ALL OFFLINE/RECOVERY CHECKS PASSED (${checks.length})`}\n`);
  process.exit(failed.length ? 1 : 0);
};

try {
  await main();
} catch (err) {
  if (server) server.kill("SIGKILL");
  console.error(`\nTHE CHECK COULD NOT RUN: ${err.message}\n`);
  process.exit(2);
}
