// Inter-Module Mesh Client Application
document.addEventListener("DOMContentLoaded", () => {
  // State
  let agents = [];
  let messages = [];
  let isExecuting = false;
  let activePacket = null; // { fromId, toId, progress, color }
  let ws = null;

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

  // Node layout configuration for the Canvas
  const nodePositions = {
    "arena-ai": { x: 0.18, y: 0.5, color: "#8b5cf6", name: "Arena AI", avatar: "⚡" },
    "copilot": { x: 0.42, y: 0.25, color: "#06b6d4", name: "Copilot", avatar: "🐙" },
    "claude": { x: 0.42, y: 0.75, color: "#d97706", name: "Claude", avatar: "🔮" },
    "gpt": { x: 0.82, y: 0.5, color: "#10b981", name: "GPT-4o", avatar: "🌐" },
  };

  // Setup Canvas sizing
  function resizeCanvas() {
    if (!canvas) return;
    const rect = canvas.parentElement.getBoundingClientRect();
    canvas.width = rect.width;
    canvas.height = rect.height;
  }
  window.addEventListener("resize", resizeCanvas);
  resizeCanvas();

  // Initialize WebSocket
  function initWebSocket() {
    const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    const wsUrl = `${protocol}//${window.location.host}/ws`;

    ws = new WebSocket(wsUrl);

    ws.onopen = () => {
      connectionStatus.className = "flex items-center space-x-2 text-xs px-2.5 py-1 rounded-full bg-emerald-950/80 border border-emerald-800 text-emerald-400";
      connectionStatus.innerHTML = '<span class="w-2 h-2 rounded-full bg-emerald-400 animate-pulse"></span><span>Live Mesh Connected</span>';
    };

    ws.onclose = () => {
      connectionStatus.className = "flex items-center space-x-2 text-xs px-2.5 py-1 rounded-full bg-amber-950/80 border border-amber-800 text-amber-400";
      connectionStatus.innerHTML = '<span class="w-2 h-2 rounded-full bg-amber-400"></span><span>Reconnecting...</span>';
      setTimeout(initWebSocket, 2000);
    };

    ws.onerror = (err) => {
      console.warn("WebSocket error:", err);
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
    } else if (data.type === "new_message") {
      const msg = data.message;
      messages.push(msg);
      appendMessageToFeed(msg);
      triggerPacketAnimation(msg.sender_id, msg.recipient_id);
    } else if (data.type === "agents_updated") {
      agents = data.agents || [];
      renderAgentList();
    } else if (data.type === "history_cleared") {
      messages = [];
      renderAllMessages();
    } else if (data.type === "run_started") {
      isExecuting = true;
      btnRun.disabled = true;
      btnRun.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i><span>Modules Communicating...</span>';
    } else if (data.type === "run_completed" || data.type === "run_error") {
      isExecuting = false;
      btnRun.disabled = false;
      btnRun.innerHTML = '<i class="fa-solid fa-play"></i><span>Execute Dialogue</span>';
      if (data.type === "run_error") {
        alert("Dialogue Error: " + data.error);
      }
    }
  }

  // Animation Loop for Topology Canvas
  function drawCanvas() {
    if (!ctx || !canvas) return;
    ctx.clearRect(0, 0, canvas.width, canvas.height);

    const w = canvas.width;
    const h = canvas.height;

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
        // Dynamic position for custom agent
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
      const pTo = posMap[activePacket.toId] || { x: w * 0.5, y: h * 0.5 }; // broadcast targets center

      if (pFrom && pTo) {
        const curX = pFrom.x + (pTo.x - pFrom.x) * activePacket.progress;
        const curY = pFrom.y + (pTo.y - pFrom.y) * activePacket.progress;

        // Glowing packet dot
        ctx.save();
        ctx.shadowBlur = 14;
        ctx.shadowColor = activePacket.color || "#a855f7";
        ctx.beginPath();
        ctx.arc(curX, curY, 6, 0, Math.PI * 2);
        ctx.fillStyle = activePacket.color || "#c084fc";
        ctx.fill();
        ctx.restore();

        activePacket.progress += 0.025;
        if (activePacket.progress >= 1.0) {
          activePacket = null;
        }
      } else {
        activePacket = null;
      }
    }

    // Draw agent nodes
    for (const [id, node] of Object.entries(posMap)) {
      const radius = 22;

      // Outer glow
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

      // Inner text / emoji
      ctx.font = "16px sans-serif";
      ctx.textAlign = "center";
      ctx.textBaseline = "middle";
      ctx.fillText(node.avatar, node.x, node.y);

      // Label below node
      ctx.font = "bold 11px sans-serif";
      ctx.fillStyle = "#cbd5e1";
      ctx.fillText(node.name, node.x, node.y + radius + 14);
    }

    requestAnimationFrame(drawCanvas);
  }

  function triggerPacketAnimation(fromId, toId) {
    const color = nodePositions[fromId]?.color || "#a855f7";
    activePacket = {
      fromId: fromId,
      toId: toId === "*" ? "gpt" : toId,
      progress: 0.0,
      color: color,
    };
  }

  // Render Registered Modules
  function renderAgentList() {
    agentCountBadge.textContent = `${agents.length} Modules`;

    // Update Dropdown options
    const currentA = selectAgentA.value;
    const currentB = selectAgentB.value;
    selectAgentA.innerHTML = "";
    selectAgentB.innerHTML = "";

    moduleList.innerHTML = "";

    agents.forEach((ag, idx) => {
      // Add to select menus
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

      // Add to sidebar module card
      const item = document.createElement("div");
      item.className = "p-2.5 rounded-lg bg-slate-800/70 border border-slate-700/60 flex items-center justify-between";
      item.innerHTML = `
        <div class="flex items-center space-x-2.5 overflow-hidden">
          <div class="w-7 h-7 rounded-md flex items-center justify-center text-sm font-bold flex-shrink-0" style="background-color: ${ag.color}25; border: 1px solid ${ag.color}">
            ${ag.avatar}
          </div>
          <div class="truncate">
            <h4 class="text-xs font-semibold text-slate-200 truncate">${ag.name}</h4>
            <p class="text-[10px] text-slate-400 truncate">${ag.role}</p>
          </div>
        </div>
        <span class="text-[10px] font-mono px-1.5 py-0.5 rounded bg-slate-900 border border-slate-800 text-slate-400">
          ${ag.provider || 'Mock'}
        </span>
      `;
      moduleList.appendChild(item);
    });
  }

  // Render Transcript Messages
  function renderAllMessages() {
    messagesContainer.innerHTML = "";
    if (messages.length === 0) {
      if (emptyPlaceholder) emptyPlaceholder.style.display = "flex";
      msgCountBadge.textContent = "0 messages";
      return;
    }
    if (emptyPlaceholder) emptyPlaceholder.style.display = "none";
    msgCountBadge.textContent = `${messages.length} message${messages.length === 1 ? '' : 's'}`;

    messages.forEach((msg) => {
      appendMessageToFeed(msg, false);
    });
    scrollFeedToBottom();
  }

  function appendMessageToFeed(msg, autoScroll = true) {
    if (emptyPlaceholder) emptyPlaceholder.style.display = "none";
    msgCountBadge.textContent = `${messages.length} message${messages.length === 1 ? '' : 's'}`;

    const card = document.createElement("div");
    card.className = "msg-bubble p-4 rounded-xl bg-slate-800/50 border border-slate-700/60 shadow-md space-y-2";

    // Header: Avatar, Name, Recipient Target, Badge, Time
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
      ? `<span class="text-[11px] font-medium text-slate-400">→ <span class="text-indigo-400 font-semibold">@${msg.recipient_name || msg.recipient_id}</span></span>`
      : `<span class="text-[11px] text-slate-500">(broadcast)</span>`;

    // Markdown parse content
    let parsedContent = "";
    try {
      parsedContent = marked.parse(msg.content);
    } catch (e) {
      parsedContent = `<pre class="text-xs">${msg.content}</pre>`;
    }

    card.innerHTML = `
      <div class="flex items-center justify-between border-b border-slate-700/40 pb-2">
        <div class="flex items-center space-x-2.5">
          <div class="w-6 h-6 rounded-md flex items-center justify-center text-xs" style="background-color: ${color}30; border: 1px solid ${color}">
            ${avatar}
          </div>
          <span class="text-xs font-bold text-slate-200">${msg.sender_name}</span>
          ${targetBadge}
          <span class="text-[10px] uppercase font-mono px-2 py-0.5 rounded-full border ${badgeClass}">
            ${msg.message_type.replace('_', ' ')}
          </span>
        </div>
        <span class="text-[10px] font-mono text-slate-500">${msg.formatted_time || ""}</span>
      </div>
      <div class="text-xs text-slate-300 leading-relaxed prose prose-invert max-w-none prose-pre:bg-slate-950/80">
        ${parsedContent}
      </div>
    `;

    // Highlight code blocks and attach copy buttons
    card.querySelectorAll("pre code").forEach((block) => {
      hljs.highlightElement(block);
      const pre = block.parentElement;
      pre.classList.add("code-container");

      const copyBtn = document.createElement("button");
      copyBtn.className = "btn-copy-code";
      copyBtn.innerHTML = '<i class="fa-regular fa-copy"></i> Copy';
      copyBtn.onclick = () => {
        navigator.clipboard.writeText(block.innerText);
        copyBtn.innerHTML = '<i class="fa-solid fa-check text-emerald-400"></i> Copied!';
        setTimeout(() => {
          copyBtn.innerHTML = '<i class="fa-regular fa-copy"></i> Copy';
        }, 2000);
      };
      pre.appendChild(copyBtn);
    });

    messagesContainer.appendChild(card);

    if (autoScroll) {
      scrollFeedToBottom();
    }
  }

  function scrollFeedToBottom() {
    messagesContainer.scrollTop = messagesContainer.scrollHeight;
  }

  // Event Handlers
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

  // Preset Buttons
  document.querySelectorAll(".preset-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      const topo = btn.dataset.topology;
      selectTopology.value = topo;
      selectTopology.dispatchEvent(new Event("change"));

      if (btn.dataset.from) selectAgentA.value = btn.dataset.from;
      if (btn.dataset.to) selectAgentB.value = btn.dataset.to;
      if (btn.dataset.prompt) inputPrompt.value = btn.dataset.prompt;
    });
  });

  // Execute Dialogue
  btnRun.addEventListener("click", async () => {
    const prompt = inputPrompt.value.trim();
    if (!prompt) {
      alert("Please enter a task prompt for the modules.");
      return;
    }

    const topo = selectTopology.value;
    const turns = parseInt(selectTurns.value, 10);
    const agentA = selectAgentA.value;
    const agentB = selectAgentB.value;

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
        const err = await resp.json();
        alert("Failed to execute dialogue: " + (err.detail || "Unknown error"));
      }
    } catch (err) {
      console.error("Run error:", err);
      alert("Network error executing dialogue.");
    }
  });

  // Clear Session
  btnClear.addEventListener("click", async () => {
    if (confirm("Clear all messages and reset module memory?")) {
      await fetch("/api/clear", { method: "POST" });
    }
  });

  // Export Buttons
  btnExportMd.addEventListener("click", async () => {
    const res = await fetch("/api/export/markdown");
    const data = await res.json();
    downloadFile("module_mesh_transcript.md", data.markdown, "text/markdown");
  });

  btnExportJson.addEventListener("click", async () => {
    const res = await fetch("/api/export/json");
    const data = await res.json();
    downloadFile("module_mesh_transcript.json", data.json, "application/json");
  });

  function downloadFile(filename, content, mime) {
    const blob = new Blob([content], { type: mime });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  }

  // Modals
  btnNewAgent.addEventListener("click", () => agentModal.classList.remove("hidden"));
  btnCloseAgentModal.addEventListener("click", () => agentModal.classList.add("hidden"));
  btnCancelAddAgent.addEventListener("click", () => agentModal.classList.add("hidden"));

  formAddAgent.addEventListener("submit", async (e) => {
    e.preventDefault();
    const payload = {
      agent_id: document.getElementById("newAgentId").value.trim(),
      name: document.getElementById("newAgentName").value.trim(),
      role: document.getElementById("newAgentRole").value.trim(),
      system_prompt: document.getElementById("newAgentPrompt").value.trim(),
      color: document.getElementById("newAgentColor").value,
      avatar: document.getElementById("newAgentAvatar").value.trim() || "🤖",
    };

    try {
      const resp = await fetch("/api/agents", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      if (resp.ok) {
        agentModal.classList.add("hidden");
        formAddAgent.reset();
      } else {
        const err = await resp.json();
        alert(err.detail || "Could not add agent");
      }
    } catch (err) {
      alert("Error adding agent: " + err.message);
    }
  });

  btnSettings.addEventListener("click", () => settingsModal.classList.remove("hidden"));
  btnCloseSettingsModal.addEventListener("click", () => settingsModal.classList.add("hidden"));
  btnCancelSettings.addEventListener("click", () => settingsModal.classList.add("hidden"));

  formSettings.addEventListener("submit", async (e) => {
    e.preventDefault();
    const payload = {
      openai_api_key: document.getElementById("inputOpenAiKey").value.trim() || null,
      anthropic_api_key: document.getElementById("inputAnthropicKey").value.trim() || null,
      openai_base_url: document.getElementById("inputOpenAiBaseUrl").value.trim() || null,
    };

    try {
      const resp = await fetch("/api/config", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      if (resp.ok) {
        settingsModal.classList.add("hidden");
        alert("Configuration saved successfully!");
      }
    } catch (err) {
      alert("Error saving settings: " + err.message);
    }
  });

  // Start WebSocket and Canvas Loop
  initWebSocket();
  requestAnimationFrame(drawCanvas);
});
