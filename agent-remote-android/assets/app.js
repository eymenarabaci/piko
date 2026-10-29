let serverHost = "";
let serverToken = "";
let activeRunId = null;
let eventPollInterval = null;
let runsPollInterval = null;
let selectedMode = "orchestrate";

// Helper for Android Bridge or LocalStorage fallback
const storage = {
  get: (key, def) => {
    try {
      if (window.AndroidBridge && window.AndroidBridge.getPref) {
        const val = window.AndroidBridge.getPref(key, "");
        return val ? val : def;
      }
      return localStorage.getItem(key) || def;
    } catch (_) {
      return def;
    }
  },
  set: (key, val) => {
    try {
      if (window.AndroidBridge && window.AndroidBridge.savePref) {
        window.AndroidBridge.savePref(key, val);
      }
      localStorage.setItem(key, val);
    } catch (_) {}
  }
};

const native = {
  vibrate: (ms) => {
    try {
      if (window.AndroidBridge && window.AndroidBridge.vibrate) {
        window.AndroidBridge.vibrate(ms);
      } else if (navigator.vibrate) {
        navigator.vibrate(ms);
      }
    } catch (_) {}
  },
  toast: (msg) => {
    try {
      if (window.AndroidBridge && window.AndroidBridge.showToast) {
        window.AndroidBridge.showToast(msg);
      } else {
        console.log("[Toast]", msg);
      }
    } catch (_) {}
  }
};

// Handle Android Back button
window.handleBackPressed = () => {
  const detailModal = document.getElementById("task-detail-modal");
  if (detailModal && !detailModal.hidden) {
    closeTaskDetail();
    return true;
  }
  return false;
};

// Preset host buttons
function applyPresetHost(host) {
  const input = document.getElementById("server-host");
  input.value = host;
  native.vibrate(15);
  document.getElementById("server-token").focus();
}

// Initialize app
window.addEventListener("DOMContentLoaded", () => {
  // Load saved connection
  const defaultHost = (window.location && window.location.protocol.startsWith("http")) ? window.location.origin : "http://127.0.0.1:8765";
  serverHost = storage.get("agent_server_host", defaultHost);
  serverToken = storage.get("agent_server_token", "");

  const hostEl = document.getElementById("server-host");
  const tokenEl = document.getElementById("server-token");
  if (hostEl) hostEl.value = serverHost;
  if (tokenEl) tokenEl.value = serverToken;

  // If no token is set yet, go straight to settings tab
  if (!serverToken) {
    switchTab("tab-settings");
    updateConnectionUI(false, "Token Girin");
  } else {
    checkConnection();
  }

  // Periodic polling for runs
  runsPollInterval = setInterval(() => {
    const isTasksTab = document.getElementById("view-tasks").classList.contains("active");
    if (isTasksTab && serverHost && serverToken) {
      fetchRuns(false);
    }
  }, 4000);
});

// Navigation tabs (Synchronizes both top tabs and bottom nav)
function switchTab(tabId) {
  // Update Top Tab buttons
  document.querySelectorAll(".top-nav-tabs .top-tab-btn").forEach(btn => {
    btn.classList.toggle("active", btn.getAttribute("data-tab") === tabId);
  });

  // Update Bottom Nav buttons
  document.querySelectorAll(".bottom-nav .nav-btn").forEach(btn => {
    btn.classList.toggle("active", btn.getAttribute("data-tab") === tabId);
  });

  const tabViewMap = {
    "tab-tasks": "view-tasks",
    "tab-new-task": "view-new-task",
    "tab-system": "view-system",
    "tab-settings": "view-settings"
  };

  const targetView = tabViewMap[tabId] || "view-tasks";
  document.querySelectorAll(".views-container .view").forEach(v => {
    v.classList.toggle("active", v.id === targetView);
  });

  native.vibrate(10);

  if (targetView === "view-tasks" && serverHost && serverToken) fetchRuns(true);
  if (targetView === "view-system" && serverHost && serverToken) fetchSystemInfo();
}

function selectMode(mode) {
  selectedMode = mode;
  document.querySelectorAll(".mode-chip").forEach(chip => {
    chip.classList.toggle("selected", chip.getAttribute("data-mode") === mode);
  });
  native.vibrate(15);
}

function toggleTokenVisibility() {
  const input = document.getElementById("server-token");
  input.type = input.type === "password" ? "text" : "password";
}

