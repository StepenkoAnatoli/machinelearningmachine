/*
 * Preset-core tests: the mirror table that keeps static/presets.js in lockstep
 * with AddAgentRequest (server/app.py) — design:
 * docs/superpowers/specs/2026-09-23--agent-presets-design.md §5.1.
 *
 *     npm install && node --test tests/js/*.test.mjs
 *
 * The core is DOM-free; it is loaded the way the browser loads it (classic
 * script eval'd into a window), same as sanitize.test.mjs.
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

function loadCore() {
  const dom = new JSDOM("<!doctype html><html><body></body></html>", {
    runScripts: "dangerously",
  });
  const win = dom.window;
  win.eval(read(join(staticDir, "presets.js")));
  return win.MLMPresets;
}

const core = loadCore();

/**
 * jsdom-realm objects fail assert/strict deepEqual on prototype identity
 * ("same structure but not reference-equal") — bring plain data home first.
 */
function native(v) {
  return JSON.parse(JSON.stringify(v));
}

/** Valid base preset that only varies by `over`. */
function base(over = {}) {
  return {
    schema: "mlm-agent-preset/1",
    label: "Security Auditor kit",
    agent_id: "security-auditor",
    name: "Security Auditor",
    role: "Application Security Reviewer",
    system_prompt: "You review code and configs for security issues.",
    color: "#0ea5e9",
    avatar: "🛡️",
    ...over,
  };
}

const MSG = {
  notObject: "That isn't a preset object.",
  version: "That preset was made for a different version of this app.",
  agentId:
    "Module ID must be lowercase letters, numbers, dashes or underscores (3-50 chars), e.g. 'security-auditor'.",
  reserved: (id) => `Module ID '${id}' is reserved — pick another.`,
  name: "Display Name must be 1-100 characters.",
  role: "Domain Role must be 1-200 characters.",
  systemPrompt: "System Prompt must be 10-2000 characters.",
  color: "Color must be a hex code like #0ea5e9.",
  avatar: "Emoji Avatar must be 8 characters or fewer.",
  label: "Preset name must be 1-60 characters.",
};

function expectFail(raw, field, error) {
  const res = core.validatePreset(raw);
  assert.equal(res.ok, false, `expected failure for ${JSON.stringify(raw)?.slice(0, 80)}`);
  assert.equal(res.field, field);
  assert.equal(res.error, error);
  return res;
}

// ---------------------------------------------------------------- LIMITS

test("LIMITS matches the mirror table (AddAgentRequest, effective rules)", () => {
  assert.equal(core.LIMITS.agent_id.min, 3);
  assert.equal(core.LIMITS.agent_id.max, 50);
  assert.equal(core.LIMITS.agent_id.pattern, "^[a-z0-9][a-z0-9-_]{1,48}[a-z0-9]$");
  assert.deepEqual(native(core.LIMITS.agent_id.reserved), [
    "system",
    "broadcast",
    "all",
    "*",
    "api",
    "admin",
    "root",
  ]);
  assert.equal(core.LIMITS.name.min, 1);
  assert.equal(core.LIMITS.name.max, 100);
  assert.equal(core.LIMITS.role.min, 1);
  assert.equal(core.LIMITS.role.max, 200);
  assert.equal(core.LIMITS.system_prompt.min, 10);
  assert.equal(core.LIMITS.system_prompt.max, 2000);
  assert.equal(core.LIMITS.label.min, 1);
  assert.equal(core.LIMITS.label.max, 60);
  assert.equal(core.LIMITS.avatar.max, 8);
  assert.equal(core.LIMITS.color.pattern, "^#[0-9a-fA-F]{6}$");
});

// ------------------------------------------------------------- agent_id

