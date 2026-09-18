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
        <span class="text-[10px] font-mono text-slate-500">${escapeHtml(msg.formatted_time || "")}</span>
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

  // Start
  initWebSocket();
  requestAnimationFrame(drawCanvas);
  
  // Initial char count
  updateCharCount();
});