function updateConnectionUI(connected, text) {
  const pill = document.getElementById("conn-pill");
  const textEl = document.getElementById("conn-text");
  const banner = document.getElementById("tasks-not-connected-banner");

  if (pill) {
    pill.className = `conn-pill ${connected ? "connected" : "disconnected"}`;
  }
  if (textEl) {
    textEl.textContent = text;
  }
  if (banner) {
    banner.hidden = connected;
  }
}

// API Fetch wrapper
async function apiRequest(endpoint, options = {}) {
  if (!serverHost) {
    throw new Error("Sunucu adresi boş.");
  }
  const cleanHost = serverHost.trim().replace(/\/+$/, "");
  const url = `${cleanHost}${endpoint}`;
  const headers = {
    "Accept": "application/json",
    ...(options.headers || {})
  };

  if (serverToken) {
    headers["Authorization"] = `Bearer ${serverToken.trim()}`;
  }

  const method = (options.method || "GET").toUpperCase();
  if (method === "POST" || method === "PUT" || method === "DELETE") {
    headers["Content-Type"] = "application/json";
    if (options.body && typeof options.body === "object") {
      options.body = JSON.stringify(options.body);
    } else if (!options.body) {
      options.body = "{}";
    }
  }

  const res = await fetch(url, { ...options, headers });
  if (!res.ok) {
    const errText = await res.text();
    let detail = errText;
    try {
      const parsed = JSON.parse(errText);
      detail = parsed.detail || detail;
    } catch (_) {}
    throw new Error(detail || `HTTP ${res.status}`);
  }
  return res.json();
}

// Check and verify connection
async function checkConnection() {
  if (!serverHost || !serverToken) {
    updateConnectionUI(false, "Token Girin");
    return;
  }

  updateConnectionUI(false, "Bağlanıyor…");

  try {
    const start = performance.now();
    await apiRequest("/api/bootstrap");
    const latency = Math.round(performance.now() - start);
    updateConnectionUI(true, `Bağlı (${latency}ms)`);
    fetchRuns(true);
    fetchSystemInfo();
  } catch (err) {
    updateConnectionUI(false, "Bağlantı Yok");
  }
}

async function testAndSaveConnection() {
  const hostInput = document.getElementById("server-host").value.trim();
  const tokenInput = document.getElementById("server-token").value.trim();
  const resultDiv = document.getElementById("conn-test-result");

  if (!hostInput) {
    resultDiv.innerHTML = "<p style='color:#EF4444;'>Lütfen bir sunucu adresi girin.</p>";
    return;
  }
  if (!tokenInput) {
    resultDiv.innerHTML = "<p style='color:#EF4444;'>Lütfen bilgisayardaki Mobil Token'ı girin.</p>";
    return;
  }

  resultDiv.innerHTML = "<p style='color: #94A3B8;'>Bağlantı test ediliyor…</p>";
  serverHost = hostInput;
  serverToken = tokenInput;

  try {
    const start = performance.now();
    const data = await apiRequest("/api/bootstrap");
    const ms = Math.round(performance.now() - start);

    storage.set("agent_server_host", serverHost);
    storage.set("agent_server_token", serverToken);

    resultDiv.innerHTML = `
      <div style="background: rgba(16, 185, 129, 0.15); border: 1px solid #10B981; padding: 10px; border-radius: 8px; margin-top: 10px;">
        <b style="color: #10B981;">✓ Bağlantı Başarılı! (${ms}ms)</b><br>
        <small style="color: #94A3B8;">Motor: ${escapeHtml(data.engine || "LangGraph")} | ${data.models ? data.models.length : 0} model aktif</small>
      </div>`;
    native.vibrate(40);
    native.toast("Sunucuya bağlanıldı!");
    updateConnectionUI(true, `Bağlı (${ms}ms)`);

    // Automatically switch to tasks view after 1.2s
    setTimeout(() => {
      switchTab("tab-tasks");
    }, 1200);
  } catch (err) {
    resultDiv.innerHTML = `
      <div style="background: rgba(239, 68, 68, 0.15); border: 1px solid #EF4444; padding: 10px; border-radius: 8px; margin-top: 10px;">
        <b style="color: #EF4444;">✕ Bağlantı Başarısız</b><br>
        <span style="font-size: 11px; color: #F1F5F9;">${escapeHtml(err.message)}</span><br>
        <small style="color: #94A3B8;">Bilgisayarda <code>remote.cmd</code> çalıştırıldığından ve IP/Token doğruluğundan emin olun.</small>
      </div>`;
    native.vibrate(100);
    updateConnectionUI(false, "Bağlantı Yok");
  }
}