test("agent_id: shape and length boundaries (effective 3-50)", () => {
  expectFail(base({ agent_id: "ab" }), "agent_id", MSG.agentId); // 2-char: HTML allows, server rejects
  expectFail(base({ agent_id: "a" }), "agent_id", MSG.agentId);
  core.validatePreset(base({ agent_id: "abc" })); // 3-char ok
  core.validatePreset(base({ agent_id: "a-b" }));
  core.validatePreset(base({ agent_id: "a_b" }));
  core.validatePreset(base({ agent_id: "a" + "b".repeat(48) + "c" })); // 50 ok
  expectFail(base({ agent_id: "a" + "b".repeat(49) + "c" }), "agent_id", MSG.agentId); // 51
  expectFail(base({ agent_id: "-abc" }), "agent_id", MSG.agentId);
  expectFail(base({ agent_id: "abc-" }), "agent_id", MSG.agentId);
  expectFail(base({ agent_id: "my agent" }), "agent_id", MSG.agentId);
  expectFail(base({ agent_id: "MY AGENT" }), "agent_id", MSG.agentId);
  expectFail(base({ agent_id: 42 }), "agent_id", MSG.agentId);
});

test("agent_id: normalized strip().lower() like the server", () => {
  const res = core.validatePreset(base({ agent_id: "  Security_Auditor  " }));
  assert.equal(res.ok, true);
  assert.equal(res.value.agent_id, "security_auditor");
  const res2 = core.validatePreset(base({ agent_id: "ABC" }));
  assert.equal(res2.value.agent_id, "abc");
});

test("agent_id: reserved ids rejected before shape", () => {
  for (const id of ["system", "broadcast", "all", "*", "api", "admin", "root"]) {
    expectFail(base({ agent_id: id }), "agent_id", MSG.reserved(id));
  }
  expectFail(base({ agent_id: "  SYSTEM  " }), "agent_id", MSG.reserved("system"));
});

// ------------------------------------------------------ name/role/prompt

test("name: 1-100, not whitespace-only, trimmed", () => {
  expectFail(base({ name: "" }), "name", MSG.name);
  expectFail(base({ name: "   " }), "name", MSG.name);
  core.validatePreset(base({ name: "x" }));
  core.validatePreset(base({ name: "x".repeat(100) }));
  expectFail(base({ name: "x".repeat(101) }), "name", MSG.name);
  expectFail(base({ name: 7 }), "name", MSG.name);
  const res = core.validatePreset(base({ name: "  Spaced  " }));
  assert.equal(res.value.name, "Spaced");
});

test("role: 1-200 bounds", () => {
  expectFail(base({ role: "" }), "role", MSG.role);
  expectFail(base({ role: "  \t " }), "role", MSG.role);
  core.validatePreset(base({ role: "x".repeat(200) }));
  expectFail(base({ role: "x".repeat(201) }), "role", MSG.role);
});

test("system_prompt: 10-2000 bounds", () => {
  expectFail(base({ system_prompt: "x".repeat(9) }), "system_prompt", MSG.systemPrompt);
  core.validatePreset(base({ system_prompt: "x".repeat(10) }));
  core.validatePreset(base({ system_prompt: "x".repeat(2000) }));
  expectFail(base({ system_prompt: "x".repeat(2001) }), "system_prompt", MSG.systemPrompt);
  expectFail(base({ system_prompt: "          " }), "system_prompt", MSG.systemPrompt);
});

// ------------------------------------------------------------------ label

test("label: 1-60, required, trimmed", () => {
  expectFail(base({ label: "" }), "label", MSG.label);
  expectFail(base({ label: "   " }), "label", MSG.label);
  expectFail({ ...base(), label: undefined }, "label", MSG.label);
  core.validatePreset(base({ label: "x".repeat(60) }));
  expectFail(base({ label: "x".repeat(61) }), "label", MSG.label);
});

// ------------------------------------------------------------------ color

test("color: hex gate, case-insensitive, default when absent", () => {
  core.validatePreset(base({ color: "#0ea5e9" }));
  core.validatePreset(base({ color: "#AABBCC" }));
  const trimmed = core.validatePreset(base({ color: "#0ea5e9  " }));
  assert.equal(trimmed.value.color, "#0ea5e9");
  expectFail(base({ color: "#abc" }), "color", MSG.color);
  expectFail(base({ color: "red" }), "color", MSG.color);
  expectFail(base({ color: "0ea5e9" }), "color", MSG.color);
  expectFail(base({ color: "#0ea5e9;" }), "color", MSG.color);
  expectFail(base({ color: "red; background:url(x)" }), "color", MSG.color);
  expectFail(base({ color: "" }), "color", MSG.color);
  expectFail(base({ color: 123 }), "color", MSG.color);
  const def = core.validatePreset(base({ color: undefined }));
  assert.equal(def.value.color, "#ec4899");
  const nul = core.validatePreset(base({ color: null }));
  assert.equal(nul.value.color, "#ec4899");
});

