/*
 * Light/dark theme tests: the theme module (static/theme.js), its wiring in the
 * dashboard, and the drift lock that keeps the hand-written light layer in
 * style.css honest.
 *
 * Why the drift lock exists: the dashboard's colours are absolute dark-palette
 * Tailwind utilities (`bg-slate-950`, `text-slate-100`, …), and light mode is a
 * second, hand-written layer of overrides keyed on `html:not(.dark)`. Nothing
 * in the build links the two, so a new dark-only utility would silently render
 * as dark-on-light in light mode. The last test here walks every colour token
 * the markup actually uses and demands it be either overridden for light mode
 * or listed as deliberately theme-neutral.
 *
 *     npm install && node --test tests/js/*.test.mjs
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
const HTML = read(join(staticDir, "index.html"));
const APP_JS = read(join(staticDir, "app.js"));
const STYLE_CSS = read(join(staticDir, "style.css"));

/** A window with a settable OS preference and a settable storage. */
function themeWindow({ stored = null, prefersLight = false, storageBroken = false } = {}) {
  const dom = new JSDOM(
    `<!doctype html><html class="dark"><head></head><body>
       <button type="button" id="btnThemeToggle" aria-pressed="false" aria-label="Light theme">
         <i class="fa-solid fa-moon" aria-hidden="true"></i>
       </button>
     </body></html>`,
    { runScripts: "dangerously", url: "http://localhost/" },
  );
  const win = dom.window;
  win.matchMedia = (query) => ({
    matches: query.includes("prefers-color-scheme: light") ? prefersLight : false,
    media: query,
    addEventListener() {},
    removeEventListener() {},
  });
  if (stored !== null) {
    win.localStorage.setItem("mesh.theme", stored);
  }
  if (storageBroken) {
    Object.defineProperty(win, "localStorage", {
      get() {
        throw new Error("storage is blocked in this browser");
      },
    });
  }
  win.eval(read(join(staticDir, "theme.js")));
  return { win, doc: win.document, api: win.MLMTheme };
}

const isDark = (win) => win.document.documentElement.classList.contains("dark");

// -------------------------------------------------------------- resolution

test("theme: no stored choice and a dark OS means dark", () => {
  const { win, api } = themeWindow(); // no stored value, no light preference
  assert.equal(api.KEY, "mesh.theme");
  assert.equal(api.current(), "dark");
  assert.equal(isDark(win), true);
  assert.equal(win.document.documentElement.dataset.theme, "dark");
});

test("theme: no stored choice and a light OS means light", () => {
  const { win, api } = themeWindow({ prefersLight: true });
  assert.equal(api.current(), "light");
  assert.equal(isDark(win), false);
  assert.equal(win.document.documentElement.dataset.theme, "light");
});

test("theme: a stored choice wins over the OS, in both directions", () => {
  const darkOnLightOs = themeWindow({ stored: "dark", prefersLight: true });
  assert.equal(darkOnLightOs.api.current(), "dark");
  assert.equal(isDark(darkOnLightOs.win), true);

  const lightOnDarkOs = themeWindow({ stored: "light", prefersLight: false });
  assert.equal(lightOnDarkOs.api.current(), "light");
  assert.equal(isDark(lightOnDarkOs.win), false);
});

test("theme: junk in storage is ignored, not obeyed", () => {
  const { win, api } = themeWindow({ stored: "midnight-neon" });
  assert.equal(api.current(), "dark"); // unknown value falls back to the OS/default
  assert.equal(isDark(win), true);
});

test("theme: blocked storage still yields a working theme", () => {
  const { win, api } = themeWindow({ prefersLight: true, storageBroken: true });
  assert.equal(api.current(), "light");
  assert.equal(isDark(win), false);
  assert.equal(api.set("dark"), "dark"); // set() must not throw either
  assert.equal(isDark(win), true);
});

// ------------------------------------------------------------------ set()

