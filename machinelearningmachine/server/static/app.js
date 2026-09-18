// Inter-Module Mesh Client Application - User-Centered Redesign
// Focus: accessibility, reliability, pleasant UX, real user value
document.addEventListener("DOMContentLoaded", () => {
  // State
  let agents = [];
  let messages = [];
  let isExecuting = false;
  let activePacket = null;
  let ws = null;
  let wsReconnectAttempts = 0;
  const MAX_RECONNECT_ATTEMPTS = 10;
  let searchFilter = "";

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
    toast.className = `toast toast-${type}`;
    toast.setAttribute("role", "alert");
    toast.setAttribute("aria-live", "polite");
    
    const icons = {
      success: "fa-check-circle",
      error: "fa-exclamation-circle",
      warning: "fa-exclamation-triangle",
      info: "fa-info-circle"
    };
    
    toast.innerHTML = `
      <i class="fa-solid ${icons[type] || icons.info} mt-0.5 flex-shrink-0"></i>
      <span class="flex-1">${escapeHtml(message)}</span>
      <button class="ml-2 text-current opacity-60 hover:opacity-100 flex-shrink-0" aria-label="Dismiss notification">
        <i class="fa-solid fa-xmark text-xs"></i>
      </button>
    `;
    
    const dismissBtn = toast.querySelector("button");
    dismissBtn.addEventListener("click", () => dismissToast(toast));
    
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

  function escapeHtml(str) {
    const div = document.createElement("div");
    div.textContent = str;
    return div.innerHTML;
  }

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

  // Safe markdown parsing - prevent XSS
  function safeMarkdownParse(content) {
    try {
      // Configure marked to not allow raw HTML for security
      if (typeof marked !== "undefined") {
        marked.setOptions({
          gfm: true,
          breaks: true,
          sanitize: false, // We'll sanitize via escaping
        });
        let html = marked.parse(content);
        // Basic XSS protection - remove script tags and event handlers
        // In production, use DOMPurify, but this is a lightweight alternative
        html = html.replace(/<script\b[^<]*(?:(?!<\/script>)<[^<]*)*<\/script>/gi, "");
        html = html.replace(/on\w+="[^"]*"/gi, "");
        html = html.replace(/on\w+='[^']*'/gi, "");
        html = html.replace(/javascript:/gi, "");
        return html;
      }
      return `<pre class="text-xs whitespace-pre-wrap">${escapeHtml(content)}</pre>`;
    } catch (e) {
      return `<pre class="text-xs whitespace-pre-wrap">${escapeHtml(content)}</pre>`;
    }
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

  // ===== Canvas Handling - Performance Optimized =====
  let animationFrameId = null;
  let needsRedraw = true;
  let lastDrawTime = 0;
  const DRAW_THROTTLE = 1000 / 30; // 30fps max to save battery

  function resizeCanvas() {
    if (!canvas) return;
    const rect = canvas.parentElement.getBoundingClientRect();
    canvas.width = rect.width * window.devicePixelRatio;
    canvas.height = rect.height * window.devicePixelRatio;
    canvas.style.width = rect.width + "px";
    canvas.style.height = rect.height + "px";
    if (ctx) ctx.scale(window.devicePixelRatio, window.devicePixelRatio);
    needsRedraw = true;
  }

  window.addEventListener("resize", resizeCanvas);
  resizeCanvas();

  // Pause animation when tab is not visible - battery saving
  document.addEventListener("visibilitychange", () => {
    if (document.hidden) {
      if (animationFrameId) {
        cancelAnimationFrame(animationFrameId);
        animationFrameId = null;
      }
    } else {
      needsRedraw = true;
      if (!animationFrameId) requestAnimationFrame(drawCanvas);
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
      agents = data.agents || [];
      messages = data.history || [];
      renderAgentList();
      renderAllMessages();
      needsRedraw = true;
    } else if (data.type === "new_message") {
      const msg = data.message;
      messages.push(msg);
      // Only append if passes search filter
      if (!searchFilter ||
          msg.content.toLowerCase().includes(searchFilter.toLowerCase()) ||
          msg.sender_name.toLowerCase().includes(searchFilter.toLowerCase())) {
        appendMessageToFeed(msg);
      } else {
        // Still update count
        updateMessageCount();
      }
      triggerPacketAnimation(msg.sender_id, msg.recipient_id);
      needsRedraw = true;
      // Read replies aloud automatically (skips short system notices)
      if (SpeechKit.isAutoRead() && msg.message_type !== "system") {
        SpeechKit.speak(msg.content, { label: msg.sender_name || "Message", msgId: msg.id });
      }
    } else if (data.type === "session_loaded") {
      agents = data.agents || [];
      messages = data.history || [];
      searchFilter = "";
      if (searchInput) searchInput.value = "";
      renderAgentList();
      renderAllMessages();
      needsRedraw = true;
      showToast(`Session "${data.name || ""}" loaded - conversation restored`, "success", 4000);
    } else if (data.type === "agents_updated") {
      agents = data.agents || [];
      renderAgentList();
      needsRedraw = true;
    } else if (data.type === "history_cleared") {
      messages = [];
      renderAllMessages();
      showToast("Session cleared successfully", "success");
    } else if (data.type === "run_started") {
      isExecuting = true;
      btnRun.disabled = true;
      btnRun.innerHTML = '<i class="fa-solid fa-spinner fa-spin" aria-hidden="true"></i><span>Modules Communicating...</span>';
      btnRun.setAttribute("aria-busy", "true");
      showToast(`Starting ${data.topology} dialogue...`, "info", 2000);
    } else if (data.type === "run_completed" || data.type === "run_error") {
      isExecuting = false;
      btnRun.disabled = false;
      btnRun.innerHTML = '<i class="fa-solid fa-play" aria-hidden="true"></i><span>Execute Dialogue</span>';
      btnRun.removeAttribute("aria-busy");
      if (data.type === "run_error") {
        showToast("Dialogue failed: " + (data.error || "Unknown error"), "error", 5000);
      } else {
        showToast("Dialogue completed successfully!", "success");
      }
    }
  }

  // Optimized canvas drawing with throttling
  function drawCanvas(timestamp = 0) {
    animationFrameId = requestAnimationFrame(drawCanvas);
    
    // Throttle drawing
    if (timestamp - lastDrawTime < DRAW_THROTTLE && !needsRedraw && !activePacket && !isExecuting) {
      return;
    }
    lastDrawTime = timestamp;
    
    if (!ctx || !canvas) return;
    
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
        needsRedraw = true;
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
  }

  function triggerPacketAnimation(fromId, toId) {
    const color = nodePositions[fromId]?.color || "#a855f7";
    activePacket = {
      fromId: fromId,
      toId: toId === "*" ? "gpt" : toId,
      progress: 0.0,
      color: color,
    };
    needsRedraw = true;
  }

  // Render Registered Modules - with accessibility
  function renderAgentList() {
    agentCountBadge.textContent = `${agents.length} Modules`;
    agentCountBadge.setAttribute("aria-label", `${agents.length} modules registered`);

    const currentA = selectAgentA.value;
    const currentB = selectAgentB.value;
    selectAgentA.innerHTML = "";
    selectAgentB.innerHTML = "";
    moduleList.innerHTML = "";

    agents.forEach((ag, idx) => {
      const optA = document.createElement("option");
      optA.value = ag.agent_id;
      optA.textContent = `${ag.name} (${ag.role.split("&")[0].trim()})`;
      if (ag.agent_id === currentA || (!currentA && idx === 0)) optA.selected = true;
      selectAgentA.appendChild(optA);

      const optB = document.createElement("option");
      optB.value = ag.agent_id;
      optB.textContent = `${ag.name} (${ag.role.split("&")[0].trim()})`;
      if (ag.agent_id === currentB || (!currentB && idx === 1)) optB.selected = true;
      selectAgentB.appendChild(optB);

      const item = document.createElement("div");
      item.className = "agent-card p-2.5 rounded-lg bg-slate-800/70 border border-slate-700/60 flex items-center justify-between cursor-pointer";
      item.setAttribute("role", "button");
      item.setAttribute("tabindex", "0");
      item.setAttribute("aria-label", `${ag.name}, ${ag.role}`);
      item.innerHTML = `
        <div class="flex items-center space-x-2.5 overflow-hidden">
          <div class="w-7 h-7 rounded-md flex items-center justify-center text-sm font-bold flex-shrink-0" style="background-color: ${ag.color}25; border: 1px solid ${ag.color}" aria-hidden="true">
            ${ag.avatar}
          </div>
          <div class="truncate">
            <h4 class="text-xs font-semibold text-slate-200 truncate">${escapeHtml(ag.name)}</h4>
            <p class="text-[10px] text-slate-400 truncate">${escapeHtml(ag.role)}</p>
          </div>
        </div>
        <span class="text-[10px] font-mono px-1.5 py-0.5 rounded bg-slate-900 border border-slate-800 text-slate-400">
          ${escapeHtml(ag.provider || 'Mock')}
        </span>
      `;
      
      // Click to show agent details
      item.addEventListener("click", () => {
        showToast(`${ag.name}: ${ag.role}. Provider: ${ag.provider || 'Mock Simulator'}`, "info", 3000);
      });
      item.addEventListener("keydown", (e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          item.click();
        }
      });
      
      moduleList.appendChild(item);
    });
  }

  function updateMessageCount() {
    const visibleCount = searchFilter 
      ? messages.filter(m => 
          m.content.toLowerCase().includes(searchFilter.toLowerCase()) ||
          m.sender_name.toLowerCase().includes(searchFilter.toLowerCase())
        ).length
      : messages.length;
    
    if (searchFilter) {
      msgCountBadge.textContent = `${visibleCount} / ${messages.length} messages`;
    } else {
      msgCountBadge.textContent = `${messages.length} message${messages.length === 1 ? '' : 's'}`;
    }
  }

  // Render Transcript Messages - with search and accessibility
  function renderAllMessages() {
    messagesContainer.innerHTML = "";
    if (messages.length === 0) {
      if (emptyPlaceholder) emptyPlaceholder.style.display = "flex";
      msgCountBadge.textContent = "0 messages";
      return;
    }
    if (emptyPlaceholder) emptyPlaceholder.style.display = "none";

    const filtered = searchFilter
      ? messages.filter(m => 
          m.content.toLowerCase().includes(searchFilter.toLowerCase()) ||
          m.sender_name.toLowerCase().includes(searchFilter.toLowerCase()) ||
          m.message_type.toLowerCase().includes(searchFilter.toLowerCase())
        )
      : messages;

    updateMessageCount();

    if (filtered.length === 0 && searchFilter) {
      const noResults = document.createElement("div");
      noResults.className = "text-center py-8 text-slate-400";
      noResults.innerHTML = `
        <i class="fa-solid fa-search text-2xl mb-2 opacity-50"></i>
        <p class="text-sm">No messages match "${escapeHtml(searchFilter)}"</p>
        <button class="mt-2 text-xs text-indigo-400 hover:text-indigo-300 underline" onclick="document.getElementById('searchMessages').value=''; document.getElementById('searchMessages').dispatchEvent(new Event('input'))">
          Clear search
        </button>
      `;
      messagesContainer.appendChild(noResults);
      return;
    }

    filtered.forEach((msg) => {
      appendMessageToFeed(msg, false);
    });
    scrollFeedToBottom();
  }

  function appendMessageToFeed(msg, autoScroll = true) {
    if (emptyPlaceholder) emptyPlaceholder.style.display = "none";
    updateMessageCount();

    const card = document.createElement("div");
    card.className = "msg-bubble p-4 rounded-xl bg-slate-800/50 border border-slate-700/60 shadow-md space-y-2";
    card.setAttribute("role", "article");
    card.setAttribute("aria-label", `Message from ${msg.sender_name}, type ${msg.message_type}`);

    const agentObj = agents.find((a) => a.agent_id === msg.sender_id) || {};
    const color = agentObj.color || "#8b5cf6";
    const avatar = agentObj.avatar || "🤖";

    const badgeColorMap = {
      task_spec: "bg-purple-900/40 text-purple-300 border-purple-700/60",
      proposal: "bg-cyan-900/40 text-cyan-300 border-cyan-700/60",
      critique: "bg-amber-900/40 text-amber-300 border-amber-700/60",
      revision: "bg-sky-900/40 text-sky-300 border-sky-700/60",
      consensus: "bg-emerald-900/40 text-emerald-300 border-emerald-700/60",
      system: "bg-slate-800 text-slate-300 border-slate-700",
    };
    const badgeClass = badgeColorMap[msg.message_type] || "bg-slate-800 text-slate-300 border-slate-700";

    const targetBadge = msg.recipient_id !== "*"
      ? `<span class="text-[11px] font-medium text-slate-400">→ <span class="text-indigo-400 font-semibold">@${escapeHtml(msg.recipient_name || msg.recipient_id)}</span></span>`
      : `<span class="text-[11px] text-slate-500">(broadcast)</span>`;

    const parsedContent = safeMarkdownParse(msg.content);

    card.innerHTML = `
      <div class="flex items-center justify-between border-b border-slate-700/40 pb-2">
        <div class="flex items-center space-x-2.5">
          <div class="w-6 h-6 rounded-md flex items-center justify-center text-xs" style="background-color: ${color}30; border: 1px solid ${color}" aria-hidden="true">
            ${avatar}
          </div>
          <span class="text-xs font-bold text-slate-200">${escapeHtml(msg.sender_name)}</span>
          ${targetBadge}
          <span class="text-[10px] uppercase font-mono px-2 py-0.5 rounded-full border ${badgeClass}">
            ${escapeHtml(msg.message_type.replace('_', ' '))}
          </span>
        </div>
        <div class="flex items-center gap-2">
          <button class="msg-speak-btn" data-msg-speak="${escapeHtml(msg.id || "")}" title="Read this message aloud" aria-label="Read this message aloud">
            <i class="fa-solid fa-volume-high" aria-hidden="true"></i>
          </button>
          <span class="text-[10px] font-mono text-slate-500">${escapeHtml(msg.formatted_time || "")}</span>
        </div>
      </div>
      <div class="text-xs text-slate-300 leading-relaxed prose prose-invert max-w-none prose-pre:bg-slate-950/80">
        ${parsedContent}
      </div>
    `;

    // Highlight code blocks and attach copy buttons
    card.querySelectorAll("pre code").forEach((block) => {
      if (typeof hljs !== "undefined") {
        hljs.highlightElement(block);
      }
      const pre = block.parentElement;
      pre.classList.add("code-container");

      const copyBtn = document.createElement("button");
      copyBtn.className = "btn-copy-code";
      copyBtn.setAttribute("aria-label", "Copy code to clipboard");
      copyBtn.innerHTML = '<i class="fa-regular fa-copy" aria-hidden="true"></i> Copy';
      copyBtn.onclick = async () => {
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
      };
      pre.appendChild(copyBtn);
    });

    // Per-message read-aloud button
    const speakBtn = card.querySelector(".msg-speak-btn");
    if (speakBtn) {
      speakBtn.addEventListener("click", (e) => {
        e.stopPropagation();
        speakMessage(msg);
      });
    }

    messagesContainer.appendChild(card);

    if (autoScroll) {
      scrollFeedToBottom();
    }
  }

  function scrollFeedToBottom() {
    // Only auto-scroll if user is near bottom
    const isNearBottom = messagesContainer.scrollHeight - messagesContainer.scrollTop - messagesContainer.clientHeight < 100;
    if (isNearBottom) {
      messagesContainer.scrollTop = messagesContainer.scrollHeight;
    }
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

  // Search messages
  if (searchInput) {
    searchInput.addEventListener("input", (e) => {
      searchFilter = e.target.value.trim();
      renderAllMessages();
    });
  }

  // Execute Dialogue with better error handling
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

    try {
      const resp = await fetch("/api/run", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });

      if (!resp.ok) {
        const err = await resp.json().catch(() => ({ detail: "Unknown error" }));
        showToast("Failed: " + (err.detail || "Unknown error"), "error", 5000);
      }
    } catch (err) {
      console.error("Run error:", err);
      showToast("Network error - check connection and try again", "error", 5000);
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
        const resp = await fetch("/api/clear", { method: "POST" });
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
      const res = await fetch("/api/export/markdown");
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
      const res = await fetch("/api/export/json");
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
      const resp = await fetch("/api/agents", {
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

  // Settings form
  formSettings.addEventListener("submit", async (e) => {
    e.preventDefault();
    const payload = {
      openai_api_key: document.getElementById("inputOpenAiKey").value.trim() || null,
      anthropic_api_key: document.getElementById("inputAnthropicKey").value.trim() || null,
      openai_base_url: document.getElementById("inputOpenAiBaseUrl").value.trim() || null,
    };

    const submitBtn = formSettings.querySelector('button[type="submit"]');
    const originalText = submitBtn.innerHTML;
    submitBtn.disabled = true;
    submitBtn.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i> Saving...';

    try {
      const resp = await fetch("/api/config", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      if (resp.ok) {
        const data = await resp.json();
        closeModal(settingsModal);
        showToast(data.message || "Configuration saved!", "success");
        formSettings.reset();
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
        const resp = await fetch("/api/read/url", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
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
      const resp = await fetch("/api/sessions");
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
        row.innerHTML = `
          <div class="min-w-0">
            <p class="text-xs font-semibold text-slate-200 truncate">${escapeHtml(s.name)}</p>
            <p class="text-[10px] text-slate-500">${dateStr} · ${s.message_count} messages</p>
          </div>
          <div class="flex items-center gap-1.5 flex-shrink-0">
            <button class="session-load px-2.5 py-1 text-[11px] rounded bg-indigo-600 hover:bg-indigo-500 text-white transition focus-visible:ring-2 focus-visible:ring-indigo-400" data-id="${escapeHtml(s.id)}" aria-label="Load session ${escapeHtml(s.name)}">
              Load
            </button>
            <button class="session-del px-2 py-1 text-[11px] rounded bg-slate-800 hover:bg-red-900/60 border border-slate-700 text-slate-400 hover:text-red-300 transition focus-visible:ring-2 focus-visible:ring-red-400" data-id="${escapeHtml(s.id)}" aria-label="Delete session ${escapeHtml(s.name)}">
              <i class="fa-solid fa-trash-can" aria-hidden="true"></i>
            </button>
          </div>
        `;
        row.querySelector(".session-load").addEventListener("click", async () => {
          const btn = row.querySelector(".session-load");
          btn.disabled = true;
          try {
            const resp = await fetch("/api/sessions/load", {
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
            const resp = await fetch("/api/sessions/" + encodeURIComponent(s.id), { method: "DELETE" });
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
        const resp = await fetch("/api/sessions", {
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
  requestAnimationFrame(drawCanvas);
  initSpeechUI();
  initDictation();

  // Initial char count
  updateCharCount();
});