// ----------------------------------------------------------------- avatar

test("avatar: code-point bounds (Python len() parity) and 🤖 default", () => {
  core.validatePreset(base({ avatar: "🛡️" })); // 2 code points
  core.validatePreset(base({ avatar: "👩‍💻" })); // ZWJ sequence: 3 code points
  core.validatePreset(base({ avatar: "12345678" })); // 8 ok
  expectFail(base({ avatar: "123456789" }), "avatar", MSG.avatar); // 9
  const def1 = core.validatePreset(base({ avatar: undefined }));
  assert.equal(def1.value.avatar, "🤖");
  const def2 = core.validatePreset(base({ avatar: "   " }));
  assert.equal(def2.value.avatar, "🤖");
  expectFail(base({ avatar: 5 }), "avatar", MSG.avatar);
});

// ----------------------------------------------------------------- schema

test("schema gate: absent ok, v1 ok, anything else rejected", () => {
  const noSchema = { ...base() };
  delete noSchema.schema;
  assert.equal(core.validatePreset(noSchema).ok, true);
  assert.equal(core.validatePreset(base({ schema: "mlm-agent-preset/1" })).ok, true);
  expectFail(base({ schema: "mlm-agent-preset/2" }), "schema", MSG.version);
  expectFail(base({ schema: "" }), "schema", MSG.version);
  expectFail(base({ schema: 1 }), "schema", MSG.version);
});

test("non-objects are rejected outright", () => {
  for (const raw of [null, undefined, "hello", 42, [1, 2], true]) {
    expectFail(raw, null, MSG.notObject);
  }
});

// --------------------------------------------------------- error ordering

test("first offending field is named (agent_id → name → role → system_prompt → color → avatar → label)", () => {
  expectFail(
    base({
      label: "",
      agent_id: "",
      name: "",
      role: "",
      system_prompt: "short",
      color: "junk",
      avatar: "123456789",
    }),
    "agent_id",
    MSG.agentId,
  );
  expectFail(base({ name: "", label: "" }), "name", MSG.name);
  expectFail(base({ role: "", label: "" }), "role", MSG.role);
  expectFail(base({ system_prompt: "short", label: "" }), "system_prompt", MSG.systemPrompt);
  expectFail(base({ color: "junk", label: "" }), "color", MSG.color);
  expectFail(base({ avatar: "123456789", label: "" }), "avatar", MSG.avatar);
});

// ------------------------------------------------------------ normalization

test("valid preset normalizes to a complete v1 object", () => {
  const res = core.validatePreset({
    label: "  Security Auditor kit  ",
    agent_id: "  Security-Auditor ",
    name: " Security Auditor ",
    role: "  Application Security Reviewer ",
    system_prompt: "  You review code and configs for security issues.  ",
    color: "#0EA5E9",
    avatar: "🛡️",
  });
  assert.equal(res.ok, true);
  assert.deepEqual(native(res.value), {
    schema: "mlm-agent-preset/1",
    label: "Security Auditor kit",
    agent_id: "security-auditor",
    name: "Security Auditor",
    role: "Application Security Reviewer",
    system_prompt: "You review code and configs for security issues.",
    color: "#0EA5E9",
    avatar: "🛡️",
  });
});

test("unknown keys are ignored (whitelist-copy)", () => {
  const res = core.validatePreset(
    base({
      __proto__: { polluted: true },
      extra: "ignored",
      constructor: "ignored",
    }),
  );
  assert.equal(res.ok, true);
  assert.deepEqual(Object.keys(res.value).sort(), [
    "agent_id",
    "avatar",
    "color",
    "label",
    "name",
    "role",
    "schema",
    "system_prompt",
  ]);
  assert.equal({}.polluted, undefined);
});