test("theme: set() flips the class, data-theme and the stored value", () => {
  const { win, api } = themeWindow();
  assert.equal(api.set("light"), "light");
  assert.equal(win.localStorage.getItem("mesh.theme"), "light");
  assert.equal(isDark(win), false);
  assert.equal(win.document.documentElement.dataset.theme, "light");
  assert.equal(api.set("dark"), "dark");
  assert.equal(win.localStorage.getItem("mesh.theme"), "dark");
  assert.equal(isDark(win), true);
});

test("theme: palette() follows the theme and pins both canvases", () => {
  const { api } = themeWindow();
  assert.deepEqual(native(api.palette("dark")), {
    edge: "rgba(51, 65, 85, 0.4)",
    nodeFill: "rgba(15, 23, 42, 0.8)",
    nodeLabel: "#cbd5e1",
  });
  assert.deepEqual(native(api.palette("light")), {
    edge: "rgba(148, 163, 184, 0.55)",
    nodeFill: "rgba(255, 255, 255, 0.9)",
    nodeLabel: "#334155",
  });
  assert.deepEqual(native(api.palette()), native(api.palette("dark")), "no argument = the current theme");
});

function native(v) {
  return JSON.parse(JSON.stringify(v));
}

// ----------------------------------------------------------------- mount()

test("theme: mount() wires the button, reports state and notifies on change", () => {
  const { win, doc, api } = themeWindow();
  const changes = [];
  assert.equal(api.mount({ onChange: (theme) => changes.push(theme) }), true);
  const btn = doc.getElementById("btnThemeToggle");
  assert.equal(btn.getAttribute("aria-pressed"), "false", "dark: the 'Light theme' toggle is not pressed");
  assert.equal(btn.querySelector("i").className, "fa-solid fa-moon");

  btn.click();
  assert.equal(api.current(), "light");
  assert.equal(win.localStorage.getItem("mesh.theme"), "light");
  assert.equal(isDark(win), false);
  assert.equal(btn.getAttribute("aria-pressed"), "true");
  assert.equal(btn.querySelector("i").className, "fa-solid fa-sun");
  assert.deepEqual(changes, ["light"]);

  btn.click();
  assert.equal(api.current(), "dark");
  assert.equal(btn.getAttribute("aria-pressed"), "false");
  assert.equal(btn.querySelector("i").className, "fa-solid fa-moon");
  assert.deepEqual(changes, ["light", "dark"]);
});

test("theme: mount() is idempotent and fails soft without the button", () => {
  const { win, doc, api } = themeWindow();
  api.mount();
  api.mount();
  doc.getElementById("btnThemeToggle").click();
  assert.equal(api.current(), "light", "one listener per click, not two double-flips");

  const bare = new JSDOM(`<!doctype html><html class="dark"><body></body></html>`, {
    runScripts: "dangerously",
    url: "http://localhost/",
  });
  bare.window.eval(read(join(staticDir, "theme.js")));
  assert.equal(bare.window.MLMTheme.mount(), false);
  assert.equal(bare.window.MLMTheme.set("light"), "light", "the module still works without the button");
});

// ----------------------------------------------------------- the document

test("theme: index.html applies the theme in the head, before anything renders", () => {
  const headEnd = HTML.indexOf("</head>");
  const themeScript = HTML.indexOf('<script src="/static/theme.js"></script>');
  assert.notEqual(themeScript, -1, "theme.js must be loaded");
  assert.ok(themeScript < headEnd, "it must load in the head: a theme applied after first paint flashes");
  assert.ok(
    themeScript < HTML.indexOf('<script src="/static/app.js"'),
    "and before app.js, which only wires the button",
  );
  assert.ok(HTML.includes('<html lang="en" class="dark">'), "no-JS default stays dark");
  const toggle = HTML.indexOf('id="btnThemeToggle"');
  assert.notEqual(toggle, -1, "the toggle button must exist");
  const toggleTag = HTML.slice(HTML.lastIndexOf("<button", toggle), HTML.indexOf(">", toggle) + 1);
  assert.ok(toggleTag.includes('type="button"'), "the toggle can never submit anything");
  assert.ok(toggleTag.includes('aria-pressed="false"'), "it starts unpressed (dark)");
  assert.ok(toggleTag.includes('aria-label="Light theme"'), "it is labelled as the light-theme toggle");
  assert.ok(HTML.slice(toggle, HTML.indexOf("</button>", toggle)).includes("fa-moon"), "the icon shows the current state");
});

