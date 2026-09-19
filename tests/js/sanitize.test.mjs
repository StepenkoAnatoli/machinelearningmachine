/*
 * XSS regression tests for static/markdown.js, run under jsdom:
 *
 *     npm install && node --test tests/js/*.test.mjs

 * The glob is not decoration: `node --test tests/js/` fails outright
 * ("Cannot find module"), and a quoted pattern only works on node >= 21.
 *
 * These payloads are the shapes that used to slip past the old regex
 * "sanitizer" (event handlers without quotes, SVG/MathML wrappers, malformed
 * tags, odd casing, encoded schemes). If any of them ever renders as live
 * markup again, this test fails.
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

function read(p) {
  return readFileSync(p, "utf8");
}

/** Build a window with the same scripts the browser loads, in the same order. */
function makeWindow({ marked = true, dompurify = true } = {}) {
  const dom = new JSDOM(`<!doctype html><html><body><div id="root"></div></body></html>`, {
    runScripts: "dangerously",
  });
  const win = dom.window;
  if (marked) win.eval(read(join(staticDir, "vendor", "marked", "marked.min.js")));
  if (dompurify) win.eval(read(join(staticDir, "vendor", "dompurify", "purify.min.js")));
  win.eval(read(join(staticDir, "markdown.js")));
  return win;
}

const win = makeWindow();
const R = () => win.MeshRender;

function render(content) {
  return R().renderToString(content);
}

/** The rendered DOM - the thing the browser actually executes. */
function renderDom(content) {
  const holder = win.document.createElement("div");
  holder.appendChild(R().render(content));
  return holder;
}

function tagsIn(holder) {
  return Array.from(holder.querySelectorAll("*")).map((el) => el.tagName.toLowerCase());
}

function handlerAttrsIn(holder) {
  const hits = [];
  Array.from(holder.querySelectorAll("*")).forEach((el) => {
    Array.from(el.attributes).forEach((attr) => {
      if (/^on[a-z]+$/i.test(attr.name)) hits.push(`${el.tagName.toLowerCase()}[${attr.name}]`);
    });
  });
  return hits;
}

test("the module exposes the expected surface", () => {
  assert.equal(typeof win.MeshRender.render, "function");
  assert.equal(typeof win.MeshRender.renderToString, "function");
  assert.equal(win.MeshRender.sanitizerAvailable(), true);
});

