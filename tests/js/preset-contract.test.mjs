/*
 * Drift lock: the client half of the module-preset contract.
 *
 * `static/presets.js` validates presets in the browser against limits it
 * restates from `AddAgentRequest` — nothing links the two by construction, so
 * this file and its Python sibling (`tests/test_preset_contract.py`) each
 * restate the mirror table from the design spec literally:
 *
 *     docs/superpowers/specs/2026-09-23--agent-presets-design.md §5.1
 *
 * The Python file asserts the server equals that restatement; this one asserts
 * `MLMPresets.LIMITS` and `MLMPresets.BUILTINS` do. Change one side and one of
 * the two goes red. The table is deliberately duplicated rather than shared:
 * a shared fixture would drift along with the implementation instead of
 * failing. Runs under `node --test` and, via the frontend-security suite, under
 * `pytest`.
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

function loadCore() {
  const dom = new JSDOM("<!doctype html><html><body></body></html>", { runScripts: "dangerously" });
  dom.window.eval(readFileSync(join(staticDir, "presets.js"), "utf8"));
  return dom.window.MLMPresets;
}

const core = loadCore();
const native = (v) => JSON.parse(JSON.stringify(v));

// --------------------------------------------------------------------------
// The mirror table, restated literally from spec §5.1 — same literals as
// tests/test_preset_contract.py.
// --------------------------------------------------------------------------

const TABLE = {
  agentIdMin: 3, // effective: the pattern needs a 3rd char
  agentIdMax: 50,
  agentIdPattern: "^[a-z0-9][a-z0-9-_]{1,48}[a-z0-9]$",
  reserved: ["system", "broadcast", "all", "*", "api", "admin", "root"],
  name: [1, 100],
  role: [1, 200],
  systemPrompt: [10, 2000],
  label: [1, 60], // ours, not the server's: the spec defines it
  colorPattern: "^#[0-9a-fA-F]{6}$",
  avatarMax: 8, // code points, the same count as Python's len()
};

//: Spec §4.2: names, count and slugs are fixed, in this order.
const ROSTER = [
  ["Security Auditor", "security-auditor"],
  ["DB Expert", "db-expert"],
  ["Performance Engineer", "perf-engineer"],
  ["QA/Test Engineer", "qa-engineer"],
  ["Technical Writer", "tech-writer"],
];

test("contract: LIMITS equals the mirror table, field for field", () => {
  assert.equal(core.LIMITS.agent_id.min, TABLE.agentIdMin);
  assert.equal(core.LIMITS.agent_id.max, TABLE.agentIdMax);
  assert.equal(core.LIMITS.agent_id.pattern, TABLE.agentIdPattern);
  assert.deepEqual(native(core.LIMITS.agent_id.reserved), TABLE.reserved);
  assert.equal(core.LIMITS.name.min, TABLE.name[0]);
  assert.equal(core.LIMITS.name.max, TABLE.name[1]);
  assert.equal(core.LIMITS.role.min, TABLE.role[0]);
  assert.equal(core.LIMITS.role.max, TABLE.role[1]);
  assert.equal(core.LIMITS.system_prompt.min, TABLE.systemPrompt[0]);
  assert.equal(core.LIMITS.system_prompt.max, TABLE.systemPrompt[1]);
  assert.equal(core.LIMITS.label.min, TABLE.label[0]);
  assert.equal(core.LIMITS.label.max, TABLE.label[1]);
  assert.equal(core.LIMITS.avatar.max, TABLE.avatarMax);
  assert.equal(core.LIMITS.color.pattern, TABLE.colorPattern);
});

test("contract: schemas and the one storage key are the spec's", () => {
  assert.equal(core.SCHEMA, "mlm-agent-preset/1");
  assert.equal(core.LIB_SCHEMA, "mlm-agent-preset-lib/1");
  assert.equal(core.STORAGE_KEY, "mlm.agentPresets.v1");
  assert.deepEqual(native(Object.keys(core.BOUNDS).sort()), ["libraryChars", "presetChars", "presets"]);
  assert.equal(core.BOUNDS.presets, 50);
  assert.equal(core.BOUNDS.presetChars, 8 * 1024);
  assert.equal(core.BOUNDS.libraryChars, 256 * 1024);
});

test("contract: the gallery is exactly the spec's roster, in order", () => {
  assert.deepEqual(
    native(core.BUILTINS.map((b) => [b.label, b.agent_id])),
    ROSTER,
  );
  assert.equal(new Set(core.BUILTINS.map((b) => b.label)).size, ROSTER.length, "labels are unique");
  assert.equal(new Set(core.BUILTINS.map((b) => b.agent_id)).size, ROSTER.length, "ids are unique");
});

test("contract: every builtin is registrable — it satisfies the table on its own", () => {
  // Restated as raw checks, not by calling validatePreset: a builtin that only
  // passes because the validator is lenient is exactly the bug this catches.
  const agentIdRe = new RegExp(TABLE.agentIdPattern);
  const colorRe = new RegExp(TABLE.colorPattern);
  for (const preset of core.BUILTINS) {
    const where = `builtin ${preset.label}`;
    assert.ok(agentIdRe.test(preset.agent_id), `${where}: id shape`);
    assert.ok(preset.agent_id.length >= TABLE.agentIdMin && preset.agent_id.length <= TABLE.agentIdMax, `${where}: id length`);
    assert.ok(!TABLE.reserved.includes(preset.agent_id), `${where}: id is reserved`);
    assert.equal(preset.agent_id, preset.agent_id.trim().toLowerCase(), `${where}: id is pre-normalised (§4.2)`);
    assert.ok([...preset.name.trim()].length >= TABLE.name[0] && [...preset.name.trim()].length <= TABLE.name[1], `${where}: name`);
    assert.ok([...preset.role.trim()].length >= TABLE.role[0] && [...preset.role.trim()].length <= TABLE.role[1], `${where}: role`);
    const prompt = [...preset.system_prompt.trim()].length;
    assert.ok(prompt >= TABLE.systemPrompt[0] && prompt <= TABLE.systemPrompt[1], `${where}: prompt`);
    assert.ok(colorRe.test(preset.color), `${where}: colour`);
    assert.ok([...preset.avatar].length <= TABLE.avatarMax, `${where}: avatar`);
    assert.ok([...preset.label.trim()].length >= TABLE.label[0] && [...preset.label.trim()].length <= TABLE.label[1], `${where}: label`);
  }
});

test("contract: validatePreset is the normaliser the table promises", () => {
  const res = core.validatePreset({
    agent_id: "  Security-Auditor  ",
    name: "  Security Auditor  ",
    role: "  Reviewer  ",
    system_prompt: "  You review code carefully.  ",
    color: "  #0EA5E9  ",
    avatar: " 🛡️ ",
    label: "  Kit  ",
  });
  assert.equal(res.ok, true, JSON.stringify(res));
  assert.deepEqual(native(res.value), {
    schema: "mlm-agent-preset/1",
    agent_id: "security-auditor",
    name: "Security Auditor",
    role: "Reviewer",
    system_prompt: "You review code carefully.",
    color: "#0EA5E9",
    avatar: "🛡️",
    label: "Kit",
  });
});

test("contract: the effective agent_id floor is 3, not the field's 2", () => {
  // The one place the two sides' spellings differ: AddAgentRequest says
  // min_length=2, the pattern needs a third character. Both reject a 2-char
  // id, and that is what the table documents.
  const agentIdRe = new RegExp(TABLE.agentIdPattern);
  for (const id of ["ab", "a1", "9_", "*-"]) {
    assert.equal(agentIdRe.test(id), false, `${id} must not pass the table pattern`);
    const res = core.validatePreset({ ...sample(), agent_id: id });
    assert.equal(res.ok, false, `${id} must not pass validatePreset`);
  }
});

function sample(over = {}) {
  return {
    agent_id: "security-auditor",
    name: "Security Auditor",
    role: "Application Security Reviewer",
    system_prompt: "You review code and configs for security issues.",
    color: "#0ea5e9",
    avatar: "🛡️",
    label: "Security Auditor kit",
    ...over,
  };
}