test("theme: app.js wires the toggle and repaints the canvas through MLMTheme", () => {
  assert.ok(APP_JS.includes("MLMTheme.mount("), "app.js should mount the theme module");
  assert.ok(APP_JS.includes("MLMTheme.palette("), "the canvas palette comes from the theme module");
  assert.ok(APP_JS.includes("requestRedraw(true)"), "a theme change must force a canvas repaint");
  // The palette must not be re-hardcoded next to the drawing code.
  assert.ok(!APP_JS.includes('ctx.strokeStyle = "rgba(51, 65, 85, 0.4)"'), "edge colour must come from the palette");
  assert.ok(!APP_JS.includes('ctx.fillStyle = "rgba(15, 23, 42, 0.8)"'), "node fill must come from the palette");
  assert.ok(!APP_JS.includes('ctx.fillStyle = "#cbd5e1"'), "node label colour must come from the palette");
});

// -------------------------------------------------------------- drift lock

const PALETTE_SHADES =
  "slate|gray|zinc|neutral|stone|red|orange|amber|yellow|lime|green|emerald|teal|cyan|sky|blue|indigo|violet|purple|fuchsia|pink|rose";

/**
 * A Tailwind *colour* utility: prefix + a named colour or shade, optional alpha.
 * Deliberately tight — `text-lg`, `border-t`, `bg-clip-text`, `bg-gradient-to-r`
 * and `focus-visible:ring-2` share the prefixes but carry no colour.
 */
const COLOUR_UTILITY = new RegExp(
  "^(?:(?:hover|focus|focus-visible|active|sm|md|lg|file|placeholder|group-hover|disabled):)*" +
    "(?:bg|text|border|ring|from|via|to|accent|divide|decoration|shadow|fill|stroke)-" +
    `(?:white|black|transparent|current|(?:${PALETTE_SHADES})-\\d{2,3})(?:\\/\\d+)?$`,
);

/** Colour utilities actually used by the markup, runtime-built ones included. */
function usedColourTokens() {
  const tokens = new Set();
  for (const text of [HTML, APP_JS]) {
    for (const pattern of [/class="([^"]*)"/g, /className\s*=\s*"([^"]*)"/g, /classList\.(?:add|remove|toggle)\(\s*"([^"]*)"/g]) {
      for (const match of text.matchAll(pattern)) {
        for (const token of match[1].split(/\s+/)) {
          if (COLOUR_UTILITY.test(token)) {
            tokens.add(token);
          }
        }
      }
    }
  }
  tokens.delete("");
  return tokens;
}

/** The light layer, delimited by sentinels so the test reads exactly what ships. */
function lightLayer() {
  const start = STYLE_CSS.indexOf("/* >>> light-theme utility overrides");
  const end = STYLE_CSS.indexOf("/* <<< light-theme utility overrides");
  assert.notEqual(start, -1, "style.css must keep the light-theme utility block, sentinels included");
  assert.notEqual(end, -1, "the light-theme utility block must be closed");
  assert.ok(start < end, "sentinels are in the wrong order");
  return STYLE_CSS.slice(start, end);
}

/** `.hover\:bg-slate-700:hover` → `hover:bg-slate-700`, `bg-slate-800\/70` → `bg-slate-800/70` */
function overriddenTokens() {
  const block = lightLayer();
  const found = new Set();
  // Modifiers (escaped colons), the utility, then an optional escaped alpha.
  const escapedName = /html:not\(\.dark\)\s*\.((?:[a-z-]+\\:)*[a-z0-9-]+(?:\\\/[0-9]+)?)/g;
  for (const match of block.matchAll(escapedName)) {
    found.add(decodeURIComponent(match[1].replace(/\\/g, "")));
  }
  return found;
}