// ---------------------------------------------------------------- BUILTINS

test("BUILTINS: exactly the 5 spec entries, in spec order (spec §4.2 roster)", () => {
  assert.deepEqual(native(core.BUILTINS.map((b) => [b.label, b.agent_id])), [
    ["Security Auditor", "security-auditor"],
    ["DB Expert", "db-expert"],
    ["Performance Engineer", "perf-engineer"],
    ["QA/Test Engineer", "qa-engineer"],
    ["Technical Writer", "tech-writer"],
  ]);
});

test("BUILTINS: every entry passes validatePreset, labels and ids unique", () => {
  assert.equal(core.BUILTINS.length, 5);
  for (const b of core.BUILTINS) {
    const res = core.validatePreset(b);
    assert.equal(res.ok, true, `${b.label}: ${res.ok ? "" : res.error}`);
  }
  const labels = core.BUILTINS.map((b) => b.label);
  const ids = core.BUILTINS.map((b) => b.agent_id);
  assert.equal(new Set(labels).size, labels.length);
  assert.equal(new Set(ids).size, ids.length);
});

// ------------------------------------------------------------- uniqueLabel

test("uniqueLabel: clean, free labels are kept as-is", () => {
  assert.equal(core.uniqueLabel("My kit", []), "My kit");
  assert.equal(core.uniqueLabel("My kit", ["Other", "Another"]), "My kit");
  assert.equal(core.uniqueLabel("Kit", ["Kit (2)"], "import"), "Kit");
});

test("uniqueLabel: manual family runs (2), (3)… against the full set, filling gaps", () => {
  assert.equal(core.uniqueLabel("My kit", ["My kit"]), "My kit (2)");
  assert.equal(core.uniqueLabel("My kit", ["My kit", "My kit (2)"]), "My kit (3)");
  assert.equal(core.uniqueLabel("My kit", ["My kit", "My kit (2)", "My kit (3)"]), "My kit (4)");
  assert.equal(core.uniqueLabel("My kit", ["My kit", "My kit (3)"]), "My kit (2)"); // gap wins
  // collides with built-in labels too
  assert.equal(core.uniqueLabel("DB Expert", core.BUILTINS.map((b) => b.label)), "DB Expert (2)");
  // exact match only — different case is a different label
  assert.equal(core.uniqueLabel("My kit", ["my kit"]), "My kit");
});

test("uniqueLabel: import family runs ' (imported)', ' (imported 2)'…", () => {
  assert.equal(core.uniqueLabel("Kit", ["Kit"], "import"), "Kit (imported)");
  assert.equal(core.uniqueLabel("Kit", ["Kit", "Kit (imported)"], "import"), "Kit (imported 2)");
  assert.equal(core.uniqueLabel("Kit", ["Kit", "Kit (imported)", "Kit (imported 2)"], "import"), "Kit (imported 3)");
});

test("uniqueLabel: accepts arrays or Sets as the taken set", () => {
  assert.equal(core.uniqueLabel("A", new Set(["A"])), "A (2)");
  assert.equal(core.uniqueLabel("B", new Set(["B", "B (2)", "B (3)"])), "B (4)");
});

// ---------------------------------------------------------- mount: chips UI

function mountFixture() {
  const dom = new JSDOM(
    `<!doctype html><html><body>
      <div id="agentModal">
        <div id="presetChips" role="group" aria-label="Module presets"></div>
        <form id="formAddAgent">
          <input id="newAgentId"><input id="newAgentName"><input id="newAgentRole">
          <textarea id="newAgentPrompt"></textarea>
          <input id="newAgentColor"><input id="newAgentAvatar">
        </form>
      </div>
    </body></html>`,
    { runScripts: "dangerously" },
  );
  const win = dom.window;
  win.eval(read(join(staticDir, "presets.js")));
  const calls = { fill: [], toast: [], submit: 0 };
  win.document.getElementById("formAddAgent").addEventListener("submit", (e) => {
    e.preventDefault();
    calls.submit += 1;
  });
  return {
    win,
    api: win.MLMPresets,
    calls,
    chips: win.document.getElementById("presetChips"),
    start() {
      return win.MLMPresets.mount({
        fillForm: (p) => calls.fill.push(p),
        toast: (...a) => calls.toast.push(a),
      });
    },
  };
}