// Fetch and render runs
async function fetchRuns(showLoader = false) {
  const container = document.getElementById("runs-list");
  if (!serverToken) return;

  try {
    const runs = await apiRequest("/api/runs");
    if (!runs || runs.length === 0) {
      container.innerHTML = `
        <div class="empty-state">
          <span class="icon">◫</span>
          <p>Henüz aktif veya geçmiş görev yok.<br>Üstteki <b>"＋ Yeni Görev"</b> sekmesinden ilk görevinizi başlatabilirsiniz.</p>
        </div>`;
      return;
    }

    // Sort newest first
    runs.sort((a, b) => (b.created_at || "").localeCompare(a.created_at || ""));

    container.innerHTML = runs.map(run => {
      const statusClass = run.status || "running";
      const plan = run.plan || {};
      const steps = plan.steps || [];
      const completedSteps = Object.values(run.results || {}).filter(r => r.status === "completed").length;
      const progressText = steps.length > 0 ? `${completedSteps}/${steps.length} adım` : "Planlanıyor";

      return `
        <div class="run-card" onclick="openTaskDetail('${run.id}')">
          <div class="run-card-header">
            <span class="run-id">#${run.id.slice(0, 8)}</span>
            <span class="status-chip ${statusClass}">${run.status}</span>
          </div>
          <div class="run-prompt-preview">${escapeHtml(run.prompt)}</div>
          <div class="run-card-footer">
            <span>Model: ${escapeHtml(run.planner_model_actual || run.planner_model || "auto")}</span>
            <span>${progressText}</span>
          </div>
        </div>`;
    }).join("");
  } catch (err) {
    if (showLoader) {
      container.innerHTML = `<div class="empty-state"><p style="color:#EF4444;">Görevler alınamadı: ${escapeHtml(err.message)}</p></div>`;
    }
  }
}

function switchDetailSubtab(subtab) {
  document.querySelectorAll(".detail-subtab-btn").forEach(btn => {
    btn.classList.toggle("active", btn.getAttribute("data-subtab") === subtab);
  });
  document.querySelectorAll(".detail-pane").forEach(pane => {
    pane.classList.toggle("active", pane.id === `detail-pane-${subtab}`);
  });
  native.vibrate(10);
}