/**
 * Colour utilities that need no light-mode counterpart, each because its dark
 * value already reads correctly on a light surface. Keeping this list short and
 * explicit is the point: a new dark-only colour has to be added here on purpose.
 */
const THEME_NEUTRAL = new Set([
  // text that is white/transparent by design, on coloured surfaces
  "text-white", "text-transparent", "text-current",
  // mid-greys: legible on white already
  "text-slate-500", "text-slate-600", "placeholder-slate-500",
  // solid accent fills (buttons, status dots, gradients that keep white text)
  "bg-indigo-600", "hover:bg-indigo-500", "bg-emerald-500", "bg-emerald-400",
  "bg-amber-400", "bg-amber-500", "hover:bg-amber-500", "bg-amber-600",
  "bg-red-600", "hover:bg-red-500", "bg-red-400", "bg-pink-600", "hover:bg-pink-500",
  "bg-purple-500", "bg-cyan-500", "bg-transparent", "accent-pink-500",
  "from-indigo-600", "from-indigo-500", "hover:from-indigo-500",
  "via-purple-500", "to-purple-600", "hover:to-purple-500", "to-pink-500",
  // focus/active accents: indigo is the app's focus colour in both themes
  "focus:border-indigo-500", "focus:border-pink-500", "focus:border-amber-500", "border-amber-500",
  "focus-visible:ring-indigo-400", "focus-visible:ring-indigo-500", "focus-visible:ring-slate-500",
  "focus-visible:ring-emerald-500", "focus-visible:ring-purple-500", "focus-visible:ring-pink-400",
  "focus-visible:ring-pink-500", "focus-visible:ring-amber-400", "focus-visible:ring-amber-500",
  "focus:ring-amber-500", "focus-visible:ring-red-400", "focus-visible:ring-2",
  // elevation: black-alpha shadows read as soft shadows on light too
  "shadow-sm", "shadow-md", "shadow-lg", "shadow-2xl",
  "shadow-indigo-600/30", "shadow-purple-500/20",
  // the file-input variant, and the modal scrim (a dark scrim is correct in both)
  "file:bg-indigo-600", "file:text-white", "hover:file:bg-indigo-500", "file:border-0", "file:text-xs",
]);

test("theme: every colour utility the markup uses is light-mode ready or deliberately neutral", () => {
  const overrides = overriddenTokens();
  const missing = [...usedColourTokens()].filter((t) => !overrides.has(t) && !THEME_NEUTRAL.has(t)).sort();
  assert.deepEqual(
    missing,
    [],
    "these colours have no html:not(.dark) rule and are not in THEME_NEUTRAL — light mode would render them as-is",
  );
});

test("theme: the light layer only overrides tokens the markup still uses, scoped to light", () => {
  const used = usedColourTokens();
  const overrides = [...overriddenTokens()];
  const dead = overrides.filter((t) => !used.has(t)).sort();
  assert.deepEqual(dead, [], "these light-mode rules target utilities nothing uses any more");
  assert.ok(overrides.length > 40, `expected a substantial light layer, found ${overrides.length} rules`);
  // The light layer writes one rule per line, selector first.
  const block = lightLayer();
  const rules = block
    .split("\n")
    .map((line) => line.trim())
    .filter((line) => line.includes("{") && line.endsWith("}") && !line.startsWith("/*"));
  assert.ok(rules.length > 40, `expected to parse the rules, found ${rules.length}`);
  const unscoped = rules.filter((line) => !line.startsWith("html:not(.dark) "));
  assert.deepEqual(unscoped, [], "every light rule must be scoped to html:not(.dark) so dark mode is untouched");
  const modifiers = rules.filter((line) => line.includes("\\:") && !line.includes(":hover") && !line.includes(":focus"));
  assert.deepEqual(modifiers, [], "a hover:/focus: utility must keep its modifier in the class name *and* as a pseudo-class");
});

