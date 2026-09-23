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
        <div class="mb-4">
          <div id="presetChips" role="group" aria-label="Module presets"></div>
          <button type="button" id="btnSavePreset"><span>Save as preset</span></button>
          <div class="preset-manager-bar">
            <button type="button" id="btnPresetManager" aria-expanded="false" aria-controls="presetManager">
              <span id="presetManagerBadge">Saved presets (0)</span>
            </button>
            <div class="preset-manager-actions">
              <button type="button" id="btnPresetImport" aria-label="Import preset file">
                <span>Import</span>
              </button>
              <button type="button" id="btnPresetExportAll" disabled aria-label="Export all saved presets">
                <span>Export all</span>
              </button>
            </div>
            <input type="file" id="presetImportInput" accept=".json,application/json" hidden>
          </div>
          <div id="presetManager" role="list" aria-label="Saved presets" hidden></div>
          <p id="presetManagerEmpty" hidden>No saved presets yet — fill the form and hit 'Save as preset'.</p>
        </div>
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
  const doc = win.document;
  const storage = fakeStorage();
  const calls = { fill: [], toast: [], submit: 0, download: [], files: [] };
  doc.getElementById("formAddAgent").addEventListener("submit", (e) => {
    e.preventDefault();
    calls.submit += 1;
  });
  const fields = () => ({
    agent_id: doc.getElementById("newAgentId").value,
    name: doc.getElementById("newAgentName").value,
    role: doc.getElementById("newAgentRole").value,
    system_prompt: doc.getElementById("newAgentPrompt").value,
    color: doc.getElementById("newAgentColor").value,
    avatar: doc.getElementById("newAgentAvatar").value,
  });
  return {
    win,
    doc,
    api: win.MLMPresets,
    calls,
    storage,
    chips: doc.getElementById("presetChips"),
    saveBtn: doc.getElementById("btnSavePreset"),
    fields,
    fill(over = {}) {
      const f = {
        agent_id: "my-auditor",
        name: "My Auditor",
        role: "Reviewer",
        system_prompt: "You review things carefully.",
        color: "#0ea5e9",
        avatar: "🔐",
        ...over,
      };
      doc.getElementById("newAgentId").value = f.agent_id;
      doc.getElementById("newAgentName").value = f.name;
      doc.getElementById("newAgentRole").value = f.role;
      doc.getElementById("newAgentPrompt").value = f.system_prompt;
      doc.getElementById("newAgentColor").value = f.color;
      doc.getElementById("newAgentAvatar").value = f.avatar;
    },
    readStore() {
      return native(win.MLMPresets.createStore({ storage }).load());
    },
    seed(entries) {
      const store = win.MLMPresets.createStore({ storage });
      for (const entry of entries) {
        const raw = typeof entry === "string" ? { label: entry } : entry;
        const slug = String(raw.label)
          .toLowerCase()
          .replace(/[^a-z0-9]+/g, "-")
          .replace(/^-+|-+$/g, "")
          .slice(0, 40);
        const res = store.save({
          name: "Seeded agent",
          role: "Seeder",
          system_prompt: "Seeded preset used by the manager tests.",
          color: "#0ea5e9",
          avatar: "🧩",
          ...raw,
          agent_id: ("seed-" + (slug || "preset")).slice(0, 50).replace(/-+$/g, ""),
        });
        if (!res.ok) throw new Error("seed failed: " + JSON.stringify(res));
      }
    },
    open() {
      doc.getElementById("btnPresetManager").click();
    },
    pickFile(body) {
      const input = doc.getElementById("presetImportInput");
      Object.defineProperty(input, "files", { value: [{ name: "kit.json", body }], configurable: true });
      input.dispatchEvent(new win.Event("change", { bubbles: true }));
      Object.defineProperty(input, "files", { value: [], configurable: true });
    },
    rows() {
      return [...doc.getElementById("presetManager").children];
    },
    rowByLabel(label) {
      return this.rows().find((r) => r.dataset.label === label) || null;
    },
    labelOf(row) {
      return row.querySelector(".preset-row-label").textContent;
    },
    start(over = {}) {
      return win.MLMPresets.mount({
        getFormData: fields,
        fillForm: (p) => calls.fill.push(p),
        toast: (...a) => calls.toast.push(a),
        download: (name, text) => calls.download.push([name, text]),
        readFile: (file, done) => {
          calls.files.push(file);
          done(file.body);
        },
        storage,
        ...over,
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
  const saveAt = html.indexOf('id="btnSavePreset"');
  assert.ok(saveAt !== -1 && saveAt < formAt, "Save-as-preset sits above the form, with the chips");
  const saveTag = html.slice(html.lastIndexOf("<button", saveAt), html.indexOf(">", saveAt) + 1);
  assert.ok(saveTag.includes('type="button"'), "Save-as-preset can never submit the form");
  assert.ok(html.slice(saveAt, saveAt + 400).includes("Save as preset"));
  const discAt = html.indexOf('id="btnPresetManager"');
  assert.ok(discAt !== -1 && discAt < formAt, "the manager disclosure sits above the form");
  const discTag = html.slice(html.lastIndexOf("<button", discAt), html.indexOf(">", discAt) + 1);
  assert.ok(discTag.includes('type="button"'), "the disclosure can never submit the form");
  assert.ok(discTag.includes('aria-expanded="false"'), "the manager starts collapsed");
  assert.ok(discTag.includes('aria-controls="presetManager"'), "the disclosure points at the panel");
  assert.ok(html.slice(discAt, discAt + 400).includes("Saved presets (0)"), "badge shows the count");
  const managerAt = html.indexOf('id="presetManager"');
  assert.ok(managerAt !== -1 && managerAt < formAt, "the manager panel sits above the form");
  const managerTag = html.slice(html.lastIndexOf("<div", managerAt), html.indexOf(">", managerAt) + 1);
  assert.ok(managerTag.includes('role="list"'), "the panel is a list");
  assert.ok(managerTag.includes('aria-label="Saved presets"'), "the panel is labelled");
  assert.ok(managerTag.includes("hidden"), "the panel starts collapsed");
  const emptyAt = html.indexOf('id="presetManagerEmpty"');
  assert.ok(emptyAt !== -1 && emptyAt < formAt, "the empty-state line sits above the form");
  assert.ok(
    html.includes("No saved presets yet — fill the form and hit 'Save as preset'."),
    "the empty-state copy is the spec's, verbatim",
  );
  const exportAt = html.indexOf('id="btnPresetExportAll"');
  assert.ok(exportAt !== -1 && exportAt < formAt, "Export-all sits on the disclosure row");
  const exportTag = html.slice(html.lastIndexOf("<button", exportAt), html.indexOf(">", exportAt) + 1);
  assert.ok(exportTag.includes('type="button"'), "Export-all can never submit the form");
  assert.ok(exportTag.includes("disabled"), "Export-all starts disabled (empty library)");
  assert.ok(exportTag.includes('aria-label="Export all saved presets"'), "Export-all is labelled");
  const importAt = html.indexOf('id="btnPresetImport"');
  assert.ok(importAt !== -1 && importAt < formAt, "Import sits on the disclosure row");
  const importTag = html.slice(html.lastIndexOf("<button", importAt), html.indexOf(">", importAt) + 1);
  assert.ok(importTag.includes('type="button"'), "Import can never submit the form");
  assert.ok(importTag.includes('aria-label="Import preset file"'), "Import is labelled");
  const inputAt = html.indexOf('id="presetImportInput"');
  assert.ok(inputAt !== -1 && inputAt < formAt, "the file picker is inside the modal");
  const inputTag = html.slice(html.lastIndexOf("<input", inputAt), html.indexOf(">", inputAt) + 1);
  assert.ok(inputTag.includes('type="file"') && inputTag.includes("hidden"), "the picker is a hidden file input");
  assert.ok(inputTag.includes('accept=".json,application/json"'), "the picker accepts JSON (validated regardless — §5.4)");
});

// ------------------------------------------------------- store (localStorage)

function fakeStorage(init = {}) {
  const map = new Map(Object.entries(init));
  return {
    getItem: (k) => (map.has(k) ? map.get(k) : null),
    setItem: (k, v) => {
      map.set(k, String(v));
    },
    removeItem: (k) => {
      map.delete(k);
    },
    _map: map,
  };
}

const SESSION_ONLY = "Presets won't persist in this browser session.";

test("store: key, library schema and default bounds are exact (spec §4.3)", () => {
  assert.equal(core.STORAGE_KEY, "mlm.agentPresets.v1");
  assert.equal(core.LIB_SCHEMA, "mlm-agent-preset-lib/1");
  assert.deepEqual(native(core.BOUNDS), {
    presets: 50,
    presetChars: 8 * 1024,
    libraryChars: 256 * 1024,
  });
});

test("store: empty storage loads as []", () => {
  const store = core.createStore({ storage: fakeStorage() });
  assert.equal(store.available, true);
  assert.deepEqual(native(store.load()), []);
});

test("store: save/load round-trip is lossless and labels stay clean when free", () => {
  const store = core.createStore({ storage: fakeStorage() });
  const res = store.save(base());
  assert.equal(res.ok, true);
  assert.equal(res.value.label, "Security Auditor kit");
  assert.deepEqual(native(store.load()), [native(res.value)]);
});

test("store: collisions suffix (2), (3)… against builtins and saved labels", () => {
  const store = core.createStore({ storage: fakeStorage() });
  const r1 = store.save(base({ label: "DB Expert" })); // builtin label
  assert.equal(r1.value.label, "DB Expert (2)");
  const r2 = store.save(base({ label: "DB Expert" }));
  assert.equal(r2.value.label, "DB Expert (3)");
});

test("store: preset-count bound rejects the overflow with a plain error", () => {
  const notices = [];
  const store = core.createStore({
    storage: fakeStorage(),
    onNotice: (m) => notices.push(m),
    bounds: { presets: 2, presetChars: 8192, libraryChars: 262144 },
  });
  assert.equal(store.save(base({ label: "One", agent_id: "one-a" })).ok, true);
  assert.equal(store.save(base({ label: "Two", agent_id: "two-a" })).ok, true);
  const res = store.save(base({ label: "Three", agent_id: "three-a" }));
  assert.equal(res.ok, false);
  assert.equal(res.error, "Your preset library is full (2 presets). Remove or export some first.");
  assert.equal(store.load().length, 2);
  assert.deepEqual(notices, []); // full is an error, not a persistence notice
});

test("store: size bounds reject oversized preset and oversized library", () => {
  const smallPreset = core.createStore({
    storage: fakeStorage(),
    bounds: { presets: 50, presetChars: 200, libraryChars: 262144 },
  });
  const r1 = smallPreset.save(base());
  assert.equal(r1.ok, false);
  assert.equal(r1.error, "That preset is too large to save.");
  const smallLib = core.createStore({
    storage: fakeStorage(),
    bounds: { presets: 50, presetChars: 8192, libraryChars: 350 },
  });
  assert.equal(smallLib.save(base({ label: "A", agent_id: "a-aa" })).ok, true);
  const r2 = smallLib.save(base({ label: "B", agent_id: "b-bb" }));
  assert.equal(r2.ok, false);
  assert.equal(r2.error, "Your preset library is too large to save more. Export it, then remove some presets.");
  assert.equal(smallLib.load().length, 1); // nothing partially written
});

test("store: a throwing setItem leaves the old library intact", () => {
  const storage = fakeStorage();
  const store = core.createStore({ storage });
  assert.equal(store.save(base({ label: "Keep", agent_id: "keep-a" })).ok, true);
  const before = storage._map.get(core.STORAGE_KEY);
  storage.setItem = () => {
    throw new Error("QuotaExceededError");
  };
  const res = store.save(base({ label: "Lose", agent_id: "lose-a" }));
  assert.equal(res.ok, false);
  assert.equal(res.error, "Couldn't save — this browser's storage is full or blocked.");
  assert.equal(storage._map.get(core.STORAGE_KEY), before); // untouched
  assert.deepEqual(native(store.load().map((p) => p.label)), ["Keep"]);
});

test("store: unavailable storage → in-memory round-trip + one-time notice", () => {
  const notices = [];
  const broken = {
    getItem: () => null,
    setItem: () => {
      throw new Error("SecurityError");
    },
    removeItem: () => {},
  };
  const store = core.createStore({ storage: broken, onNotice: (m) => notices.push(m) });
  assert.equal(store.available, false);
  assert.deepEqual(notices, [SESSION_ONLY]);
  const res = store.save(base({ label: "Volatile", agent_id: "volatile-1" }));
  assert.equal(res.ok, true);
  assert.deepEqual(native(store.load().map((p) => p.label)), ["Volatile"]);
  store.save(base({ label: "Volatile2", agent_id: "volatile-2" }));
  assert.deepEqual(notices, [SESSION_ONLY]); // still one notice only
});

test("store: corrupt or foreign blobs load as [] without throwing", () => {
  for (const junk of [
    "not json",
    "[1,2,3]",
    '{"schema":"nope","presets":[]}',
    '{"schema":"mlm-agent-preset-lib/1","presets":"no"}',
    '{"presets":[]}',
    "",
  ]) {
    const store = core.createStore({ storage: fakeStorage({ [core.STORAGE_KEY]: junk }) });
    assert.deepEqual(native(store.load()), [], `junk: ${junk.slice(0, 20)}`);
  }
});

test("store: tampered entries are dropped, valid ones kept and whitelist-copied", () => {
  const good1 = core.validatePreset(base({ label: "Good One", agent_id: "good-one" })).value;
  const good2 = core.validatePreset(base({ label: "Good Two", agent_id: "good-two" })).value;
  const tampered = { ...good1, label: "Tampered", extra: "evil", __proto__: { x: 1 } };
  const blob = JSON.stringify({
    schema: core.LIB_SCHEMA,
    presets: [good1, 42, "hello", { label: "x" }, tampered, good2],
  });
  const store = core.createStore({ storage: fakeStorage({ [core.STORAGE_KEY]: blob }) });
  const labels = native(store.load().map((p) => p.label));
  assert.deepEqual(labels, ["Good One", "Tampered", "Good Two"]); // object with label+known keys validates
  for (const p of native(store.load())) {
    assert.deepEqual(Object.keys(p).sort(), [
      "agent_id",
      "avatar",
      "color",
      "label",
      "name",
      "role",
      "schema",
      "system_prompt",
    ]);
  }
  assert.equal({}.x, undefined);
});

test("store: deleteByLabel removes and persists; unknown labels fail plainly", () => {
  const storage = fakeStorage();
  const store = core.createStore({ storage });
  store.save(base({ label: "One", agent_id: "one-a" }));
  store.save(base({ label: "Two", agent_id: "two-a" }));
  const res = store.deleteByLabel("One");
  assert.equal(res.ok, true);
  assert.deepEqual(native(store.load().map((p) => p.label)), ["Two"]);
  const missing = store.deleteByLabel("nope");
  assert.equal(missing.ok, false);
  assert.equal(missing.error, "No preset named 'nope'.");
});

test("store: rename changes the label; collisions, invalids and unknowns are refused", () => {
  const storage = fakeStorage();
  const store = core.createStore({ storage });
  store.save(base({ label: "Alpha", agent_id: "alpha-a" }));
  store.save(base({ label: "Beta", agent_id: "beta-bb" }));
  const ok = store.rename("Alpha", "Gamma");
  assert.equal(ok.ok, true);
  assert.equal(ok.value.label, "Gamma");
  assert.deepEqual(native(store.load().map((p) => p.label).sort()), ["Beta", "Gamma"]);
  const same = store.rename("Gamma", "Gamma"); // no-op succeeds
  assert.equal(same.ok, true);
  const taken = store.rename("Gamma", "Beta");
  assert.equal(taken.ok, false);
  assert.equal(taken.field, "label");
  assert.equal(taken.error, "That name is already taken — pick another.");
  const takenBuiltin = store.rename("Gamma", "DB Expert");
  assert.equal(takenBuiltin.ok, false);
  assert.equal(takenBuiltin.error, "That name is already taken — pick another.");
  const invalid = store.rename("Gamma", "x".repeat(61));
  assert.equal(invalid.ok, false);
  assert.equal(invalid.field, "label");
  assert.equal(invalid.error, "Preset name must be 1-60 characters.");
  const unknown = store.rename("ghost", "Anything");
  assert.equal(unknown.ok, false);
  assert.equal(unknown.error, "No preset named 'ghost'.");
  assert.deepEqual(native(store.load().map((p) => p.label).sort()), ["Beta", "Gamma"]); // unchanged
});

// --------------------------------------------------------- save-as-preset UI

test("save: hidden without getFormData, shown with it", () => {
  const hidden = mountFixture();
  hidden.api.mount({ fillForm: () => {}, toast: () => {}, storage: hidden.storage });
  assert.equal(hidden.saveBtn.hidden, true);
  const shown = mountFixture();
  shown.start();
  assert.equal(shown.saveBtn.hidden, false);
});

test("save: happy path — label defaults to Display Name, success toast, stored", () => {
  const fx = mountFixture();
  fx.start();
  fx.fill({ name: "My Auditor" });
  fx.saveBtn.click();
  assert.deepEqual(fx.calls.toast, [["Saved 'My Auditor' to your presets.", "success", 2500]]);
  const lib = fx.readStore();
  assert.deepEqual(lib.map((p) => p.label), ["My Auditor"]);
  assert.equal(lib[0].name, "My Auditor");
});

test("save: invalid form names the first offending field and saves nothing", () => {
  const fx = mountFixture();
  fx.start();
  fx.fill({ agent_id: "", name: "", role: "", system_prompt: "", color: "", avatar: "" });
  fx.saveBtn.click();
  assert.deepEqual(fx.calls.toast, [[MSG.agentId, "error", 4000]]);
  fx.fill({ system_prompt: "short" }); // id/name/role back to valid
  fx.saveBtn.click();
  assert.deepEqual(fx.calls.toast[1], [MSG.systemPrompt, "error", 4000]);
  assert.deepEqual(fx.readStore(), []); // nothing partially written
});

test("save: label clamps to 60 chars when Display Name runs longer", () => {
  const fx = mountFixture();
  fx.start();
  fx.fill({ name: "N".repeat(80), agent_id: "long-name-a" });
  fx.saveBtn.click();
  const lib = fx.readStore();
  assert.equal(lib[0].label, "N".repeat(60));
  assert.equal(lib[0].name, "N".repeat(80)); // the form field itself is untouched
  assert.equal(fx.calls.toast[0][0], `Saved '${"N".repeat(60)}' to your presets.`);
});

test("save: collisions suffix (2), (3) against builtins — toast shows the final label", () => {
  const fx = mountFixture();
  fx.start();
  fx.fill({ name: "DB Expert", agent_id: "db-expert-x" });
  fx.saveBtn.click();
  fx.calls.toast.length = 0;
  fx.fill({ name: "DB Expert", agent_id: "db-expert-y" });
  fx.saveBtn.click();
  assert.deepEqual(fx.readStore().map((p) => p.label), ["DB Expert (2)", "DB Expert (3)"]);
  assert.deepEqual(fx.calls.toast, [["Saved 'DB Expert (3)' to your presets.", "success", 2500]]);
});

test("save: a rapid double-click saves exactly once", () => {
  const fx = mountFixture();
  fx.start();
  fx.fill();
  fx.saveBtn.click();
  fx.saveBtn.click();
  assert.equal(fx.readStore().length, 1);
  assert.equal(fx.calls.toast.length, 1);
});

test("save: hostile Display Name is stored as data and toasted as plain text", () => {
  const fx = mountFixture();
  fx.start();
  const evil = "<img src=x onerror=alert(1)>";
  fx.fill({ name: evil, agent_id: "evil-one" });
  fx.saveBtn.click();
  assert.deepEqual(fx.calls.toast, [[`Saved '${evil}' to your presets.`, "success", 2500]]);
  const lib = fx.readStore();
  assert.equal(lib[0].label, evil);
  assert.equal(lib[0].name, evil);
  assert.equal(fx.doc.images.length, 0); // nothing ever parsed it as markup
});

// ---------------------------------------------------------- manager UI (P2)

test("manager: collapsed with a count badge by default, opens as a disclosure", () => {
  const fx = mountFixture();
  fx.start();
  const disc = fx.doc.getElementById("btnPresetManager");
  assert.equal(disc.getAttribute("aria-expanded"), "false");
  assert.equal(fx.doc.getElementById("presetManager").hidden, true);
  assert.equal(fx.doc.getElementById("presetManagerEmpty").hidden, true);
  assert.equal(fx.doc.getElementById("presetManagerBadge").textContent, "Saved presets (0)");
  fx.open();
  assert.equal(disc.getAttribute("aria-expanded"), "true");
  assert.equal(fx.doc.getElementById("presetManager").hidden, false);
  assert.equal(fx.doc.getElementById("presetManagerEmpty").hidden, false);
  assert.equal(
    fx.doc.getElementById("presetManagerEmpty").textContent,
    "No saved presets yet — fill the form and hit 'Save as preset'.",
  );
  fx.open(); // second click collapses again
  assert.equal(disc.getAttribute("aria-expanded"), "false");
  assert.equal(fx.doc.getElementById("presetManager").hidden, true);
  assert.equal(fx.doc.getElementById("presetManagerEmpty").hidden, true);
});

test("manager: rows mirror the sessions pattern — info block, quoted aria-labels, no builtins", () => {
  const fx = mountFixture();
  fx.seed(["My DBA kit", "Sec review kit"]);
  fx.start();
  assert.equal(fx.doc.getElementById("presetManagerBadge").textContent, "Saved presets (2)");
  fx.open();
  const rows = fx.rows();
  assert.equal(rows.length, 2);
  assert.equal(rows[0].getAttribute("role"), "listitem");
  assert.equal(fx.labelOf(rows[0]), "🧩 My DBA kit");
  assert.equal(rows[0].querySelector(".preset-row-meta").textContent, "Seeded agent · Seeder");
  const load = rows[0].querySelector(".preset-act-load");
  assert.equal(load.textContent, "Load");
  assert.equal(load.getAttribute("aria-label"), 'Load preset "My DBA kit"');
  assert.equal(rows[0].querySelector(".preset-rename").getAttribute("aria-label"), 'Rename preset "My DBA kit"');
  assert.equal(rows[0].querySelector(".preset-delete").getAttribute("aria-label"), 'Delete preset "My DBA kit"');
  for (const btn of rows[0].querySelectorAll("button")) {
    assert.equal(btn.type, "button", "manager buttons can never submit");
  }
  assert.ok(!fx.doc.getElementById("presetManager").textContent.includes("Security Auditor"), "no builtin rows");
});

test("manager: Load fills the form and toasts exactly like a chip", () => {
  const fx = mountFixture();
  fx.seed(["My DBA kit"]);
  fx.start();
  fx.open();
  fx.rows()[0].querySelector(".preset-act-load").click();
  assert.deepEqual(native(fx.calls.fill), [
    {
      agent_id: "seed-my-dba-kit",
      name: "Seeded agent",
      role: "Seeder",
      system_prompt: "Seeded preset used by the manager tests.",
      color: "#0ea5e9",
      avatar: "🧩",
    },
  ]);
  assert.deepEqual(fx.calls.toast, [["Loaded preset: My DBA kit", "info", 2000]]);
});

test("manager: rename swaps in a focused input, Enter commits and focus returns", () => {
  const fx = mountFixture();
  fx.seed(["My DBA kit"]);
  fx.start();
  fx.open();
  const renameBtn = fx.rows()[0].querySelector(".preset-rename");
  renameBtn.click();
  const input = fx.doc.getElementById("presetManager").querySelector(".preset-rename-input");
  assert.ok(input, "the label swapped for an input");
  assert.equal(input.value, "My DBA kit");
  assert.equal(input.getAttribute("aria-label"), "Preset name");
  assert.equal(input.maxLength, 60);
  assert.equal(fx.doc.activeElement, input, "focus lands in the input");
  input.value = "Renamed kit";
  input.dispatchEvent(new fx.win.KeyboardEvent("keydown", { key: "Enter", bubbles: true }));
  assert.deepEqual(fx.readStore().map((p) => p.label), ["Renamed kit"]);
  assert.equal(fx.labelOf(fx.rows()[0]), "🧩 Renamed kit");
  assert.equal(fx.doc.activeElement, fx.rows()[0].querySelector(".preset-rename"), "focus returns to ✎");
  assert.deepEqual(fx.calls.toast, [], "a visible rename needs no toast");
});

test("manager: rename commits on blur and Esc cancels", () => {
  const fx = mountFixture();
  fx.seed(["My DBA kit", "Sec review kit"]);
  fx.start();
  fx.open();
  const inputOf = (label) => {
    fx.rowByLabel(label).querySelector(".preset-rename").click();
    return fx.doc.getElementById("presetManager").querySelector(".preset-rename-input");
  };
  let input = inputOf("My DBA kit");
  input.value = "Blurred kit";
  input.blur();
  assert.deepEqual(fx.readStore().map((p) => p.label).sort(), ["Blurred kit", "Sec review kit"]);

  input = inputOf("Sec review kit");
  input.value = "Discarded";
  input.dispatchEvent(new fx.win.KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
  assert.deepEqual(fx.readStore().map((p) => p.label).sort(), ["Blurred kit", "Sec review kit"], "Esc changed nothing");
  assert.ok(!fx.doc.getElementById("presetManager").querySelector(".preset-rename-input"), "input closed");
  assert.deepEqual(fx.calls.toast, [], "cancelling is silent");
});

test("manager: rename refuses taken, builtin and invalid labels — old label stands", () => {
  const fx = mountFixture();
  fx.seed(["My DBA kit", "Sec review kit"]);
  fx.start();
  fx.open();
  const tryRename = (from, to) => {
    fx.rowByLabel(from).querySelector(".preset-rename").click();
    const input = fx.doc.getElementById("presetManager").querySelector(".preset-rename-input");
    input.value = to;
    input.dispatchEvent(new fx.win.KeyboardEvent("keydown", { key: "Enter", bubbles: true }));
    return fx.doc.activeElement;
  };
  let focus = tryRename("My DBA kit", "Sec review kit");
  assert.deepEqual(fx.calls.toast[0], ["That name is already taken — pick another.", "error", 4000]);
  focus = tryRename("My DBA kit", "DB Expert");
  assert.deepEqual(fx.calls.toast[1], ["That name is already taken — pick another.", "error", 4000]);
  assert.ok(fx.rowByLabel("My DBA kit"), "the old label still stands");
  assert.equal(focus, fx.rowByLabel("My DBA kit").querySelector(".preset-rename"), "focus returns to ✎");
  tryRename("My DBA kit", "   ");
  assert.deepEqual(fx.calls.toast[2], [MSG.label, "error", 4000]);
  assert.deepEqual(fx.readStore().map((p) => p.label).sort(), ["My DBA kit", "Sec review kit"]);
});

test("manager: core rename/delete refuse builtins and unknown labels", () => {
  const fx = mountFixture();
  const store = fx.win.MLMPresets.createStore({ storage: fx.storage });
  const renamed = store.rename("DB Expert", "Mine now");
  assert.equal(renamed.ok, false);
  assert.equal(renamed.error, "No preset named 'DB Expert'.");
  const deleted = store.deleteByLabel("DB Expert");
  assert.equal(deleted.ok, false);
  assert.equal(deleted.error, "No preset named 'DB Expert'.");
});

test("manager: delete is one-click, silent and updates badge + empty state", () => {
  const fx = mountFixture();
  fx.seed(["My DBA kit"]);
  fx.start();
  fx.open();
  fx.rows()[0].querySelector(".preset-delete").click();
  assert.deepEqual(fx.readStore(), []);
  assert.equal(fx.rows().length, 0);
  assert.equal(fx.doc.getElementById("presetManagerBadge").textContent, "Saved presets (0)");
  assert.equal(fx.doc.getElementById("presetManagerEmpty").hidden, false, "empty state returns");
  assert.deepEqual(fx.calls.toast, [], "sessions-style delete is silent");
});

test("manager: saving refreshes the badge and rows; a re-mount keeps one listener per button", () => {
  const fx = mountFixture();
  fx.start();
  fx.open();
  fx.fill({ name: "Fresh kit", agent_id: "fresh-kit" });
  fx.saveBtn.click();
  assert.equal(fx.doc.getElementById("presetManagerBadge").textContent, "Saved presets (1)");
  assert.equal(fx.rows().length, 1);
  assert.equal(fx.labelOf(fx.rows()[0]), "🔐 Fresh kit");

  fx.start(); // idempotent re-mount: no duplicated rows, no duplicated handlers
  fx.fill({ name: "Second kit", agent_id: "second-kit" });
  fx.saveBtn.click();
  assert.equal(fx.doc.getElementById("presetManagerBadge").textContent, "Saved presets (2)");
  assert.equal(fx.rows().length, 2);
  assert.equal(fx.calls.toast.length, 2, "each save toasted exactly once");
  assert.equal(fx.doc.getElementById("btnPresetManager").getAttribute("aria-expanded"), "false", "re-mount folds it");
  fx.open();
  assert.equal(fx.doc.getElementById("presetManager").hidden, false, "one toggle per click");
  assert.equal(fx.doc.getElementById("presetManagerEmpty").hidden, true, "rows exist: no empty line");
});

test("manager: hostile labels render inert and never touch style attributes", () => {
  const fx = mountFixture();
  const evil = "<img src=x onerror=alert(1)>";
  fx.seed([{ label: evil, name: "<script>alert(2)</script>", role: '"><b onmouseover=alert(3)>' }]);
  fx.start();
  fx.open();
  const row = fx.rows()[0];
  assert.equal(fx.labelOf(row), "🧩 " + evil);
  assert.equal(row.querySelector(".preset-row-meta").textContent, '<script>alert(2)</script> · "><b onmouseover=alert(3)>');
  assert.equal(fx.doc.images.length, 0, "nothing was parsed as markup");
  assert.equal(row.querySelectorAll("[style]").length, 0, "no inline styles from presets");
  const input = (row.querySelector(".preset-rename").click(), fx.doc.getElementById("presetManager").querySelector(".preset-rename-input"));
  assert.equal(input.value, evil, "the rename input carries the raw label, unsanitized and inert");
});

// ------------------------------------------------------------------- export

test("export: single preset file is the spec's pretty-printed object, nothing else", () => {
  const fx = mountFixture();
  const preset = {
    label: "My DBA kit",
    agent_id: "my-dba-kit",
    name: "My DBA kit",
    role: "DBA",
    system_prompt: "You are a careful DBA.",
    color: "#0ea5e9",
    avatar: "🧩",
    evil: "should never be written",
  };
  assert.equal(
    fx.win.MLMPresets.encodePreset(preset),
    `{
  "schema": "mlm-agent-preset/1",
  "label": "My DBA kit",
  "agent_id": "my-dba-kit",
  "name": "My DBA kit",
  "role": "DBA",
  "system_prompt": "You are a careful DBA.",
  "color": "#0ea5e9",
  "avatar": "🧩"
}
`,
  );
});

test("export: library file wraps self-describing entries and round-trips losslessly", () => {
  const fx = mountFixture();
  fx.seed(["My DBA kit", "Sec review kit"]);
  const stored = fx.readStore(); // exactly what the library holds
  const text = fx.win.MLMPresets.encodeLibrary(stored);
  assert.ok(text.endsWith("\n"), "trailing newline");
  const data = native(JSON.parse(text));
  assert.deepEqual(Object.keys(data), ["schema", "presets"]);
  assert.equal(data.schema, "mlm-agent-preset-lib/1");
  assert.equal(data.presets.length, 2);
  for (const entry of data.presets) {
    assert.equal(entry.schema, "mlm-agent-preset/1", "every entry stays self-describing");
    for (const [key, value] of Object.entries(entry)) {
      assert.equal(typeof value, "string", `${key} must be a string (no numbers, no nesting)`);
    }
  }
  // Round-trip: parse → validate each → deep-equal what the library held
  // (criterion #2). Both sides carry their own `schema` — nothing is lost.
  const back = data.presets.map((raw) => {
    const res = fx.win.MLMPresets.validatePreset(raw);
    assert.equal(res.ok, true, JSON.stringify(res));
    return native(res.value);
  });
  assert.deepEqual(back, stored);
});

test("export: filenames follow the slug rules (§5.4)", () => {
  const { exportName, libraryExportName } = mountFixture().win.MLMPresets;
  assert.equal(exportName("Security Auditor"), "preset-security-auditor.json");
  assert.equal(exportName("🧩 Küt/Kit  v2!"), "preset-k-t-kit-v2.json", "unicode runs collapse to one dash");
  assert.equal(exportName("!!!"), "preset-preset.json", "empty slug falls back to 'preset'");
  assert.equal(exportName("A".repeat(60)), "preset-" + "a".repeat(40) + ".json", "capped at 40 chars");
  assert.equal(
    exportName("a".repeat(39) + "!b c"),
    "preset-" + "a".repeat(39) + ".json",
    "a dash stranded by the cap is trimmed",
  );
  assert.equal(libraryExportName(new Date(2026, 8, 23, 12)), "agent-presets-2026-09-23.json");
  assert.match(libraryExportName(new Date()), /^agent-presets-\d{4}-\d{2}-\d{2}\.json$/);
});

test("export: [⬇ All] is disabled while the library is empty, then exports the whole library", () => {
  const fx = mountFixture();
  const btn = () => fx.doc.getElementById("btnPresetExportAll");
  fx.start();
  assert.equal(btn().disabled, true, "nothing to export yet");
  fx.seed(["My DBA kit", "Sec review kit"]);
  fx.start();
  assert.equal(btn().disabled, false);
  btn().click();
  assert.equal(fx.calls.download.length, 1);
  const [name, text] = fx.calls.download[0];
  assert.match(name, /^agent-presets-\d{4}-\d{2}-\d{2}\.json$/);
  const data = native(JSON.parse(text));
  assert.equal(data.schema, "mlm-agent-preset-lib/1");
  assert.deepEqual(data.presets.map((p) => p.label), ["My DBA kit", "Sec review kit"]);
  assert.deepEqual(fx.calls.toast, [["Exported 2 presets.", "success", 2500]]);
});

test("export: per-row [⬇] writes one self-describing preset file", () => {
  const fx = mountFixture();
  fx.seed(["My DBA kit"]);
  fx.start();
  fx.open();
  const rowExport = fx.rows()[0].querySelector(".preset-export");
  assert.equal(rowExport.getAttribute("aria-label"), 'Export preset "My DBA kit"');
  rowExport.click();
  assert.equal(fx.calls.download.length, 1);
  const [name, text] = fx.calls.download[0];
  assert.equal(name, "preset-my-dba-kit.json");
  const data = native(JSON.parse(text));
  assert.equal(data.schema, "mlm-agent-preset/1");
  assert.equal(data.label, "My DBA kit");
  assert.deepEqual(fx.calls.toast, [["Exported 'My DBA kit'.", "success", 2500]]);
  assert.equal(fx.doc.images.length, 0);
});

test("export: a failing download seam toasts plainly and stays non-destructive", () => {
  const fx = mountFixture();
  fx.seed(["My DBA kit"]);
  fx.start({ download: () => { throw new Error("blocked by the browser"); } });
  fx.open();
  fx.rows()[0].querySelector(".preset-export").click();
  assert.deepEqual(fx.calls.toast, [["Couldn't export that file.", "error", 4000]]);
  assert.deepEqual(fx.readStore().map((p) => p.label), ["My DBA kit"], "library untouched");
  fx.doc.getElementById("btnPresetExportAll").click();
  assert.deepEqual(fx.calls.toast[1], ["Couldn't export that file.", "error", 4000]);
});

// ------------------------------------------------------------------- import

test("import: gate 1 — a file over 512 KB is refused before parsing", () => {
  const store = core.createStore({ storage: fakeStorage() });
  const res = store.importText("x".repeat(512 * 1024 + 1));
  assert.equal(res.ok, false);
  assert.equal(res.error, "That file is too large to be a preset file.");
  assert.deepEqual(native(store.load()), []);
});

test("import: gate 2 — non-JSON is refused in plain words", () => {
  const store = core.createStore({ storage: fakeStorage() });
  const res = store.importText("{{{ definitely not json");
  assert.equal(res.ok, false);
  assert.equal(res.error, "That file isn't valid JSON.");
});

test("import: gate 3 — the file must self-describe; shape picks single vs library", () => {
  const store = core.createStore({ storage: fakeStorage() });
  const bad = [
    "{}",
    "[]",
    JSON.stringify({ schema: "mlm-agent-preset/9", label: "X" }),
    JSON.stringify({ schema: "mlm-agent-preset-lib/1" }),
    JSON.stringify({ schema: "mlm-agent-preset-lib/1", presets: {} }),
    JSON.stringify({ schema: "mlm-agent-preset-lib/1", presets: "nope" }),
  ];
  for (const text of bad) {
    const res = store.importText(text);
    assert.equal(res.ok, false, text);
    assert.equal(res.error, "That file was made for a different version of this app.", text);
  }
  const tooMany = {
    schema: "mlm-agent-preset-lib/1",
    presets: Array.from({ length: 101 }, (_, i) => base({ label: "Kit " + i, agent_id: "kit-" + i + "-one" })),
  };
  const res = store.importText(JSON.stringify(tooMany));
  assert.equal(res.ok, false);
  assert.equal(res.error, "That file lists more presets than this app supports.");
});

test("import: single preset files and library files both auto-detect", () => {
  const store = core.createStore({ storage: fakeStorage() });
  const one = store.importText(JSON.stringify(base({ label: "Solo kit", agent_id: "solo-kit" })));
  assert.deepEqual(native(one.value), { imported: 1, skipped: 0, total: 1 });
  const two = store.importText(
    JSON.stringify({
      schema: "mlm-agent-preset-lib/1",
      presets: [base({ label: "Kit A", agent_id: "kit-a-one" }), base({ label: "Kit B", agent_id: "kit-b-one" })],
    }),
  );
  assert.deepEqual(native(two.value), { imported: 2, skipped: 0, total: 2 });
  assert.deepEqual(native(store.load().map((p) => p.label)), ["Solo kit", "Kit A", "Kit B"]);
});

test("import: prototype-pollution payloads are inert and extra keys are dropped", () => {
  const store = core.createStore({ storage: fakeStorage() });
  const text =
    '{"schema":"mlm-agent-preset-lib/1","__proto__":{"polluted":"yes"},"constructor":{"prototype":{"polluted":"yes"}},' +
    '"presets":[{"schema":"mlm-agent-preset/1","__proto__":{"polluted":"yes"},"prototype":{"polluted":"yes"},' +
    '"constructor":{"prototype":{"polluted":"yes"}},"label":"Hostile kit","agent_id":"hostile-one","name":"H",' +
    '"role":"R","system_prompt":"At least ten characters here.","color":"#0ea5e9","avatar":"🧩",' +
    '"extra":{"nested":{"deep":[1,2,3]}}}]}';
  const res = store.importText(text);
  assert.deepEqual(native(res.value), { imported: 1, skipped: 0, total: 1 });
  assert.equal(Object.prototype.polluted, undefined, "Object.prototype stays clean");
  assert.equal({}.polluted, undefined);
  const [entry] = native(store.load());
  assert.deepEqual(Object.keys(entry), [
    "schema",
    "agent_id",
    "name",
    "role",
    "system_prompt",
    "color",
    "avatar",
    "label",
  ]);
  assert.equal(entry.extra, undefined);
});

test("import: valid entries land, bad ones are skipped, each skip is reported", () => {
  const warns = [];
  const store = core.createStore({ storage: fakeStorage(), warn: (m) => warns.push(m) });
  const res = store.importText(
    JSON.stringify({
      schema: "mlm-agent-preset-lib/1",
      presets: [
        base({ label: "Good one", agent_id: "good-one" }),
        base({ label: "Bad color", agent_id: "bad-color", color: "red" }),
        base({ label: "Bad prompt", agent_id: "bad-prompt", system_prompt: "short" }),
        base({ label: "Nested junk", agent_id: "nested-junk", system_prompt: { deep: ["x"] } }),
        base({ label: "Bad types", agent_id: "bad-types", name: 42 }),
        base({ label: "Good two", agent_id: "good-two" }),
      ],
    }),
  );
  assert.deepEqual(native(res.value), { imported: 2, skipped: 4, total: 6 });
  assert.deepEqual(native(store.load().map((p) => p.label)), ["Good one", "Good two"]);
  assert.equal(warns.length, 4, "every skipped entry is reported with details");
  assert.ok(warns.some((m) => m.includes("Bad color") && m.includes("hex code")), warns.join(" | "));
});

test("import: collisions take the (imported) family against builtins and saved labels", () => {
  const store = core.createStore({ storage: fakeStorage() });
  store.save(base({ label: "My kit", agent_id: "my-kit" }));
  const res = store.importText(
    JSON.stringify({
      schema: "mlm-agent-preset-lib/1",
      presets: [
        base({ label: "My kit", agent_id: "my-kit-2" }),
        base({ label: "DB Expert", agent_id: "db-expert-2" }),
        base({ label: "My kit", agent_id: "my-kit-3" }),
      ],
    }),
  );
  assert.equal(res.ok, true);
  assert.deepEqual(native(store.load().map((p) => p.label)), [
    "My kit",
    "My kit (imported)",
    "DB Expert (imported)",
    "My kit (imported 2)",
  ]);
});

test("import: fills to the preset cap, reports the remainder, refuses a full library", () => {
  const bounds = { presets: 4, presetChars: 8192, libraryChars: 262144 };
  const store = core.createStore({ storage: fakeStorage(), bounds });
  store.save(base({ label: "Existing", agent_id: "existing-one" }));
  const presets = Array.from({ length: 5 }, (_, i) => base({ label: "Kit " + i, agent_id: "kit-" + i + "-one" }));
  const res = store.importText(JSON.stringify({ schema: "mlm-agent-preset-lib/1", presets }));
  assert.deepEqual(native(res.value), { imported: 3, skipped: 2, total: 5 });
  assert.equal(store.load().length, 4, "filled to the cap, no further");

  const full = core.createStore({ storage: fakeStorage(), bounds });
  for (const i of [0, 1, 2, 3]) full.save(base({ label: "N" + i, agent_id: "n" + i + "-one" }));
  const refused = full.importText(JSON.stringify({ schema: "mlm-agent-preset-lib/1", presets: [presets[0]] }));
  assert.equal(refused.ok, false);
  assert.equal(refused.error, "Your preset library is full (4 presets). Remove or export some first.");
  assert.equal(full.load().length, 4);
});

test("import: per-preset and library size bounds hold on the merge", () => {
  // A base preset is ~235 compact chars; 300 lets the small one in and keeps
  // the 500-char-prompt one out, so the bound is exercised, not the validator.
  const store = core.createStore({
    storage: fakeStorage(),
    bounds: { presets: 50, presetChars: 300, libraryChars: 262144 },
  });
  const res = store.importText(
    JSON.stringify({
      schema: "mlm-agent-preset-lib/1",
      presets: [base({ label: "Small", agent_id: "small-one" }), base({ label: "Big", agent_id: "big-one", system_prompt: "y".repeat(500) })],
    }),
  );
  assert.deepEqual(native(res.value), { imported: 1, skipped: 1, total: 2 });

  const tight = core.createStore({
    storage: fakeStorage(),
    bounds: { presets: 50, presetChars: 8192, libraryChars: 400 },
  });
  const over = tight.importText(
    JSON.stringify({
      schema: "mlm-agent-preset-lib/1",
      presets: [base({ label: "A", agent_id: "a-one" }), base({ label: "B", agent_id: "b-one" })],
    }),
  );
  assert.equal(over.ok, false);
  assert.equal(over.error, "Your preset library is too large to save more. Export it, then remove some presets.");
  assert.deepEqual(native(tight.load()), []);
});

test("import: a throwing setItem leaves the old library intact", () => {
  const storage = fakeStorage();
  const store = core.createStore({ storage });
  store.save(base({ label: "Before", agent_id: "before-one" }));
  storage.setItem = () => {
    throw new Error("quota exceeded");
  };
  const res = store.importText(JSON.stringify(base({ label: "After", agent_id: "after-one" })));
  assert.equal(res.ok, false);
  assert.equal(res.error, "Couldn't save — this browser's storage is full or blocked.");
  assert.deepEqual(native(store.load().map((p) => p.label)), ["Before"]);
});

test("import: a file where nothing survives validation changes nothing", () => {
  const store = core.createStore({ storage: fakeStorage() });
  const res = store.importText(
    JSON.stringify({
      schema: "mlm-agent-preset-lib/1",
      presets: [base({ label: "Bad", agent_id: "no" }), base({ label: "Also bad", agent_id: "x" })],
    }),
  );
  assert.equal(res.ok, false);
  assert.equal(res.error, "That file didn't contain any usable presets.");
  assert.deepEqual(native(store.load()), []);
});

test("import UI: the Import button opens the hidden picker, and the picker imports", () => {
  const fx = mountFixture();
  fx.start();
  const input = fx.doc.getElementById("presetImportInput");
  let opened = 0;
  input.click = () => {
    opened += 1;
  };
  fx.doc.getElementById("btnPresetImport").click();
  assert.equal(opened, 1, "Import opens the file picker");

  const text = JSON.stringify({
    schema: "mlm-agent-preset-lib/1",
    presets: [base({ label: "Kit A", agent_id: "kit-a-one" }), base({ label: "Kit B", agent_id: "kit-b-one" })],
  });
  fx.pickFile(text);
  assert.equal(fx.calls.files.length, 1, "the chosen file reached the read seam");
  assert.deepEqual(native(fx.readStore().map((p) => p.label)), ["Kit A", "Kit B"]);
  assert.deepEqual(fx.calls.toast, [["Imported 2 presets.", "success", 3000]]);
  assert.equal(fx.doc.getElementById("presetManagerBadge").textContent, "Saved presets (2)");
  assert.equal(fx.doc.getElementById("btnPresetExportAll").disabled, false);
});

test("import UI: partial imports are reported honestly and the same file can re-fire", () => {
  const fx = mountFixture();
  fx.start();
  const text = JSON.stringify({
    schema: "mlm-agent-preset-lib/1",
    presets: [
      base({ label: "Good", agent_id: "good-one" }),
      base({ label: "Bad", agent_id: "bad-one", color: "nope" }),
      base({ label: "Also good", agent_id: "also-good-one" }),
      base({ label: "Bad two", agent_id: "bad-two-one", name: "" }),
    ],
  });
  fx.pickFile(text);
  assert.deepEqual(fx.calls.toast[0], ["Imported 2 of 4 — 2 skipped.", "warning", 4000]);
  assert.equal(fx.readStore().length, 2);
  fx.pickFile(text); // the input was cleared, so picking the same file imports again
  assert.equal(fx.calls.toast.length, 2);
  assert.deepEqual(native(fx.readStore().map((p) => p.label)), ["Good", "Also good", "Good (imported)", "Also good (imported)"]);
});

test("import UI: failures toast plainly, stay non-destructive and never throw", () => {
  const fx = mountFixture();
  fx.start();
  fx.pickFile("not json at all");
  assert.deepEqual(fx.calls.toast, [["That file isn't valid JSON.", "error", 4000]]);
  assert.deepEqual(native(fx.readStore()), []);

  const unreadable = mountFixture();
  unreadable.start({ readFile: (file, done) => done(null) });
  unreadable.pickFile("whatever");
  assert.deepEqual(unreadable.calls.toast, [["Couldn't read that file.", "error", 4000]]);
});