function renderMarkdown(md) {
  if (!md) return "";
  let html = escapeHtml(md);
  html = html.replace(/```([\s\S]*?)```/g, '<pre class="code-block"><code>$1</code></pre>');
  html = html.replace(/`([^`]+)`/g, '<code class="inline-code">$1</code>');
  html = html.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
  html = html.replace(/\*([^*]+)\*/g, '<em>$1</em>');
  html = html.replace(/^\s*[-*]\s+(.*)$/gm, '<li>$1</li>');
  html = html.replace(/\n\n/g, '<br><br>').replace(/\n/g, '<br>');
  return html;
}

// Open Task Detail
async function openTaskDetail(runId) {
  activeRunId = runId;
  const modal = document.getElementById("task-detail-modal");
  modal.hidden = false;
  switchDetailSubtab("chat");
  native.vibrate(20);

  await refreshTaskDetail();

  if (eventPollInterval) clearInterval(eventPollInterval);
  eventPollInterval = setInterval(refreshTaskDetail, 2500);
}

function closeTaskDetail() {
  activeRunId = null;
  const modal = document.getElementById("task-detail-modal");
  modal.hidden = true;
  if (eventPollInterval) clearInterval(eventPollInterval);
  fetchRuns(false);
}

async function refreshTaskDetail() {
  if (!activeRunId) return;

  try {
    const run = await apiRequest(`/api/runs/${activeRunId}`);
    document.getElementById("detail-run-id").textContent = `#${run.id.slice(0, 8)}`;
    document.getElementById("detail-prompt").textContent = run.prompt || "—";
    const timeEl = document.getElementById("detail-time");
    if (timeEl) timeEl.textContent = (run.created_at || "").slice(11, 16);

    const statusEl = document.getElementById("detail-status");
    statusEl.className = `status-chip ${run.status}`;
    statusEl.textContent = run.status;

    // Mobile Action buttons logic
    const btnApprove = document.getElementById("btn-approve");
    const btnResume = document.getElementById("btn-resume");
    const btnAbort = document.getElementById("btn-abort");

    if (run.status === "planned") {
      btnApprove.hidden = false;
      btnResume.hidden = true;
      btnAbort.hidden = false;
    } else if (run.status === "failed") {
      btnApprove.hidden = true;
      btnResume.hidden = false;
      btnAbort.hidden = false;
    } else if (run.status === "completed" || run.status === "cancelled") {
      btnApprove.hidden = true;
      btnResume.hidden = true;
      btnAbort.hidden = true;
    } else {
      btnApprove.hidden = true;
      btnResume.hidden = true;
      btnAbort.hidden = false;
    }

    const plan = run.plan || {};
    const steps = plan.steps || [];
    const results = run.results || {};
    const currentSteps = run.current_steps || [];
    const finishedCount = Object.values(results).filter(r => r.status === "completed").length;

    const countBadge = document.getElementById("steps-count-badge");
    if (countBadge) countBadge.textContent = `${finishedCount}/${steps.length}`;

    // 1. Render Pending Questions Awaiting User Input
    const questionsContainer = document.getElementById("detail-pending-questions");
    const pendingQuestions = steps.filter(s => results[s.id]?.awaiting_input);
    if (questionsContainer) {
      if (pendingQuestions.length === 0) {
        questionsContainer.innerHTML = "";
      } else {
        questionsContainer.innerHTML = pendingQuestions.map(s => `
          <div class="inline-question-card">
            <div class="inline-question-header">⚠️ ${escapeHtml(s.title || s.id)} · Yanıtınız Gerekiyor</div>
            <div class="inline-question-text">${escapeHtml((results[s.id].output || "Ajan devam etmek için yanıt bekliyor.").slice(-400))}</div>
            <div class="inline-question-row">
              <input type="text" id="answer-input-${s.id}" class="inline-question-input" placeholder="Yanıtınızı buraya yazın…">
              <button class="btn btn-primary" onclick="submitStepResponse('${s.id}')">Gönder & Devam Et</button>
            </div>
          </div>
        `).join("");
      }
    }

    // 2. Render Step Summaries
    const summariesContainer = document.getElementById("detail-step-summaries");
    if (summariesContainer) {
      const finishedSteps = steps.filter(s => results[s.id]?.status === "completed" && results[s.id]?.output);
      if (finishedSteps.length > 0) {
        summariesContainer.innerHTML = finishedSteps.map(s => {
          const raw = String(results[s.id].output || "");
          const snippet = raw.replace(/```[\s\S]*?```/g, " [Kod Çıktısı] ").replace(/[*#`_]/g, "").trim().slice(0, 180);
          return `
            <div class="step-summary-card">
              <div class="step-summary-header">✓ Adım Tamamlandı: ${escapeHtml(s.title || s.id)}</div>
              <div class="step-summary-text">${escapeHtml(snippet)}${raw.length > 180 ? "…" : ""}</div>
            </div>
          `;
        }).join("");
      } else {
        summariesContainer.innerHTML = "";
      }
    }

    // 3. Render Continuous Chat Thread
    const chatContainer = document.getElementById("detail-chat-thread");
    if (chatContainer) {
      let chatHtml = "";
      const chats = run.chat_history || [];

      if (chats.length > 0) {
        chatHtml = chats.map(msg => {
          if (msg.role === "user") {
            return `
              <div class="chat-bubble user-bubble">
                <div class="bubble-meta">
                  <span class="avatar">👤</span>
                  <b>Siz</b>
                  <time class="time-meta">${(msg.created_at || "").slice(11, 16)}</time>
                </div>
                <div class="bubble-body">${renderMarkdown(msg.text || "")}</div>
              </div>
            `;
          } else {
            return `
              <div class="chat-bubble assistant-bubble">
                <div class="bubble-meta">
                  <span class="avatar">◈</span>
                  <b>Orkestratör</b>
                  <span class="worker-badge">${escapeHtml(msg.model_id || "AI")}</span>
                  <time class="time-meta">${(msg.created_at || "").slice(11, 16)}</time>
                </div>
                <div class="bubble-body">${renderMarkdown(msg.text || "")}</div>
              </div>
            `;
          }
        }).join("");
      } else if (run.orchestrator_answer) {
        chatHtml = `
          <div class="chat-bubble assistant-bubble">
            <div class="bubble-meta">
              <span class="avatar">◈</span>
              <b>Orkestratör</b>
              <time class="time-meta">${(run.updated_at || "").slice(11, 16)}</time>
            </div>
            <div class="bubble-body">${renderMarkdown(run.orchestrator_answer)}</div>
          </div>
        `;
      } else {
        chatHtml = `
          <div class="empty-state" style="padding: 16px 0;">
            <p style="font-size: 12px; color: var(--text-muted);">Aşağıdaki kutudan orkestratöre bir soru sorabilir veya görevi yönlendirebilirsiniz.</p>
          </div>
        `;
      }
      chatContainer.innerHTML = chatHtml;
    }

    // 4. Render Plan Steps List (in Steps subtab)
    const stepsListEl = document.getElementById("detail-steps-list");
    if (stepsListEl) {
      if (steps.length === 0) {
        stepsListEl.innerHTML = "<p class='hint'>Plan adımları henüz oluşturuluyor…</p>";
      } else {
        stepsListEl.innerHTML = steps.map(s => {
          const res = results[s.id] || {};
          const isRunning = currentSteps.includes(s.id);
          const statusClass = isRunning ? "running" : (res.status || "pending");

          return `
            <div class="step-item ${statusClass}">
              <div class="step-item-title">
                <span>${escapeHtml(s.title || s.prompt || s.id)}</span>
                <small class="status-chip ${statusClass}">${isRunning ? "çalışıyor" : (res.status || "bekliyor")}</small>
              </div>
              <div class="step-item-meta">
                <span>Rol: ${escapeHtml(s.persona || "developer")}</span> · 
                <span>Model: ${escapeHtml(s.model_id || "otomatik")}</span>
              </div>
            </div>`;
        }).join("");
      }
    }

    // 5. Render Events / Terminal Logs
    const events = await apiRequest(`/api/runs/${activeRunId}/events?after=0`);
    const logsEl = document.getElementById("detail-terminal-logs");
    if (logsEl && events?.events?.length > 0) {
      logsEl.textContent = events.events.map(e => `[${(e.created_at || "").slice(11, 19)}] ${e.kind || e.event_type}: ${e.message}`).join("\n");
      logsEl.scrollTop = logsEl.scrollHeight;
    }
  } catch (err) {
    console.error("Refresh error:", err);
  }
}