test("markdown still renders normally", () => {
  const html = render("## Title\n\nSome **bold** text and `code`.\n\n- one\n- two\n\n```python\nprint('hi')\n```");
  assert.match(html, /<h2[^>]*>Title<\/h2>/);
  assert.match(html, /<strong>bold<\/strong>/);
  assert.match(html, /<code>code<\/code>/);
  assert.match(html, /<li>one<\/li>/);
  assert.match(html, /print\(&#39;hi&#39;\)|print\('hi'\)/);
});

test("script tags are removed", () => {
  for (const payload of [
    "<script>alert(1)</script>",
    "<ScRiPt>alert(1)</ScRiPt>",
    "<script src=https://evil.example/x.js></script>",
    "text<img src=x onerror=alert(1)>",
    "<script\n>alert(1)</script\n>",
  ]) {
    const html = render(payload);
    assert.ok(!/<script/i.test(html), `script survived: ${payload} -> ${html}`);
    assert.ok(!/alert\(1\)/i.test(html) || !/onerror/i.test(html), `handler survived: ${html}`);
  }
});

test("event handler attributes never survive, quoted or not", () => {
  const payloads = [
    '<img src="x" onerror="alert(1)">',
    "<img src=x onerror=alert(1)>",
    "<div ONLOAD='alert(1)'>hi</div>",
    '<a href="#" onclick="alert(1)">click</a>',
    '<p onmouseover = "alert(1)">hover</p>',
    '<svg><animate onbegin="alert(1)" attributeName="x" dur="1s"></animate></svg>',
    '<math><maction actiontype="statusline#" xlink:href="javascript:alert(1)">x</maction></math>',
    "<iframe srcdoc=\"<script>alert(1)</script>\"></iframe>",
    '<form><button formaction="javascript:alert(1)">go</button></form>',
    '<object data="javascript:alert(1)"></object>',
    '<embed src="javascript:alert(1)">',
    '<body onload="alert(1)">x',
    '<template><script>alert(1)</script></template>',
    '<noscript><p title="</noscript><img src=x onerror=alert(1)>"></p></noscript>',
    '<svg><foreignObject><div onclick="alert(1)">x</div></foreignObject></svg>',
  ];
  for (const payload of payloads) {
    const holder = renderDom(payload);
    assert.deepEqual(handlerAttrsIn(holder), [], `handler attribute survived: ${payload}`);
    const dangerous = tagsIn(holder).filter((t) =>
      ["script", "iframe", "object", "embed", "svg", "math", "template", "noscript", "form", "animate", "foreignobject"].includes(t));
    assert.deepEqual(dangerous, [], `dangerous element survived: ${payload} -> ${holder.innerHTML}`);
    assert.ok(!/javascript:/i.test(holder.innerHTML), `javascript: URL survived: ${payload}`);
  }
});

test("hostile URLs are stripped from links", () => {
  const cases = [
    "[click](javascript:alert(1))",
    "[click](JaVaScRiPt:alert(1))",
    "[click](java\tscript:alert(1))",
    "[click](vbscript:msgbox(1))",
    "[click](data:text/html;base64,PHNjcmlwdD5hbGVydCgxKTwvc2NyaXB0Pg==)",
    "[click](file:///etc/passwd)",
  ];
  for (const md of cases) {
    const html = render(md);
    assert.ok(!/javascript:|vbscript:|data:text\/html|file:\/\//i.test(html), `${md} -> ${html}`);
  }
  const ok = render("[site](https://example.com/doc)");
  assert.match(ok, /href="https:\/\/example\.com\/doc"/);
  assert.match(ok, /rel="[^"]*noopener/);
  assert.match(ok, /target="_blank"/);
});

test("style attributes and images cannot fetch anything", () => {
  const html = render('<p style="background:url(javascript:alert(1))">x</p>\n\n![alt](https://evil.example/pixel.png)');
  assert.ok(!/style=/i.test(html), html);
  assert.ok(!/src=/i.test(html), html);
  assert.match(html, />x</);
});

test("malformed markup is neutralised rather than trusted", () => {
  const payloads = [
    "<img src=x onerror=alert(1)//>",
    "< svg/onload=alert(1)>",
    "<<script>alert(1)</script>",
    "<img/src=\"x\"onerror=\"alert(1)\">",
    "\u0000<script>alert(1)</script>",
    "<a href=\"jav\u0000ascript:alert(1)\">x</a>",
  ];
  for (const payload of payloads) {
    const holder = renderDom(payload);
    assert.deepEqual(handlerAttrsIn(holder), [], `handler survived: ${payload}`);
    assert.ok(!tagsIn(holder).includes("script"), `script survived: ${payload}`);
    assert.ok(!/<script/i.test(holder.innerHTML), `script markup survived: ${payload}`);
  }
});

test("render() returns DOM nodes, not a string", () => {
  const frag = R().render("**bold**");
  assert.equal(frag.nodeType, 11); // DOCUMENT_FRAGMENT_NODE
  assert.match(frag.textContent, /bold/);
});

test("without a sanitizer the renderer escapes instead of injecting", () => {
  const bare = makeWindow({ marked: true, dompurify: false });
  assert.equal(bare.MeshRender.sanitizerAvailable(), false);
  const html = bare.MeshRender.renderToString("<img src=x onerror=alert(1)>");
  // The payload must survive only as escaped text, never as an element.
  assert.ok(!/<img\b/i.test(html), `raw markup was emitted without DOMPurify: ${html}`);
  assert.match(html, /&lt;img/);
});

test("without any vendor library at all it still degrades safely", () => {
  const none = makeWindow({ marked: false, dompurify: false });
  const html = none.MeshRender.renderToString("# hello <script>alert(1)</script>");
  assert.ok(!/<script\b/i.test(html), html);
  assert.match(html, /hello/);
});

test("colours and avatars are validated before they reach the DOM", () => {
  assert.equal(R().safeColor("#06b6d4"), "#06b6d4");
  assert.equal(R().safeColor('#effefe" onmouseover="alert(1)'), "#8b5cf6");
  assert.equal(R().safeColor(null, "#111111"), "#111111");

  assert.equal(R().safeAvatar("🦾", "🤖"), "🦾");
  assert.equal(R().safeAvatar('<img src=x onerror=alert(1)>', "🤖"), "🤖");
  assert.equal(R().safeAvatar('🤖" onload="x', "🤖"), "🤖");
  assert.equal(R().safeAvatar(undefined, "🤖"), "🤖");
});

test("paintChip writes through the CSSOM only for a valid colour", () => {
  const d = win.document;
  const el = d.createElement("div");
  R().paintChip(el, 'red; background-image: url(javascript:alert(1))', "20");
  const css = el.getAttribute("style") || "";
  // No payload fragment may reach the style attribute...
  assert.ok(!/url\(/i.test(css), `injection reached the style attribute: ${css}`);
  assert.ok(!/javascript:|expression\(|alert/i.test(css), `injection reached the style attribute: ${css}`);
  // ...and the fallback colour (safe #8b5cf6) is what got applied instead.
  assert.match(el.style.getPropertyValue("background-color"), /139,\s*92,\s*246/);
});

test("the app source itself keeps untrusted strings out of innerHTML", () => {
  const app = read(join(staticDir, "app.js"));
  // Message bodies must go through MeshRender, and no template may interpolate
  // agent-controlled fields (name/role/avatar/color/content) into HTML.
  assert.match(app, /MeshRender\.render\(content\)|renderMarkdownFragment\(content\)/);
  assert.doesNotMatch(app, /innerHTML\s*=\s*[^;]*\$\{\s*(msg\.content|ag\.avatar|agentObj\.avatar|ag\.name|ag\.color)/);
  assert.doesNotMatch(app, /marked\.parse/); // only markdown.js may call marked
  assert.doesNotMatch(app, /replace\(\/on\\w\+/); // the old regex "sanitizer" must stay gone
});

test("no user data is ever concatenated into HTML by the app", () => {
  // The invariant that makes per-field checks unnecessary: every innerHTML
  // assignment in app.js receives literal markup only, so untrusted values can
  // reach the DOM solely through textContent / setAttribute / MeshRender.
  const app = read(join(staticDir, "app.js"));

  const offenders = [];
  for (const match of app.matchAll(/\.innerHTML\s*=\s*([\s\S]{0,400}?);\n/g)) {
    if (match[1].includes("${")) offenders.push(match[1].replace(/\s+/g, " ").slice(0, 80));
  }
  assert.deepEqual(offenders, [], "innerHTML must receive literal markup only");

  // Interpolating into an attribute is the classic breakout - one unescaped
  // quote in a saved session name and the markup is yours. Attributes that
  // carry data must be set through setAttribute/dataset instead.
  const attrs = app.match(/(data-[a-z-]+|aria-[a-z-]+|title)="[^"]*\$\{/g) || [];
  assert.deepEqual(attrs, [], `interpolated attribute values: ${attrs.slice(0, 3)}`);

  // No hand-rolled escaper may come back: the whole point of markdown.js is
  // that escaping is one audited module with a test corpus.
  assert.doesNotMatch(app, /function escapeHtml|escapeAttribute|sanitiz\w*\s*=\s*\(text/);
  assert.doesNotMatch(app, /insertAdjacentHTML|document\.write|createContextualFragment/);
});

test("toasts and session rows are built from DOM nodes, not strings", () => {
  const app = read(join(staticDir, "app.js"));

  // Toasts quote provider errors and server details - all untrusted text.
  const toast = app.slice(app.indexOf("function showToast"), app.indexOf("function dismissToast"));
  assert.ok(toast.length > 100, "showToast should be found");
  assert.doesNotMatch(toast, /innerHTML/, "showToast must not build HTML from a message");
  assert.match(toast, /label\.textContent\s*=\s*String\(message/);
  // The class name comes from a fixed key lookup, never from the caller's string.
  assert.match(toast, /Object\.prototype\.hasOwnProperty\.call\(icons, type\)/);

  // Saved-session rows carry names that come from a file on disk.
  const rowsStart = app.indexOf("sessions.forEach((s) =>");
  const rows = app.slice(rowsStart, app.indexOf("async function deleteSession", rowsStart) > -1
    ? app.indexOf("async function deleteSession", rowsStart)
    : rowsStart + 4000);
  assert.ok(rows.length > 100, "the session list renderer should be found");
  assert.doesNotMatch(rows, /innerHTML/, "session rows must be built with DOM APIs");
  assert.match(rows, /nameEl\.textContent\s*=\s*String\(s\.name/);
  assert.match(rows, /btn\.dataset\.id\s*=\s*String\(s\.id/);
});
