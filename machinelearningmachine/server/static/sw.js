/*
 * Service worker: the dashboard's shell survives the server being down.
 *
 * What this file is, and is not:
 *
 *   It caches the *shell* - the HTML, the scripts, the styles, the vendored
 *   libraries, the icons - so that opening the dashboard with the server stopped
 *   shows the dashboard (and, from the P3 work, an honest banner) instead of the
 *   browser's connection error. It never caches a thing the server produced:
 *   no /api/ response, no transcript, no session. That is the line: with the
 *   server down there is no mesh, no queue and no providers, so offline can only
 *   mean "the page still explains itself", never "the page still works".
 *
 * Why network-first, with no artificial timeout:
 *
 *   A slow-but-alive server must always beat a cached copy. Serving a stale
 *   app.js while the real one is answering is the failure mode that makes people
 *   distrust a PWA, and this repository has no hashed filenames and no build step
 *   to fall back on. So: ask the network, and only when it fails reach for the
 *   cache - and never with a race against a timer.
 *
 * Scope: this worker is registered from /, so it sees the whole origin. Anything
 * that is not a same-origin GET for a shell URL is left strictly alone.
 *
 * Tests: tests/js/service-worker.test.mjs evaluates this file in a worker-shaped
 * sandbox - the shell list below is drift-locked against the files that ship, and
 * /api/ + /ws are pinned as never-cached.
 */
"use strict";

/** Bump when the shell's *shape* changes; activate() deletes every other name. */
const CACHE_NAME = "mlm-shell-v1";

/*
 * The shell, as the server serves it: most files live behind /static, while the
 * document, the manifest and the favicon are root routes. Deliberately absent:
 * sw.js itself (a worker that precaches itself cannot update itself),
 * vendor/licenses/* (legal text, never requested at runtime) and
 * vendor/MANIFEST.json (build metadata).
 */
const PRECACHE = [
  "/",
  "/manifest.webmanifest",
  "/favicon.ico",
  "/static/style.css",
  "/static/app.js",
  "/static/theme.js",
  "/static/presets.js",
  "/static/markdown.js",
  "/static/vendor/tailwind.css",
  "/static/vendor/fontawesome/css/all.min.css",
  "/static/vendor/fontawesome/webfonts/fa-solid-900.woff2",
  "/static/vendor/fontawesome/webfonts/fa-regular-400.woff2",
  "/static/vendor/marked/marked.min.js",
  "/static/vendor/dompurify/purify.min.js",
  "/static/vendor/highlight.js/highlight.min.js",
  "/static/vendor/highlight.js/styles/atom-one-dark.min.css",
  "/static/icons/icon.svg",
  "/static/icons/icon-192.png",
  "/static/icons/icon-512.png",
  "/static/icons/apple-touch-icon.png",
];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches
      .open(CACHE_NAME)
      // `cache: "reload"` bypasses the HTTP cache: a shell file that is already
      // stale in the browser's cache must not be the copy we keep for offline.
      .then((cache) => cache.addAll(PRECACHE, { cache: "reload" }))
      .then(() => self.skipWaiting()),
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((names) => Promise.all(names.filter((name) => name !== CACHE_NAME).map((name) => caches.delete(name))))
      .then(() => self.clients.claim()),
  );
});

/** Is this request one of ours to answer? */
function isShellRequest(request) {
  if (request.method !== "GET") {
    return false; // never answer a write from a cache of reads
  }
  let url;
  try {
    url = new URL(request.url);
  } catch (error) {
    return false;
  }
  if (url.origin !== self.location.origin) {
    return false; // other origins are not ours
  }
  // The server's own answers - live state, transcripts, sessions - are exactly
  // what must never be cached. They pass straight through, untouched.
  return !(url.pathname.startsWith("/api/") || url.pathname === "/ws" || url.pathname.startsWith("/ws/"));
}

/** A navigation is a document request: `mode` in a worker, Accept in a test. */
function isDocument(request) {
  if (request.mode === "navigate") {
    return true;
  }
  const accept = (request.headers && request.headers.get && request.headers.get("accept")) || "";
  return accept.includes("text/html");
}

self.addEventListener("fetch", (event) => {
  const request = event.request;
  if (!isShellRequest(request)) {
    return; // the browser does what it always did
  }

  event.respondWith(
    fetch(request).catch(() =>
      caches.open(CACHE_NAME).then((cache) =>
        cache.match(request).then((hit) => {
          if (hit) {
            return hit;
          }
          // A deep link was never precached; the shell can still answer it.
          // (No client-side routing exists today, but a bookmarked path must not
          // become a blank screen just because the server is down.)
          return isDocument(request) ? cache.match("/") : undefined;
        }),
      ),
    ).then((response) => response || Response.error()),
  );
});