// Send interactive message to orchestrator
async function sendRunMessage(event) {
  event.preventDefault();
  if (!activeRunId) return;

  const input = document.getElementById("detail-chat-input");
  const message = input.value.trim();
  if (!message) return;

  const sendBtn = document.getElementById("btn-send-chat");
  sendBtn.disabled = true;
  native.vibrate(20);

  // Optimistically add user bubble to thread
  const chatContainer = document.getElementById("detail-chat-thread");
  if (chatContainer) {
    chatContainer.innerHTML += `
      <div class="chat-bubble user-bubble">
        <div class="bubble-meta">
          <span class="avatar">👤</span>
          <b>Siz</b>
          <time class="time-meta">Şimdi</time>
        </div>
        <div class="bubble-body">${renderMarkdown(message)}</div>
      </div>
      <div class="chat-bubble assistant-bubble" id="chat-thinking-bubble">
        <div class="bubble-meta">
          <span class="avatar">◈</span>
          <b>Orkestratör</b>
          <span class="worker-badge">Düşünüyor…</span>
        </div>
        <div class="bubble-body"><span class="pulse-dot"></span> Yanıt hazırlanıyor…</div>
      </div>
    `;
    const scrollBox = document.getElementById("chat-messages-container");
    if (scrollBox) scrollBox.scrollTop = scrollBox.scrollHeight;
  }

  input.value = "";

  try {
    const res = await apiRequest(`/api/runs/${activeRunId}/chat`, {
      method: "POST",
      body: { message: message, allow_writes: true }
    });
    native.vibrate(30);
    await refreshTaskDetail();
  } catch (error) {
    native.toast(`Mesaj gönderilemedi: ${error.message}`);
    const thinking = document.getElementById("chat-thinking-bubble");
    if (thinking) thinking.remove();
  } finally {
    sendBtn.disabled = false;
  }
}

