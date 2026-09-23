/*
 * Module presets for the Add Custom Module modal: a built-in gallery, a
 * personal library (localStorage) and local-file import/export.
 *
 * Design: docs/superpowers/specs/2026-09-23--agent-presets-design.md
 *
 * This file is a classic script (like markdown.js): it only defines
 * window.MLMPresets; app.js calls MLMPresets.mount(...) to attach the UI.
 * The core below is DOM-free so tests can exercise it via jsdom eval.
 *
 * Invariants:
 *  - The server stays ignorant of presets; no network I/O ever.
 *  - LIMITS mirrors AddAgentRequest (server/app.py) — locked by
 *    tests/js/preset-contract.test.mjs and tests/test_preset_contract.py.
 *  - Imported files are hostile data: whitelist-copy only, textContent-only
 *    rendering in the UI layer.
 */
(function () {
  "use strict";

  var PRESET_SCHEMA = "mlm-agent-preset/1";
  var LIB_SCHEMA = "mlm-agent-preset-lib/1";
  var STORAGE_KEY = "mlm.agentPresets.v1";

  /** The mirror table (spec §5.1). Server-authoritative. */
  var LIMITS = {
    agent_id: {
      min: 3,
      max: 50,
      pattern: "^[a-z0-9][a-z0-9-_]{1,48}[a-z0-9]$",
      reserved: ["system", "broadcast", "all", "*", "api", "admin", "root"],
    },
    name: { min: 1, max: 100 },
    role: { min: 1, max: 200 },
    system_prompt: { min: 10, max: 2000 },
    label: { min: 1, max: 60 },
    avatar: { max: 8 },
    color: { pattern: "^#[0-9a-fA-F]{6}$" },
  };

  var AGENT_ID_RE = new RegExp(LIMITS.agent_id.pattern);
  var COLOR_RE = new RegExp(LIMITS.color.pattern);
  var DEFAULT_COLOR = "#ec4899"; // matches the form's colour input default
  var DEFAULT_AVATAR = "\u{1F916}"; // 🤖 — matches app.js payload fallback

  var MSG = {
    notObject: "That isn't a preset object.",
    version: "That preset was made for a different version of this app.",
    agentId:
      "Module ID must be lowercase letters, numbers, dashes or underscores (3-50 chars), e.g. 'security-auditor'.",
    name: "Display Name must be 1-100 characters.",
    role: "Domain Role must be 1-200 characters.",
    systemPrompt: "System Prompt must be 10-2000 characters.",
    color: "Color must be a hex code like #0ea5e9.",
    avatar: "Emoji Avatar must be 8 characters or fewer.",
    label: "Preset name must be 1-60 characters.",
  };

  /** Length in code points — matches Python's len() on the server side. */
  function clen(s) {
    return Array.from(s).length;
  }

  function isPlainObject(v) {
    return typeof v === "object" && v !== null && !Array.isArray(v);
  }

  function asString(v) {
    return typeof v === "string" ? v : null;
  }

  function fail(field, error) {
    return { ok: false, field: field, error: error };
  }

  function pass(value) {
    return { ok: true, value: value };
  }

  /**
   * Validate one preset (form save or import entry) against the mirror table.
   * Returns { ok: true, value } with a normalized whitelist copy, or
   * { ok: false, field, error } naming the first offending field
   * (order: schema → agent_id → name → role → system_prompt → color →
   * avatar → label).
   */
  function validatePreset(raw) {
    if (!isPlainObject(raw)) {
      return fail(null, MSG.notObject);
    }
    if (raw.schema !== undefined && raw.schema !== null && raw.schema !== PRESET_SCHEMA) {
      return fail("schema", MSG.version);
    }

    var value = { schema: PRESET_SCHEMA };

    // agent_id: strip().lower() then reserved then shape — AddAgentRequest order.
    var aid = asString(raw.agent_id);
    if (aid === null) {
      return fail("agent_id", MSG.agentId);
    }
    aid = aid.trim().toLowerCase();
    if (LIMITS.agent_id.reserved.indexOf(aid) !== -1) {
      return fail("agent_id", "Module ID '" + aid + "' is reserved \u2014 pick another.");
    }
    if (clen(aid) < LIMITS.agent_id.min || clen(aid) > LIMITS.agent_id.max || !AGENT_ID_RE.test(aid)) {
      return fail("agent_id", MSG.agentId);
    }
    value.agent_id = aid;

    // name / role / system_prompt: trimmed strings within bounds.
    var textFields = [
      ["name", MSG.name],
      ["role", MSG.role],
      ["system_prompt", MSG.systemPrompt],
    ];
    for (var i = 0; i < textFields.length; i++) {
      var key = textFields[i][0];
      var message = textFields[i][1];
      var lim = LIMITS[key];
      var v = asString(raw[key]);
      if (v === null) {
        return fail(key, message);
      }
      v = v.trim();
      if (clen(v) < lim.min || clen(v) > lim.max) {
        return fail(key, message);
      }
      value[key] = v;
    }

    // color: absent/null → form default; present must pass the hex gate.
    if (raw.color === undefined || raw.color === null) {
      value.color = DEFAULT_COLOR;
    } else {
      var col = asString(raw.color);
      if (col === null) {
        return fail("color", MSG.color);
      }
      col = col.trim();
      if (!COLOR_RE.test(col)) {
        return fail("color", MSG.color);
      }
      value.color = col;
    }

    // avatar: absent/blank → 🤖; otherwise ≤ 8 code points.
    if (raw.avatar === undefined || raw.avatar === null) {
      value.avatar = DEFAULT_AVATAR;
    } else {
      var av = asString(raw.avatar);
      if (av === null) {
        return fail("avatar", MSG.avatar);
      }
      av = av.trim();
      if (av === "") {
        value.avatar = DEFAULT_AVATAR;
      } else if (clen(av) > LIMITS.avatar.max) {
        return fail("avatar", MSG.avatar);
      } else {
        value.avatar = av;
      }
    }

    // label: required, 1–60, trimmed.
    var lab = asString(raw.label);
    if (lab === null) {
      return fail("label", MSG.label);
    }
    lab = lab.trim();
    if (clen(lab) < LIMITS.label.min || clen(lab) > LIMITS.label.max) {
      return fail("label", MSG.label);
    }
    value.label = lab;

    return pass(value);
  }

  window.MLMPresets = {
    SCHEMA: PRESET_SCHEMA,
    LIB_SCHEMA: LIB_SCHEMA,
    STORAGE_KEY: STORAGE_KEY,
    LIMITS: LIMITS,
    validatePreset: validatePreset,
  };
})();
