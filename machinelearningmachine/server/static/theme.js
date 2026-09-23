/*
 * Theme module — light/dark for the dashboard.
 *
 * Loaded synchronously in <head> (the project bans inline scripts — see
 * tests/test_frontend_security.py), which is what lets the first paint already
 * carry the right theme: resolving it after the body renders would flash.
 *
 * Dark is the dashboard's default look. On a first visit the OS preference
 * decides; an explicit choice is stored under the app's own `mesh.*` key and
 * wins from then on. Nothing here throws: a browser that blocks localStorage
 * still gets a working toggle, it just forgets the choice between visits.
 *
 * `mount()` is the seam app.js calls at the end of the body: it wires the
 * header button and keeps its aria-pressed/icon in step with the theme.
 * `palette()` is the canvas palette — the graph is drawn in JS, so CSS cannot
 * theme it.
 *
 * Tests: tests/js/theme.test.mjs (resolution, persistence, mount, and the
 * drift lock over the light layer in style.css).
 */
(function () {
  "use strict";

  var KEY = "mesh.theme"; // the app's existing localStorage convention (mesh.tts.*)
  var DARK = "dark";
  var LIGHT = "light";

  //: Canvas colours per theme. Dark matches the drawing code's original values.
  var PALETTES = {
    dark: {
      edge: "rgba(51, 65, 85, 0.4)",
      nodeFill: "rgba(15, 23, 42, 0.8)",
      nodeLabel: "#cbd5e1",
    },
    light: {
      edge: "rgba(148, 163, 184, 0.55)",
      nodeFill: "rgba(255, 255, 255, 0.9)",
      nodeLabel: "#334155",
    },
  };

  var state = null; // { button, onChange } — one wiring per page

  /** The stored choice, or null when there is none (junk included). */
  function readStored() {
    try {
      var value = window.localStorage.getItem(KEY);
      return value === LIGHT || value === DARK ? value : null;
    } catch (e) {
      return null; // private windows, blocked storage: work, just don't remember
    }
  }

  /** What the OS asks for; dark when it expresses no preference. */
  function osPrefers() {
    try {
      if (typeof window.matchMedia !== "function") {
        return DARK;
      }
      return window.matchMedia("(prefers-color-scheme: light)").matches ? LIGHT : DARK;
    } catch (e) {
      return DARK;
    }
  }

  function themeClasses() {
    return document.documentElement.classList;
  }

  function current() {
    return themeClasses().contains(DARK) ? DARK : LIGHT;
  }

  /** Put a theme on the root element. `dark` is the only class we touch, so
   *  the compiled stylesheet's `html:not(.dark)` light layer keys off it. */
  function apply(theme) {
    var root = document.documentElement;
    if (theme === DARK) {
      root.classList.add(DARK);
    } else {
      root.classList.remove(DARK);
    }
    root.dataset.theme = theme;
  }

  function syncButton() {
    if (!state || !state.button) {
      return;
    }
    var light = current() === LIGHT;
    state.button.setAttribute("aria-pressed", light ? "true" : "false");
    var icon = state.button.querySelector("i");
    if (icon) {
      icon.className = light ? "fa-solid fa-sun" : "fa-solid fa-moon";
    }
  }

  /** Choose a theme, remember it (best effort), apply it, tell the page. */
  function set(theme) {
    var next = theme === LIGHT ? LIGHT : DARK;
    try {
      window.localStorage.setItem(KEY, next);
    } catch (e) {
      // Not remembering is an acceptable outcome; not switching is not.
    }
    apply(next);
    syncButton();
    if (state && typeof state.onChange === "function") {
      state.onChange(next);
    }
    return next;
  }

  function toggle() {
    return set(current() === DARK ? LIGHT : DARK);
  }

  /**
   * Wire the header button. `onChange(theme)` runs after every change (app.js
   * uses it to repaint the canvas). Returns false — never throws — when the
   * page has no button, so a caller can mount unconditionally.
   */
  function mount(options) {
    options = options || {};
    var button = document.getElementById("btnThemeToggle");
    if (!button) {
      return false;
    }
    if (!state) {
      state = { button: button, onChange: null };
      button.addEventListener("click", toggle);
    }
    state.button = button;
    state.onChange = typeof options.onChange === "function" ? options.onChange : null;
    apply(current());
    syncButton();
    return true;
  }

  /** Canvas colours for a theme (current theme when no argument is given). */
  function palette(theme) {
    var name = theme === LIGHT || theme === DARK ? theme : current();
    var colors = PALETTES[name];
    return { edge: colors.edge, nodeFill: colors.nodeFill, nodeLabel: colors.nodeLabel };
  }

  // Resolve before the first paint — this file is in <head> for that reason.
  apply(readStored() || osPrefers());

  window.MLMTheme = {
    KEY: KEY,
    DARK: DARK,
    LIGHT: LIGHT,
    current: current,
    set: set,
    toggle: toggle,
    mount: mount,
    palette: palette,
  };
})();