/*
 * The utility lock above only sees Tailwind classes. The base of style.css also
 * hard-codes colours for hand-written components (reader tabs, markdown bodies,
 * provenance badges, toasts, …), and those break in light mode the same way —
 * except nothing in the class-coverage guard or the utility lock would notice.
 * This one walks every base rule that declares a colour and demands a light
 * counterpart with the same selector, unless the rule is on the short, reasoned
 * list of surfaces that are accent-coloured in *both* themes.
 */
const COMPONENT_NEUTRAL = new Set([
  // Filled brand-accent buttons: a coloured surface with white text, identical in both themes.
  ".preset-act-load",
  ".preset-act-load:hover",
  ".skip-link",
  // Focus indicators: an accent ring drawn around the element, not a surface.
  ".preset-chip:focus-visible",
  ".preset-disclosure:focus-visible",
  ".preset-act:focus-visible",
  ".preset-rename-input:focus",
  ".search-input:focus",
  "#inputPrompt:focus",
  "button:focus-visible, select:focus-visible, textarea:focus-visible, input:focus-visible",
  // Active/recording states: the pink accent *is* the signal.
  ".msg-speak-btn.speaking",
  "#btnDictate.recording",
  // The toast shell itself declares no surface colour — only a drop shadow.
  ".toast",
]);

/** The light-theme component block, plus a failure if its sentinels were removed. */
function lightComponents() {
  const start = STYLE_CSS.indexOf("/* >>> light-theme component overrides */");
  const end = STYLE_CSS.indexOf("/* <<< light-theme component overrides */");
  assert.notEqual(start, -1, "style.css must keep the light-theme component block, sentinels included");
  assert.notEqual(end, -1, "the light-theme component block must be closed");
  assert.ok(start < end, "component sentinels are in the wrong order");
  return STYLE_CSS.slice(start, end + "/* <<< light-theme component overrides */".length);
}

test("theme: hand-written components are light-mode ready or deliberately neutral", () => {
  const strip = (text) => text.replace(/\/\*[\s\S]*?\*\//g, "");
  const normalize = (text) => text.split(/\s+/).filter(Boolean).join(" ");

  const utility = lightLayer();
  const components = lightComponents();
  // Everything outside the two light blocks is the original dark-mode stylesheet.
  const light = strip(utility + components);
  const base = strip(STYLE_CSS.replace(utility, "").replace(components, ""));

  // Light selectors, `html:not(.dark)` removed: `.md-body th` is what a base rule must match.
  const lightSelectors = new Set();
  for (const rule of light.matchAll(/html:not\(\.dark\)\s*([^{]+?)\s*\{/g)) {
    for (const part of rule[1].split(",")) {
      lightSelectors.add(normalize(part).replace(/^html:not\(\.dark\)\s*/, ""));
    }
  }
  assert.ok(lightSelectors.size > 40, `expected to parse the light layer, found ${lightSelectors.size} rules`);

  const missing = [];
  for (const rule of base.matchAll(/([^{}]*)\{([^{}]*)\}/g)) {
    const selector = normalize(rule[1]);
    if (!selector || selector.startsWith("@") || /^[\d%\s,]+$/.test(selector)) {
      continue; // at-rules and keyframe steps carry no selector
    }
    if (!/#[0-9a-f]{3,8}\b|rgba?\(/i.test(rule[2])) {
      continue; // declares no colour, so there is nothing to theme
    }
    if (COMPONENT_NEUTRAL.has(selector)) {
      continue;
    }
    // Every selector in the rule (comma-separated) needs the same selector in the light layer —
    // a `:hover` or `:focus` override does not restore the plain state.
    const wanted = selector.split(",").map(normalize);
    if (!wanted.every((part) => lightSelectors.has(part))) {
      missing.push(selector);
    }
  }

  assert.deepEqual(
    missing,
    [],
    "these hand-written components hard-code dark colours and have no html:not(.dark) rule with the " +
      "same selector (add one before the component sentinel, or justify it in COMPONENT_NEUTRAL)",
  );
});
