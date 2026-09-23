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

  /**
   * First free label in one collision family, checked against every existing
   * label (built-ins + user) — one helper for both families (spec §5.2/§6.2):
   *   manual: "X" → "X (2)" → "X (3)"…
   *   import: "X" → "X (imported)" → "X (imported 2)"…
   * `existing` may be an array or a Set. Exact-match semantics: labels that
   * differ in case or spacing are different labels.
   */
  function uniqueLabel(base, existing, kind) {
    var taken = new Set(existing || []);
    if (!taken.has(base)) {
      return base;
    }
    if (kind === "import") {
      var cand = base + " (imported)";
      var n = 2;
      while (taken.has(cand)) {
        cand = base + " (imported " + n + ")";
        n += 1;
      }
      return cand;
    }
    var i = 2;
    while (taken.has(base + " (" + i + ")")) {
      i += 1;
    }
    return base + " (" + i + ")";
  }

  /**
   * Built-in gallery (spec §4.2 roster — names and slugs fixed there).
   * Code, not data: never stored in localStorage, never deletable or
   * editable; label collisions against these run through uniqueLabel.
   */
  var BUILTINS = [
    {
      schema: PRESET_SCHEMA,
      label: "Security Auditor",
      agent_id: "security-auditor",
      name: "Security Auditor",
      role: "Application Security Reviewer",
      system_prompt:
        "You are a security auditor. Review code, configurations and architecture " +
        "for vulnerabilities: injection risks, auth and secrets handling, unsafe " +
        "defaults, dependency and supply-chain exposure. Report findings by severity " +
        "with concrete, minimal fixes. Never invent CVEs; say when you are unsure.",
      color: "#ef4444",
      avatar: "🛡️",
    },
    {
      schema: PRESET_SCHEMA,
      label: "DB Expert",
      agent_id: "db-expert",
      name: "DB Expert",
      role: "Database Schema & Query Optimization Specialist",
      system_prompt:
        "You are a database expert. Design schemas, normalize without over-normalizing, " +
        "write and tune queries, and reason about indexes, transactions and migrations. " +
        "Give concrete SQL and call out lock and migration risks before recommending them.",
      color: "#0ea5e9",
      avatar: "🗄️",
    },
    {
      schema: PRESET_SCHEMA,
      label: "Performance Engineer",
      agent_id: "perf-engineer",
      name: "Performance Engineer",
      role: "Latency, Throughput & Resource Efficiency Analyst",
      system_prompt:
        "You are a performance engineer. Find bottlenecks with measurements before " +
        "opinions: complexity analysis, hot paths, allocation and I/O costs, caching " +
        "and concurrency. Prefer the smallest change that removes the bottleneck; " +
        "quantify the expected impact.",
      color: "#f59e0b",
      avatar: "⚡️",
    },
    {
      schema: PRESET_SCHEMA,
      label: "QA/Test Engineer",
      agent_id: "qa-engineer",
      name: "QA/Test Engineer",
      role: "Test Strategy & Edge-Case Hunter",
      system_prompt:
        "You are a QA and test engineer. Design test plans that chase edge cases and " +
        "regressions: boundaries, failures, concurrency and permissions. Write focused " +
        "automated tests first where it helps, and describe what you deliberately do not cover.",
      color: "#10b981",
      avatar: "🧪",
    },
    {
      schema: PRESET_SCHEMA,
      label: "Technical Writer",
      agent_id: "tech-writer",
      name: "Technical Writer",
      role: "Clear Docs, APIs & Release Notes",
      system_prompt:
        "You are a technical writer. Turn rough notes and code into clear docs: READMEs, " +
        "API references, tutorials and release notes. Prefer plain language, short " +
        "sentences and honest caveats over marketing tone. Never document behavior you " +
        "have not seen.",
      color: "#8b5cf6",
      avatar: "📝",
    },
  ];

  /** Style tokens must pass the hex gate before touching element.style (spec §5.3). */
  function safeColor(c, fallback) {
    return typeof c === "string" && COLOR_RE.test(c) ? c : fallback;
  }

  /** The six form fields — the fillForm/getFormData seam payload (spec §4.1). */
  function six(p) {
    return {
      agent_id: p.agent_id,
      name: p.name,
      role: p.role,
      system_prompt: p.system_prompt,
      color: p.color,
      avatar: p.avatar,
    };
  }

  /** Label defaults to Display Name, clamped to 60 code points (the form
   *  allows 100; rename in the manager refines it). Spec §4.2/§6.2. */
  function labelFromName(name) {
    var s = typeof name === "string" ? name.trim() : "";
    return Array.from(s)
      .slice(0, LIMITS.label.max)
      .join("");
  }

  /** Canonical file shape (spec §4.2 field order) — a whitelist-copy, so an
   *  export can never write a key the format does not define. */
  function filePreset(preset) {
    return {
      schema: PRESET_SCHEMA,
      label: preset.label,
      agent_id: preset.agent_id,
      name: preset.name,
      role: preset.role,
      system_prompt: preset.system_prompt,
      color: preset.color,
      avatar: preset.avatar,
    };
  }

  /** Pretty-printed + trailing newline (spec §5.4): human-diffable, and
   *  exactly what the import gate expects to read back. */
  function encodePreset(preset) {
    return JSON.stringify(filePreset(preset), null, 2) + "\n";
  }

  function encodeLibrary(list) {
    return JSON.stringify({ schema: LIB_SCHEMA, presets: list.map(filePreset) }, null, 2) + "\n";
  }

  /** File-name slug (spec §5.4): lowercase, every non-[a-z0-9] run → one dash,
   *  trimmed, ≤ 40 chars, 'preset' when nothing survives. */
  function slugify(label) {
    var s = String(label)
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, "-")
      .replace(/^-+|-+$/g, "")
      .slice(0, 40)
      .replace(/-+$/g, ""); // a dash stranded by the cap would read as a typo
    return s || "preset";
  }

  function exportName(label) {
    return "preset-" + slugify(label) + ".json";
  }

  function libraryExportName(when) {
    // Duck-typed, not `instanceof`: callers may pass a Date from another realm
    // (jsdom tests do) and that must still stamp *their* date.
    var d = when && typeof when.getFullYear === "function" ? when : new Date();
    function pad(n) {
      return (n < 10 ? "0" : "") + n;
    }
    return "agent-presets-" + d.getFullYear() + "-" + pad(d.getMonth() + 1) + "-" + pad(d.getDate()) + ".json";
  }

  /** Production file reader: Blob.text(); anything unreadable lands as null so
   *  the caller can toast plainly instead of throwing (spec §5.5). */
  function readFileText(file, done) {
    try {
      if (file && typeof file.text === "function") {
        file.text().then(done, function () {
          done(null);
        });
        return;
      }
    } catch (e) {
      // fall through to the plain failure below
    }
    done(null);
  }

  /** Production download: Blob + anchor, no network, no navigation (spec §4.4). */
  function downloadFile(filename, text) {
    var blob = new Blob([text], { type: "application/json" });
    var url = URL.createObjectURL(blob);
    var a = document.createElement("a");
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  }

  // Per-page save-as-preset wiring (the button is static markup: one listener,
  // latest callbacks/store swapped in on each mount).
  var saveState = null;

  // Per-page manager wiring, same deal for the static disclosure button.
  var managerState = null;

  /**
   * Attach the preset UI inside the Add Custom Module modal. P1: built-in
   * chips. P2: Save-as-preset + the saved-presets manager. `fillForm(payload)`
   * writes the six fields; `getFormData()` reads them back; `toast(msg, type,
   * ms)` is app.js's showToast. Returns false (never throws) when the page
   * lacks the container or the seam is incomplete.
   */
  function mount(options) {
    options = options || {};
    var fillForm = typeof options.fillForm === "function" ? options.fillForm : null;
    var getFormData = typeof options.getFormData === "function" ? options.getFormData : null;
    var toast = typeof options.toast === "function" ? options.toast : null;
    var download = typeof options.download === "function" ? options.download : downloadFile;
    var readFile = typeof options.readFile === "function" ? options.readFile : readFileText;
    var chips = typeof document === "object" ? document.getElementById("presetChips") : null;
    if (!chips || !fillForm) {
      return false;
    }
    var store =
      options.store ||
      createStore({
        storage: options.storage,
        onNotice: function (msg) {
          if (toast) {
            toast(msg, "warning", 4000);
          }
        },
      });

    var disclosure = document.getElementById("btnPresetManager");
    var manager = document.getElementById("presetManager");
    var badge = document.getElementById("presetManagerBadge");
    var emptyMsg = document.getElementById("presetManagerEmpty");
    var exportAllBtn = document.getElementById("btnPresetExportAll");
    var importBtn = document.getElementById("btnPresetImport");
    var importInput = document.getElementById("presetImportInput");
    var managerOpen = false;

    function loadPreset(preset) {
      fillForm(six(preset));
      if (toast) {
        toast("Loaded preset: " + preset.label, "info", 2000);
      }
    }

    /** Hand one file to the download seam; a blocked browser is a toast, never
     *  a throw, and the library is untouched either way (spec §5.5). */
    function saveFile(filename, text) {
      try {
        download(filename, text);
      } catch (e) {
        if (toast) {
          toast("Couldn't export that file.", "error", 4000);
        }
        return false;
      }
      return true;
    }

    function exportOne(preset) {
      if (saveFile(exportName(preset.label), encodePreset(preset)) && toast) {
        toast("Exported '" + preset.label + "'.", "success", 2500);
      }
    }

    function exportAll() {
      var list = store.load();
      if (!list.length) {
        return; // the button is disabled while empty; belt and braces
      }
      if (saveFile(libraryExportName(new Date()), encodeLibrary(list)) && toast) {
        toast(list.length === 1 ? "Exported 1 preset." : "Exported " + list.length + " presets.", "success", 2500);
      }
    }

    // Rows are built like Saved Sessions rows: createElement + textContent,
    // never interpolated markup. An aria-label quoting the label goes through
    // setAttribute (a label containing a quote cannot escape it).
    function makeButton(className, ariaLabel, iconClass, text) {
      var btn = document.createElement("button");
      btn.type = "button";
      btn.className = className;
      btn.setAttribute("aria-label", ariaLabel);
      if (iconClass) {
        var ico = document.createElement("i");
        ico.className = iconClass;
        ico.setAttribute("aria-hidden", "true");
        btn.appendChild(ico);
      } else {
        btn.textContent = text;
      }
      return btn;
    }

    function renameButtonFor(label) {
      if (!manager) {
        return null;
      }
      var rows = manager.querySelectorAll(".preset-row");
      for (var i = 0; i < rows.length; i++) {
        if (rows[i].dataset.label === label) {
          return rows[i].querySelector(".preset-rename");
        }
      }
      return null;
    }

    /**
     * Inline rename (spec §6.2 flow 3): the label swaps for a focused input;
     * Enter/blur commit, Esc cancels. `done` is set before any re-render, so
     * the blur that browser fires when the input leaves the DOM can never
     * commit twice. A refused commit keeps the old label and toasts the rule.
     */
    function startRename(row, preset) {
      var input = document.createElement("input");
      input.type = "text";
      input.className = "preset-rename-input";
      input.maxLength = LIMITS.label.max;
      input.setAttribute("aria-label", "Preset name");
      input.value = preset.label;
      row.querySelector(".preset-row-info").replaceChild(input, row.querySelector(".preset-row-label"));
      input.focus();
      input.select();

      var done = false;
      function settle(focusLabel) {
        done = true;
        renderManager();
        var btn = renameButtonFor(focusLabel);
        if (btn) {
          btn.focus();
        }
      }
      function commit() {
        if (done) {
          return;
        }
        var res = store.rename(preset.label, input.value);
        if (!res.ok) {
          if (toast) {
            toast(res.error, "error", 4000);
          }
          settle(preset.label); // refused: the old label stands
          return;
        }
        settle(res.value.label);
      }
      input.addEventListener("keydown", function (e) {
        if (e.key === "Enter") {
          e.preventDefault();
          commit();
        } else if (e.key === "Escape") {
          e.preventDefault();
          settle(preset.label); // cancel: nothing saved, focus returns
        }
      });
      input.addEventListener("blur", commit);
    }

    function makeRow(preset) {
      var row = document.createElement("div");
      row.className = "preset-row";
      row.setAttribute("role", "listitem");
      row.dataset.label = preset.label;

      var info = document.createElement("div");
      info.className = "preset-row-info";
      var labelEl = document.createElement("p");
      labelEl.className = "preset-row-label";
      labelEl.textContent = preset.avatar + " " + preset.label;
      labelEl.title = labelEl.textContent;
      var metaEl = document.createElement("p");
      metaEl.className = "preset-row-meta";
      metaEl.textContent = preset.name + " \u00b7 " + preset.role;
      metaEl.title = metaEl.textContent;
      info.appendChild(labelEl);
      info.appendChild(metaEl);

      var actions = document.createElement("div");
      actions.className = "preset-row-actions";
      var loadBtn = makeButton("preset-act preset-act-load", 'Load preset "' + preset.label + '"', null, "Load");
      var renameBtn = makeButton("preset-act preset-rename", 'Rename preset "' + preset.label + '"', "fa-solid fa-pen");
      var delBtn = makeButton(
        "preset-act preset-act-danger preset-delete",
        'Delete preset "' + preset.label + '"',
        "fa-solid fa-trash-can",
      );
      var exportBtn = makeButton(
        "preset-act preset-export",
        'Export preset "' + preset.label + '"',
        "fa-solid fa-download",
      );
      loadBtn.addEventListener("click", function () {
        loadPreset(preset);
      });
      renameBtn.addEventListener("click", function () {
        startRename(row, preset);
      });
      exportBtn.addEventListener("click", function () {
        exportOne(preset);
      });
      delBtn.addEventListener("click", function () {
        var res = store.deleteByLabel(preset.label);
        if (!res.ok) {
          if (toast) {
            toast(res.error, "error", 4000);
          }
          return;
        }
        renderManager(); // one-click and silent, like session rows
      });
      actions.appendChild(loadBtn);
      actions.appendChild(renameBtn);
      actions.appendChild(exportBtn);
      actions.appendChild(delBtn);

      row.appendChild(info);
      row.appendChild(actions);
      return row;
    }

    /** A picked file → store import → honest summary (spec §5.2/§6.2). Any
     *  failure is a toast; the library is untouched by definition. */
    function importPicked(file) {
      readFile(file, function (text) {
        if (typeof text !== "string") {
          if (toast) {
            toast("Couldn't read that file.", "error", 4000);
          }
          return;
        }
        var res = store.importText(text);
        renderManager(); // badge/rows/Export-all reflect the library either way
        if (!toast) {
          return;
        }
        if (!res.ok) {
          toast(res.error, "error", 4000);
          return;
        }
        var v = res.value;
        if (v.skipped > 0) {
          toast("Imported " + v.imported + " of " + v.total + " \u2014 " + v.skipped + " skipped.", "warning", 4000);
        } else {
          toast(v.imported === 1 ? "Imported 1 preset." : "Imported " + v.imported + " presets.", "success", 3000);
        }
      });
    }

    /** Rebuild rows, badge and empty state from the store (single source of
     *  truth: localStorage — nothing is cached in this closure). */
    function renderManager() {
      var list = store.load();
      if (badge) {
        badge.textContent = "Saved presets (" + list.length + ")";
      }
      if (exportAllBtn) {
        exportAllBtn.disabled = list.length === 0; // nothing to export yet
      }
      if (emptyMsg) {
        emptyMsg.hidden = !managerOpen || list.length > 0;
      }
      if (!manager) {
        return;
      }
      manager.textContent = ""; // textContent discipline: nothing is parsed
      list.forEach(function (preset) {
        manager.appendChild(makeRow(preset));
      });
      manager.hidden = !managerOpen;
    }

    function setManagerOpen(next) {
      managerOpen = next;
      if (disclosure) {
        disclosure.setAttribute("aria-expanded", next ? "true" : "false");
      }
      renderManager();
    }

    while (chips.firstChild) {
      chips.removeChild(chips.firstChild); // idempotent re-mount
    }
    BUILTINS.forEach(function (preset) {
      var btn = document.createElement("button");
      btn.type = "button"; // chips can never submit the form
      // One hand-written class, styled in style.css (the .preset-btn convention
      // for classes that are not Tailwind utilities): presets.js sits outside
      // the Tailwind content scan (scripts/build_vendor.py), so utility classes
      // here would ship unstyled. Deliberately NOT .preset-btn: app.js wires
      // that class to the scenario loader (fills the prompt box). DOM built
      // like Saved Sessions rows: createElement + textContent only, never
      // interpolated markup.
      btn.className = "preset-chip";
      btn.style.borderColor = safeColor(preset.color, "#475569");
      btn.setAttribute("aria-label", "Load preset " + preset.label);
      var face = document.createElement("span");
      face.setAttribute("aria-hidden", "true");
      face.textContent = preset.avatar;
      btn.appendChild(face);
      btn.appendChild(document.createTextNode(" " + preset.label));
      btn.addEventListener("click", function () {
        loadPreset(preset);
      });
      chips.appendChild(btn);
    });

    // Save-as-preset (spec §6.2 flow 2). The button is static markup, so the
    // click listener is wired once per page (module-level `saveState` below
    // always carries the latest seam callbacks and store).
    var saveBtn = document.getElementById("btnSavePreset");
    if (saveBtn) {
      saveBtn.hidden = !getFormData;
      if (!saveState) {
        saveState = {
          getFormData: null,
          store: null,
          toast: null,
          afterSave: null,
          lastSaveAt: 0,
          lastFingerprint: null,
        };
        saveBtn.addEventListener("click", function () {
          var st = saveState;
          if (!st || !st.getFormData) {
            return;
          }
          var data = st.getFormData();
          var fingerprint = JSON.stringify(data);
          var now = Date.now();
          if (st.lastFingerprint === fingerprint && now - st.lastSaveAt < 400) {
            return; // identical intent twice inside a double-click; failures and
            // edits never block a retry. Deliberately timer-free.
          }
          var res = st.store.save({
            label: labelFromName(data.name),
            agent_id: data.agent_id,
            name: data.name,
            role: data.role,
            system_prompt: data.system_prompt,
            color: data.color,
            avatar: data.avatar,
          });
          if (!res.ok) {
            if (st.toast) {
              st.toast(res.error, "error", 4000);
            }
            return;
          }
          st.lastSaveAt = now;
          st.lastFingerprint = fingerprint;
          if (st.toast) {
            st.toast("Saved '" + res.value.label + "' to your presets.", "success", 2500);
          }
          if (st.afterSave) {
            st.afterSave();
          }
        });
      }
      saveState.getFormData = getFormData;
      saveState.store = store;
      saveState.toast = toast;
      saveState.afterSave = renderManager;
    }

    // Manager row — disclosure, Import, Export-all (spec §6.2 flows 3–4):
    // static markup, so one listener per page that delegates to the latest
    // mount. The row stays visible whether the manager is open or closed.
    if (!managerState) {
      managerState = { toggle: null, exportAll: null, pick: null, picked: null };
      if (disclosure) {
        disclosure.addEventListener("click", function () {
          if (managerState.toggle) {
            managerState.toggle();
          }
        });
      }
      if (exportAllBtn) {
        exportAllBtn.addEventListener("click", function () {
          if (managerState.exportAll) {
            managerState.exportAll();
          }
        });
      }
      if (importBtn && importInput) {
        importBtn.addEventListener("click", function () {
          if (managerState.pick) {
            managerState.pick();
          }
        });
      }
      if (importInput) {
        importInput.addEventListener("change", function () {
          var file = importInput.files && importInput.files[0];
          // Clear first: picking the same file twice must fire again, and the
          // queued read gets the handle we captured.
          importInput.value = "";
          if (file && managerState.picked) {
            managerState.picked(file);
          }
        });
      }
    }
    managerState.toggle = function () {
      setManagerOpen(!managerOpen); // collapsed by default; badge shows the count
    };
    managerState.exportAll = exportAll;
    managerState.pick = function () {
      if (importInput) {
        importInput.click();
      }
    };
    managerState.picked = importPicked;
    setManagerOpen(false);
    return true;
  }

  /**
   * Parse and gate a candidate import file (spec §5.2 gates 1–5). Pure: no
   * storage access, no writes — the caller decides whether to commit.
   *
   * `taken` is every label already in play (built-ins + saved); accepted
   * entries leave here carrying their final ` (imported)` family label, so a
   * file with three copies of "Kit" cannot produce three identical labels.
   *
   * Returns {ok:true, value:{presets, skipped, total}} — `presets` are ready
   * to append — or {ok:false, field, error} with a plain-language message.
   */
  function planImport(text, taken, bounds, warn) {
    if (typeof text !== "string" || text.length > IMPORT_MAX_BYTES) {
      return fail(null, MSG_STORE.tooLarge);
    }
    var data = null;
    try {
      data = JSON.parse(text);
    } catch (e) {
      return fail(null, MSG_STORE.notJson);
    }
    if (!isPlainObject(data)) {
      return fail(null, MSG_STORE.fileVersion);
    }
    var entries = null;
    if (data.schema === PRESET_SCHEMA) {
      entries = [data];
    } else if (data.schema === LIB_SCHEMA && Array.isArray(data.presets)) {
      if (data.presets.length > IMPORT_MAX_ENTRIES) {
        return fail(null, MSG_STORE.tooMany);
      }
      entries = data.presets;
    } else {
      return fail(null, MSG_STORE.fileVersion);
    }

    // Gate 5: accept valid entries, skip bad ones, report honestly. Every read
    // is a top-level known field, type-checked inside validatePreset and
    // whitelist-copied into a fresh object — parsed JSON is never spread or
    // walked, so nested junk and __proto__ payloads have nowhere to land.
    var labels = taken.slice();
    var presets = [];
    var skipped = 0;
    for (var i = 0; i < entries.length; i++) {
      // A skipped entry still deserves a name in the console log when it
      // carries a readable one — truncated, because a hostile file can put
      // anything in there.
      var raw = entries[i];
      var said = isPlainObject(raw) && typeof raw.label === "string" ? raw.label.trim().slice(0, 60) : "";
      var where = "Skipped preset " + (i + 1) + (said ? ' ("' + said + '")' : "");
      var v = validatePreset(raw);
      if (!v.ok) {
        skipped += 1;
        warn(where + ": " + v.error);
        continue;
      }
      if (compactSize(v.value) > bounds.presetChars) {
        skipped += 1;
        warn(where + ": " + MSG_STORE.presetLarge);
        continue;
      }
      v.value.label = uniqueLabel(v.value.label, labels, "import");
      labels.push(v.value.label);
      presets.push(v.value);
    }
    return { ok: true, value: { presets: presets, skipped: skipped, total: entries.length } };
  }

  /**
   * Library store: one localStorage key, atomic writes, bounded size (spec
   * §4.3). Storage, notice callback and bounds are injectable for tests; the
   * production default is `localStorage` with the spec bounds. When storage is
   * unavailable (private windows), the same API works against an in-memory
   * list and `onNotice` fires once with MSG.sessionOnly.
   */
  var BOUNDS = {
    presets: 50,
    presetChars: 8 * 1024,
    libraryChars: 256 * 1024,
  };

  var MSG_STORE = {
    sessionOnly: "Presets won't persist in this browser session.",
    full: function (n) {
      return "Your preset library is full (" + n + " presets). Remove or export some first.";
    },
    presetLarge: "That preset is too large to save.",
    libraryLarge: "Your preset library is too large to save more. Export it, then remove some presets.",
    writeFail: "Couldn't save \u2014 this browser's storage is full or blocked.",
    missing: function (label) {
      return "No preset named '" + label + "'.";
    },
    taken: "That name is already taken \u2014 pick another.",
    // Import gates (spec §5.2). Note `fileVersion` is deliberately distinct
    // from validatePreset's per-preset version message: one is about a file,
    // the other about an entry.
    tooLarge: "That file is too large to be a preset file.",
    notJson: "That file isn't valid JSON.",
    fileVersion: "That file was made for a different version of this app.",
    tooMany: "That file lists more presets than this app supports.",
    noUsable: "That file didn't contain any usable presets.",
  };

  // Import gates 1 and 3 (spec §5.2). 512 KB measured in UTF-16 code units —
  // the same number for any ASCII JSON, and a conservative bound otherwise.
  var IMPORT_MAX_BYTES = 512 * 1024;
  var IMPORT_MAX_ENTRIES = 100;

  /** Compact-JSON length — the serialization all size bounds are measured on. */
  function compactSize(v) {
    return JSON.stringify(v).length;
  }

  function createStore(options) {
    options = options || {};
    var bounds = options.bounds || BOUNDS;
    var builtinLabels = options.builtinLabels ||
      BUILTINS.map(function (b) {
        return b.label;
      });
    var onNotice = typeof options.onNotice === "function" ? options.onNotice : function () {};
    // Gate-5 details go to the console by default (spec §5.2); tests inject.
    var warn =
      typeof options.warn === "function"
        ? options.warn
        : function (msg) {
            if (typeof console === "object" && console && typeof console.warn === "function") {
              console.warn(msg);
            }
          };
    var storage = options.storage;
    var memory = [];
    var available = false;

    if (storage === undefined) {
      try {
        storage = typeof localStorage === "object" && localStorage ? localStorage : null;
      } catch (e) {
        storage = null;
      }
    }
    if (storage) {
      try {
        var probe = STORAGE_KEY + ":probe";
        storage.setItem(probe, "1");
        storage.removeItem(probe);
        available = true;
      } catch (e) {
        storage = null;
      }
    }
    if (!available) {
      onNotice(MSG_STORE.sessionOnly);
    }

    function read() {
      if (!available) {
        return memory.slice();
      }
      var raw = null;
      try {
        raw = storage.getItem(STORAGE_KEY);
      } catch (e) {
        return [];
      }
      if (raw === null || raw === "") {
        return [];
      }
      var data = null;
      try {
        data = JSON.parse(raw);
      } catch (e) {
        return [];
      }
      if (!isPlainObject(data) || data.schema !== LIB_SCHEMA || !Array.isArray(data.presets)) {
        return [];
      }
      var out = [];
      for (var i = 0; i < data.presets.length; i++) {
        var v = validatePreset(data.presets[i]);
        if (v.ok) {
          out.push(v.value); // whitelist-copy drops any tampered extras
        }
      }
      return out;
    }

    function write(list) {
      if (!available) {
        memory = list.slice();
        return true;
      }
      try {
        // One atomic setItem: on throw the old library stays byte-identical.
        storage.setItem(STORAGE_KEY, JSON.stringify({ schema: LIB_SCHEMA, presets: list }));
        return true;
      } catch (e) {
        return false;
      }
    }

    /** Every label in a library list — the taken set for uniqueLabel. */
    function labelsOf(list) {
      return list.map(function (p) {
        return p.label;
      });
    }

    function save(raw) {
      var v = validatePreset(raw);
      if (!v.ok) {
        return v;
      }
      var list = read();
      if (list.length >= bounds.presets) {
        return fail(null, MSG_STORE.full(bounds.presets));
      }
      if (compactSize(v.value) > bounds.presetChars) {
        return fail(null, MSG_STORE.presetLarge);
      }
      var taken = builtinLabels.concat(labelsOf(list));
      v.value.label = uniqueLabel(v.value.label, taken, "manual");
      var next = list.concat([v.value]);
      if (compactSize({ schema: LIB_SCHEMA, presets: next }) > bounds.libraryChars) {
        return fail(null, MSG_STORE.libraryLarge);
      }
      if (!write(next)) {
        return fail(null, MSG_STORE.writeFail);
      }
      return { ok: true, value: v.value };
    }

    function deleteByLabel(label) {
      var list = read();
      var idx = -1;
      for (var i = 0; i < list.length; i++) {
        if (list[i].label === label) {
          idx = i;
          break;
        }
      }
      if (idx === -1) {
        return fail(null, MSG_STORE.missing(label));
      }
      var next = list.slice();
      next.splice(idx, 1);
      if (!write(next)) {
        return fail(null, MSG_STORE.writeFail);
      }
      return { ok: true };
    }

    function rename(oldLabel, newLabel) {
      var list = read();
      var idx = -1;
      for (var i = 0; i < list.length; i++) {
        if (list[i].label === oldLabel) {
          idx = i;
          break;
        }
      }
      if (idx === -1) {
        return fail(null, MSG_STORE.missing(oldLabel));
      }
      if (newLabel === oldLabel) {
        return { ok: true, value: list[idx] };
      }
      var candidate = {};
      for (var k in list[idx]) {
        if (Object.prototype.hasOwnProperty.call(list[idx], k)) {
          candidate[k] = list[idx][k];
        }
      }
      candidate.label = newLabel;
      var v = validatePreset(candidate);
      if (!v.ok) {
        return v;
      }
      var taken = builtinLabels.concat(
        labelsOf(
          list.filter(function (p, i2) {
            return i2 !== idx;
          }),
        ),
      );
      if (taken.indexOf(v.value.label) !== -1) {
        return fail("label", MSG_STORE.taken);
      }
      var next = list.slice();
      next[idx] = v.value;
      if (!write(next)) {
        return fail(null, MSG_STORE.writeFail);
      }
      return { ok: true, value: v.value };
    }

    /**
     * Import a whole file (spec §5.2 gates 1–7). The merge happens entirely in
     * memory and lands as ONE setItem — a failure at any gate leaves the
     * stored library byte-identical.
     */
    function importText(text) {
      var list = read();
      var taken = builtinLabels.concat(labelsOf(list));
      var plan = planImport(text, taken, bounds, warn);
      if (!plan.ok) {
        return plan;
      }
      var room = bounds.presets - list.length;
      if (room <= 0) {
        return fail(null, MSG_STORE.full(bounds.presets));
      }
      var incoming = plan.value.presets.slice(0, room); // gate 6: fill to the cap
      var skipped = plan.value.skipped + (plan.value.presets.length - incoming.length);
      if (!incoming.length) {
        return fail(null, MSG_STORE.noUsable);
      }
      var next = list.concat(incoming);
      if (compactSize({ schema: LIB_SCHEMA, presets: next }) > bounds.libraryChars) {
        return fail(null, MSG_STORE.libraryLarge);
      }
      if (!write(next)) {
        return fail(null, MSG_STORE.writeFail);
      }
      return { ok: true, value: { imported: incoming.length, skipped: skipped, total: plan.value.total } };
    }

    return {
      available: available,
      load: read,
      save: save,
      deleteByLabel: deleteByLabel,
      rename: rename,
      importText: importText,
    };
  }

  window.MLMPresets = {
    SCHEMA: PRESET_SCHEMA,
    LIB_SCHEMA: LIB_SCHEMA,
    STORAGE_KEY: STORAGE_KEY,
    LIMITS: LIMITS,
    BOUNDS: BOUNDS,
    BUILTINS: BUILTINS,
    validatePreset: validatePreset,
    uniqueLabel: uniqueLabel,
    createStore: createStore,
    encodePreset: encodePreset,
    encodeLibrary: encodeLibrary,
    exportName: exportName,
    libraryExportName: libraryExportName,
    mount: mount,
  };
})();
