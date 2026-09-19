// Inter-Module Mesh Client Application - User-Centered Redesign
// Focus: accessibility, reliability, pleasant UX, real user value
document.addEventListener("DOMContentLoaded", () => {
  // State
  let agents = [];
  let messages = [];
  let isExecuting = false;
  /**
   * Two flags, one picture. `localRun` is this tab's own Execute click;
   * `remoteRunActive` is "some tab in this session is running", learned from the
   * feed. The button used to be disabled only when `run_started` arrived over the
   * WebSocket - so with a dead socket the user could click Execute repeatedly, and
   * a missed event left the spinner running forever. Now the request that started
   * the run is what decides, and the feed only decorates.
   */
  let localRun = false;
  let remoteRunActive = false;
  let activeRunId = null;
  let stopRequested = false;
  /** This tab's own run while it waits (202): its id and 1-indexed position. */
  let queuedRunId = null;
  let queuePosition = null;
  /**
   * Terminal frames by run id (`run_completed`/`run_error`/`run_cancelled`).
   * HTTP and the socket are separate connections, so a terminal frame can beat
   * the 202 that names the run; the late 202 reconciles against this instead of
   * adopting a dead run as queued. Run ids are never reused, so an entry can
   * only ever match the run it records. Bounded: old runs are forgotten.
   */
  let terminalRuns = new Map();
  /** When this tab's pending run request started (0 when none is in flight). */
  let pendingRequestAt = 0;
  /** True when this tab's run was queued (its HTTP already returned 202). */
  let localQueued = false;
  /** The server released this session's mesh; reconnecting would only re-allocate. */
  let sessionReleased = false;
  let activePacket = null;
  let ws = null;
  let wsReconnectAttempts = 0;
  const MAX_RECONNECT_ATTEMPTS = 10;
  let searchFilter = "";
  // Bounded by the server's own limit (sent in the WS "init" payload) so a tab
  // left open for weeks cannot grow the transcript array forever.
  let maxMessagesClient = 1000;
  let urlReaderEnabled = true;
  let authenticated = true;

  // DOM Elements
  const canvas = document.getElementById("topologyCanvas");
  const ctx = canvas ? canvas.getContext("2d") : null;
  const messagesContainer = document.getElementById("messagesContainer");
  const emptyPlaceholder = document.getElementById("emptyPlaceholder");
  const moduleList = document.getElementById("moduleList");
  const msgCountBadge = document.getElementById("msgCountBadge");
  const agentCountBadge = document.getElementById("agentCountBadge");
  const topologyLabelBadge = document.getElementById("topologyLabelBadge");
  const connectionStatus = document.getElementById("connectionStatus");

  // Form Controls
  const selectTopology = document.getElementById("selectTopology");
  const selectAgentA = document.getElementById("selectAgentA");
  const selectAgentB = document.getElementById("selectAgentB");
  const selectTurns = document.getElementById("selectTurns");
  const inputPrompt = document.getElementById("inputPrompt");
  const btnStop = document.getElementById("btnStop");
  const btnRun = document.getElementById("btnRun");
  const btnClear = document.getElementById("btnClear");
  const btnExportMd = document.getElementById("btnExportMd");
  const btnExportJson = document.getElementById("btnExportJson");
  const charCountEl = document.getElementById("charCount");
  const promptClearBtn = document.getElementById("btnClearPrompt");
  const searchInput = document.getElementById("searchMessages");
  const toastContainer = document.getElementById("toastContainer");

  // Read-Aloud / TTS controls
  const btnReadPrompt = document.getElementById("btnReadPrompt");
  const btnDictate = document.getElementById("btnDictate");
  const btnReadAll = document.getElementById("btnReadAll");
  const btnStopSpeech = document.getElementById("btnStopSpeech");
  const ttsStatus = document.getElementById("ttsStatus");
  const ttsVoiceSelect = document.getElementById("ttsVoice");
  const ttsRateSelect = document.getElementById("ttsRate");
  const ttsAutoReadBox = document.getElementById("ttsAutoRead");
  const ttsReadPromptBox = document.getElementById("ttsReadPrompt");
  const readPromptHint = document.getElementById("readPromptHint");
  const readerText = document.getElementById("readerText");
  const readerFile = document.getElementById("readerFile");
  const readerUrl = document.getElementById("readerUrl");
  const readerDisplay = document.getElementById("readerDisplay");
  const btnReadText = document.getElementById("btnReadText");
  const btnReadFile = document.getElementById("btnReadFile");
  const btnReadUrl = document.getElementById("btnReadUrl");
  const btnTestVoice = document.getElementById("btnTestVoice");

  // Sessions controls
  const btnSessions = document.getElementById("btnSessions");
  const sessionsModal = document.getElementById("sessionsModal");
  const btnCloseSessionsModal = document.getElementById("btnCloseSessionsModal");
  const sessionNameInput = document.getElementById("sessionName");
  const btnSaveSession = document.getElementById("btnSaveSession");
  const sessionsList = document.getElementById("sessionsList");
  const sessionsEmpty = document.getElementById("sessionsEmpty");

  // Modals
  const agentModal = document.getElementById("agentModal");
  const btnNewAgent = document.getElementById("btnNewAgent");
  const btnCloseAgentModal = document.getElementById("btnCloseAgentModal");
  const btnCancelAddAgent = document.getElementById("btnCancelAddAgent");
  const formAddAgent = document.getElementById("formAddAgent");

  const settingsModal = document.getElementById("settingsModal");
  const btnSettings = document.getElementById("btnSettings");
  const btnCloseSettingsModal = document.getElementById("btnCloseSettingsModal");
  const btnCancelSettings = document.getElementById("btnCancelSettings");
  const formSettings = document.getElementById("formSettings");

  const helpModal = document.getElementById("helpModal");
  const btnHelp = document.getElementById("btnHelp");
  const btnCloseHelpModal = document.getElementById("btnCloseHelpModal");
  const btnDoneHelp = document.getElementById("btnDoneHelp");

  const clearConfirmModal = document.getElementById("clearConfirmModal");
  const btnConfirmClear = document.getElementById("btnConfirmClear");
  const btnCancelClear = document.getElementById("btnCancelClear");
  const btnCloseClearModal = document.getElementById("btnCloseClearModal");

  // Node layout configuration
  const nodePositions = {
    "arena-ai": { x: 0.18, y: 0.5, color: "#8b5cf6", name: "Arena AI", avatar: "⚡" },
    "copilot": { x: 0.42, y: 0.25, color: "#06b6d4", name: "Copilot", avatar: "🐙" },
    "claude": { x: 0.42, y: 0.75, color: "#d97706", name: "Claude", avatar: "🔮" },
    "gpt": { x: 0.82, y: 0.5, color: "#10b981", name: "GPT-4o", avatar: "🌐" },
  };

  // ===== User-Centered Utilities =====

  function showToast(message, type = "info", duration = 4000) {
    if (!toastContainer) return;

    const toast = document.createElement("div");
    // Only the four known kinds may reach the class list: a value from a
    // response must never be able to add a class (or anything else) to markup.
    const icons = {
      success: "fa-check-circle",
      error: "fa-exclamation-circle",
      warning: "fa-exclamation-triangle",
      info: "fa-info-circle",
    };
    const kind = Object.prototype.hasOwnProperty.call(icons, type) ? type : "info";
    toast.className = "toast toast-" + kind;
    toast.setAttribute("role", "alert");
    toast.setAttribute("aria-live", "polite");

    const icon = document.createElement("i");
    icon.className = "fa-solid " + icons[kind] + " mt-0.5 flex-shrink-0";
    icon.setAttribute("aria-hidden", "true");

    const label = document.createElement("span");
    label.className = "flex-1";
    // textContent, not an HTML string: toasts quote provider errors, saved
    // session names and server details, all of which are untrusted text.
    label.textContent = String(message == null ? "" : message);

    const dismissBtn = document.createElement("button");
    dismissBtn.className = "ml-2 text-current opacity-60 hover:opacity-100 flex-shrink-0";
    dismissBtn.setAttribute("aria-label", "Dismiss notification");
    const dismissIcon = document.createElement("i");
    dismissIcon.className = "fa-solid fa-xmark text-xs";
    dismissIcon.setAttribute("aria-hidden", "true");
    dismissBtn.appendChild(dismissIcon);
    dismissBtn.addEventListener("click", () => dismissToast(toast));

    toast.appendChild(icon);
    toast.appendChild(label);
    toast.appendChild(dismissBtn);

    toastContainer.appendChild(toast);
    
    // Auto dismiss
    const timeout = setTimeout(() => dismissToast(toast), duration);
    toast.addEventListener("mouseenter", () => clearTimeout(timeout));
    toast.addEventListener("mouseleave", () => {
      setTimeout(() => dismissToast(toast), 2000);
    });
  }

  function dismissToast(toast) {
    toast.style.animation = "slideOutRight 0.3s ease-in forwards";
    setTimeout(() => {
      if (toast.parentNode) toast.parentNode.removeChild(toast);
    }, 300);
  }

  // ===== API access =====
  // One wrapper for every call: JSON in, and a 401 reveals the token prompt
  // instead of failing silently (the server requires a token when it is bound
  // to a non-loopback interface).
  async function apiFetch(url, options) {
    const opts = Object.assign({}, options || {});
    if (opts.body && typeof opts.body !== "string") opts.body = JSON.stringify(opts.body);
    if (opts.body) {
      opts.headers = Object.assign({ "Content-Type": "application/json" }, opts.headers || {});
    }
    opts.credentials = opts.credentials || "same-origin";
    let resp;
    try {
      resp = await fetch(url, opts);
    } catch (e) {
      if (url.indexOf("/api/auth/") !== 0) showAuthPanel("Cannot reach the server - is it still running?");
      throw e;
    }
    if (resp.status === 401 && url.indexOf("/api/auth/") !== 0) {
      showAuthPanel();
      authenticated = false;
    } else if (resp.ok && url.indexOf("/api/auth/") === 0) {
      authenticated = true;
    }
    return resp;
  }

  function authPanel() {
    return document.getElementById("authPanel");
  }

  function showAuthPanel(message) {
    const panel = authPanel();
    if (!panel) return;
    panel.classList.remove("hidden");
    const hint = document.getElementById("authPanelHint");
    if (hint && message) setText(hint, message);
    const input = document.getElementById("authTokenInput");
    if (input && document.activeElement !== input) input.focus();
  }

  function hideAuthPanel() {
    const panel = authPanel();
    if (panel) panel.classList.add("hidden");
  }

  function wireAuthPanel() {
    const form = document.getElementById("authForm");
    if (!form) return;
    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      const input = document.getElementById("authTokenInput");
      const status = document.getElementById("authPanelHint");
      const token = (input && input.value ? input.value : "").trim();
      if (!token) {
        if (status) setText(status, "Paste the token the server was started with.");
        return;
      }
      try {
        const resp = await apiFetch("/api/auth/login", { method: "POST", body: { token: token } });
        if (resp.ok) {
          if (input) input.value = "";
          hideAuthPanel();
          showToast("Signed in - this browser has its own mesh", "success");
          reconnectWebSocket();
          refreshMeshState();
        } else {
          const data = await resp.json().catch(() => ({}));
          if (status) setText(status, data.detail || "That token was not accepted.");
        }
      } catch (err) {
        if (status) setText(status, "Could not reach the server.");
      }
    });
  }

  /** Pull agents/history/limits after signing in or loading a session. */
  async function refreshMeshState() {
    try {
      const [agentsResp, statusResp] = await Promise.all([
        apiFetch("/api/agents"),
        apiFetch("/api/status"),
      ]);
      if (agentsResp.ok) {
        agents = await agentsResp.json();
        renderAgentList();
        requestRedraw(true);
      }
      if (statusResp.ok) applyStatus(await statusResp.json());
    } catch (e) {
      /* the WebSocket will bring state anyway */
    }
  }

  function applyStatus(status) {
    if (!status) return;
    const mode = status.provider_mode || "simulated";
    if (mode === "simulated") {
      setModeBanner({
        live: false,
        text: "Simulation mode: answers come from the built-in template simulator. No model was called, and no generated code was run or tested.",
      });
    } else if (mode === "unverified") {
      setModeBanner({
        live: true,
        text: "Provider configured but not verified" + ((status.last_run_warnings || []).length
          ? ` - last run reported: ${status.last_run_warnings.join("; ")}` : ""),
      });
    } else {
      setModeBanner({
        live: true,
        text: "Live providers configured for this browser session only (keys are never saved to disk).",
      });
    }
    const msgs = document.getElementById("statusSessionInfo");
    if (msgs) {
      setText(msgs, `${status.agents || 0} modules · ${status.messages || 0} messages · ` +
        `${status.live_sessions || 0} live session(s) on this server`);
    }
  }

  // No hand-rolled escaper lives here any more. Text that must become markup is
  // rendered through MeshRender (static/markdown.js); everything else uses
  // textContent/setAttribute, which cannot be escaped out of.

  // ===== SpeechKit: read what you write (browser Web Speech API) =====
  // Zero-install text-to-speech: uses the voices already on the user's
  // computer. Settings persist in localStorage; long texts are chunked to
  // work around the Chrome long-utterance cutoff.
  const SpeechKit = (() => {
    const supported = "speechSynthesis" in window && "SpeechSynthesisUtterance" in window;
    let voices = [];
    let voiceURI = localStorage.getItem("mesh.tts.voice") || "";
    let rate = parseFloat(localStorage.getItem("mesh.tts.rate") || "1") || 1;
    let autoRead = localStorage.getItem("mesh.tts.auto") === "1";
    let readPromptOnRun = localStorage.getItem("mesh.tts.prompt") === "1";
    let queue = [];            // { text, label, msgId }
    let busy = false;
    let speakingMsgId = null;
    let speakingLabel = null;

    function loadVoices() {
      if (!supported) return;
      const v = window.speechSynthesis.getVoices();
      if (v && v.length) {
        voices = Array.from(v);
        if (!voiceURI) {
          const def = voices.find(x => x.default) || voices.find(x => x.lang.startsWith("en")) || voices[0];
          voiceURI = def ? def.voiceURI : "";
        }
      }
    }
    if (supported) {
      loadVoices();
      window.speechSynthesis.onvoiceschanged = loadVoices;
    }

    function pickVoice() {
      if (!voiceURI) return null;
      return voices.find(v => v.voiceURI === voiceURI) || null;
    }

    function stripMarkdown(text) {
      if (!text) return "";
      let t = String(text);
      t = t.replace(/```[\s\S]*?```/g, (m) => m.replace(/^```[^\n]*\n?/, "").replace(/```$/, ""));
      t = t.replace(/`([^`]+)`/g, "$1");
      t = t.replace(/!\[([^\]]*)\]\([^)]*\)/g, "$1");
      t = t.replace(/\[([^\]]+)\]\([^)]*\)/g, "$1");
      t = t.replace(/^#{1,6}\s+/gm, "");
      t = t.replace(/^\s*(?:[-*+]|\d+\.)\s+/gm, "");
      t = t.replace(/[*_~>#|]/g, " ");
      t = t.replace(/https?:\/\/\S+/g, " (link) ");
      return t.replace(/\n{3,}/g, "\n\n").trim();
    }

    function chunkText(text, size = 320) {
      const chunks = [];
      let rest = text;
      while (rest.length > size) {
        let cut = rest.lastIndexOf(".", size);
        if (cut < size * 0.4) cut = rest.lastIndexOf("\n", size);
        if (cut < size * 0.4) cut = rest.lastIndexOf(" ", size);
        if (cut < size * 0.4) cut = size;
        chunks.push(rest.slice(0, cut + 1));
        rest = rest.slice(cut + 1);
      }
      if (rest) chunks.push(rest);
      return chunks;
    }

    function setSpeakingUI(msgId, label) {
      speakingMsgId = msgId || null;
      speakingLabel = label || null;
      updateStatus();
      document.querySelectorAll("[data-msg-speak]").forEach((b) => {
        const on = !!speakingMsgId && b.dataset.msgSpeak === String(speakingMsgId);
        b.classList.toggle("speaking", on);
        b.innerHTML = on
          ? '<i class="fa-solid fa-stop" aria-hidden="true"></i>'
          : '<i class="fa-solid fa-volume-high" aria-hidden="true"></i>';
        b.setAttribute("aria-label", on ? "Stop reading this message" : "Read this message aloud");
        b.title = on ? "Stop reading" : "Read this message aloud";
      });
    }

    function updateStatus() {
      if (!ttsStatus) return;
      const inQueue = queue.length;
      if (busy && speakingLabel) {
        ttsStatus.textContent = `🔊 Speaking: ${speakingLabel}${inQueue ? ` · ${inQueue} more in queue` : ""}`;
        ttsStatus.className = "text-[11px] text-pink-400 speaking-pulse";
      } else {
        ttsStatus.textContent = inQueue ? `⏸ ${inQueue} in queue` : "Idle";
        ttsStatus.className = "text-[11px] text-slate-500";
      }
    }

    function processQueue() {
      if (busy || !supported) return;
      const item = queue.shift();
      updateStatus();
      if (!item) { setSpeakingUI(null, null); return; }
      busy = true;
      setSpeakingUI(item.msgId, item.label);
      const chunks = chunkText(item.text);
      let i = 0;
      const next = () => {
        if (!busy) return; // stopped by user
        if (i < chunks.length) {
          const u = new SpeechSynthesisUtterance(chunks[i++]);
          const v = pickVoice();
          if (v) { u.voice = v; u.lang = v.lang; }
          u.rate = rate;
          u.onend = next;
          u.onerror = next;
          window.speechSynthesis.speak(u);
        } else {
          busy = false;
          processQueue();
        }
      };
      next();
    }

    function enqueue(text, opts = {}) {
      const clean = stripMarkdown(text);
      if (!clean) return false;
      queue.push({
        text: clean,
        label: opts.label || "Speech",
        msgId: opts.msgId != null ? String(opts.msgId) : null,
      });
      processQueue();
      return true;
    }

    function speak(text, opts = {}) {
      if (!supported) {
        showToast("Speech is not supported in this browser - try Chrome or Edge", "warning", 4000);
        return false;
      }
      if (opts.interrupt !== false) {
        queue = [];
        busy = false;
        window.speechSynthesis.cancel();
      }
      return enqueue(text, opts);
    }

    function stop() {
      queue = [];
      busy = false;
      if (supported) window.speechSynthesis.cancel();
      setSpeakingUI(null, null);
    }

    // Pause/resume keeps working when the user switches tabs in Chrome.
    let pauseTimer = null;
    if (supported) {
      setInterval(() => {
        if (window.speechSynthesis.speaking && !window.speechSynthesis.paused) {
          // Chrome pauses long speech on its own; nudge it to keep flowing.
          window.speechSynthesis.pause();
          clearTimeout(pauseTimer);
          pauseTimer = setTimeout(() => window.speechSynthesis.resume(), 50);
        }
      }, 10000);
    }

    return {
      supported,
      speak,
      enqueue,
      stop,
      getVoices: () => voices,
      getVoiceURI: () => voiceURI,
      setVoice(uri) { voiceURI = uri; localStorage.setItem("mesh.tts.voice", uri); },
      getRate: () => rate,
      setRate(r) { rate = r; localStorage.setItem("mesh.tts.rate", String(r)); },
      isAutoRead: () => autoRead,
      setAutoRead(on) { autoRead = on; localStorage.setItem("mesh.tts.auto", on ? "1" : "0"); },
      isReadPromptOnRun: () => readPromptOnRun,
      setReadPromptOnRun(on) {
        readPromptOnRun = on;
        localStorage.setItem("mesh.tts.prompt", on ? "1" : "0");
        if (readPromptHint) readPromptHint.classList.toggle("hidden", !on);
      },
      getSpeakingMsgId: () => speakingMsgId,
    };
  })();

  function initSpeechUI() {
    // Voice dropdown
    if (ttsVoiceSelect && SpeechKit.supported) {
      const populate = () => {
        const current = ttsVoiceSelect.value;
        const voices = SpeechKit.getVoices();
        ttsVoiceSelect.innerHTML = "";
        const groups = {};
        voices.forEach((v) => {
          const lang = (v.lang || "other").split("-")[0];
          (groups[lang] = groups[lang] || []).push(v);
        });
        Object.keys(groups).sort().forEach((lang) => {
          const og = document.createElement("optgroup");
          og.label = lang.toUpperCase();
          groups[lang].forEach((v) => {
            const opt = document.createElement("option");
            opt.value = v.voiceURI;
            opt.textContent = `${v.name} (${v.lang})${v.default ? " · default" : ""}`;
            if (v.voiceURI === SpeechKit.getVoiceURI()) opt.selected = true;
            og.appendChild(opt);
          });
          ttsVoiceSelect.appendChild(og);
        });
        if (current && !ttsVoiceSelect.value) ttsVoiceSelect.value = current;
      };
      populate();
      ttsVoiceSelect.addEventListener("change", () => {
        SpeechKit.setVoice(ttsVoiceSelect.value);
        showToast(`Voice: ${ttsVoiceSelect.options[ttsVoiceSelect.selectedIndex]?.text || ""}`, "info", 1500);
      });
    } else if (ttsVoiceSelect) {
      ttsVoiceSelect.disabled = true;
      ttsVoiceSelect.placeholder = "Not supported";
    }
    if (ttsRateSelect) {
      ttsRateSelect.value = String(SpeechKit.getRate());
      ttsRateSelect.addEventListener("change", () => SpeechKit.setRate(parseFloat(ttsRateSelect.value) || 1));
    }
    if (ttsAutoReadBox) {
      ttsAutoReadBox.checked = SpeechKit.isAutoRead();
      ttsAutoReadBox.addEventListener("change", () => {
        SpeechKit.setAutoRead(ttsAutoReadBox.checked);
        showToast(ttsAutoReadBox.checked ? "Replies will be read aloud automatically" : "Auto-read off", "info", 2000);
      });
    }
    if (ttsReadPromptBox) {
      ttsReadPromptBox.checked = SpeechKit.isReadPromptOnRun();
      readPromptHint.classList.toggle("hidden", !SpeechKit.isReadPromptOnRun());
      ttsReadPromptBox.addEventListener("change", () => {
        SpeechKit.setReadPromptOnRun(ttsReadPromptBox.checked);
        showToast(ttsReadPromptBox.checked ? "Your prompt will be read aloud before it is sent" : "Prompt read-aloud off", "info", 2000);
      });
    }
    if (btnTestVoice) {
      btnTestVoice.addEventListener("click", () => {
        SpeechKit.speak("Hello! This is how your chosen voice will sound in the Machine Learning Machine.", { label: "Voice test" });
      });
    }
    if (btnStopSpeech) {
      btnStopSpeech.addEventListener("click", () => {
        SpeechKit.stop();
        showToast("Speech stopped", "info", 1500);
      });
    }
  }

  function speakMessage(msg) {
    const id = msg.id ? String(msg.id) : null;
    if (id && SpeechKit.getSpeakingMsgId() === id) {
      SpeechKit.stop();
      return;
    }
    SpeechKit.speak(msg.content, { label: msg.sender_name || "Message", msgId: id });
  }

  // ===== Safe rendering of untrusted text =====
  // The implementation lives in markdown.js (loaded before this file) because it
  // is the app's XSS boundary and is unit-tested under jsdom in
  // tests/js/sanitize.test.mjs. If that file ever fails to load, this shim keeps
  // the app alive by rendering escaped plain text instead of unsanitised HTML.
  const MeshRender = window.MeshRender || {
    render(content) {
      const frag = document.createDocumentFragment();
      const pre = document.createElement("pre");
      pre.className = "md-plain";
      pre.textContent = content == null ? "" : String(content);
      frag.appendChild(pre);
      return frag;
    },
    renderToString(content) {
      const holder = document.createElement("div");
      holder.appendChild(this.render(content));
      return holder.innerHTML;
    },
    safeColor: (value, fallback) =>
      /^#[0-9a-fA-F]{6}$/.test(String(value || "").trim()) ? String(value).trim() : fallback || "#8b5cf6",
    safeAvatar: (value, fallback) => {
      const text = String(value == null ? "" : value).trim().slice(0, 8);
      const clean = text && !/[<>"'&;=()\[\]{}%`\\/|]/.test(text) ? text : "";
      return clean || (fallback == null ? "" : String(fallback));
    },
    setText,
    paintChip,
    sanitizerAvailable: () => false,
  };

  function renderMarkdownFragment(content) {
    return MeshRender.render(content);
  }

  /** Set text safely; used everywhere a value came from a user or a file. */
  function setText(el, value) {
    if (el) el.textContent = value == null ? "" : String(value);
    return el;
  }

  function safeColor(value, fallback) {
    return MeshRender.safeColor(value, fallback);
  }

  function paintChip(el, color, alphaSuffix) {
    return MeshRender.paintChip(el, color, alphaSuffix);
  }

  // Focus trap for modals - accessibility
  function trapFocus(modal) {
    const focusable = modal.querySelectorAll(
      'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])'
    );
    if (focusable.length === 0) return;
    
    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    
    function handleTab(e) {
      if (e.key !== "Tab") return;
      if (e.shiftKey) {
        if (document.activeElement === first) {
          e.preventDefault();
          last.focus();
        }
      } else {
        if (document.activeElement === last) {
          e.preventDefault();
          first.focus();
        }
      }
    }
    
    modal.addEventListener("keydown", handleTab);
    first.focus();
    
    return () => modal.removeEventListener("keydown", handleTab);
  }

  let currentFocusTrapCleanup = null;
  let lastFocusedElement = null;

  function openModal(modal) {
    lastFocusedElement = document.activeElement;
    modal.classList.remove("hidden");
    modal.setAttribute("aria-hidden", "false");
    // Small delay to ensure modal is visible
    setTimeout(() => {
      currentFocusTrapCleanup = trapFocus(modal);
    }, 50);
    document.body.style.overflow = "hidden";
  }

  function closeModal(modal) {
    modal.classList.add("hidden");
    modal.setAttribute("aria-hidden", "true");
    if (currentFocusTrapCleanup) {
      currentFocusTrapCleanup();
      currentFocusTrapCleanup = null;
    }
    document.body.style.overflow = "";
    if (lastFocusedElement) {
      lastFocusedElement.focus();
      lastFocusedElement = null;
    }
  }

  // Character count with user feedback
  function updateCharCount() {
    if (!inputPrompt || !charCountEl) return;
    const len = inputPrompt.value.length;
    const max = 5000;
    charCountEl.textContent = `${len} / ${max}`;
    
    charCountEl.className = "char-count";
    if (len > max * 0.9) charCountEl.classList.add("warning");
    if (len > max) charCountEl.classList.add("error");
    
    // Auto-resize
    inputPrompt.style.height = "auto";
    inputPrompt.style.height = Math.min(inputPrompt.scrollHeight, 200) + "px";
  }

  // ===== Canvas Handling - draws only while something is actually moving =====
  let animationFrameId = null;
  let needsRedraw = true;
  let lastDrawTime = 0;
  const DRAW_THROTTLE = 1000 / 30; // 30fps max to save battery

  /** Ask for a frame. The loop runs only while the graph has work to do. */
  function requestRedraw(force) {
    if (force) needsRedraw = true;
    if (animationFrameId === null && !document.hidden && canvas && ctx) {
      animationFrameId = requestAnimationFrame(drawCanvas);
    }
  }

  function stopRedraw() {
    if (animationFrameId !== null) {
      cancelAnimationFrame(animationFrameId);
      animationFrameId = null;
    }
  }

  function resizeCanvas() {
    if (!canvas) return;
    const rect = canvas.parentElement.getBoundingClientRect();
    canvas.width = rect.width * window.devicePixelRatio;
    canvas.height = rect.height * window.devicePixelRatio;
    canvas.style.width = rect.width + "px";
    canvas.style.height = rect.height + "px";
    if (ctx) ctx.scale(window.devicePixelRatio, window.devicePixelRatio);
    requestRedraw(true);
  }

  window.addEventListener("resize", resizeCanvas);
  resizeCanvas();

  // Pause animation when tab is not visible - battery saving
  document.addEventListener("visibilitychange", () => {
    if (document.hidden) {
      stopRedraw();
    } else {
      requestRedraw(true);
    }
  });

  function initWebSocket() {
    const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    const wsUrl = `${protocol}//${window.location.host}/ws`;

    ws = new WebSocket(wsUrl);

    ws.onopen = () => {
      wsReconnectAttempts = 0;
      connectionStatus.className = "flex items-center space-x-2 text-xs px-2.5 py-1 rounded-full bg-emerald-950/80 border border-emerald-800 text-emerald-400";
      connectionStatus.innerHTML = '<span class="w-2 h-2 rounded-full bg-emerald-400 animate-pulse"></span><span>Live Mesh Connected</span>';
      connectionStatus.setAttribute("aria-label", "Connected to live mesh");
      showToast("Connected to live mesh", "success", 2000);
    };

    ws.onclose = () => {
      if (sessionReleased) {
        // The mesh behind this tab is gone (idle timeout or capacity). Reconnecting
        // would quietly hand the tab a different, empty session, so say what
        // happened and stop instead of pretending the conversation is still there.
        connectionStatus.className = "flex items-center space-x-2 text-xs px-2.5 py-1 rounded-full bg-amber-950/80 border border-amber-800 text-amber-400";
        connectionStatus.innerHTML = '<span class="w-2 h-2 rounded-full bg-amber-400"></span><span>Session released - Reload</span>';
        return;
      }
      wsReconnectAttempts++;
      connectionStatus.className = "flex items-center space-x-2 text-xs px-2.5 py-1 rounded-full bg-amber-950/80 border border-amber-800 text-amber-400";
      connectionStatus.innerHTML = '<span class="w-2 h-2 rounded-full bg-amber-400"></span><span>Reconnecting...</span>';
      connectionStatus.setAttribute("aria-label", "Reconnecting to mesh");
      
      if (wsReconnectAttempts <= MAX_RECONNECT_ATTEMPTS) {
        const delay = Math.min(1000 * Math.pow(1.5, wsReconnectAttempts), 10000);
        setTimeout(initWebSocket, delay);
      } else {
        connectionStatus.className = "flex items-center space-x-2 text-xs px-2.5 py-1 rounded-full bg-red-950/80 border border-red-800 text-red-400";
        connectionStatus.innerHTML = '<span class="w-2 h-2 rounded-full bg-red-400"></span><span>Disconnected - Refresh to retry</span>';
        showToast("Connection lost. Please refresh the page.", "error", 5000);
      }
    };

    ws.onerror = () => {
      console.warn("WebSocket error");
    };

    ws.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data);
        handleWsEvent(data);
      } catch (err) {
        console.error("Failed to parse WS payload:", err);
      }
    };
  }

  function handleWsEvent(data) {
    if (data.type === "init") {
      activeRunId = data.active_run_id || null;
      remoteRunActive = Boolean(activeRunId);
      stopRequested = Boolean(data.cancel_requested);
      // A queued run that the snapshot no longer knows (restart, eviction) will
      // never start: release this tab instead of leaving it busy forever.
      if (localQueued && queuedRunId) {
        const stillQueued = Array.isArray(data.queued_run_ids) && data.queued_run_ids.indexOf(queuedRunId) !== -1;
        const nowActive = data.active_run_id === queuedRunId;
        if (!stillQueued && !nowActive) {
          localQueued = false;
          queuedRunId = null;
          queuePosition = null;
          localRun = false;
          showToast("The queued run was lost (the server restarted or released this session). Try again.", "warning", 6000);
        }
      }
      syncRunActivity();
      agents = data.agents || [];
      messages = (data.history || []).slice(-maxMessagesClient);
      authenticated = data.authenticated !== false;
      if (data.limits && data.limits.max_messages_client) {
        maxMessagesClient = data.limits.max_messages_client;
        messages = messages.slice(-maxMessagesClient);
      }
      if (data.flags) urlReaderEnabled = data.flags.url_reader_enabled !== false;
      applyReaderAvailability();
      hideAuthPanel();
      visibleCount = RENDER_WINDOW;
      renderAgentList();
      renderAllMessages();
      requestRedraw(true);
    } else if (data.type === "new_message") {
      const msg = data.message;
      messages.push(msg);
      trimMessages();
      // Only append if passes search filter
      if (messageMatches(msg)) {
        appendMessageToFeed(msg, true, true);
      } else {
        // Still update count
        updateMessageCount();
      }
      triggerPacketAnimation(msg.sender_id, msg.recipient_id);
      requestRedraw(true);
      // Read replies aloud automatically (skips short system notices)
      if (SpeechKit.isAutoRead() && msg.message_type !== "system") {
        SpeechKit.speak(msg.content, { label: msg.sender_name || "Message", msgId: msg.id });
      }
    } else if (data.type === "session_loaded") {
      agents = data.agents || [];
      messages = (data.history || []).slice(-maxMessagesClient);
      searchFilter = "";
      visibleCount = RENDER_WINDOW;
      if (searchInput) searchInput.value = "";
      renderAgentList();
      renderAllMessages();
      requestRedraw(true);
      showToast(`Session "${data.name || ""}" loaded - conversation restored`, "success", 4000);
    } else if (data.type === "agents_updated") {
      agents = data.agents || [];
      renderAgentList();
      requestRedraw(true);
    } else if (data.type === "history_cleared") {
      messages = [];
      visibleCount = RENDER_WINDOW;
      setModeBanner(null);
      renderAllMessages();
      showToast("Session cleared successfully", "success");
    } else if (data.type === "run_queued") {
      // Another tab (or this tab's own 202, arriving over the socket first) is
      // waiting. The button stays busy; only an idle tab is told, so the tab
      // that queued it does not get the same position twice (its HTTP already
      // said it).
      // Adopt the run it waits behind: a tab that missed run_started (a gap ate
      // it) would otherwise go busy with no id to attribute the later
      // completion to, and wedge busy forever. Frames arrive in publish order,
      // so this id is never older than what the tab already tracks.
      if (data.active_run_id) {
        activeRunId = data.active_run_id;
      }
      remoteRunActive = true;
      syncRunActivity();
      if (!localRun && data.queue_position) {
        showToast(`A run was queued at position ${data.queue_position}${data.run_id ? " (" + data.run_id + ")" : ""}`, "info", 4000);
      }
    } else if (data.type === "run_started") {
      // This tab's queued run is now the active one: it keeps localRun (its HTTP
      // returned long ago) but no longer needs its queued identity.
      if (queuedRunId && data.run_id === queuedRunId) {
        queuedRunId = null;
        queuePosition = null;
      }
      activeRunId = data.run_id;
      stopRequested = false;
      remoteRunActive = true;
      syncRunActivity();
      showToast(`Starting ${data.topology} dialogue${data.run_id ? " (" + data.run_id + ")" : ""}...`, "info", 2000);
    } else if (data.type === "run_completed" || data.type === "run_error" || data.type === "run_cancelled") {
      if (data.run_id) {
        terminalRuns.set(data.run_id, { type: data.type, error: data.error || null, at: Date.now() });
        while (terminalRuns.size > 20) {
          terminalRuns.delete(terminalRuns.keys().next().value);
        }
      }
      const ownQueuedDone = Boolean(queuedRunId && data.run_id === queuedRunId);
      const activeDone = Boolean(activeRunId && data.run_id === activeRunId);
      if (ownQueuedDone) {
        // This tab's queued run ended before it ever started (Stop while queued).
        // The active run (another tab's) is untouched.
        const wasQueuedCancel = data.type === "run_cancelled";
        queuedRunId = null;
        queuePosition = null;
        localQueued = false;
        localRun = false;
        syncRunActivity();
        if (wasQueuedCancel) {
          showToast("Queued run cancelled before it started.", "info");
        } else if (data.type === "run_error") {
          showToast("Dialogue failed: " + (data.error || "Unknown error"), "error", 5000);
        } else {
          showToast("Dialogue completed", "success");
        }
        apiFetch("/api/status").then((r) => (r.ok ? r.json() : null)).then(applyStatus).catch(() => {});
      } else if (activeDone) {
        const ownActiveWasQueued = Boolean(localQueued && localRun);
        remoteRunActive = false;
        activeRunId = null;
        if (ownActiveWasQueued) {
          // This tab's run was queued, became active, and has now finished: its
          // HTTP returned 202 long ago, so this frame is what releases the button.
          localQueued = false;
          localRun = false;
          apiFetch("/api/status").then((r) => (r.ok ? r.json() : null)).then(applyStatus).catch(() => {});
        }
        syncRunActivity();
        if (data.type === "run_cancelled") {
          if (ownActiveWasQueued && data.queued) {
            showToast("Queued run cancelled before it started.", "info");
          } else {
            showToast("Run cancelled. Replies already received have been kept.", "info");
          }
        } else if (data.type === "run_error") {
          showToast("Dialogue failed: " + (data.error || "Unknown error"), "error", 5000);
        } else if (data.truncated_count) {
          // The transcript the user is about to export is missing the tail of an
          // answer. The message itself carries a "Truncated:" notice and a badge, but
          // neither is visible while you are reading a wall of code, so say it out loud.
          showToast(`${data.truncated_count} ${data.truncated_count === 1 ? "reply was" : "replies were"} longer than the 50,000-character message limit, so the transcript is cut short`, "warning", 8000);
        } else if (data.simulated_count) {
          showToast(`${data.simulated_count} simulated answer${data.simulated_count === 1 ? "" : "s"} - nothing was executed or tested`, "warning", 6000);
        } else {
          showToast("Dialogue completed", "success");
        }
      } else if (!activeRunId && !queuedRunId && !localRun) {
        // No run tracked here at all (a completion without its start, or another
        // tab's run on an idle tab): say what happened, but touch no buttons.
        if (data.type === "run_cancelled") {
          showToast("Run cancelled. Replies already received have been kept.", "info");
        } else if (data.type === "run_error") {
          showToast("Dialogue failed: " + (data.error || "Unknown error"), "error", 5000);
        } else if (data.truncated_count) {
          showToast(`${data.truncated_count} ${data.truncated_count === 1 ? "reply was" : "replies were"} longer than the 50,000-character message limit, so the transcript is cut short`, "warning", 8000);
        } else if (data.simulated_count) {
          showToast(`${data.simulated_count} simulated answer${data.simulated_count === 1 ? "" : "s"} - nothing was executed or tested`, "warning", 6000);
        } else {
          showToast("Dialogue completed", "success");
        }
      } else {
        // Another tab's run completed while this tab tracks its own (active or
        // queued): leave this tab's buttons exactly as they are, so its own
        // completion frame - not someone else's - is what releases it.
      }
    } else if (data.type === "stream_gap") {
      // The server could not keep this tab's send queue fed (a throttled tab, a
      // stalled radio). Rather than let the transcript quietly miss messages,
      // re-pull what the bus still holds.
      resyncHistoryFromServer(`Some messages were skipped while this tab was not keeping up (${data.dropped || 0}) - refetched`);
    } else if (data.type === "session_released") {
      sessionReleased = true;
      localRun = false;
      localQueued = false;
      queuedRunId = null;
      queuePosition = null;
      remoteRunActive = false;
      activeRunId = null;
      syncRunActivity();
      showToast(data.detail || "This session was released by the server. Reload the page.", "warning", 12000);
    }
  }

  /** Enable/disable the run button from the two sources of truth, once. */
  function syncRunActivity() {
    const busy = localRun || remoteRunActive;
    isExecuting = busy;
    btnRun.disabled = busy;
    const stopTarget = queuedRunId || activeRunId;
    btnStop.disabled = !busy || !stopTarget || stopRequested;
    if (busy) {
      btnRun.innerHTML = '<i class="fa-solid fa-spinner fa-spin" aria-hidden="true"></i><span>Modules Communicating...</span>';
      btnRun.setAttribute("aria-busy", "true");
    } else {
      btnRun.innerHTML = '<i class="fa-solid fa-play" aria-hidden="true"></i><span>Execute Dialogue</span>';
      btnRun.removeAttribute("aria-busy");
      requestRedraw(true); // one last frame so the graph settles without a live loop
    }
  }

  /**
   * Re-read the transcript after the live feed admitted it dropped frames.
   * Non-fatal by construction: if the refetch fails the messages already on
   * screen stay exactly as they are.
   */
  async function resyncHistoryFromServer(note) {
    try {
      const resp = await apiFetch("/api/history?limit=" + encodeURIComponent(String(maxMessagesClient)));
      if (!resp.ok) return;
      const history = await resp.json();
      if (!Array.isArray(history)) return;
      messages = history.slice(-maxMessagesClient);
      visibleCount = RENDER_WINDOW;
      renderAllMessages();
      requestRedraw(true);
      if (note) showToast(note, "info", 5000);
    } catch (e) {
      /* keep what we have; the next message arrives normally */
    }
  }

  // ===== Provider-mode banner =====
  /** Say out loud what produced the transcript the user is looking at. */
  function setModeBanner(info) {
    const banner = document.getElementById("modeBanner");
    if (!banner) return;
    if (!info) {
      banner.classList.add("hidden");
      banner.replaceChildren();
      return;
    }
    banner.classList.remove("hidden");
    banner.replaceChildren();
    const icon = document.createElement("i");
    icon.className = "fa-solid " + (info.live ? "fa-plug-circle-check" : "fa-flask");
    icon.setAttribute("aria-hidden", "true");
    const text = document.createElement("span");
    text.textContent = info.text;
    banner.appendChild(icon);
    banner.appendChild(text);
  }

  // ===== Availability of the (opt-in) page reader =====
  function applyReaderAvailability() {
    const btn = document.getElementById("btnReadUrl");
    const hint = document.getElementById("readerUrlHint");
    if (!btn) return;
    if (urlReaderEnabled) {
      btn.disabled = false;
      if (hint) hint.classList.add("hidden");
      return;
    }
    btn.disabled = true;
    btn.title = "Disabled by the server";
    if (hint) {
      hint.classList.remove("hidden");
      setText(hint, "Reading web pages is disabled on this server. Start it with --enable-url-reader.");
    }
  }

  function drawCanvas(timestamp = 0) {
    animationFrameId = null;

    if (!ctx || !canvas || document.hidden) {
      return; // no loop while the tab is hidden or the canvas is gone
    }

    // Throttle, but never below the "something moved" signal.
    if (timestamp - lastDrawTime < DRAW_THROTTLE && !needsRedraw && !activePacket && !isExecuting) {
      return;
    }
    lastDrawTime = timestamp;
    
    const w = canvas.width / window.devicePixelRatio;
    const h = canvas.height / window.devicePixelRatio;
    
    ctx.clearRect(0, 0, w, h);

    // Build map of active positions
    const posMap = {};
    agents.forEach((ag, idx) => {
      if (nodePositions[ag.agent_id]) {
        posMap[ag.agent_id] = {
          x: nodePositions[ag.agent_id].x * w,
          y: nodePositions[ag.agent_id].y * h,
          color: ag.color || nodePositions[ag.agent_id].color,
          name: ag.name,
          avatar: ag.avatar,
        };
      } else {
        const angle = (idx / agents.length) * 2 * Math.PI;
        posMap[ag.agent_id] = {
          x: w * 0.5 + Math.cos(angle) * (w * 0.35),
          y: h * 0.5 + Math.sin(angle) * (h * 0.35),
          color: ag.color || "#ec4899",
          name: ag.name,
          avatar: ag.avatar || "🧩",
        };
      }
    });

    // Draw connection edges
    const agentIds = Object.keys(posMap);
    for (let i = 0; i < agentIds.length; i++) {
      for (let j = i + 1; j < agentIds.length; j++) {
        const p1 = posMap[agentIds[i]];
        const p2 = posMap[agentIds[j]];
        ctx.beginPath();
        ctx.moveTo(p1.x, p1.y);
        ctx.lineTo(p2.x, p2.y);
        ctx.strokeStyle = "rgba(51, 65, 85, 0.4)";
        ctx.lineWidth = 1.5;
        ctx.setLineDash([4, 4]);
        ctx.stroke();
        ctx.setLineDash([]);
      }
    }

    // Draw active animated packet if any
    if (activePacket) {
      const pFrom = posMap[activePacket.fromId];
      const pTo = posMap[activePacket.toId] || { x: w * 0.5, y: h * 0.5 };

      if (pFrom && pTo) {
        const curX = pFrom.x + (pTo.x - pFrom.x) * activePacket.progress;
        const curY = pFrom.y + (pTo.y - pFrom.y) * activePacket.progress;

        ctx.save();
        ctx.shadowBlur = 14;
        ctx.shadowColor = activePacket.color || "#a855f7";
        ctx.beginPath();
        ctx.arc(curX, curY, 6, 0, Math.PI * 2);
        ctx.fillStyle = activePacket.color || "#c084fc";
        ctx.fill();
        ctx.restore();

        activePacket.progress += 0.03;
        if (activePacket.progress >= 1.0) {
          activePacket = null;
        }
        needsRedraw = true; // keeps this frame's follow-up scheduled
      } else {
        activePacket = null;
      }
    }

    // Draw agent nodes
    for (const [id, node] of Object.entries(posMap)) {
      const radius = 22;

      ctx.save();
      ctx.beginPath();
      ctx.arc(node.x, node.y, radius + 2, 0, Math.PI * 2);
      ctx.fillStyle = "rgba(15, 23, 42, 0.8)";
      ctx.fill();
      ctx.lineWidth = 2.5;
      ctx.strokeStyle = node.color;
      ctx.shadowBlur = isExecuting ? 12 : 6;
      ctx.shadowColor = node.color;
      ctx.stroke();
      ctx.restore();

      ctx.font = "16px sans-serif";
      ctx.textAlign = "center";
      ctx.textBaseline = "middle";
      ctx.fillText(node.avatar, node.x, node.y);

      ctx.font = "bold 11px sans-serif";
      ctx.fillStyle = "#cbd5e1";
      ctx.fillText(node.name, node.x, node.y + radius + 14);
    }

    needsRedraw = false;

    // Only keep the loop alive if the packet is still in flight or a run is
    // executing. An idle graph costs nothing: no frame, no CPU.
    if (activePacket || isExecuting || needsRedraw) {
      requestRedraw();
    }
  }

  function triggerPacketAnimation(fromId, toId) {
    // Any new packet restarts the loop if it had gone idle.
    const color = nodePositions[fromId]?.color || "#a855f7";
    activePacket = {
      fromId: fromId,
      toId: toId === "*" ? "gpt" : toId,
      progress: 0.0,
      color: color,
    };
    needsRedraw = true;
  }

  // Render Registered Modules.
  // Names, roles, colours and avatars are user-supplied (POST /api/agents, or a
  // hand-edited saved-session file), so they are written with textContent and
  // CSSOM - never interpolated into an HTML string.
  function renderAgentList() {
    setText(agentCountBadge, `${agents.length} Modules`);
    agentCountBadge.setAttribute("aria-label", `${agents.length} modules registered`);

    const currentA = selectAgentA.value;
    const currentB = selectAgentB.value;
    selectAgentA.replaceChildren();
    selectAgentB.replaceChildren();
    moduleList.replaceChildren();

    agents.forEach((ag, idx) => {
      const roleShort = String(ag.role || "").split("&")[0].trim();

      const optA = document.createElement("option");
      optA.value = String(ag.agent_id || "");
      optA.textContent = `${ag.name || ag.agent_id} (${roleShort})`;
      if (ag.agent_id === currentA || (!currentA && idx === 0)) optA.selected = true;
      selectAgentA.appendChild(optA);

      const optB = document.createElement("option");
      optB.value = String(ag.agent_id || "");
      optB.textContent = `${ag.name || ag.agent_id} (${roleShort})`;
      if (ag.agent_id === currentB || (!currentB && idx === 1)) optB.selected = true;
      selectAgentB.appendChild(optB);

      const item = document.createElement("div");
      item.className = "agent-card p-2.5 rounded-lg bg-slate-800/70 border border-slate-700/60 flex items-center justify-between cursor-pointer";
      item.setAttribute("role", "button");
      item.setAttribute("tabindex", "0");
      item.setAttribute("aria-label", `${ag.name || "module"}, ${roleShort}`);

      const left = document.createElement("div");
      left.className = "flex items-center space-x-2.5 overflow-hidden";

      const chip = document.createElement("div");
      chip.className = "w-7 h-7 rounded-md flex items-center justify-center text-sm font-bold flex-shrink-0";
      chip.setAttribute("aria-hidden", "true");
      paintChip(chip, ag.color, "25");
      setText(chip, MeshRender.safeAvatar(ag.avatar, "🤖"));

      const names = document.createElement("div");
      names.className = "truncate";
      const title = document.createElement("h4");
      title.className = "text-xs font-semibold text-slate-200 truncate";
      setText(title, ag.name || ag.agent_id);
      const sub = document.createElement("p");
      sub.className = "text-[10px] text-slate-400 truncate";
      setText(sub, ag.role || "");
      names.appendChild(title);
      names.appendChild(sub);

      left.appendChild(chip);
      left.appendChild(names);

      const kind = ag.provider_kind === "live" ? `Live: ${ag.provider || ""}` : "Simulator";
      const prov = document.createElement("span");
      prov.className = "text-[10px] font-mono px-1.5 py-0.5 rounded border " +
        (ag.provider_kind === "live"
          ? "bg-emerald-950 border-emerald-800 text-emerald-300"
          : "bg-slate-900 border-slate-800 text-slate-400");
      setText(prov, kind);

      item.appendChild(left);
      item.appendChild(prov);

      const detail = `${ag.name || "module"}: ${ag.role || ""}. ${kind}`;
      item.addEventListener("click", () => showToast(detail, "info", 3000));
      item.addEventListener("keydown", (e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          item.click();
        }
      });

      moduleList.appendChild(item);
    });
  }

  function updateMessageCount(visibleCount) {
    const total = messages.length;
    const shown = typeof visibleCount === "number" ? visibleCount : total;
    if (searchFilter) {
      setText(msgCountBadge, `${shown} / ${total} messages`);
    } else if (shown < total) {
      setText(msgCountBadge, `Showing last ${shown} of ${total} messages`);
    } else {
      setText(msgCountBadge, `${total} message${total === 1 ? "" : "s"}`);
    }
  }

  // ===== Transcript rendering =====
  // Two separate bounds, both taken from the server:
  //  * `messages` never grows past max_messages_client (the bus limit), so a
  //    tab left open for days cannot eat all the memory;
  //  * at most RENDER_WINDOW cards are in the DOM at once, which keeps a long
  //    transcript scrollable without a thousand live nodes.
  const RENDER_WINDOW = 200;
  let visibleCount = RENDER_WINDOW;

  function trimMessages() {
    const limit = maxMessagesClient || messages.length;
    if (messages.length > limit) {
      messages.splice(0, messages.length - limit);
    }
  }

  function messageMatches(msg) {
    if (!searchFilter) return true;
    const needle = searchFilter.toLowerCase();
    return [msg.content, msg.sender_name, msg.message_type]
      .some((field) => String(field || "").toLowerCase().includes(needle));
  }

  function filteredMessages() {
    return messages.filter(messageMatches);
  }

  function updateEarlierButton(hidden) {
    let more = messagesContainer.querySelector(".btn-show-earlier");
    if (hidden <= 0) {
      if (more) more.remove();
      return;
    }
    const step = Math.min(hidden, RENDER_WINDOW);
    if (!more) {
      more = document.createElement("button");
      more.type = "button";
      more.className = "btn-show-earlier w-full text-[11px] py-1.5 mb-2 rounded bg-slate-800/70 border border-slate-700 text-slate-400 hover:text-slate-200";
      more.addEventListener("click", () => {
        visibleCount += RENDER_WINDOW;
        renderAllMessages();
      });
      messagesContainer.prepend(more);
    }
    setText(more, `Show ${step} earlier message${step === 1 ? "" : "s"} (${hidden} not in view)`);
  }

  // Render Transcript Messages - with search and accessibility
  function renderAllMessages() {
    messagesContainer.replaceChildren();
    if (messages.length === 0) {
      if (emptyPlaceholder) emptyPlaceholder.style.display = "flex";
      setText(msgCountBadge, "0 messages");
      return;
    }
    if (emptyPlaceholder) emptyPlaceholder.style.display = "none";

    const filtered = filteredMessages();
    if (filtered.length === 0 && searchFilter) {
      const noResults = document.createElement("div");
      noResults.className = "text-center py-8 text-slate-400 no-results-msg";
      const icon = document.createElement("i");
      icon.className = "fa-solid fa-search text-2xl mb-2 opacity-50";
      icon.setAttribute("aria-hidden", "true");
      const label = document.createElement("p");
      label.className = "text-sm";
      setText(label, `No messages match "${searchFilter}"`);
      const clear = document.createElement("button");
      clear.className = "mt-2 text-xs text-indigo-400 hover:text-indigo-300 underline";
      clear.type = "button";
      setText(clear, "Clear search");
      clear.addEventListener("click", () => {
        searchFilter = "";
        visibleCount = RENDER_WINDOW;
        if (searchInput) searchInput.value = "";
        renderAllMessages();
      });
      noResults.appendChild(icon);
      noResults.appendChild(label);
      noResults.appendChild(clear);
      messagesContainer.appendChild(noResults);
      updateMessageCount(0);
      return;
    }

    const totalMatching = filtered.length;
    const windowSize = Math.min(visibleCount, totalMatching);
    const hidden = totalMatching - windowSize;
    const windowed = filtered.slice(hidden);

    if (hidden > 0) {
      updateEarlierButton(hidden);
    }

    updateMessageCount(windowed.length);
    windowed.forEach((msg) => appendMessageToFeed(msg, false, false));
    if (!searchFilter) scrollFeedToBottom();
  }

  function buildMessageCard(msg) {
    const card = document.createElement("div");
    card.className = "msg-bubble p-4 rounded-xl bg-slate-800/50 border border-slate-700/60 shadow-md space-y-2";
    card.setAttribute("role", "article");
    card.setAttribute("aria-label", `Message from ${msg.sender_name || "module"}, type ${msg.message_type || "message"}`);

    const meta = msg.metadata || {};
    const agentObj = agents.find((a) => a.agent_id === msg.sender_id) || {};

    const header = document.createElement("div");
    header.className = "flex items-center justify-between border-b border-slate-700/40 pb-2";

    const headLeft = document.createElement("div");
    headLeft.className = "flex items-center space-x-2.5";

    const chip = document.createElement("div");
    chip.className = "w-6 h-6 rounded-md flex items-center justify-center text-xs";
    chip.setAttribute("aria-hidden", "true");
    paintChip(chip, agentObj.color, "30");
    setText(chip, MeshRender.safeAvatar(agentObj.avatar, "🤖"));

    const who = document.createElement("span");
    who.className = "text-xs font-bold text-slate-200";
    setText(who, msg.sender_name || msg.sender_id || "module");

    headLeft.appendChild(chip);
    headLeft.appendChild(who);

    const target = document.createElement("span");
    if (msg.recipient_id && msg.recipient_id !== "*") {
      target.className = "text-[11px] font-medium text-slate-400";
      const at = document.createElement("span");
      at.className = "text-indigo-400 font-semibold";
      setText(at, `@${msg.recipient_name || msg.recipient_id}`);
      target.textContent = "→ ";
      target.appendChild(at);
    } else {
      target.className = "text-[11px] text-slate-500";
      setText(target, "(broadcast)");
    }
    headLeft.appendChild(target);

    const badgeColorMap = {
      task_spec: "bg-purple-900/40 text-purple-300 border-purple-700/60",
      proposal: "bg-cyan-900/40 text-cyan-300 border-cyan-700/60",
      critique: "bg-amber-900/40 text-amber-300 border-amber-700/60",
      revision: "bg-sky-900/40 text-sky-300 border-sky-700/60",
      consensus: "bg-emerald-900/40 text-emerald-300 border-emerald-700/60",
      system: "bg-slate-800 text-slate-300 border-slate-700",
    };
    const badge = document.createElement("span");
    badge.className = "text-[10px] uppercase font-mono px-2 py-0.5 rounded-full border " +
      (badgeColorMap[msg.message_type] || "bg-slate-800 text-slate-300 border-slate-700");
    setText(badge, String(msg.message_type || "message").replace("_", " "));
    headLeft.appendChild(badge);

    // Provenance badge: simulated output must never look like a real answer.
    if (meta.provider_error) {
      const warn = document.createElement("span");
      warn.className = "prov-badge prov-degraded";
      warn.title = `${meta.provider_error} - the simulator answered instead`;
      setText(warn, "provider failed → simulated");
      headLeft.appendChild(warn);
    } else if (meta.simulated) {
      const sim = document.createElement("span");
      sim.className = "prov-badge prov-sim";
      sim.title = "Written by the built-in simulator: no model API call, no execution, no tests";
      setText(sim, "simulated");
      headLeft.appendChild(sim);
    } else if (meta.provider) {
      const live = document.createElement("span");
      live.className = "prov-badge prov-live";
      live.title = `Answer from ${meta.provider}`;
      setText(live, "live");
      headLeft.appendChild(live);
    }

    // A clamped reply says so inside its own text as well, but a badge is what you
    // scan a transcript with - and the exported Markdown is where the missing tail
    // would otherwise be silently gone.
    if (meta.content_truncated) {
      const clipped = document.createElement("span");
      clipped.className = "prov-badge prov-clipped";
      clipped.title = `${meta.content_truncated} characters were over the protocol's message limit and were dropped from the end`;
      setText(clipped, "clipped");
      headLeft.appendChild(clipped);
    }

    const headRight = document.createElement("div");
    headRight.className = "flex items-center gap-2";
    const speakBtn = document.createElement("button");
    speakBtn.className = "msg-speak-btn";
    speakBtn.type = "button";
    speakBtn.title = "Read this message aloud";
    speakBtn.setAttribute("aria-label", "Read this message aloud");
    speakBtn.innerHTML = '<i class="fa-solid fa-volume-high" aria-hidden="true"></i>';
    const time = document.createElement("span");
    time.className = "text-[10px] font-mono text-slate-500";
    setText(time, msg.formatted_time || "");
    headRight.appendChild(speakBtn);
    headRight.appendChild(time);

    const body = document.createElement("div");
    body.className = "md-body text-xs text-slate-300 leading-relaxed";
    body.appendChild(renderMarkdownFragment(msg.content));

    card.appendChild(header);
    header.appendChild(headLeft);
    header.appendChild(headRight);
    card.appendChild(body);
    card.dataset.msgId = String(msg.id || "");
    speakBtn.addEventListener("click", (e) => {
      e.stopPropagation();
      speakMessage(msg);
    });
    return card;
  }

  function appendMessageToFeed(msg, autoScroll = true, enforceWindow = true) {
    if (emptyPlaceholder) emptyPlaceholder.style.display = "none";
    const noRes = messagesContainer.querySelector(".no-results-msg");
    if (noRes) noRes.remove();

    const card = buildMessageCard(msg);

    // Highlight code blocks and attach copy buttons.
    card.querySelectorAll("pre code").forEach((block) => {
      if (typeof hljs !== "undefined") {
        try { hljs.highlightElement(block); } catch (e) { /* highlighting is cosmetic */ }
      }
      const pre = block.parentElement;
      pre.classList.add("code-container");

      const copyBtn = document.createElement("button");
      copyBtn.type = "button";
      copyBtn.className = "btn-copy-code";
      copyBtn.setAttribute("aria-label", "Copy code to clipboard");
      copyBtn.innerHTML = '<i class="fa-regular fa-copy" aria-hidden="true"></i> Copy';
      copyBtn.addEventListener("click", async () => {
        try {
          await navigator.clipboard.writeText(block.innerText);
          copyBtn.innerHTML = '<i class="fa-solid fa-check text-emerald-400" aria-hidden="true"></i> Copied!';
          showToast("Code copied to clipboard", "success", 2000);
          setTimeout(() => {
            copyBtn.innerHTML = '<i class="fa-regular fa-copy" aria-hidden="true"></i> Copy';
          }, 2000);
        } catch (e) {
          showToast("Failed to copy code", "error");
        }
      });
      pre.appendChild(copyBtn);
    });

    messagesContainer.appendChild(card);

    if (enforceWindow) {
      const filtered = filteredMessages();
      const currentCards = messagesContainer.querySelectorAll(".msg-bubble");
      const maxAllowed = Math.min(visibleCount, filtered.length);
      if (currentCards.length > maxAllowed && currentCards.length > 0) {
        currentCards[0].remove();
      }
      const hidden = filtered.length - maxAllowed;
      updateEarlierButton(hidden);
    }

    if (autoScroll) {
      scrollFeedToBottom();
    }
    updateMessageCount(messagesContainer.querySelectorAll(".msg-bubble").length);
  }


  function scrollFeedToBottom() {
    // Only auto-scroll if user is near bottom
    const isNearBottom = messagesContainer.scrollHeight - messagesContainer.scrollTop - messagesContainer.clientHeight < 100;
    if (isNearBottom) {
      messagesContainer.scrollTop = messagesContainer.scrollHeight;
    }
  }

  /** Drop and re-open the socket (after signing in, or when the server restarted). */
  function reconnectWebSocket() {
    wsReconnectAttempts = 0;
    if (ws) {
      try {
        ws.onclose = null;
        ws.close();
      } catch (e) { /* already gone */ }
      ws = null;
    }
    initWebSocket();
  }

  // ===== Event Handlers - User Centered =====

  // Topology change with better UX
  selectTopology.addEventListener("change", (e) => {
    const topo = e.target.value;
    const p2pA = document.getElementById("p2pConfigA");
    const p2pB = document.getElementById("p2pConfigB");

    if (topo === "p2p") {
      p2pA.style.display = "block";
      p2pB.style.display = "block";
      topologyLabelBadge.textContent = "P2P: Direct Bilateral Dialogue";
    } else if (topo === "pipeline") {
      p2pA.style.display = "none";
      p2pB.style.display = "none";
      topologyLabelBadge.textContent = "Sequential Pipeline: Arena -> Claude -> Copilot -> GPT";
    } else if (topo === "debate") {
      p2pA.style.display = "none";
      p2pB.style.display = "none";
      topologyLabelBadge.textContent = "Multi-Agent Debate: Copilot ⇄ Claude ⇄ GPT";
    } else if (topo === "hub") {
      p2pA.style.display = "block";
      p2pB.style.display = "none";
      topologyLabelBadge.textContent = "Hub & Spoke: Arena AI (Lead) ⇄ Spokes";
    }
  });

  // Preset buttons with feedback
  document.querySelectorAll(".preset-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      const topo = btn.dataset.topology;
      selectTopology.value = topo;
      selectTopology.dispatchEvent(new Event("change"));

      if (btn.dataset.from) selectAgentA.value = btn.dataset.from;
      if (btn.dataset.to) selectAgentB.value = btn.dataset.to;
      if (btn.dataset.prompt) {
        inputPrompt.value = btn.dataset.prompt;
        updateCharCount();
      }
      showToast(`Loaded preset: ${btn.textContent.trim()}`, "info", 2000);
      inputPrompt.focus();
    });
  });

  // Character count and auto-resize
  if (inputPrompt) {
    inputPrompt.addEventListener("input", updateCharCount);
    updateCharCount();
    
    // Keyboard shortcut: Ctrl+Enter to run
    inputPrompt.addEventListener("keydown", (e) => {
      if ((e.ctrlKey || e.metaKey) && e.key === "Enter") {
        e.preventDefault();
        btnRun.click();
      }
    });
  }

  // Clear prompt button
  if (promptClearBtn) {
    promptClearBtn.addEventListener("click", () => {
      inputPrompt.value = "";
      updateCharCount();
      inputPrompt.focus();
      showToast("Prompt cleared", "info", 1500);
    });
  }

  wireAuthPanel();

  // Search messages
  if (searchInput) {
    searchInput.addEventListener("input", (e) => {
      searchFilter = e.target.value.trim();
      visibleCount = RENDER_WINDOW;
      renderAllMessages();
    });
  }

  // Execute Dialogue with better error handling
  btnStop.addEventListener("click", async () => {
    const target = queuedRunId || activeRunId;
    if (!target || stopRequested) return;
    const stoppingQueued = Boolean(queuedRunId);
    stopRequested = true;
    syncRunActivity();
    try {
      const resp = await apiFetch(`/api/runs/${encodeURIComponent(target)}/cancel`, { method: "POST" });
      if (!resp.ok) throw new Error("Stop request was refused. The run may already have ended.");
      showToast(stoppingQueued ? "Cancelling the queued run..." : "Stopping after the current agent replies...", "info");
    } catch (err) {
      stopRequested = false;
      showToast(err.message || "Could not request Stop", "error");
      syncRunActivity();
    }
  });

  btnRun.addEventListener("click", async () => {
    const prompt = inputPrompt.value.trim();
    if (!prompt) {
      showToast("Please enter a task prompt for the modules", "warning");
      inputPrompt.focus();
      inputPrompt.classList.add("border-amber-500");
      setTimeout(() => inputPrompt.classList.remove("border-amber-500"), 2000);
      return;
    }

    if (prompt.length < 5) {
      showToast("Prompt too short - please describe your task more fully", "warning");
      return;
    }

    if (prompt.length > 5000) {
      showToast("Prompt too long (max 5000 characters)", "error");
      return;
    }

    const topo = selectTopology.value;
    const turns = parseInt(selectTurns.value, 10);
    const agentA = selectAgentA.value;
    const agentB = selectAgentB.value;

    if (topo === "p2p" && agentA === agentB) {
      showToast("Please select different agents for P2P dialogue", "warning");
      return;
    }

    const payload = {
      topology: topo,
      prompt: prompt,
      from_agent: agentA,
      to_agent: agentB,
      turns: turns,
    };

    // Read what the user wrote before sending it, if enabled.
    if (SpeechKit.isReadPromptOnRun()) {
      SpeechKit.speak(prompt, { label: "Your prompt" });
    }

    localRun = true;
    // The reconcile window for a late 202: only terminal frames inside this
    // request may speak for it. Run ids restart in every session, so a record
    // older than this click belongs to a dead session and must not match.
    pendingRequestAt = Date.now();
    syncRunActivity();
    let ownStarted = false;
    let ownQueued = false;
    try {
      const resp = await apiFetch("/api/run", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });

      if (resp.status === 202) {
        // This session is busy, so this run waits its turn (FIFO, bounded by
        // --max-queued). The button stays busy until this run's own completion
        // frame arrives; Stop targets the queued run, not the active one.
        const queued = await resp.json().catch(() => ({}));
        if (queued && queued.status === "queued" && queued.run_id) {
          const terminal = terminalRuns.get(queued.run_id);
          if (terminal && terminal.at >= pendingRequestAt) {
            // The socket beat the HTTP response: this run already ended (another
            // tab cancelled it while queued, or it ran to completion first) and
            // its frame stayed silent for want of an identity to attach to. Say
            // the ending it actually had; the feed's picture of any other run is
            // left untouched.
            terminalRuns.delete(queued.run_id);
            localRun = false;
            if (terminal.type === "run_cancelled") {
              showToast("Queued run cancelled before it started.", "info");
            } else if (terminal.type === "run_error") {
              showToast("Dialogue failed: " + (terminal.error || "Unknown error"), "error", 5000);
            } else {
              showToast("Dialogue completed", "success");
            }
            apiFetch("/api/status").then((r) => (r.ok ? r.json() : null)).then(applyStatus).catch(() => {});
            syncRunActivity();
          } else if (activeRunId === queued.run_id) {
            // Started before the 202 arrived: already the active run, not queued.
            // localRun is kept until its completion frame releases it.
            ownQueued = true;
            localQueued = true;
            showToast(`Already started (${queued.run_id}) - running now.`, "info", 4000);
            syncRunActivity();
          } else {
            ownQueued = true;
            localQueued = true;
            queuedRunId = queued.run_id;
            queuePosition = queued.queue_position || null;
            showToast(
              `Queued at position ${queued.queue_position || 1} (${queued.run_id}). It runs automatically when the active run finishes.`,
              "info",
              6000
            );
            syncRunActivity();
          }
        } else {
          showToast("Failed: the server queued the run but did not name it", "error", 5000);
        }
      } else if (resp.status === 409) {
        // --max-queued 0: a run is already holding this session's mesh (another
        // tab, or a stale request). Refusing is the configured answer; say so.
        const conflict = await resp.json().catch(() => ({}));
        showToast(conflict.detail || "A run is already in progress in this session", "info", 6000);
      } else if (resp.status === 429) {
        const limited = await resp.json().catch(() => ({}));
        const detail = limited.detail || "Too many requests";
        // A full queue is "wait and retry", like a 409 - not a failure.
        if (/queue/i.test(detail)) {
          showToast(detail, "info", 6000);
        } else {
          showToast("Failed: " + detail, "error", 5000);
        }
      } else if (!resp.ok) {
        const err = await resp.json().catch(() => ({ detail: "Unknown error" }));
        showToast("Failed: " + (err.detail || "Unknown error"), "error", 5000);
      } else {
        const data = await resp.json().catch(() => ({}));
        ownStarted = true;
        // Say what the transcript actually is - simulated vs. real provider output.
        if (data.status === "cancelled") {
          resyncHistoryFromServer("Run cancelled. Replies already received have been kept.");
        } else if (data && data.simulated) {
          showToast(
            "Done - these are simulated answers. Nothing was compiled, executed, or tested.",
            "warning",
            7000
          );
        } else if (data && data.partially_simulated) {
          showToast("Part of this run fell back to the simulator - see the badges on the messages", "warning", 7000);
        }
        if (data && Array.isArray(data.provider_warnings) && data.provider_warnings.length) {
          showToast("Provider problem: " + data.provider_warnings.join("; "), "error", 8000);
        }
        apiFetch("/api/status").then((r) => (r.ok ? r.json() : null)).then(applyStatus).catch(() => {});
      }
    } catch (err) {
      console.error("Run error:", err);
      showToast("Network error - check connection and try again", "error", 5000);
    } finally {
      if (ownQueued) {
        // The HTTP returned 202 but the run has not executed: keep localRun until
        // this run's own completion frame (run_started/run_completed with its id).
      } else if (ownStarted) {
        // This tab's run executed synchronously; release everything, so a missed
        // socket frame cannot leave the spinner running forever.
        localRun = false;
        localQueued = false;
        queuedRunId = null;
        queuePosition = null;
        remoteRunActive = false;
        activeRunId = null;
      } else {
        // Refused or failed before starting: only this tab's click ends. The
        // feed's picture of another tab's run (if any) is left untouched.
        localRun = false;
      }
      syncRunActivity();
    }
  });

  // Clear Session with custom modal instead of confirm()
  btnClear.addEventListener("click", () => {
    if (messages.length === 0) {
      showToast("No messages to clear", "info", 2000);
      return;
    }
    openModal(clearConfirmModal);
  });

  if (btnConfirmClear) {
    btnConfirmClear.addEventListener("click", async () => {
      closeModal(clearConfirmModal);
      try {
        const resp = await apiFetch("/api/clear", { method: "POST" });
        if (!resp.ok) {
          showToast("Failed to clear session", "error");
        }
      } catch (e) {
        showToast("Network error clearing session", "error");
      }
    });
  }

  if (btnCancelClear) {
    btnCancelClear.addEventListener("click", () => closeModal(clearConfirmModal));
  }
  if (btnCloseClearModal) {
    btnCloseClearModal.addEventListener("click", () => closeModal(clearConfirmModal));
  }

  // Export Buttons with feedback
  btnExportMd.addEventListener("click", async () => {
    if (messages.length === 0) {
      showToast("No messages to export yet", "info", 2000);
      return;
    }
    try {
      btnExportMd.disabled = true;
      btnExportMd.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i><span>Exporting...</span>';
      const res = await apiFetch("/api/export/markdown");
      const data = await res.json();
      downloadFile("module_mesh_transcript.md", data.markdown, "text/markdown");
      showToast("Markdown exported successfully", "success");
    } catch (e) {
      showToast("Failed to export markdown", "error");
    } finally {
      btnExportMd.disabled = false;
      btnExportMd.innerHTML = '<i class="fa-solid fa-file-arrow-down"></i><span>Export MD</span>';
    }
  });

  btnExportJson.addEventListener("click", async () => {
    if (messages.length === 0) {
      showToast("No messages to export yet", "info", 2000);
      return;
    }
    try {
      btnExportJson.disabled = true;
      btnExportJson.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i><span>Exporting...</span>';
      const res = await apiFetch("/api/export/json");
      const data = await res.json();
      downloadFile("module_mesh_transcript.json", data.json, "application/json");
      showToast("JSON exported successfully", "success");
    } catch (e) {
      showToast("Failed to export JSON", "error");
    } finally {
      btnExportJson.disabled = false;
      btnExportJson.innerHTML = '<i class="fa-solid fa-code"></i><span>Export JSON</span>';
    }
  });

  function downloadFile(filename, content, mime) {
    const blob = new Blob([content], { type: mime });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = filename;
    a.setAttribute("aria-hidden", "true");
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  }

  // Modals with accessibility
  function setupModal(modal, openBtn, closeBtn, cancelBtn) {
    if (openBtn) {
      openBtn.addEventListener("click", () => openModal(modal));
    }
    if (closeBtn) {
      closeBtn.addEventListener("click", () => closeModal(modal));
    }
    if (cancelBtn) {
      cancelBtn.addEventListener("click", () => closeModal(modal));
    }
    
    // Close on backdrop click
    modal.addEventListener("click", (e) => {
      if (e.target === modal) closeModal(modal);
    });
    
    // Close on ESC
    modal.addEventListener("keydown", (e) => {
      if (e.key === "Escape") closeModal(modal);
    });
  }

  setupModal(agentModal, btnNewAgent, btnCloseAgentModal, btnCancelAddAgent);
  setupModal(settingsModal, btnSettings, btnCloseSettingsModal, btnCancelSettings);
  setupModal(clearConfirmModal, null, btnCloseClearModal, btnCancelClear);
  // Guarded: the how-to-use button is hidden on phone widths (the empty
  // transcript carries the same three steps there), but the modal itself is
  // always present, so the "Got it" button still closes it.
  if (helpModal) setupModal(helpModal, btnHelp, btnCloseHelpModal, btnDoneHelp);

  // Global ESC handler for modals
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") {
      const openModalEl = document.querySelector(".modal-backdrop:not(.hidden)");
      if (openModalEl) closeModal(openModalEl);
    }
  });

  // Add Agent form with validation
  formAddAgent.addEventListener("submit", async (e) => {
    e.preventDefault();
    
    const agentIdInput = document.getElementById("newAgentId");
    const payload = {
      agent_id: agentIdInput.value.trim().toLowerCase(),
      name: document.getElementById("newAgentName").value.trim(),
      role: document.getElementById("newAgentRole").value.trim(),
      system_prompt: document.getElementById("newAgentPrompt").value.trim(),
      color: document.getElementById("newAgentColor").value,
      avatar: document.getElementById("newAgentAvatar").value.trim() || "🤖",
    };

    // Client-side validation with helpful messages
    if (!/^[a-z0-9][a-z0-9-_]{0,48}[a-z0-9]$|^[a-z0-9]$/.test(payload.agent_id) || payload.agent_id.length < 2) {
      showToast("Agent ID must be lowercase alphanumeric with dashes, e.g. 'my-agent' (2-50 chars)", "error", 4000);
      agentIdInput.focus();
      return;
    }

    if (payload.name.length < 1) {
      showToast("Display name is required", "warning");
      return;
    }

    if (payload.system_prompt.length < 10) {
      showToast("System prompt must be at least 10 characters", "warning");
      return;
    }

    const submitBtn = formAddAgent.querySelector('button[type="submit"]');
    const originalText = submitBtn.innerHTML;
    submitBtn.disabled = true;
    submitBtn.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i> Registering...';

    try {
      const resp = await apiFetch("/api/agents", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      if (resp.ok) {
        closeModal(agentModal);
        formAddAgent.reset();
        document.getElementById("newAgentColor").value = "#ec4899";
        showToast(`Module "${payload.name}" registered successfully!`, "success");
      } else {
        const err = await resp.json().catch(() => ({ detail: "Unknown error" }));
        showToast(err.detail || "Could not add agent", "error", 4000);
      }
    } catch (err) {
      showToast("Network error adding agent: " + err.message, "error");
    } finally {
      submitBtn.disabled = false;
      submitBtn.innerHTML = originalText;
    }
  });

  const btnClearProviders = document.getElementById("btnClearProviders");
  if (btnClearProviders) {
    btnClearProviders.addEventListener("click", async () => {
      btnClearProviders.disabled = true;
      try {
        const resp = await apiFetch("/api/config", { method: "POST", body: { clear: true } });
        const data = await resp.json().catch(() => ({}));
        if (resp.ok) {
          showToast(data.message || "Back to the built-in simulator.", "info", 3000);
          const agentsResp = await apiFetch("/api/agents");
          if (agentsResp.ok) { agents = await agentsResp.json(); renderAgentList(); }
          apiFetch("/api/status").then((r) => (r.ok ? r.json() : null)).then(applyStatus).catch(() => {});
        } else {
          showToast(data.detail || "Could not clear providers", "error");
        }
      } catch (e) {
        showToast("Network error", "error");
      } finally {
        btnClearProviders.disabled = false;
      }
    });
  }

  // Settings form
  formSettings.addEventListener("submit", async (e) => {
    e.preventDefault();
    const verifyBox = document.getElementById("inputVerifyKeys");
    const payload = {
      openai_api_key: document.getElementById("inputOpenAiKey").value.trim() || null,
      anthropic_api_key: document.getElementById("inputAnthropicKey").value.trim() || null,
      openai_base_url: document.getElementById("inputOpenAiBaseUrl").value.trim() || null,
      verify: !!(verifyBox && verifyBox.checked),
    };

    const submitBtn = formSettings.querySelector('button[type="submit"]');
    const originalText = submitBtn.innerHTML;
    submitBtn.disabled = true;
    submitBtn.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i> Saving...';

    try {
      const resp = await apiFetch("/api/config", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      if (resp.ok || resp.status === 200) {
        const data = await resp.json().catch(() => ({}));
        closeModal(settingsModal);
        showToast(data.message || "Configuration saved for this session.", data.status === "partial" ? "warning" : "success", 7000);
        formSettings.reset();
        apiFetch("/api/status").then((r) => (r.ok ? r.json() : null)).then(applyStatus).catch(() => {});
        apiFetch("/api/agents").then((r) => (r.ok ? r.json() : null)).then((list) => {
          if (Array.isArray(list)) { agents = list; renderAgentList(); }
        }).catch(() => {});
      } else {
        const err = await resp.json().catch(() => ({ detail: "Failed" }));
        showToast(err.detail || "Failed to save configuration", "error");
      }
    } catch (err) {
      showToast("Error saving settings: " + err.message, "error");
    } finally {
      submitBtn.disabled = false;
      submitBtn.innerHTML = originalText;
    }
  });

  // ===== Read-Aloud Studio & prompt speech controls =====

  if (btnReadPrompt) {
    btnReadPrompt.addEventListener("click", () => {
      const text = inputPrompt.value.trim();
      if (!text) {
        showToast("Type or paste your prompt first - then I will read it aloud", "warning");
        inputPrompt.focus();
        return;
      }
      const speakingThis = btnReadPrompt.classList.contains("speaking");
      if (speakingThis) {
        SpeechKit.stop();
      } else {
        SpeechKit.speak(text, { label: "Your prompt" });
      }
    });
  }

  // Voice dictation (speech recognition) - speak the prompt into the mic
  function initDictation() {
    const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (!SR || !btnDictate) {
      if (btnDictate) btnDictate.style.display = "none"; // unsupported browser
      return;
    }
    const rec = new SR();
    rec.continuous = false;
    rec.interimResults = true;
    rec.lang = navigator.language || "en-US";
    let base = "";
    rec.onstart = () => {
      base = inputPrompt.value.trim() ? inputPrompt.value.trim() + " " : "";
      btnDictate.classList.add("recording");
      btnDictate.innerHTML = '<i class="fa-solid fa-stop" aria-hidden="true"></i><span>Stop listening</span>';
      showToast("Listening... speak your task now", "info", 2500);
    };
    rec.onresult = (e) => {
      let interim = "";
      let final = "";
      for (const r of e.results) {
        if (r.isFinal) final += r[0].transcript;
        else interim += r[0].transcript;
      }
      inputPrompt.value = (base + final).trim() + (interim ? " " + interim : "");
      updateCharCount();
    };
    rec.onerror = (e) => {
      showToast("Voice input problem: " + (e.error || "unknown") + " - try again", "warning", 3500);
    };
    rec.onend = () => {
      btnDictate.classList.remove("recording");
      btnDictate.innerHTML = '<i class="fa-solid fa-microphone" aria-hidden="true"></i><span>Dictate</span>';
    };
    btnDictate.addEventListener("click", () => {
      if (rec.recognizing) { rec.stop(); return; }
      try {
        rec.start();
      } catch (e) {
        showToast("Could not start the microphone - check browser permissions", "warning");
      }
    });
  }

  if (btnReadAll) {
    btnReadAll.addEventListener("click", () => {
      if (!messages.length) {
        showToast("No messages to read yet - run a dialogue first", "info");
        return;
      }
      // First message interrupts, the rest queue up in order.
      SpeechKit.speak(messages[0].content, { label: messages[0].sender_name, msgId: messages[0].id });
      for (const m of messages.slice(1)) {
        SpeechKit.enqueue(m.content, { label: m.sender_name, msgId: m.id });
      }
      showToast(`Reading all ${messages.length} messages aloud`, "info", 2500);
    });
  }

  // Reader tabs
  document.querySelectorAll(".reader-tab").forEach((tab) => {
    tab.addEventListener("click", () => {
      document.querySelectorAll(".reader-tab").forEach((t) => {
        t.classList.remove("reader-tab-active");
        t.setAttribute("aria-selected", "false");
      });
      tab.classList.add("reader-tab-active");
      tab.setAttribute("aria-selected", "true");
      document.querySelectorAll(".reader-tab-panel").forEach((p) => p.classList.add("hidden"));
      const panel = document.getElementById("readerTab-" + tab.dataset.readerTab);
      if (panel) panel.classList.remove("hidden");
    });
  });

  function showReaderDisplay(title, text) {
    if (!readerDisplay) return;
    readerDisplay.classList.remove("hidden");
    readerDisplay.textContent = (title ? `— ${title} —\n\n` : "") + text;
    readerDisplay.scrollTop = 0;
  }

  if (btnReadText) {
    btnReadText.addEventListener("click", () => {
      const text = readerText.value.trim();
      if (!text) {
        showToast("Type or paste the text you want read aloud first", "warning");
        readerText.focus();
        return;
      }
      SpeechKit.speak(text, { label: "Your text" });
      showToast("Reading your text aloud", "info", 1500);
    });
  }

  if (btnReadFile) {
    btnReadFile.addEventListener("click", () => {
      const file = readerFile.files && readerFile.files[0];
      if (!file) {
        showToast("Choose a file first, then I will read it aloud", "warning");
        readerFile.focus();
        return;
      }
      if (file.size > 1024 * 1024) {
        showToast("File is bigger than 1 MB - choose a smaller one", "error");
        return;
      }
      const reader = new FileReader();
      reader.onload = () => {
        const text = String(reader.result || "");
        if (!text.trim()) {
          showToast("That file appears to be empty or not plain text", "warning");
          return;
        }
        showReaderDisplay(file.name, text);
        SpeechKit.speak(text, { label: file.name });
        showToast(`Reading "${file.name}" aloud`, "info", 2000);
      };
      reader.onerror = () => showToast("Could not read that file", "error");
      reader.readAsText(file);
    });
  }

  if (btnReadUrl) {
    btnReadUrl.addEventListener("click", async () => {
      const url = readerUrl.value.trim();
      if (!url) {
        showToast("Paste a web page link first", "warning");
        readerUrl.focus();
        return;
      }
      if (!/^https?:\/\//i.test(url)) {
        showToast("The link must start with http:// or https://", "warning");
        return;
      }
      btnReadUrl.disabled = true;
      btnReadUrl.innerHTML = '<i class="fa-solid fa-spinner fa-spin" aria-hidden="true"></i><span>Fetching...</span>';
      try {
        if (!urlReaderEnabled) {
          showToast("Reading web pages is disabled on this server (start it with --enable-url-reader)", "warning", 5000);
          return;
        }
        const resp = await apiFetch("/api/read/url", {
          method: "POST",
          body: JSON.stringify({ url }),
        });
        const data = await resp.json().catch(() => ({}));
        if (!resp.ok) {
          showToast(data.detail || "Could not fetch that page", "error", 5000);
          return;
        }
        showReaderDisplay(`${data.title || url} (${data.url})${data.truncated ? " [trimmed]" : ""}`, data.text);
        SpeechKit.speak(data.text, { label: data.title || url });
        showToast(data.truncated
          ? "Page is long - reading the first part aloud"
          : "Reading the page aloud", "info", 2500);
      } catch (e) {
        showToast("Network error while fetching the page", "error", 4000);
      } finally {
        btnReadUrl.disabled = false;
        btnReadUrl.innerHTML = '<i class="fa-solid fa-download" aria-hidden="true"></i><span>Fetch &amp; Read</span>';
      }
    });
  }

  // ===== Saved Sessions =====

  async function refreshSessions() {
    try {
      const resp = await apiFetch("/api/sessions");
      const data = await resp.json();
      const sessions = data.sessions || [];
      sessionsList.innerHTML = "";
      sessionsEmpty.classList.toggle("hidden", sessions.length > 0);
      sessions.forEach((s) => {
        const when = new Date(s.saved_at * 1000);
        const dateStr = when.toLocaleDateString() + " " + when.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });

        const row = document.createElement("div");
        row.className = "session-row flex items-center justify-between gap-2 p-2.5 rounded-lg bg-slate-800/70 border border-slate-700/60";
        row.setAttribute("role", "listitem");

        const info = document.createElement("div");
        info.className = "min-w-0";
        const nameEl = document.createElement("p");
        nameEl.className = "text-xs font-semibold text-slate-200 truncate";
        nameEl.textContent = String(s.name || s.id || "Untitled session");
        nameEl.title = nameEl.textContent;
        const metaEl = document.createElement("p");
        metaEl.className = "text-[10px] text-slate-500";
        // "trimmed" is the store's own admission that the oldest messages were
        // dropped to fit the per-file cap - the row must not imply a full transcript.
        metaEl.textContent =
          `${dateStr} \u00b7 ${Number(s.message_count) || 0} messages` +
          (s.trimmed ? " \u00b7 oldest dropped to fit the size limit" : "");
        info.appendChild(nameEl);
        info.appendChild(metaEl);

        const actions = document.createElement("div");
        actions.className = "flex items-center gap-1.5 flex-shrink-0";

        const makeButton = (cls, label, iconCls) => {
          const btn = document.createElement("button");
          btn.className = cls;
          // dataset/setAttribute, never an interpolated attribute in a template:
          // a session name containing a quote must not be able to escape it.
          btn.dataset.id = String(s.id || "");
          btn.setAttribute("aria-label", label);
          if (iconCls) {
            const ico = document.createElement("i");
            ico.className = iconCls;
            ico.setAttribute("aria-hidden", "true");
            btn.appendChild(ico);
          } else {
            btn.textContent = label;
          }
          return btn;
        };

        const loadBtn = makeButton(
          "session-load px-2.5 py-1 text-[11px] rounded bg-indigo-600 hover:bg-indigo-500 text-white transition focus-visible:ring-2 focus-visible:ring-indigo-400",
          `Load session "${nameEl.textContent}"`
        );
        const delBtn = makeButton(
          "session-del px-2 py-1 text-[11px] rounded bg-slate-800 hover:bg-red-900/60 border border-slate-700 text-slate-400 hover:text-red-300 transition focus-visible:ring-2 focus-visible:ring-red-400",
          `Delete "${nameEl.textContent}"`,
          "fa-solid fa-trash-can"
        );
        delBtn.querySelector("i").insertAdjacentElement("afterend", document.createTextNode(" "));
        actions.appendChild(loadBtn);
        actions.appendChild(delBtn);

        row.appendChild(info);
        row.appendChild(actions);

        row.querySelector(".session-load").addEventListener("click", async () => {
          const btn = loadBtn;
          btn.disabled = true;
          try {
            const resp = await apiFetch("/api/sessions/load", {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ session_id: s.id }),
            });
            if (!resp.ok) {
              const err = await resp.json().catch(() => ({}));
              showToast(err.detail || "Could not load that session", "error", 4000);
              btn.disabled = false;
            } else {
              closeModal(sessionsModal);
            }
          } catch (e) {
            showToast("Network error loading session", "error", 4000);
            btn.disabled = false;
          }
        });
        row.querySelector(".session-del").addEventListener("click", async () => {
          const btn = row.querySelector(".session-del");
          btn.disabled = true;
          try {
            const resp = await apiFetch("/api/sessions/" + encodeURIComponent(s.id), { method: "DELETE" });
            if (!resp.ok) showToast("Could not delete that session", "error");
            refreshSessions();
          } catch (e) {
            showToast("Network error deleting session", "error");
            btn.disabled = false;
          }
        });
        sessionsList.appendChild(row);
      });
    } catch (e) {
      showToast("Could not list saved sessions", "error");
    }
  }

  if (btnSessions) {
    btnSessions.addEventListener("click", () => {
      openModal(sessionsModal);
      refreshSessions();
    });
  }
  if (btnCloseSessionsModal) {
    btnCloseSessionsModal.addEventListener("click", () => closeModal(sessionsModal));
  }
  if (sessionsModal) {
    sessionsModal.addEventListener("click", (e) => {
      if (e.target === sessionsModal) closeModal(sessionsModal);
    });
    sessionsModal.addEventListener("keydown", (e) => {
      if (e.key === "Escape") closeModal(sessionsModal);
    });
  }
  if (btnSaveSession) {
    btnSaveSession.addEventListener("click", async () => {
      const name = sessionNameInput ? sessionNameInput.value.trim() : "";
      if (!messages.length) {
        showToast("There is no conversation to save yet - run a dialogue first", "warning");
        return;
      }
      btnSaveSession.disabled = true;
      btnSaveSession.innerHTML = '<i class="fa-solid fa-spinner fa-spin" aria-hidden="true"></i><span>Saving...</span>';
      try {
        const resp = await apiFetch("/api/sessions", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ name: name || null }),
        });
        const data = await resp.json().catch(() => ({}));
        if (!resp.ok) {
          showToast(data.detail || "Could not save the session", "error", 4000);
        } else {
          showToast(`Session "${data.session.name}" saved to this computer`, "success", 3500);
          if (sessionNameInput) sessionNameInput.value = "";
          refreshSessions();
        }
      } catch (e) {
        showToast("Network error saving session", "error", 4000);
      } finally {
        btnSaveSession.disabled = false;
        btnSaveSession.innerHTML = '<i class="fa-solid fa-floppy-disk" aria-hidden="true"></i><span>Save</span>';
      }
    });
    // Allow Enter in the name field to save
    if (sessionNameInput) {
      sessionNameInput.addEventListener("keydown", (e) => {
        if (e.key === "Enter") {
          e.preventDefault();
          btnSaveSession.click();
        }
      });
    }
  }

  // Start
  initWebSocket();
  // One frame to paint the graph; the loop then stops itself until a packet,
  // a run, or a resize asks for another.
  requestRedraw(true);
  initSpeechUI();
  initDictation();
  applyReaderAvailability();

  // Ask the server what it is actually running (auth, page reader, providers).
  apiFetch("/api/auth/status")
    .then((r) => (r.ok ? r.json() : null))
    .then((info) => {
      if (!info) return;
      authenticated = info.authenticated !== false;
      urlReaderEnabled = info.url_reader_enabled !== false;
      applyReaderAvailability();
      if (info.auth_required && info.authenticated === false) showAuthPanel();
    })
    .catch(() => {})
    .then(() => apiFetch("/api/status"))
    .then((r) => (r.ok ? r.json() : null))
    .then(applyStatus)
    .catch(() => {});

  // Initial char count
  updateCharCount();
});