// Respond to an inline pending question
async function submitStepResponse(stepId) {
  const input = document.getElementById(`answer-input-${stepId}`);
  if (!input || !input.value.trim()) return;

  const answer = input.value.trim();
  native.vibrate(25);

  try {
    await apiRequest(`/api/runs/${activeRunId}/steps/${encodeURIComponent(stepId)}/respond`, {
      method: "POST",
      body: { response: answer }
    });
    native.toast("Yanıt iletildi, adım devam ettiriliyor.");
    await refreshTaskDetail();
  } catch (error) {
    native.toast(`Hata: ${error.message}`);
  }
}

// Action Handlers
async function approveAndResume() {
  if (!activeRunId) return;
  native.vibrate(30);
  try {
    await apiRequest(`/api/runs/${activeRunId}/resume`, { method: "POST" });
    native.toast("Plan onaylandı, adımlar başlatılıyor!");
    await refreshTaskDetail();
  } catch (err) {
    native.toast(`Hata: ${err.message}`);
  }
}

async function resumeCurrentRun() {
  if (!activeRunId) return;
  native.vibrate(30);
  try {
    await apiRequest(`/api/runs/${activeRunId}/resume`, { method: "POST" });
    native.toast("Görev devam ettiriliyor.");
    await refreshTaskDetail();
  } catch (err) {
    native.toast(`Hata: ${err.message}`);
  }
}

async function abortCurrentRun() {
  if (!activeRunId) return;
  native.vibrate(50);
  if (!confirm("Bu görevi iptal etmek istediğinize emin misiniz?")) return;
  try {
    await apiRequest(`/api/runs/${activeRunId}/cancel`, { method: "POST" });
    native.toast("Görev iptal edildi.");
    await refreshTaskDetail();
  } catch (err) {
    native.toast(`Hata: ${err.message}`);
  }
}

// Submit New Task
async function submitNewTask(e) {
  e.preventDefault();
  const prompt = document.getElementById("task-prompt").value.trim();
  const model = document.getElementById("task-model").value;
  const allowWrites = document.getElementById("task-allow-writes").checked;

  if (!prompt) return;

  const btn = document.getElementById("btn-submit-task");
  btn.disabled = true;
  btn.textContent = "Oluşturuluyor…";
  native.vibrate(25);

  try {
    const payload = {
      prompt: prompt,
      mode: selectedMode,
      planner_model: model || "auto",
      planning_level: "fast",
      allow_writes: allowWrites
    };

    const run = await apiRequest("/api/runs", {
      method: "POST",
      body: payload
    });

    native.toast("Görev oluşturuldu!");
    document.getElementById("task-prompt").value = "";
    btn.disabled = false;
    btn.textContent = "🚀 Görevi Başlat";

    // Switch to task detail
    switchTab("tab-tasks");
    openTaskDetail(run.id);
  } catch (err) {
    btn.disabled = false;
    btn.textContent = "🚀 Görevi Başlat";
    alert(`Görev başlatılamadı: ${err.message}`);
  }
}

// Fetch System Info
async function fetchSystemInfo() {
  try {
    const data = await apiRequest("/api/bootstrap");
    const tokensEl = document.getElementById("daily-tokens-val");
    if (tokensEl) tokensEl.textContent = (data.daily_tokens || 0).toLocaleString();

    const modelsContainer = document.getElementById("models-list");
    if (modelsContainer && data.models && data.models.length > 0) {
      modelsContainer.innerHTML = data.models.map(m => {
        const isLocal = m.provider === "ollama";
        return `
          <div class="run-card" style="margin-bottom: 8px; padding: 10px;">
            <div style="display:flex; justify-content:space-between; align-items:center;">
              <b>${escapeHtml(m.id)}</b>
              <span class="status-chip ${isLocal ? 'completed' : 'running'}">${isLocal ? 'Yerel (RTX 3060)' : m.provider}</span>
            </div>
            <div style="font-size:11px; color:#94A3B8; margin-top:4px;">
              Roller: ${(m.capabilities || []).join(", ")}
            </div>
          </div>`;
      }).join("");
    }
  } catch (err) {
    console.error("System info error:", err);
  }
}

function escapeHtml(str) {
  if (!str) return "";
  return str.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}
