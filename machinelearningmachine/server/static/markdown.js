/*
 * Rendering of untrusted text for the dashboard.
 *
 * Everything rendered here comes from a source an attacker may control: agent
 * replies (a configured LLM can be steered by the task text), custom agent
 * names/roles created through POST /api/agents, or a hand-edited JSON file in
 * ~/.module_mesh/sessions. So:
 *
 *   - Marked turns markdown into HTML, DOMPurify sanitises it against an
 *     explicit allowlist. No regex "sanitising" - that class of filter is
 *     bypassable (unquoted event handlers, odd casing, SVG/MathML payloads,
 *     parser differences).
 *   - Output is produced as DOM nodes (RETURN_DOM_FRAGMENT) and inserted with
 *     replaceChildren(), so raw text is never assigned to innerHTML.
 *   - If a library is missing (vendor folder not built, file corrupted) the
 *     renderer degrades to escaped plain text, never to unsanitised HTML.
 *
 * Exposed as window.MeshRender and as a CommonJS module so the behaviour can be
 * unit-tested under jsdom - see tests/js/sanitize.test.mjs.
 */
(function (root, factory) {
  "use strict";
  var api = factory(root);
  root.MeshRender = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function (globalScope) {
  "use strict";

  // Tags that must never survive, even as an empty shell. `svg`/`math` are on
  // the list because foreign-content subtrees are where HTML sanitizers have
  // historically been bypassed (foreignObject, animate, set).
  var BLOCKED_TAGS = [
    "style", "script", "iframe", "object", "embed", "applet", "svg", "math",
    "form", "template", "noscript", "link", "meta", "base", "frame", "frameset",
    "xmp", "plaintext", "animate", "animatetransform", "set", "audio", "video",
    "source", "track", "canvas", "map", "area",
  ];

  // What markdown legitimately needs. `src`/`srcset` are deliberately absent so
  // rendered content can never fetch a remote resource (or a tracking pixel)
  // on its own; image syntax therefore degrades to its alt text.
  var ALLOWED_TAGS = [
    "a", "b", "blockquote", "br", "code", "col", "colgroup", "dd", "del",
    "details", "div", "dl", "dt", "em", "figcaption", "figure", "h1", "h2",
    "h3", "h4", "h5", "h6", "hr", "i", "ins", "kbd", "li", "mark", "ol", "p",
    "pre", "q", "rp", "rt", "s", "samp", "section", "small", "span", "strong",
    "sub", "summary", "sup", "table", "tbody", "td", "tfoot", "th", "thead",
    "tr", "ul", "var", "wbr",
  ];

  var ALLOWED_ATTR = [
    "href", "title", "alt", "class", "colspan", "rowspan", "start", "reversed",
    "align", "valign", "lang", "dir",
  ];

  // Only these URL shapes survive: javascript:, data:, vbscript:, file: do not.
  var SAFE_URI = /^(?:(?:https?|mailto|tel):|[#/])/i;

  function doc() {
    return (globalScope && globalScope.document) || (typeof document !== "undefined" ? document : null);
  }

  function markedLib() {
    return typeof globalScope.marked !== "undefined" ? globalScope.marked
      : (typeof marked !== "undefined" ? marked : null);
  }

  function purify() {
    if (typeof globalScope.DOMPurify !== "undefined" && globalScope.DOMPurify) {
      return globalScope.DOMPurify.isSupported === false ? null : globalScope.DOMPurify;
    }
    return null;
  }

  /** True when a real sanitizer is available; callers must not render raw HTML without it. */
  function sanitizerAvailable() {
    return purify() !== null;
  }

  function sanitizeOptions() {
    return {
      ALLOWED_TAGS: ALLOWED_TAGS,
      ALLOWED_ATTR: ALLOWED_ATTR,
      ALLOW_DATA_ATTR: false,
      ALLOW_ARIA_ATTR: false,
      ALLOWED_URI_REGEXP: SAFE_URI,
      FORBID_TAGS: BLOCKED_TAGS,
      FORBID_ATTR: ["src", "srcdoc", "srcset", "formaction", "action", "xlink:href",
        "background", "style", "data", "poster", "dynsrc", "lowsrc", "ping", "nonce"],
      KEEP_CONTENT: true,          // drop the tag, keep the (escaped) text
      ALLOW_UNKNOWN_PROTOCOLS: false,
    };
  }

  function markdownToHtml(text) {
    var lib = markedLib();
    if (lib && typeof lib.parse === "function") {
      if (typeof lib.setOptions === "function") {
        lib.setOptions({ gfm: true, breaks: true, headerIds: false, mangle: false });
      }
      return lib.parse(String(text));
    }
    // No markdown engine: treat the content as literal text. Escaping happens in
    // the DOM (textContent), so there is no string path that could inject markup.
    return null;
  }

  function textFragment(text, className) {
    var d = doc();
    if (!d) return null;
    var pre = d.createElement("pre");
    pre.className = className || "md-plain";
    pre.textContent = text == null ? "" : String(text);
    var frag = d.createDocumentFragment();
    frag.appendChild(pre);
    return frag;
  }

  function decorate(container) {
    var links = container.querySelectorAll ? container.querySelectorAll("a[href]") : [];
    Array.prototype.forEach.call(links, function (a) {
      var href = a.getAttribute("href") || "";
      if (!SAFE_URI.test(href.trim())) {
        a.removeAttribute("href");
      } else if (/^https?:/i.test(href)) {
        a.setAttribute("target", "_blank");
      }
      // A link the user clicks must not be able to reach back into the app.
      a.setAttribute("rel", "noopener noreferrer nofollow ugc");
    });
    var codes = container.querySelectorAll ? container.querySelectorAll("pre code") : [];
    Array.prototype.forEach.call(codes, function (block) {
      if (block.parentElement) block.parentElement.classList.add("code-block");
    });
    return container;
  }

  /**
   * Render untrusted markdown into a detached DOM fragment.
   *
   * Never throws and never returns markup it could not sanitize: a missing
   * library falls back to escaped text, which is ugly but inert.
   */
  function render(content) {
    var d = doc();
    if (!d) {
      throw new Error("MeshRender needs a DOM (browser or jsdom)");
    }
    var raw = content == null ? "" : String(content);
    var html = markdownToHtml(raw);
    var sanitize = purify();
    if (html === null || !sanitize) {
      return textFragment(raw);
    }
    var clean;
    try {
      clean = sanitize.sanitize(html, Object.assign(sanitizeOptions(), { RETURN_DOM_FRAGMENT: true }));
    } catch (e) {
      return textFragment(raw);
    }
    if (!clean || !clean.childNodes || clean.childNodes.length === 0) {
      var empty = d.createDocumentFragment();
      empty.appendChild(d.createTextNode(""));
      return empty;
    }
    return decorate(clean);
  }

  /**
   * Same output as a string, for the few callers that compose HTML fragments
   * themselves. The string comes from the serializer of sanitized DOM nodes, so
   * it cannot contain anything the allowlist rejected.
   */
  function renderToString(content) {
    var d = doc();
    if (!d) return "";
    var holder = d.createElement("div");
    holder.appendChild(render(content));
    return holder.innerHTML;
  }

  /** Plain text with markup stripped - used for speech and search. */
  function toPlainText(content) {
    var d = doc();
    var raw = content == null ? "" : String(content);
    if (!d) return raw;
    var holder = d.createElement("div");
    holder.appendChild(render(raw));
    return (holder.textContent || "").replace(/[ \t]+\n/g, "\n").trim();
  }

  /** Only a plain hex colour may reach an inline style. */
  function safeColor(value, fallback) {
    var hex = typeof value === "string" ? value.trim() : "";
    return /^#[0-9a-fA-F]{6}$/.test(hex) ? hex : fallback || "#8b5cf6";
  }

  /** Short printable label only: markup characters discard the whole value. */
  function safeAvatar(value, fallback, maxChars) {
    var limit = maxChars || 8;
    if (typeof value !== "string") return fallback == null ? "" : String(fallback);
    var cleaned = value.trim().slice(0, limit);
    if (!cleaned) return fallback == null ? "" : String(fallback);
    for (var i = 0; i < cleaned.length; i++) {
      var code = cleaned.charCodeAt(i);
      var ch = cleaned[i];
      if ('<>"\'&;=()[]{}%`\\/|'.indexOf(ch) !== -1 || code < 32 || code === 127) {
        return fallback == null ? "" : String(fallback);
      }
    }
    return cleaned;
  }

  function setText(el, value) {
    if (el) el.textContent = value == null ? "" : String(value);
    return el;
  }

  /** Paint an agent chip through the CSSOM - no string interpolation at all. */
  function paintChip(el, color, alphaSuffix) {
    if (!el || !el.style || !el.style.setProperty) return el;
    var hex = safeColor(color, "#8b5cf6");
    el.style.setProperty("background-color", hex + (alphaSuffix || "30"));
    el.style.setProperty("border", "1px solid " + hex);
    return el;
  }

  return {
    render: render,
    renderToString: renderToString,
    toPlainText: toPlainText,
    safeColor: safeColor,
    safeAvatar: safeAvatar,
    setText: setText,
    paintChip: paintChip,
    sanitizerAvailable: sanitizerAvailable,
    ALLOWED_TAGS: ALLOWED_TAGS.slice(),
    BLOCKED_TAGS: BLOCKED_TAGS.slice(),
  };
});