test("mount renders 5 chips in spec order, as non-submitting buttons with avatar faces", () => {
  const fx = mountFixture();
  assert.equal(fx.start(), true);
  const btns = [...fx.chips.querySelectorAll("button")];
  assert.equal(btns.length, 5);
  assert.deepEqual(
    btns.map((b) => b.getAttribute("aria-label")),
    native(fx.api.BUILTINS.map((b) => `Load preset ${b.label}`)),
  );
  btns.forEach((b, i) => {
    assert.equal(b.type, "button"); // never submits
    assert.equal(b.className.includes("preset-btn"), false); // app.js owns .preset-btn (scenario loader)
    assert.equal(b.children.length, 1); // one avatar face span, then a text node
    assert.equal(b.children[0].getAttribute("aria-hidden"), "true");
    assert.equal(b.children[0].textContent, fx.api.BUILTINS[i].avatar);
    assert.ok(b.textContent.includes(fx.api.BUILTINS[i].label));
  });
});

test("chip click fills the six-field payload and toasts 'Loaded preset: …'", () => {
  const fx = mountFixture();
  fx.start();
  const first = fx.api.BUILTINS[0];
  fx.chips.querySelectorAll("button")[0].click();
  assert.equal(fx.calls.fill.length, 1);
  assert.deepEqual(native(fx.calls.fill[0]), {
    agent_id: first.agent_id,
    name: first.name,
    role: first.role,
    system_prompt: first.system_prompt,
    color: first.color,
    avatar: first.avatar,
  });
  assert.deepEqual(fx.calls.toast, [["Loaded preset: Security Auditor", "info", 2000]]);
});

test("chips never submit the form", () => {
  const fx = mountFixture();
  fx.start();
  for (const b of fx.chips.querySelectorAll("button")) b.click();
  assert.equal(fx.calls.submit, 0);
});

test("mount is idempotent and fails soft without a container or fillForm", () => {
  const fx = mountFixture();
  fx.start();
  fx.start(); // second mount re-renders, never duplicates
  assert.equal(fx.chips.querySelectorAll("button").length, 5);
  const bare = mountFixture();
  bare.chips.remove();
  assert.equal(bare.start(), false);
  const noFill = mountFixture();
  assert.equal(noFill.api.mount({ toast: () => {} }), false);
});

test("mount renders labels via textContent — hostile labels stay inert", () => {
  const fx = mountFixture();
  const evil = fx.api.validatePreset({
    ...base(),
    label: "<img src=x onerror=alert(1)>",
    agent_id: "evil-one",
  });
  assert.equal(evil.ok, true); // label rules don't (and needn't) forbid markup characters
  fx.api.BUILTINS.push(evil.value);
  try {
    fx.start();
    assert.equal(fx.chips.querySelectorAll("img").length, 0);
    assert.equal(fx.chips.querySelectorAll("script").length, 0);
    assert.ok(fx.chips.textContent.includes("<img src=x onerror=alert(1)>"));
    assert.equal(fx.win.document.images.length, 0);
  } finally {
    fx.api.BUILTINS.pop();
  }
});

test("index.html wires the presets seam (sync script before app.js, container above the form)", () => {
  const html = read(join(staticDir, "index.html"));
  const pres = html.indexOf('<script src="/static/presets.js"');
  const app = html.indexOf('<script src="/static/app.js"');
  assert.ok(pres !== -1 && app !== -1 && pres < app, "presets.js must load before app.js");
  const chipsAt = html.indexOf('id="presetChips"');
  const formAt = html.indexOf('id="formAddAgent"');
  assert.ok(chipsAt !== -1 && formAt !== -1 && chipsAt < formAt, "#presetChips sits above the form");
  const chipTag = html.slice(html.lastIndexOf("<div", chipsAt), html.indexOf(">", chipsAt) + 1);
  assert.ok(chipTag.includes('role="group"'), "chips container keeps role=group");
  assert.ok(chipTag.includes('aria-label="Module presets"'), "chips container is labelled");
});
