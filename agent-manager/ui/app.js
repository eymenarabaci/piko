const $ = selector => document.querySelector(selector);
const $$ = selector => [...document.querySelectorAll(selector)];
const activeStatuses = new Set(['queued', 'planning', 'running', 'waiting_quota', 'waiting_retry']);
const statusLabels = {
  queued: 'Sırada', planning: 'Planlanıyor', planned: 'Plan hazır',
  running: 'Çalışıyor', awaiting_review: 'İnceleme bekliyor',
  awaiting_user_test: 'Kullanıcı testi bekleniyor', scheduled: 'Zamanlandı',
  completed: 'Tamamlandı', failed: 'Başarısız', cancelled: 'İptal edildi',
  interrupted: 'Kesildi', pending: 'Bekliyor', blocked: 'Bağımlılık bekliyor',
  waiting_retry: 'Ağ/servis hatası · yeniden deneme bekleniyor', success: 'Tamamlandı', skipped: 'Atlandı', waiting_quota: '⏳ Kota Bekleniyor'
};
const unifiedHint = 'Sorunuzu veya yapmak istediğiniz işi yazın. Gerektiğinde planı inceleyip onaylayabilirsiniz.';
const instructionDeliveryLabels = {
  now: 'Şimdiki ajana', next_step: 'Sıradaki adımda', next_plan: 'Sonraki planda'
};
const instructionStatusLabels = {
  queued: 'Sırada', sending: 'İletiliyor', delivered: 'İletildi', applied: 'Uygulandı',
  delivery_unknown: 'Teslim doğrulanamadı', failed: 'İletilemedi',
  deferred: 'Sonraki plana ayrıldı'
};
const phaseStatusLabels = {
  planning: 'Planlanıyor', planned: 'Plan hazır', executing: 'Uygulanıyor',
  auditing: 'Önceki iş denetleniyor', awaiting_review: 'İnceleme bekleniyor',
  awaiting_user_test: 'Kullanıcı testi bekleniyor', completed: 'Tamamlandı'
};

function instructionRecords(run) {
  const records = [...(run.instruction_history || []), ...(run.instruction_queue || [])];
  const latest = new Map();
  for (const item of records) if (item && typeof item === 'object' && item.id) latest.set(item.id, item);
  return [...latest.values()].sort((a, b) => String(a.created_at || '').localeCompare(String(b.created_at || '')));
}

function programProgress(run) {
  const items = Array.isArray(run.program_items) ? run.program_items : [];
  if (!items.length) return null;
  const index = Math.min(Math.max(Number(run.program_index) || 0, 0), items.length - 1);
  return {index, total: items.length, item: items[index] || {}};
}

async function submitActiveInstruction(run, message, delivery) {
  const res = await api(`/api/runs/${encodeURIComponent(run.id)}/instructions`, {
    method: 'POST',
    body: JSON.stringify({message, delivery, client_message_id: crypto.randomUUID().replaceAll('-', '')})
  });
  if (res.run) {
    storeRun(res.run);
    if (state.run?.id === run.id) state.run = res.run;
  }
  state.fingerprint = '';
  renderRun();
  const actual = res.actual_delivery || res.delivery || res.instruction?.delivery || delivery;
  toast(actual !== delivery
    ? `Şimdiki ajana canlı iletim desteklenmedi; talimat ${instructionDeliveryLabels[actual] || actual} için sıraya alındı.`
    : `Talimat kaydedildi: ${instructionDeliveryLabels[actual] || actual}.`);
  return res;
}

const state = {
  bootstrap: null, runs: [], run: null, selectedId: null,
  page: 'chat', mode: 'auto', tab: 'flow', step: null,
  events: [], cursor: 0, busy: false, timer: null, generation: 0,
  fingerprint: '', polling: false, settings: {},
  personasData: { personas: [], skills: [], mcps: [] },
  activeTerminalId: null, runTokens: 0,
  projects: [], activeProject: null, pendingAttachments: [], conversationRuns: [], runTokenTotals: {},
  view: 'piko', notifications: [], notificationsAt: 0
};

const MAX_ATTACHMENT_BYTES = 6000000;
const MAX_ATTACHMENTS = 4;

function attachmentIds() {
  return state.pendingAttachments.filter(item => item.id).map(item => item.id);
}

function attachmentChips(container, attachments, removable = false) {
  if (!attachments?.length) return;
  const list = node('div', 'attachment-chips');
  for (const item of attachments) {
    const chip = node('span', 'attachment-chip');
    if (item.mime?.startsWith('image/')) {
      const img = node('img', 'attachment-thumb');
      img.src = item.previewUrl || `/api/attachments/${encodeURIComponent(item.id)}/content`;
      img.alt = '';
      chip.append(img);
    } else chip.append(node('span', 'attachment-icon', '▤'));
    const label = node('span', 'attachment-name', item.name || 'Dosya');
    label.title = item.name || '';
    chip.append(label);
    if (item.uploading) chip.append(node('span', 'attachment-state', 'Yükleniyor…'));
    else if (removable) chip.append(button('×', 'attachment-remove', () => {
      state.pendingAttachments = state.pendingAttachments.filter(attachment => attachment !== item);
      if (item.previewUrl) URL.revokeObjectURL(item.previewUrl);
      renderPendingAttachments();
    }));
    else if (item.id) {
      const link = node('a', 'attachment-open', '↗');
      link.href = `/api/attachments/${encodeURIComponent(item.id)}/content`;
      link.target = '_blank'; link.rel = 'noopener';
      link.title = 'Dosyayı aç';
      chip.append(link);
    }
    list.append(chip);
  }
  container.append(list);
}

function renderPendingAttachments() {
  const holder = $('#attachment-list');
  holder.replaceChildren();
  attachmentChips(holder, state.pendingAttachments, true);
  holder.hidden = state.pendingAttachments.length === 0;
}

function clearPendingAttachments() {
  for (const item of state.pendingAttachments) if (item.previewUrl) URL.revokeObjectURL(item.previewUrl);
  state.pendingAttachments = [];
  renderPendingAttachments();
}

function fileBase64(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onerror = () => reject(new Error('Dosya okunamadı.'));
    reader.onload = () => resolve(String(reader.result).split(',', 2)[1] || '');
    reader.readAsDataURL(file);
  });
}

async function queueAttachments(files) {
  if (!files?.length) return;

  for (const file of files) {
    if (state.pendingAttachments.length >= MAX_ATTACHMENTS) {
      toast('Bir mesajda en fazla dört dosya ekleyebilirsiniz.');
      break;
    }
    if (!file.size || file.size > MAX_ATTACHMENT_BYTES) {
      toast(`${file.name}: Dosya boş veya 6 MB sınırını aşıyor.`);
      continue;
    }
    const item = {name: file.name, mime: file.type, size: file.size, uploading: true,
      previewUrl: file.type.startsWith('image/') ? URL.createObjectURL(file) : ''};
    state.pendingAttachments.push(item);
    renderPendingAttachments();
    try {
      const content_base64 = await fileBase64(file);
      const uploaded = await api('/api/attachments', {method: 'POST',
        body: JSON.stringify({name: file.name, content_base64}), timeoutMs: 60000});
      Object.assign(item, uploaded, {uploading: false});
    } catch (error) {
      state.pendingAttachments = state.pendingAttachments.filter(attachment => attachment !== item);
      if (item.previewUrl) URL.revokeObjectURL(item.previewUrl);
      toast(`${file.name}: ${errorText(error)}`);
    }
    renderPendingAttachments();
  }
}

function stripAnsi(str) {
  if (!str) return '';
  return str.replace(/[\u001b\u009b][[()#;?]*(?:[0-9]{1,4}(?:;[0-9]{0,4})*)?[0-9A-ORZcf-nqry=><]/g, '');
}

function escapeHtml(str) {
  return String(str).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

async function copyToClipboard(text, btn) {
  try {
    await navigator.clipboard.writeText(text);
    if (btn) {
      const orig = btn.textContent;
      btn.textContent = '✓ Kopyalandı!';
      setTimeout(() => { btn.textContent = orig; }, 2000);
    }
    toast('Panoya kopyalandı.');
  } catch (err) {
    toast('Kopyalama başarısız oldu.');
  }
}

function renderMarkdownHtml(raw) {
  if (!raw) return '';
  let text = stripAnsi(String(raw));

  // Extract code blocks first to protect them from inline formatting
  const codeBlocks = [];
  text = text.replace(/```([a-zA-Z0-9_\-\+]*)\n([\s\S]*?)```/g, (match, lang, code) => {
    const idx = codeBlocks.length;
    codeBlocks.push({ lang: lang || 'kod', code: code.trimEnd() });
    return `%%CODE_BLOCK_${idx}%%`;
  });
  // Agent output is untrusted text. Keep only formatting generated below as HTML.
  text = escapeHtml(text);

  // Headers (# ...)
  text = text.replace(/^### (.*$)/gim, '<h3>$1</h3>');
  text = text.replace(/^## (.*$)/gim, '<h2>$1</h2>');
  text = text.replace(/^# (.*$)/gim, '<h1>$1</h1>');

  // Blockquotes (> ...)
  text = text.replace(/^\> (.*$)/gim, '<blockquote>$1</blockquote>');

  // Bold & Italic
  text = text.replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>');
  text = text.replace(/\*(.*?)\*/g, '<em>$1</em>');

  // Inline code (`...`)
  text = text.replace(/`([^`]+)`/g, '<code>$1</code>');

  // Group consecutive list lines; do not merge unrelated paragraphs into a list.
  text = text.replace(/(?:^|\n)((?:\s*[-*]\s+[^\n]+(?:\n|$)){1,})/g, (_match, list) =>
    `\n<ul>${list.trim().split('\n').map(line => `<li>${line.replace(/^\s*[-*]\s+/, '')}</li>`).join('')}</ul>\n`);
  text = text.replace(/(?:^|\n)((?:\s*\d+\.\s+[^\n]+(?:\n|$)){1,})/g, (_match, list) =>
    `\n<ol>${list.trim().split('\n').map(line => `<li>${line.replace(/^\s*\d+\.\s+/, '')}</li>`).join('')}</ol>\n`);

  // Line breaks / paragraphs
  const paragraphs = text.split(/\n{2,}/);
  text = paragraphs.map(p => {
    p = p.trim();
    if (!p) return '';
    if (p.startsWith('<h') || p.startsWith('<ul>') || p.startsWith('<ol>') || p.startsWith('<blockquote>') || p.startsWith('%%CODE_BLOCK_')) {
      return p;
    }
    return `<p>${p.replace(/\n/g, '<br>')}</p>`;
  }).join('\n');

  // Restore code blocks with syntax cards and Copy button
  text = text.replace(/%%CODE_BLOCK_(\d+)%%/g, (match, id) => {
    const block = codeBlocks[Number(id)];
    if (!block) return '';
    const safeCode = escapeHtml(block.code);
    if ((block.lang || '').toLowerCase() === 'mermaid') {
      return `<div class="mermaid-diagram" data-mermaid="${encodeURIComponent(block.code)}"><div class="mermaid-target">${safeCode}</div></div>`;
    }
    return `
      <div class="code-block-wrap">
        <div class="code-block-header">
          <span class="code-lang">${escapeHtml(block.lang)}</span>
          <button type="button" class="copy-code-btn" data-code="${encodeURIComponent(block.code)}">📋 Kopyala</button>
        </div>
        <pre class="code-content"><code>${safeCode}</code></pre>
      </div>
    `;
  });

  return text;
}

function renderMermaidDiagrams() {
  if (window.mermaid) {
    try {
      window.mermaid.initialize({ startOnLoad: false, theme: 'default', securityLevel: 'loose' });
      const targets = document.querySelectorAll('.mermaid-target:not([data-processed="true"])');
      targets.forEach(async (el, idx) => {
        el.setAttribute('data-processed', 'true');
        try {
          const parent = el.closest('.mermaid-diagram');
          const code = decodeURIComponent(parent?.dataset?.mermaid || '') || el.textContent;
          const id = `mermaid-svg-${Date.now()}-${idx}`;
          const { svg } = await window.mermaid.render(id, code);
          if (parent) parent.innerHTML = svg;
        } catch (e) {
          console.warn('Mermaid SVG render failed:', e);
        }
      });
    } catch (err) {
      console.warn('Mermaid initialize error:', err);
    }
  }
}

function _noop() { if (1) { while (1) { if (1) {
    }
  }
}


}

function load(key, fallback) {
  try { return JSON.parse(localStorage.getItem(key)) ?? fallback; }
  catch { return fallback; }
}
function save(key, value) {
  try { localStorage.setItem(key, JSON.stringify(value)); } catch {}
}
state.settings = load('agent-manager-settings', {});
const planningLevelLabels = {fast: 'Hızlı', balanced: 'Dengeli', detailed: 'Ayrıntılı', parallel: 'Paralel'};
if (!Object.hasOwn(planningLevelLabels, state.settings.planning_level)) state.settings.planning_level = 'fast';
$('#planning-level').value = state.settings.planning_level;

function node(tag, className, text) {
  const el = document.createElement(tag);
  if (className) el.className = className;
  if (text !== undefined) el.textContent = String(text);
  return el;
}
function button(text, className, action) {
  const el = node('button', className, text);
  el.type = 'button';
  if (action) el.addEventListener('click', action);
  return el;
}
function badge(status) {
  return node('span', `status-badge status-${Object.hasOwn(statusLabels, status) ? status : 'pending'}`,
              statusLabels[status] || status || 'Bekliyor');
}
function date(value, short = false) {
  if (!value) return '';
  const d = new Date(value);
  return Number.isNaN(d.valueOf()) ? '' : d.toLocaleString('tr-TR', short
    ? { hour: '2-digit', minute: '2-digit', second: '2-digit' }
    : { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' });
}
function toast(message) {
  const el = $('#toast');
  el.textContent = message;
  el.hidden = false;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => el.hidden = true, 7000);
}
function errorText(error) {
  return error?.message || 'İşlem tamamlanamadı.';
}

async function api(path, options = {}) {
  const { timeoutMs = 20000, ...requestOptions } = options;
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const response = await fetch(path, {
      ...requestOptions,
      headers: { 'Content-Type': 'application/json', ...requestOptions.headers },
      signal: controller.signal
    });
    let data;
    const rawText = await response.text();
    try { data = rawText ? JSON.parse(rawText) : {}; }
    catch {
      if (response.ok) return {};
      throw new Error(rawText?.trim() || `Sunucu yanıtı okunamadı (${response.status}).`);
    }
    if (!response.ok) {
      const detail = data.detail || data.error || data.message;
      throw new Error(typeof detail === 'string' ? detail : detail ? JSON.stringify(detail) : `İstek başarısız (${response.status}).`);
    }
    return data;
  } catch (error) {
    if (error.name === 'AbortError') {
      throw new Error('Sunucu zaman aşımına uğradı. Bağlantıyı kontrol edip yeniden deneyin.');
    }
    throw error;
  } finally {
    clearTimeout(timer);
  }
}

function connected(ok) {
  const el = $('#connection');
  el.className = `connection ${ok ? 'online' : 'offline'}`;
  el.querySelector('span').textContent = ok ? 'Bağlı' : 'Bağlantı kesildi';
}
function setBusy(value) {
  state.busy = value;
  $('#send-button').disabled = value;
  $('#composer').setAttribute('aria-busy', String(value));
}

function beginChatLive(prompt, modelId, runId, attachments = []) {
  $('#chat-live')?.remove();
  const live = node('section', 'chat-live');
  live.id = 'chat-live';
  live.setAttribute('aria-live', 'polite');
  const user = node('article', 'message-bubble user-message');
  user.append(node('div', 'user-head', '👤 Siz'), node('div', 'user-body', prompt));
  attachmentChips(user, attachments);
  const assistant = node('article', 'message-bubble chat-bubble-assistant chat-live-answer');
  const head = node('div', 'chat-bubble-head');
  head.append(node('span', 'chat-live-dot'), node('span', '', 'Ajan'),
    node('small', 'worker-id-pill', modelId || 'Seçili model'));
  const status = node('p', 'chat-live-status', 'İstek gönderiliyor…');
  const elapsed = node('small', 'chat-live-elapsed', '0 sn');
  assistant.append(head, status, elapsed);
  live.append(user, assistant);
  $('#messages').append(live);
  $('#welcome').hidden = true;
  $('#messages').scrollTop = $('#messages').scrollHeight;

  const started = Date.now();
  let cursor = state.cursor || 0;
  let closed = false;
  let phase = 'Model yanıtı bekleniyor';
  if (window.PikoConstellation) {
    window.PikoConstellation.setMood('thinking', 'Talimatınız analiz ediliyor...');
  }
  const timer = setInterval(() => {
    const seconds = Math.floor((Date.now() - started) / 1000);
    elapsed.textContent = `${seconds} sn`;
    status.textContent = `${phase}…`;
    if (window.PikoConstellation) {
      if (phase.includes('çıktı') || phase.includes('yanıtlıyor')) {
        window.PikoConstellation.setMood('speaking', `${phase} (${seconds} sn)`);
      } else {
        window.PikoConstellation.setMood('thinking', `${phase} (${seconds} sn)`);
      }
    }
  }, 1000);
  const pollActivity = async () => {
    if (!runId || closed) return;
    try {
      const data = await api(`/api/runs/${encodeURIComponent(runId)}/events?after=${cursor}`, {timeoutMs: 8000});
      cursor = data.cursor ?? cursor;
      for (const item of data.events || []) {
        if (item.data?.step_id !== 'chat' && item.data?.step_id !== 'orchestrator_chat') continue;
        if (item.kind === 'provider_started') phase = 'Ajan çalışıyor';
        if (item.kind === 'provider_activity') {
          phase = item.data?.received_bytes > 0 ? 'Ajan çıktı üretiyor' : 'Ajan çalışıyor, yanıt bekleniyor';
        }
      }
    } catch { /* First poll may arrive before a new chat is saved. */ }
    if (!closed) setTimeout(pollActivity, 2000);
  };
  pollActivity();
  return {
    finish(error = '') {
      closed = true;
      clearInterval(timer);
      if (error) {
        if (window.PikoConstellation) window.PikoConstellation.setMood('worried', error);
        assistant.classList.add('chat-live-error');
        head.querySelector('.chat-live-dot')?.remove();
        status.textContent = error;
        elapsed.textContent = 'Yanıt alınamadı';
      } else {
        if (window.PikoConstellation) {
          window.PikoConstellation.setMood('speaking', 'Piko yanıtı sundu.');
          setTimeout(() => {
            if (window.PikoConstellation && !state.run) window.PikoConstellation.setMood('idle');
          }, 3500);
        }
        live.remove();
      }
    }
  };
}

function setPage(page) {
  state.page = page;
  $('#chat-page').hidden = page !== 'chat';
  $('#data-page').hidden = page === 'chat';
  $$('[data-page]').forEach(el => el.classList.toggle('active', el.dataset.page === page));
  $('#page-title').textContent = {
    chat: 'Sohbet',
    models: 'Modeller ve CLI',
    personas: 'Personalar ve Beceriler (Skills)',
    memory: 'Kalıcı Bellek (Long-Term Memory)',
    triggers: 'Proaktif Tetikleyiciler (Autonomous Triggers)',
    history: 'Görev Geçmişi',
    remote: 'Mobil & Uzaktan Erişim'
  }[page] || 'Görev Orkestrasyonu';

  if (page === 'models') renderModels();
  if (page === 'personas') renderPersonasPage();
  if (page === 'memory') renderMemoryPage();
  if (page === 'triggers') renderTriggersPage();
  if (page === 'history') {
    renderHistory();
    refreshRuns().then(renderHistory).catch(e => toast(errorText(e)));
  }
  if (page === 'remote') renderRemotePage();
  schedulePoll();
}

function setMode() {
  state.mode = 'auto';
  $('#mode-hint').textContent = unifiedHint;
  $('#planner-model').disabled = true;
  $('#planner-model-control').hidden = true;
  $('#chat-model-control').hidden = false;
}

function stepStatus(step) {
  const result = state.run?.results?.[step.id];
  if (result?.status) return result.status;
  if (state.run?.current_steps?.includes(step.id)) return 'running';
  if (step.dependencies?.some(id => !['completed', 'success'].includes(state.run?.results?.[id]?.status))) return 'blocked';
  return 'pending';
}

function conversationKey(run) { return run?.conversation_id || run?.id; }
function conversationList() { return state.conversationRuns.length ? state.conversationRuns : (state.run ? [state.run] : []); }
function groupedRuns(runs) {
  const groups = new Map();
  for (const run of runs) {
    const key = conversationKey(run);
    const existing = groups.get(key);
    if (!existing || String(run.execution_created_at || run.created_at) > String(existing.execution_created_at || existing.created_at)) groups.set(key, run);
  }
  return [...groups.values()];
}
async function loadConversation(id, generation = state.generation) {
  const data = await api(`/api/runs/${encodeURIComponent(id)}/conversation`);
  if (generation !== state.generation) return;
  const runs = data.runs || [];
  if (runs.length) {
    state.conversationRuns = runs;
    const latest = runs[runs.length - 1];
    state.run = latest;
    state.selectedId = latest.id;
    save('agent-manager-selected', latest.id);
    for (const run of runs) storeRun(run);
  }
}

function storeRun(run) {
  const i = state.runs.findIndex(r => r.id === run.id);
  if (i < 0) state.runs.unshift(run);
  else state.runs[i] = run;
  const group = state.conversationRuns;
  if (group.some(item => conversationKey(item) === conversationKey(run))) {
    const index = group.findIndex(item => item.id === run.id);
    if (index >= 0) group[index] = run; else group.push(run);
    group.sort((a, b) => String(a.execution_created_at || a.created_at).localeCompare(String(b.execution_created_at || b.created_at)));
    state.run = group[group.length - 1];
    state.selectedId = state.run.id;
  }
  renderRecent();
}

let recentRefreshTimer = null;
let recentRefreshInFlight = false;
async function refreshRecentInBackground() {
  if (document.hidden || recentRefreshInFlight) return;
  recentRefreshInFlight = true;
  const generation = state.generation;
  const selected = state.selectedId;
  try {
    await refreshRuns();
    if (generation !== state.generation || selected !== state.selectedId || state.polling || !state.run) return;
    const latest = groupedRuns(state.runs).find(run => conversationKey(run) === conversationKey(state.run));
    if (latest && (latest.id !== state.run.id || latest.status !== state.run.status || latest.updated_at !== state.run.updated_at)) {
      await loadConversation(selected, generation);
      if (generation === state.generation) { renderRun(); schedulePoll(0); }
    }
  } catch {} finally { recentRefreshInFlight = false; }
}
function startRecentRefresh() {
  if (recentRefreshTimer) clearInterval(recentRefreshTimer);
  recentRefreshTimer = setInterval(refreshRecentInBackground, 15000);
}

async function refreshRuns() {
  const data = await api('/api/runs');
  state.runs = Array.isArray(data) ? data : (data.runs || []);
  renderRecent();
  return state.runs;
}

async function fetchProjects() {
  try {
    const list = await api('/api/projects');
    state.projects = Array.isArray(list) ? list : [];
    const savedProjId = load('agent-manager-active-project-id', null);
    let active = state.projects.find(p => p.id === savedProjId);
    if (!active && state.projects.length) {
      active = state.projects[0];
    }
    setActiveProject(active);
  } catch (err) {
    console.error('Projeler yüklenemedi:', err);
  }
}

function setActiveProject(proj) {
  state.activeProject = proj;
  if (!proj) return;
  save('agent-manager-active-project-id', proj.id);
  const nameEl = $('#active-project-name');
  const pathEl = $('#active-project-path');
  const topbarEl = $('#topbar-project-name');
  const sidebarProjEl = $('#sidebar-project-current');
  const wpEl = $('#workspace-summary');
  if (nameEl) nameEl.textContent = proj.name;
  if (pathEl) pathEl.textContent = proj.path;
  if (topbarEl) topbarEl.textContent = proj.name;
  if (sidebarProjEl) sidebarProjEl.textContent = proj.name;
  if (wpEl) wpEl.textContent = `Proje: ${proj.name}`;
  if (state.settings) {
    state.settings.workspace = proj.path;
    save('agent-manager-settings', state.settings);
  }
  renderRecent();
  loadMemoryStats();
  loadTriggersStats();
  loadProjectMetrics();
}

async function loadProjectMetrics() {
  if (!state.activeProject) return;
  const project = state.activeProject;
  try {
    const stats = await api(`/api/projects/${project.id}/stats`);
    let measured = stats.provider_reported_tokens;
    if (measured === undefined) {
      // Older servers expose only mixed totals on project stats. Sum their
      // actual per-run provider measurements instead; never show estimates.
      const normalized = value => String(value || '').replace(/\\/g, '/').replace(/\/+$/, '').toLowerCase();
      const runs = [...new Map([...state.runs, ...state.conversationRuns, ...(state.run ? [state.run] : [])]
        .filter(run => run.project_id === project.id || (!run.project_id && normalized(run.workspace) === normalized(project.path)))
        .map(run => [run.id, run])).values()];
      if (runs.length) {
        const totals = await Promise.all(runs.map(run => api(`/api/runs/${encodeURIComponent(run.id)}/tokens`)));
        if (totals.every(total => Number.isFinite(total.provider_reported_tokens))) {
          measured = totals.reduce((sum, total) => sum + total.provider_reported_tokens, 0);
        }
      } else if (stats.total_runs === 0) measured = 0;
    }
    if (state.activeProject?.id !== project.id) return;
    const tokEl = $('#metric-total-tokens');
    const stepEl = $('#metric-total-steps');
    const trigEl = $('#metric-active-triggers');
    const projEl = $('#metric-proj-name');
    if (tokEl) tokEl.textContent = formatTokenCount(measured);
    if (stepEl) stepEl.textContent = `${stats.total_steps} adım`;
    if (trigEl) trigEl.textContent = `${stats.active_triggers} tetikleyici`;
    if (projEl) projEl.textContent = state.activeProject.name;
  } catch (e) {
    console.warn('loadProjectMetrics error', e);
  }
}

function openProjectModal() {
  const modal = $('#project-dialog');
  if (!modal) return;
  renderProjectList();
  $('#create-project-form')?.setAttribute('hidden', '');
  modal.showModal();
}

function renderProjectList() {
  const listEl = $('#project-list');
  if (!listEl) return;
  listEl.replaceChildren();
  if (!state.projects.length) {
    listEl.append(node('div', 'empty-note', 'Kayıtlı proje bulunamadı. Yeni bir proje oluşturun.'));
    return;
  }
  for (const proj of state.projects) {
    const isAct = state.activeProject?.id === proj.id;
    const item = node('div', `project-compact-item ${isAct ? 'active-project' : ''}`);
    const col = node('div', 'project-compact-info');
    col.append(
      node('div', 'project-compact-title', proj.name),
      node('div', 'project-compact-path', proj.path)
    );
    item.append(col);
    if (isAct) {
      item.append(node('span', 'project-compact-check', '✓'));
    }
    item.addEventListener('click', () => {
      setActiveProject(proj);
      $('#project-dialog')?.close();
      toast(`'${proj.name}' projesine geçildi.`);
    });
    listEl.append(item);
  }
}

async function deleteConversation(run) {
  if (state.busy) { toast('Devam eden sohbet yanıtını bekleyin.'); return; }
  const confirmed = await new Promise(resolve => {
    const dialog = node('dialog', 'delete-conversation-dialog');
    const body = node('div', 'dialog-body');
    body.append(node('h3', '', 'Sohbet silinsin mi?'),
      node('p', '', 'Bu sohbet, içindeki tüm planlar ve kayıtlı çıktılar silinecek. Proje dosyalarınız korunur.'));
    const actions = node('div', 'dialog-footer');
    let accepted = false;
    actions.append(button('Vazgeç', 'outline-button', () => dialog.close()),
      button('Sohbeti sil', 'primary-button', () => { accepted = true; dialog.close(); }));
    dialog.append(body, actions);
    dialog.addEventListener('close', () => { dialog.remove(); resolve(accepted); }, {once: true});
    document.body.append(dialog);
    dialog.showModal();
  });
  if (!confirmed) return;
  try {
    await api(`/api/runs/${encodeURIComponent(run.id)}`, {method: 'DELETE'});
    state.runs = state.runs.filter(item => conversationKey(item) !== conversationKey(run));
    if (conversationKey(state.run) === conversationKey(run)) newChat();
    renderRecent();
    renderHistory();
    toast('Sohbet ve görev kayıtları silindi.');
  } catch (error) { toast(errorText(error)); }
}
function deleteConversationButton(run) {
  const remove = button('', 'delete-conversation', () => deleteConversation(run));
  remove.innerHTML = '<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M3 6h18M9 6V4h6v2M5 6l1 14h12l1-14M10 10v6M14 10v6"/></svg>';
  remove.setAttribute('aria-label', 'Sohbeti sil');
  remove.title = 'Sohbeti ve içindeki planları sil';
  return remove;
}

function renderRecent() {
  const container = $('#recent-runs');
  if (!container) return;
  container.replaceChildren();

  let allRuns = groupedRuns(state.runs);
  let filtered = allRuns;
  const isProjectScope = (state.recentScope || 'project') === 'project';

  if (isProjectScope && state.activeProject?.path) {
    const normProjPath = state.activeProject.path.toLowerCase().replace(/\\/g, '/');
    const matched = allRuns.filter(r => {
      if (r.project_id && r.project_id === state.activeProject.id) return true;
      const rWp = (r.workspace || '').toLowerCase().replace(/\\/g, '/');
      return rWp === normProjPath;
    });
    filtered = matched;
  }

  if (isProjectScope && allRuns.length > filtered.length) {
    container.append(button(`Diğer projelerde ${allRuns.length - filtered.length} sohbet — Tümünü göster`, 'outline-button', () => $('#scope-all-btn')?.click()));
  }
  if (!filtered.length) {
    container.append(node('p', 'empty-note', isProjectScope ? 'Bu projede henüz sohbet yok.' : 'Henüz sohbet yok.'));
    return;
  }

  filtered.slice(0, 25).forEach(run => {
    const el = button('', 'recent-run', () => selectRun(run.id));
    if (!isProjectScope) {
      const normWp = (run.workspace || '').toLowerCase().replace(/\\/g, '/');
      const p = (state.projects || []).find(pj => pj.id === run.project_id || (pj.path || '').toLowerCase().replace(/\\/g, '/') === normWp);
      if (p) {
        el.append(node('span', 'recent-run-project-tag', p.name.slice(0, 8)));
      }
    }
    el.append(document.createTextNode(run.prompt || 'İsimsiz görev'));
    el.title = `${run.prompt || run.id}\nKlasör: ${run.workspace || '-'}`;
    el.classList.toggle('selected', conversationKey(run) === conversationKey(state.run));
    const row = node('div', 'recent-run-row');
    row.append(el, deleteConversationButton(run));
    container.append(row);
  });
}

function newChat() {
  state.generation++;
  state.planViewId = null;
  state.selectedId = null;
  state.run = null;
  state.conversationRuns = [];
  state.events = [];
  state.cursor = 0;
  state.fingerprint = '';
  state.step = null;
  state.runTokens = 0;
  save('agent-manager-selected', null);
  clearTimeout(state.timer);
  setPage('chat');
  renderRun();
  renderRecent();
  $('#prompt').value = '';
  setComposerCollapsed(false);
  $('#prompt').focus();
}

async function selectRun(id) {
  state.planViewId = null;
  const generation = ++state.generation;
  state.selectedId = id;
  state.run = null;
  state.conversationRuns = [];
  state.runTokens = 0;
  state.events = [];
  state.cursor = 0;
  state.step = null;
  state.fingerprint = '';
  save('agent-manager-selected', id);
  setMode('chat');
  setPage('chat');
  renderRun();
  try {
    const run = await api(`/api/runs/${encodeURIComponent(id)}`);
    if (generation !== state.generation) return;
    state.run = run;
    const normalizedWorkspace = (run.workspace || '').toLowerCase().replace(/\\/g, '/');
    const runProject = state.projects.find(p => (p.path || '').toLowerCase().replace(/\\/g, '/') === normalizedWorkspace);
    if (runProject && runProject.id !== state.activeProject?.id) setActiveProject(runProject);
    await loadConversation(id, generation);
    $('#allow-writes').checked = run.allow_writes === true;
    renderRun();
    fetchRunTokens(id);
    await pollEvents(generation);
    connected(true);
  } catch (error) {
    if (generation === state.generation) {
      toast(errorText(error));
      connected(false);
      $('#run-content').replaceChildren(
        node('p', 'notice run-error', errorText(error)),
        button('Yeniden dene', 'outline-button', () => selectRun(id))
      );
    }
  } finally {
    schedulePoll();
  }
}
window.selectRun = selectRun;

async function fetchRunTokens(id) {
  try {
    const data = await api(`/api/runs/${encodeURIComponent(id)}/tokens`);
    state.runTokenTotals[id] = data.total_tokens || 0;
    state.runTokenBreakdowns ||= {};
    state.runTokenBreakdowns[id] = data;
    state.runTokens = state.runTokenTotals[state.selectedId] || 0;
    if (!state.tokenMetricsAt || Date.now() - state.tokenMetricsAt > 15000) {
      state.tokenMetricsAt = Date.now();
      loadProjectMetrics();
    }
    if (inspectedPlan()?.id === id) renderRun();
  } catch {}
}

function formatTokenCount(value) {
  if (!Number.isFinite(value) || value < 0) return '—';
  const number = new Intl.NumberFormat('tr-TR', {maximumFractionDigits: 1});
  if (value >= 1000000) return `${number.format(value / 1000000)}M`;
  if (value >= 1000) return `${number.format(value / 1000)}k`;
  return number.format(value);
}

function tokenAccountingNote(id) {
  const data = state.runTokenBreakdowns?.[id];
  if (!data || data.provider_reported_tokens === undefined) return 'Sağlayıcı ölçümü henüz alınmadı; tüketim bilinmiyor';
  return `Sağlayıcı bildirimi: ${data.provider_reported_tokens.toLocaleString('tr-TR')} · Ölçülmeyen kayıt: ${data.unmeasured_records ?? data.unknown_calls ?? 0}. Önbellek ve akıl yürütme dahildir; parasal ücret değildir. Tahmin üretilmez.`;
}

function inspectedPlan() {
  const runs = conversationList();
  return runs.find(run => run.id === state.planViewId) || runs.flatMap(run => run.plan_history || []).find(plan => plan.execution_id === state.planViewId) || state.run;
}
function archivedPlanSelected() { return !!state.planViewId && !conversationList().some(run => run.id === state.planViewId); }
function choosePlan(id = null) {
  state.planViewId = id;
  state.step = null;
  state.fingerprint = '';
  state.events = []; state.cursor = 0;
  renderRun();
  if (inspectedPlan()?.id) fetchRunTokens(inspectedPlan().id);
  pollEvents().catch(() => {});
}

function planGoal(run, archived = false) {
  const program = programProgress(run);
  return run.active_goal || program?.item?.goal || run.plan?.goal || run.plan?.title
    || (archived ? run.prompt || run.title : run.title) || 'Bu planın hedefi kaydedilmemiş.';
}

function stepOutcomePreview(result, running) {
  if (running) return 'Çalışıyor; sonuç henüz hazır değil.';
  if (result?.awaiting_input) return 'Devam etmek için yanıtınızı bekliyor.';
  if (result?.status === 'failed') return `Tamamlanamadı: ${String(result.error || 'Hata ayrıntılarını açın.').slice(0, 160)}`;
  const raw = String(result?.output || '');
  const outcome = raw.match(/(?:^|\n)\s*(?:#{1,4}\s*)?(?:\*\*)?(?:Sonuç|Yapılan değişiklik|Kullanıcıya etkisi|Özet)(?:\*\*)?\s*:\s*([^\n]+)(?:\n([^\n#]+))?/i);
  const text = (outcome ? outcome[1] : raw).replace(/```[\s\S]*?```/g, '').replace(/[#*`>|]/g, '').replace(/\s+/g, ' ').trim();
  return text ? (text.length > 190 ? text.slice(0, 187).trimEnd() + '…' : text) : 'Kısa sonuç özeti yok; ayrıntıları açın.';
}

function renderRunMessages(run, container, questionDrafts, archived = false) {

  for (const message of run.conversation_history || []) {
    const user = message.role === 'user';
    const bubble = node('article', `message-bubble ${user ? 'user-message' : 'chat-bubble-assistant'}`);
    bubble.append(node('div', user ? 'user-head' : 'chat-bubble-head', user ? '👤 Siz' : '◈ Orkestratör'));
    if (user) {
      bubble.append(node('div', 'user-body', message.text || ''));
      attachmentChips(bubble, message.attachments);
    } else {
      const body = node('div', 'markdown-body');
      body.innerHTML = renderMarkdownHtml(message.text || '');
      bubble.append(body);
    }
    container.append(bubble);
  }
  // 1. KULLANICI HEDEFİ BALONU
  if (!archived && !(run.mode === 'chat' && run.chat_history?.[0]?.text === run.prompt)) {
    const userBubble = node('article', `message-bubble ${archived ? 'chat-bubble-assistant piko-planned-goal' : 'user-message'}`);
    const userHead = node('div', archived ? 'chat-bubble-head' : 'user-head');
    userHead.append(node('span', 'user-avatar', archived ? '◈' : '👤'), node('b', '', archived ? 'Piko · Kendi planladığı hedef' : 'Siz'));
    if (run.created_at) userHead.append(node('time', 'time-meta', date(run.created_at, true)));
    userBubble.append(userHead, node('div', 'user-body', run.prompt));
    container.append(userBubble);
  }

  if (!archived) {
    for (const [index, previous] of (run.plan_history || []).entries()) {
      const section = node('section', 'archived-plan');
      section.append(button(`Önceki plan ${index + 1} · ${previous.title || 'Plan'} ↗`, 'archived-plan-select', () => choosePlan(previous.execution_id)));

      renderRunMessages({...previous, plan_history: [], conversation_history: [], chat_history: []}, section, [], true);
      section.querySelectorAll('.run-actions button, .inline-question button').forEach(button => button.disabled = true);
      container.append(section);
    }
  }

  const planContainer = node('section', 'plan-message-box');
  container.append(planContainer);
  const dialogueContainer = container;
  container = planContainer;

  const instructions = instructionRecords(run);
  if (instructions.length) {
    const queue = node('section', 'instruction-queue');
    queue.setAttribute('aria-label', 'Çalışan işe verilen talimatlar');
    for (const item of instructions) {
      const card = node('article', 'instruction-card');
      const head = node('div', 'instruction-card-head');
      const requested = item.requested_delivery || item.delivery;
      const deliveryLabel = requested !== item.delivery
        ? `${instructionDeliveryLabels[requested] || requested} → ${instructionDeliveryLabels[item.delivery] || item.delivery}`
        : instructionDeliveryLabels[item.delivery] || 'Ek talimat';
      head.append(node('strong', '', deliveryLabel));
      const status = node('span', `instruction-card-state status-${item.status || 'queued'}`,
        instructionStatusLabels[item.status] || item.status || 'Sırada');
      if (item.target_step_id) status.title = `Hedef adım: ${item.target_step_id}`;
      head.append(status);
      card.append(head, node('div', 'instruction-card-body', item.message || ''));
      queue.append(card);
    }
    container.append(queue);
  }
  if (!archived && run.id === state.run?.id && run.status === 'awaiting_user_test' && run.audit_user_question) {
    const request = node('form', 'user-test-request');
    request.append(node('strong', '', 'Piko sizden test sonucu bekliyor'),
      node('p', '', String(run.audit_user_question)));
    const answer = node('textarea', 'user-test-answer');
    answer.rows = 3;
    answer.required = true;
    answer.placeholder = 'Denediğiniz adımları ve gözlediğiniz sonucu yazın…';
    const submitTest = button('Test sonucunu gönder', 'primary-button');
    submitTest.type = 'submit';
    request.append(answer, submitTest);
    request.addEventListener('submit', async event => {
      event.preventDefault();
      const value = answer.value.trim();
      if (!value || state.busy) return;
      setBusy(true);
      try {
        const res = await api(`/api/runs/${encodeURIComponent(run.id)}/test-response`, {
          method: 'POST', body: JSON.stringify({answer: value})
        });
        const updated = res.run || res;
        if (updated?.id) {
          storeRun(updated);
          state.fingerprint = '';
          renderRun();
          schedulePoll();
        }
        toast('Test sonucu kaydedildi. Piko sonraki planı buna göre değerlendirecek.');
      } catch (error) { toast(errorText(error)); }
      finally { setBusy(false); }
    });
    container.append(request);
  }

  // 2. ORKESTRATÖR PLAN & MINI-DAG BALONU (Yalnızca gerçekten çok adımlı plan gerekiyorsa gösterilir)
  const steps = run.plan?.steps || [];
  const isMultiStep = steps.length > 1;

  if ((steps.length && run.mode !== 'chat') || run.status === 'planned' || programProgress(run)) {
    const planBubble = node('article', 'message-bubble plan-overview-bubble');
    const planHead = node('div', 'plan-overview-head');
    const planIdentity = node('div', 'plan-identity');
    const program = programProgress(run);
    planIdentity.append(node('span', 'plan-emblem', '◈'),
      node('b', '', program ? `İş ${program.index + 1}/${program.total} · ${program.item.title || 'Çalışma planı'}` : 'Çalışma planı'));
    const planMeta = node('div', 'plan-meta');
    if (run.planner_model_actual || run.planner_model) {
      planMeta.append(node('span', 'worker-id-pill', `🤖 ${run.planner_model_actual || run.planner_model}`));
    }
    planMeta.append(node('span', 'worker-id-pill', `Planlama: ${planningLevelLabels[run.planning_level] || planningLevelLabels.fast}`));
    planMeta.append(badge(run.status));
    planHead.append(planIdentity, planMeta);
    planBubble.append(planHead);
    if (!archived) planBubble.append(button(run.id === state.run?.id ? 'Son planı göster ↗' : 'Bu planı göster ↗', 'output-open-button', () => choosePlan(run.id)));

    const doneCount = steps.filter(step => ['completed', 'success'].includes(run.results?.[step.id]?.status)).length;
    const statusText = run.status === 'planned'
      ? `${steps.length} adımlık plan hazır. İnceleyip başlatabilirsiniz.`
      : run.status === 'completed'
        ? `${doneCount}/${steps.length} adım tamamlandı.`
        : `${doneCount}/${steps.length} adım tamamlandı · ${statusLabels[run.status] || run.status}`;
    planBubble.append(node('p', 'conversation-status', statusText));
    {
      const phaseInfo = node('p', 'program-phase-note',
        `${planGoal(run, archived)}${run.phase_status ? ` · ${phaseStatusLabels[run.phase_status] || run.phase_status}` : ''}`);
      planBubble.append(phaseInfo);
      if (program?.item.demo_requested) planBubble.append(node('span', 'program-demo-chip', 'Demo çıktısı istendi'));
      if (program?.item.schedule_hint) planBubble.append(node('span', 'program-schedule-chip', `Zamanlama: ${program.item.schedule_hint}`));
    }
    planBubble.append(button('Plan ayrıntılarını aç ↗', 'output-open-button', () => {
      state.tab = 'flow';
      renderInspector();
      $('#inspector').classList.add('open');
      $('#toggle-inspector').setAttribute('aria-expanded', 'true');
    }));

    const actions = node('div', 'run-actions');
    const more = node('details', 'plan-action-menu');
    const moreSummary = node('summary', '', '•••');
    moreSummary.setAttribute('aria-label', 'Plan işlemleri');
    const menuItems = node('div', 'plan-menu-items');
    more.append(moreSummary, menuItems);
    if (['planned', 'interrupted', 'cancelled'].includes(run.status)) {
      actions.append(button(run.status === 'planned' ? 'Planı Çalıştır' : 'Kaldığı Yerden Devam Et', 'primary-button', () => mutateRun('resume', run.id)));
    }
    const hasFailedSteps = Object.values(run.results || {}).some(r => r.status === 'failed');
    if (hasFailedSteps || run.status === 'failed') {
      actions.append(button('🔄 Başarısız Adımları Tekrar Dene', 'primary-button retry-btn', () => mutateRun('retry-failed', run.id)));
    }
    if (run.status === 'planned') {
      menuItems.append(button('Planı düzenle', 'outline-button', () => openPlanEditor(run)));
    }
    if (activeStatuses.has(run.status)) {
      menuItems.append(button('Çalışmayı durdur', 'outline-button danger-text', () => mutateRun('cancel', run.id)));
    }
    menuItems.append(button('Ajanlar ve terminaller', 'outline-button', () => {
      choosePlan(run.id);
      state.tab = 'terminals';
      renderInspector();
      $('#inspector').classList.add('open');
      $('#toggle-inspector').setAttribute('aria-expanded', 'true');
    }));
    menuItems.append(button('Kaydı indir', 'outline-button', downloadRun));
    menuItems.addEventListener('click', () => more.removeAttribute('open'));
    actions.append(more);
    planBubble.append(actions);
    container.append(planBubble);
  }

  // 3. HER ADIM İÇİN AYRI BAĞIMSIZ UZMAN AJAN MESAJ BALONLARI
  const personaIcons = {
    developer: '💻',
    security_reviewer: '🛡️',
    architect: '📐',
    data_analyst: '📊',
    ui_ux: '🎨',
    tester: '🧪',
    devops: '⚙️',
    writer: '📝',
    reporter: '📝',
    image_generator: '🎨',
    video_generator: '🎬',
    reviewer: '🔍',
    default: '👤'
  };

  const personaLabels = {
    developer: 'Yazılım Geliştirici',
    security_reviewer: 'Güvenlik Denetçisi',
    architect: 'Sistem Mimarı',
    data_analyst: 'Veri Analisti',
    ui_ux: 'Arayüz Tasarımcısı',
    tester: 'QA & Test Uzmanı',
    devops: 'DevOps & Dağıtım',
    writer: 'Teknik Yazar',
    reporter: 'Teknik Raporlayıcı',
    image_generator: 'Görsel Üretim Ajanı',
    video_generator: 'Video Üretim Ajanı',
    reviewer: 'Kod İnceleyici'
  };

  for (const step of steps) {
    if (run.mode === 'chat' && step.id === 'response') continue;
    const isRunning = (run.current_steps || []).includes(step.id);
    const savedResult = run.results?.[step.id];
    const result = isRunning ? {...savedResult, status: 'running', error: null, output: ''} : savedResult;
    if (!result && !isRunning) continue;

    const personaKey = step.persona || 'developer';
    const personaIcon = personaIcons[personaKey] || personaIcons.default;
    const personaLabel = personaLabels[personaKey] || personaKey;

    const stepBubble = node('details', 'message-bubble agent-step-bubble compact-step');
    stepBubble.dataset.stepKey = `${run.id || run.execution_id}:${step.id}`;
    const shortHead = node('summary', 'compact-step-heading');
    shortHead.append(node('strong', '', step.title || step.id),
      badge(isRunning ? 'running' : (result?.status || 'planned')),
      node('span', 'compact-step-description', stepOutcomePreview(result, isRunning)));
    stepBubble.append(shortHead);
    if (result?.awaiting_input) stepBubble.open = true;
    if (result?.status === 'completed' || result?.status === 'success') stepBubble.classList.add('status-completed');
    else if (result?.status === 'failed') stepBubble.classList.add('status-failed');
    else if (isRunning) stepBubble.classList.add('status-running');

    const bHead = node('div', 'agent-step-head');
    const bId = node('div', 'agent-step-identity');
    bId.append(node('span', '', personaIcon), node('span', '', personaLabel));
    const bPills = node('div', 'agent-step-pills');
    bPills.append(badge(isRunning && result?.status !== 'waiting_quota' ? 'running' : (result?.status || 'running')));
    bPills.append(node('span', 'worker-id-pill', `🤖 ${result?.model_id || step.model_id || 'auto'}`));
    if (result?.cao_terminal_id) {
      bPills.append(button('Terminal ↗', 'output-open-button', () => {
        openStepOutput(run, step, result);
      }));
    }
    if (isRunning && !archived) {
      const cliOutput = usesCliOutput(run, step, result);
      const live = button(cliOutput ? 'Canlı çıktıyı aç' : result?.cao_terminal_id ? 'Canlı terminali aç ↗' : 'Terminal hazırlanıyor…', 'output-open-button',
        () => cliOutput ? openStepOutput(run, step, result) : openLiveTerminal(step.title || step.id, result?.cao_terminal_id));
      live.disabled = !cliOutput && !result?.cao_terminal_id;
      bPills.append(live);
    }
    bHead.append(bId, bPills);
    stepBubble.append(bHead);

    stepBubble.append(node('h4', 'agent-step-title', `${step.title || step.id}`));

    const bodyEl = node('div', 'markdown-body agent-step-body');
    const rawContent = result?.output || (isRunning ? '⏳ Ajan çalışıyor ve komutları yürütüyor...' : (result?.error || 'Çıktı bulunamadı.'));
    bodyEl.innerHTML = renderMarkdownHtml(rawContent);
    stepBubble.append(bodyEl);

    if (result?.awaiting_input) {
      const ask = node('section', 'inline-question');
      ask.append(node('strong', '', `${step.title || step.id} · Ajan Bilgi Bekliyor`),
        node('p', '', (result.output || 'Ajan ilerlemek için yanıt bekliyor.').slice(-700)));
      const input = node('textarea', 'inline-question-input');
      input.placeholder = 'Yanıtınızı yazın…'; input.rows = 2;
      const send = button('Yanıtla ve devam et', 'primary-button', async () => {
        if (!input.value.trim()) return;
        send.disabled = true;
        try {
          await api(`/api/runs/${encodeURIComponent(run.id)}/steps/${encodeURIComponent(step.id)}/respond`,
            {method:'POST', body:JSON.stringify({response:input.value.trim()})});
          state.run = await api(`/api/runs/${encodeURIComponent(run.id)}`);
          state.fingerprint = ''; renderRun();
        } catch (error) { toast(errorText(error)); send.disabled = false; }
      });
      ask.append(input, send);
      stepBubble.append(ask);
    }

    const foot = node('div', 'agent-step-footer');
    if (result?.status === 'failed') {
      foot.append(button('⚡ Otomatik Modeli Değiştir', 'primary-button retry-btn', () => autoSwitchStepModel(run.id, step.id)));
      foot.append(button('⚙️ Modeli Manuel Seç', 'outline-button', () => openModelSwitchModal(run.id, step)));
      foot.append(button('🔄 Tekrar Dene', 'outline-button', () => retryStep(run.id, step.id)));
    }
    foot.append(button('Ayrıntılı İncele ↗', 'output-open-button', () => {
      state.step = step.id;
      state.tab = 'output';
      renderInspector();
      $('#inspector').classList.add('open');
      $('#toggle-inspector').setAttribute('aria-expanded', 'true');
    }));
    stepBubble.append(foot);

    container.append(stepBubble);
  }

  container = dialogueContainer;

  // 4. CHAT HISTORY (CANLI KULLANICI & ORKESTRATÖR DİYALOĞU)
  if (run.chat_history?.length) {
    for (const msg of run.chat_history) {
      if (msg.role === 'user') {
        const uBubble = node('article', 'message-bubble user-message');
        const uHead = node('div', 'user-head');
        uHead.append(node('span', 'user-avatar', '👤'), node('b', '', 'Siz'), node('time', 'time-meta', date(msg.created_at, true)));
        uBubble.append(uHead, node('div', 'user-body', msg.text));
        attachmentChips(uBubble, msg.attachments);
        container.append(uBubble);
      } else {
        const aiBubble = node('article', 'message-bubble chat-bubble-assistant');
        const aiHead = node('div', 'chat-bubble-head');
        aiHead.innerHTML = `<span>◈ Orkestratör</span> <small class="worker-id-pill">${msg.model_id || 'AI'}</small> <span class="chat-bubble-time">${date(msg.created_at, true)}</span>`;
        const md = node('div', 'markdown-body');
        md.innerHTML = renderMarkdownHtml(msg.text);
        aiBubble.append(aiHead, md);
        container.append(aiBubble);
      }
    }
  }

  // 5. BAŞ YÖNETİCİ ÖZETİ (EXECUTIVE BRIEFING - Yalnızca çok adımlı planlarda gösterilir)
  if (steps.length === 0 && run.orchestrator_answer && !run.chat_history?.length) {
    const aiBubble = node('article', 'message-bubble chat-bubble-assistant');
    const aiHead = node('div', 'chat-bubble-head');
    aiHead.innerHTML = `<span>◈ Orkestratör</span> <span class="chat-bubble-time">${date(run.updated_at, true)}</span>`;
    const md = node('div', 'markdown-body');
    md.innerHTML = renderMarkdownHtml(run.orchestrator_answer);
    aiBubble.append(aiHead, md);
    container.append(aiBubble);
  }

  if (run.orchestrator_answer && !run.chat_history?.length && steps.length && run.mode !== 'chat') {
    const briefingBubble = node('article', 'message-bubble executive-briefing-bubble');
    const bHead = node('div', 'briefing-head');
    bHead.append(node('span', '', '◈'), node('span', '', 'Planın sonucu'));
    bHead.append(node('span', 'briefing-badge', 'Sentez & Rapor'));
    briefingBubble.append(bHead);

    const bMd = node('div', 'markdown-body');
    bMd.innerHTML = renderMarkdownHtml(run.orchestrator_answer);
    briefingBubble.append(bMd);

    const pills = node('div', 'suggestion-pills');
    const suggestions = [
      'Değişen dosyaları ve mimariyi özetle',
      'Test senaryolarını çalıştır',
      'Sıradaki geliştirme adımını planla',
      'Kodları adım adım açıkla'
    ];
    suggestions.forEach(s => {
      const pillBtn = node('button', 'suggestion-pill-btn', `💬 ${s}`);
      pillBtn.type = 'button';
      pillBtn.onclick = () => {
        $('#prompt').value = s;
        setComposerCollapsed(false);
  $('#prompt').focus();
        $('#composer').requestSubmit();
      };
      pills.append(pillBtn);
    });
    briefingBubble.append(pills);
    planContainer.append(briefingBubble);
  }

  renderMermaidDiagrams();
}

function renderRun() {
  const run = state.run;
  statusLabels.waiting_quota = run?.quota_timer?.reason === 'transient_error' || run?.phase_status === 'retrying'
    ? 'Geçici hata · yeniden deneme bekleniyor' : 'Kota bekleniyor';
  const container = $('#run-content');
  updateMascot(run);
  if (run?.id && !workState.jobs[run.id] && !workState.loading[run.id]) loadWorkForRun(run);
  $('#welcome').hidden = !!state.selectedId;
  container.hidden = !state.selectedId;

  const anyWaitingQuota = ['waiting_quota', 'waiting_retry'].includes(run?.status) || Object.values(run?.results || {}).some(r => r.status === 'waiting_quota');
  $('#job-banner').hidden = !run || (!activeStatuses.has(run.status) && !anyWaitingQuota);
  $('#cancel-job').textContent = 'Durdur';
  const activeRun = !!run && activeStatuses.has(run.status);
  $('#instruction-delivery-control').hidden = !activeRun;
  $('#instruction-delivery-hint').textContent = activeRun
    ? ($('#instruction-delivery').value === 'now'
        ? 'Canlı terminal destekliyorsa ajana hemen iletilir; teslim durumu aşağıda görünür.'
        : $('#instruction-delivery').value === 'next_plan'
          ? 'Bu iş bittiğinde hazırlanacak yeni planın girdisi olur.'
          : 'Çalışan adımın istemi değişmez; sıradaki adıma iletilir.')
    : '';
  $('#prompt').placeholder = run && activeStatuses.has(run.status)
    ? 'Çalışan göreve ek talimat yazın…'
    : 'Mesajınızı yazın…';
  $('#job-banner').classList.toggle('banner-waiting-quota', anyWaitingQuota);
  if (anyWaitingQuota) {
    const timer = run.retry_timer || run.quota_timer || Object.values(run.results || {}).find(r => r.status === 'waiting_quota');
    const waitingStepId = timer?.step_id || Object.keys(run.results || {}).find(k => run.results[k]?.status === 'waiting_quota');
    const label = $('#job-label');
    label.replaceChildren();
    const summary = node('span', 'quota-banner-summary');
    summary.append(node('strong', '', run.phase_status === 'retrying' ? 'Geçici ağ/servis hatası · yeniden deneme bekleniyor' : 'Kota bekleniyor'), node('span', '', ' · Otomatik devam edecek · '));
    const clock = node('span', 'countdown-badge quota-countdown', 'Hesaplanıyor…');
    if (timer?.resets_at) clock.dataset.resetAt = timer.resets_at;
    summary.append(clock, node('span', '', ` · ${timer?.model_id || 'model seçimi bekleniyor'}`));
    label.append(summary);
    if (waitingStepId && !archivedPlanSelected() && run.phase_status !== 'retrying') {
      const actions = node('span', 'quota-banner-actions');
      actions.append(
        button('Model seç', 'banner-auto-switch-btn', () => {
          const step = run.plan?.steps?.find(item => item.id === waitingStepId);
          if (step) openModelSwitchModal(run.id, step);
        }),
        button('Kotayı yeniledim', 'banner-retry-btn', () => retryQuotaStep(run.id, waitingStepId))
      );
      label.append(actions);
    }
  } else {
    $('#job-label').textContent = run ? `${statusLabels[run.status] || run.status}${run.current_steps?.length ? ' · Aktif Adım: ' + run.current_steps.join(', ') : ''}` : '';
  }
  $('#footer-state').textContent = run ? statusLabels[run.status] || run.status : state.bootstrap ? 'Hazır' : 'Bağlanıyor';
  const viewRun = inspectedPlan();
  const progress = $('#conversation-progress');
  progress.hidden = !viewRun;
  if (viewRun) {
    const total = viewRun.plan?.steps?.length || 0;
    const done = Object.values(viewRun.results || {}).filter(result => ['completed', 'success'].includes(result.status)).length;
    const percent = total ? Math.round(done / total * 100) : 0;
    progress.replaceChildren();
    const stats = node('div', 'overview-stats-row');
    stats.append(node('span', 'stat-chip', `${statusLabels[viewRun.status] || viewRun.status}`));
    const program = programProgress(viewRun);
    if (program) stats.append(node('span', 'stat-chip', `İş ${program.index + 1}/${program.total}`));
    stats.append(node('span', 'stat-chip', `Adımlar ${done}/${total || '—'} · %${percent}`));
    const tokenStat = node('span', 'stat-chip', `⚡ ${formatTokenCount(state.runTokenBreakdowns?.[viewRun.id]?.provider_reported_tokens)} ölçülen token`);
    tokenStat.title = tokenAccountingNote(viewRun.id);
    stats.append(tokenStat);
    const tokenBreakdown = state.runTokenBreakdowns?.[viewRun.id];
    if (tokenBreakdown?.provider_reported_tokens !== undefined) {
      stats.append(node('span', 'stat-chip', `${tokenBreakdown.unmeasured_records ?? tokenBreakdown.unknown_calls ?? 0} kayıt ölçülemedi`));
    }
    if (viewRun.current_steps?.length) stats.append(node('span', 'stat-chip', `${viewRun.current_steps.length} ajan çalışıyor`));
    progress.append(stats);
    if (viewRun.error) progress.append(node('span', 'progress-error', String(viewRun.error).slice(0, 220)));
    const track = node('div', 'progress-bar-wrap');
    const fill = node('div', 'progress-bar-fill'); fill.style.width = `${percent}%`;
    track.append(fill); progress.append(track);
  }

  if (!run) {
    container.replaceChildren(...(state.selectedId ? [node('p', 'loading-note', 'Görev yükleniyor…')] : []));
    renderInspector();
    return;
  }

  const fingerprint = JSON.stringify([run.id, run.status, run.error, run.plan, run.results, run.quota_timer, run.current_steps, run.chat_history, run.orchestrator_answer, run.plan_history, run.program_items, run.program_index, run.phase_status, run.audit_user_question, run.instruction_queue, run.instruction_history, state.conversationRuns, state.planViewId, state.events, state.runTokens]);
  if (fingerprint === state.fingerprint) {
    renderInspector();
    return;
  }
  state.fingerprint = fingerprint;

  const messages = $('#messages');
  const oldScroll = messages.scrollTop;
  const nearBottom = messages.scrollHeight - messages.scrollTop - messages.clientHeight < 80;
  const openSteps = new Set([...container.querySelectorAll('details.compact-step[open]')].map(item => item.dataset.stepKey));
  const questionDrafts = [...container.querySelectorAll('.inline-question-input')].map(input => input.value);
  const focusedQuestion = document.activeElement?.classList?.contains('inline-question-input');
  container.replaceChildren();
  let planNumber = 0;
  for (const planRun of conversationList()) {
    const section = node('section', 'conversation-plan');
    section.dataset.runId = planRun.id;
    if (planRun.mode !== 'chat') {
      planNumber++;
      section.dataset.planNumber = String(planNumber);
      section.append(button(`Plan ${planNumber} · ${statusLabels[planRun.status] || planRun.status} · Ayrıntıları göster ↗`, 'conversation-plan-select', () => choosePlan(planRun.id)));
    }
    section.addEventListener('click', event => {
      if (event.target.closest('.archived-plan, .user-test-request, details, button, input, textarea, select')) return;
      state.planViewId = planRun.id; state.fingerprint = ''; renderRun();
    });
    renderRunMessages(planRun, section, questionDrafts);
    const navigation = section.querySelector(':scope > .conversation-plan-select');
    const box = section.querySelector(':scope > .plan-message-box');
    if (navigation && box) box.prepend(navigation);
    container.append(section);
  }
  container.querySelectorAll('details.compact-step').forEach(item => { if (openSteps.has(item.dataset.stepKey)) item.open = true; });
  renderWorkPanel(run, container);
  if (false) {
  container.replaceChildren();

  // User Message
  const user = node('article', 'user-message');
  const userHead = node('div', 'message-head');
  userHead.append(node('span', 'avatar', '👤'), node('span', '', 'Kullanıcı'));
  user.append(userHead, node('p', 'message-text', run.prompt));
  container.append(user);

  // Agent Response
  const response = node('article', 'agent-message');
  const head = node('div', 'message-head');
  head.append(node('span', 'avatar', '◈'), node('span', '', 'CAO & LangGraph Native Orkestratör'));
  response.append(head);

  // Status Line
  const statusLine = node('div', 'run-status-line');
  statusLine.append(badge(run.status), node('span', 'small-meta', date(run.created_at)));
  response.append(statusLine);

  const liveNote = node('p', 'conversation-status', activeStatuses.has(run.status)
    ? `${run.current_steps?.length || 0} ajan çalışıyor · Sorunuzu veya yönlendirmenizi aşağıya yazabilirsiniz.`
    : `Görev ${statusLabels[run.status] || run.status}. Sonuçları ve adımları aşağıda inceleyebilirsiniz.`);
  response.append(liveNote);

  // Errors stay beside the progress indicator above the composer.

  const steps = run.plan?.steps || [];

  if (!steps.length) {
    response.append(node('p', 'empty-note', activeStatuses.has(run.status)
      ? 'CAO Orkestratörü görevi inceliyor ve alt ajan planını hazırlıyor...'
      : 'Bu görev için kaydedilmiş plan bulunmuyor.'));
  }
  if (run.status === 'planned') {
    response.append(node('p', 'empty-note', 'Plan hazırlandı. Adımları ve personaları inceleyebilir, düzenleyebilir veya doğrudan yürütebilirsiniz.'));
  }

  // Action Buttons
  const actions = node('div', 'run-actions');
  if (['planned', 'failed', 'interrupted', 'cancelled'].includes(run.status)) {
    actions.append(button(run.status === 'planned' ? 'Planı Çalıştır' : 'Kaldığı Yerden Devam Et', 'primary-button', () => mutateRun('resume', run.id)));
  }
  const hasFailedSteps = Object.values(run.results || {}).some(r => r.status === 'failed');
  if (hasFailedSteps || run.status === 'failed') {
    actions.append(button('🔄 Başarısız Adımları Tekrar Dene', 'primary-button retry-btn', () => mutateRun('retry-failed', run.id)));
  }
  if (run.status === 'planned') {
    actions.append(button('Planı Düzenle', 'outline-button', () => openPlanEditor(run)));
  }
  if (activeStatuses.has(run.status)) {
    actions.append(button('İptal Et', 'outline-button danger-text', () => mutateRun('cancel', run.id)));
  }
  actions.append(button('Alt Ajanları & Terminali İncele', 'outline-button', () => {
    state.tab = 'terminals';
    renderInspector();
    $('#inspector').classList.add('open');
    $('#toggle-inspector').setAttribute('aria-expanded', 'true');
  }));
  actions.append(button('Kaydı İndir', 'outline-button', downloadRun));
  response.append(actions);

  // Completed steps read as short conversation updates. Full plan and output live in the inspector.
  for (const step of steps) {
    const result = run.results?.[step.id];
    if (!result || !['completed', 'success', 'failed'].includes(result.status)) continue;
    const update = node('article', 'step-conversation-message');
    update.append(node('div', 'message-head', `◈ Adım özeti · ${step.title || step.id}`));
    const raw = String(result.output || result.error || 'Bu adım tamamlandı.');
    const summary = raw.replace(/```[\s\S]*?```/g, ' [Kod çıktısı] ')
      .replace(/^#{1,6}\s+/gm, '').replace(/[*_`>]/g, '').replace(/\s+/g, ' ').trim();
    update.append(node('p', 'step-summary', summary.length > 420 ? summary.slice(0, 420).trimEnd() + '…' : summary));
    update.append(button('Tam çıktıyı aç ↗', 'output-open-button', () => {
      state.step = step.id;
      state.tab = 'output';
      renderInspector();
      $('#inspector').classList.add('open');
      $('#toggle-inspector').setAttribute('aria-expanded', 'true');
    }));
    response.append(update);
  }

  const pendingQuestions = steps.filter(step => run.results?.[step.id]?.awaiting_input);
  for (const [questionIndex, step] of pendingQuestions.entries()) {
    const ask = node('section', 'inline-question');
    ask.append(node('strong', '', `${step.title || step.id} · Yanıtınız gerekiyor`),
      node('p', '', (run.results[step.id].output || 'Ajan ilerlemek için yanıt bekliyor.').slice(-700)));
    const input = node('textarea', 'inline-question-input');
    input.placeholder = 'Yanıtınızı yazın…'; input.rows = 2;
    input.value = questionDrafts[questionIndex] || '';
    const send = button('Yanıtla ve devam et', 'primary-button', async () => {
      if (!input.value.trim()) return;
      send.disabled = true;
      try {
        await api(`/api/runs/${encodeURIComponent(run.id)}/steps/${encodeURIComponent(step.id)}/respond`,
          {method:'POST', body:JSON.stringify({response:input.value.trim()})});
        state.run = await api(`/api/runs/${encodeURIComponent(run.id)}`);
        state.fingerprint = ''; renderRun();
      } catch (error) { toast(errorText(error)); send.disabled = false; }
    });
    ask.append(input, send); response.append(ask);
  }
  // Continuous Chat Thread (Proje Sohbeti & Soru-Cevap Akışı)
  if (run.chat_history?.length || run.orchestrator_answer) {
    const thread = node('section', 'chat-thread-container');
    thread.innerHTML = '<div class="chat-thread-title">💬 Proje Sohbeti & Soru-Cevap Akışı</div>';

    if (run.orchestrator_answer && !run.chat_history?.length) {
      const aiBubble = node('div', 'chat-bubble-assistant');
      const head = node('div', 'chat-bubble-head');
      head.innerHTML = `<span>◈ Orkestratör</span> <span class="chat-bubble-time">${date(run.updated_at, true)}</span>`;
      const md = node('div', 'markdown-body');
      md.innerHTML = renderMarkdownHtml(run.orchestrator_answer);
      aiBubble.append(head, md);
      thread.append(aiBubble);
    }

    if (run.chat_history?.length) {
      for (const msg of run.chat_history) {
        if (msg.role === 'user') {
          const userBubble = node('div', 'chat-bubble-user');
          userBubble.textContent = msg.text;
          thread.append(userBubble);
        } else {
          const aiBubble = node('div', 'chat-bubble-assistant');
          const head = node('div', 'chat-bubble-head');
          head.innerHTML = `<span>◈ Orkestratör</span> <small class="worker-id-pill">${msg.model_id || 'AI'}</small> <span class="chat-bubble-time">${date(msg.created_at, true)}</span>`;
          const md = node('div', 'markdown-body');
          md.innerHTML = renderMarkdownHtml(msg.text);
          aiBubble.append(head, md);
          thread.append(aiBubble);
        }
      }
    }
    response.append(thread);
  }

  }
  renderInspector();
  messages.scrollTop = nearBottom ? messages.scrollHeight : oldScroll;
  if (focusedQuestion) container.querySelector('.inline-question-input')?.focus();
}

function renderInspector() {
  $$('[data-tab]').forEach(el => el.setAttribute('aria-selected', String(el.dataset.tab === state.tab)));
  const container = $('#inspector-content');
  const previousScroll = container.scrollTop;
  container.replaceChildren();
  const run = inspectedPlan();
  const tabNames = {flow: 'Plan', output: 'Çıktılar', terminals: 'Ajanlar', personas: 'Uzmanlıklar', chat: 'Sohbet', events: 'Etkinlik', work: 'İş kaydı'};
  const moreLabel = $('#inspector-more-label');
  if (moreLabel) moreLabel.textContent = ['flow', 'output'].includes(state.tab) ? 'Diğer' : tabNames[state.tab] || 'Diğer';

  if (!run && state.tab !== 'bridges') {
    const empty = node('div', 'inspector-empty');
    empty.append(node('span', '', '⋈'), node('h3', '', 'Henüz bir görev yok'), node('p', '', 'Plan, alt ajanlar, persona kartları ve canlı sohbet burada görünür.'));
    container.append(empty);
    return;
  }
  if (!run && state.tab === 'bridges') {
    renderBridgeInspector(container);
    container.scrollTop = previousScroll;
    return;
  }

  // 1. ALT AJANLAR / WORKER TERMINALS TAB
  if (state.tab === 'terminals') {
    container.append(node('h3', 'inspector-title', 'Çalışan ajanlar'));
    container.append(node('p', 'page-description', 'Adımların durumunu ve canlı terminallerini buradan takip edin.'));

    const steps = run.plan?.steps || [];
    const activeSteps = new Set(run.current_steps || []);

    if (!steps.length) {
      container.append(node('p', 'empty-note', 'Plan hazırlandığında alt ajanlar burada listelenecektir.'));
      if (activeStatuses.has(run.status)) {
        const planning = node('div', 'worker-card');
        planning.append(node('div', 'worker-card-title', 'Orkestratör ve planlayıcı'),
          node('p', 'empty-note', 'Çalışan CLI sürecinin salt okunur çıktısını ve etkinliğini izleyin.'));
        const actions = node('div', 'worker-actions');
        for (const [stepId, label] of [['program', 'Orkestratör çıktısını aç'], ['planner', 'Planlayıcı çıktısını aç']]) {
          actions.append(button(label, 'outline-button', () => openTerminalModal(label, '', null, {runId: run.id, stepId})));
        }
        planning.append(actions);
        container.append(planning);
      }
    }

    if (activeStatuses.has(run.status) && run.phase_status === 'auditing') {
      const audit = node('div', 'worker-card');
      audit.append(node('div', 'worker-card-title', 'Orkestratör denetimi'),
        button('Denetçi çıktısını aç', 'outline-button', () => openTerminalModal('Orkestratör denetimi', '', null, {runId: run.id, stepId: 'phase_audit'})));
      container.append(audit);
    }
    steps.forEach(step => {
      const result = run.results?.[step.id];
      const isRunning = activeSteps.has(step.id);
      const isDone = ['completed', 'success', 'failed', 'waiting_quota'].includes(result?.status);

      const card = node('div', 'worker-card');
      const header = node('div', 'worker-card-header');
      const title = node('div', 'worker-card-title');
      if (isRunning && result?.status !== 'waiting_quota') title.append(node('span', 'worker-pulse'));
      title.append(node('span', '', step.title || step.id));
      header.append(title, badge(result?.status === 'waiting_quota' ? 'waiting_quota' : (isRunning ? 'running' : (result?.status || 'pending'))));
      card.append(header);

      const meta = node('div', 'worker-meta');
      meta.append(node('span', 'meta-badge badge-persona', `👤 ${step.persona || 'developer'}`));
      meta.append(node('span', 'meta-badge badge-model', `🤖 ${result?.model_id || step.model_id || 'auto'}`));
      if (result?.cao_terminal_id) {
        meta.append(node('span', 'worker-id-pill', `Term: ${result.cao_terminal_id.slice(0, 8)}`));
      }
      card.append(meta);

      const actions = node('div', 'worker-actions');
      if (isRunning || isDone) {
        actions.append(button('Terminal Çıktısını Aç', 'outline-button', () => {
          openStepOutput(run, step, result);
        }));
      }
      if (isRunning && !archivedPlanSelected()) {
        const cliOutput = usesCliOutput(run, step, result);
        const live = button(cliOutput ? 'Canlı çıktıyı aç' : result?.cao_terminal_id ? 'Canlı terminali aç' : 'Terminal hazırlanıyor…', 'outline-button',
          () => cliOutput ? openStepOutput(run, step, result) : openLiveTerminal(step.title || step.id, result?.cao_terminal_id));
        live.disabled = !cliOutput && !result?.cao_terminal_id;
        actions.append(live);
      }
      if (result?.status === 'waiting_quota') {
        const resetAt = result.resets_at || (run.quota_timer?.step_id === step.id ? run.quota_timer.resets_at : '');
        const clock = node('span', 'step-quota-countdown quota-countdown', 'Hesaplanıyor…');
        if (resetAt) clock.dataset.resetAt = resetAt;
        card.append(clock);
        actions.append(button('Model seç', 'outline-button', () => openModelSwitchModal(run.id, step)));
        actions.append(button('Kotayı yeniledim', 'outline-button', () => retryQuotaStep(run.id, step.id)));
      }
      if (isRunning && result?.status !== 'waiting_quota') {
        actions.append(button('🔄 Yeniden Başlat', 'outline-button danger-text', () => {
          if (confirm(`'${step.title || step.id}' adımını sonlandırıp yeniden başlatmak istiyor musunuz?`)) {
            retryStep(run.id, step.id);
          }
        }));
      }
      if (result?.status === 'failed') {
        actions.append(button('⚡ Otomatik Modeli Değiştir', 'primary-button retry-btn', () => autoSwitchStepModel(run.id, step.id)));
        actions.append(button('⚙️ Modeli Manuel Seç', 'outline-button', () => openModelSwitchModal(run.id, step)));
        actions.append(button('🔄 Tekrar Dene', 'outline-button', () => retryStep(run.id, step.id)));
      }
      if (actions.children.length) {
        card.append(actions);
      }
      container.append(card);
    });

  // 2. PERSONALAR TAB
  } else if (state.tab === 'personas') {
    container.append(node('h3', 'inspector-title', 'Bu Görevde Kullanılan Personalar'));
    container.append(node('p', 'page-description', 'Görevin mimarisi için atanan özel ajan profilleri, sistem talimatları ve araç bağlantıları.'));

    const steps = run.plan?.steps || [];
    const usedPersonas = new Map();
    steps.forEach(s => {
      const pName = s.persona || 'developer';
      if (!usedPersonas.has(pName)) {
        usedPersonas.set(pName, {
          name: pName,
          skills: s.skills || [],
          mcp_servers: s.mcp_servers || [],
          steps: [s.title]
        });
      } else {
        const existing = usedPersonas.get(pName);
        s.skills?.forEach(sk => !existing.skills.includes(sk) && existing.skills.push(sk));
        s.mcp_servers?.forEach(m => !existing.mcp_servers.includes(m) && existing.mcp_servers.push(m));
        existing.steps.push(s.title);
      }
    });

    if (!usedPersonas.size) {
      container.append(node('p', 'empty-note', 'Plana atanmış özel persona henüz bulunmuyor.'));
    }

    usedPersonas.forEach(p => {
      const card = node('div', 'persona-card active-in-plan');
      const top = node('div', 'persona-card-top');
      top.append(node('div', 'persona-avatar', '👤'));
      const col = node('div');
      col.append(node('h4', 'persona-name', p.name), node('small', 'persona-source', `${p.steps.length} adımda görevli`));
      top.append(col);
      card.append(top);

      const tags = node('div', 'persona-tags');
      p.skills.forEach(sk => tags.append(node('span', 'skill-pill', `🛠️ ${sk}`)));
      p.mcp_servers.forEach(m => tags.append(node('span', 'mcp-pill', `🔌 ${m}`)));
      card.append(tags);

      const promptPreview = node('div', 'persona-prompt-preview', `Sorumlu Adımlar:\n• ${p.steps.join('\n• ')}`);
      card.append(promptPreview);
      container.append(card);
    });

  // 3. FLOW / DAG TAB
  } else if (state.tab === 'flow') {
    const steps = run.plan?.steps || [];
    container.append(node('h3', 'inspector-title', steps.length ? `Plan · ${steps.length} adım` : 'Plan bekleniyor'));
  // OVERVIEW DASHBOARD BAR (Steps progress, Active Sub-agents, Personas, Tokens)
  if (steps.length) {
    const completedCount = Object.values(run.results || {}).filter(r => ['completed', 'success'].includes(r.status)).length;
    const progressPercent = Math.round((completedCount / steps.length) * 100);
    const activeWorkers = run.current_steps || [];

    const overviewBar = node('div', 'run-overview-bar');
    const statsRow = node('div', 'overview-stats-row');

    // Steps Stat
    const stepsChip = node('div', 'stat-chip');
    stepsChip.innerHTML = `<span>📋</span> Adımlar: <strong>${completedCount} / ${steps.length} (%${progressPercent})</strong>`;
    statsRow.append(stepsChip);

    // Active Sub-agents / Quota Timer
    if (['waiting_quota', 'waiting_retry'].includes(run.status)) {
      const timerChip = node('div', 'stat-chip quota-timer-chip');
      timerChip.append(node('span', '', '⏳'), node('strong', '', run.phase_status === 'retrying' ? 'Geçici hata sonrası devam:' : 'Kota yenilenince devam:'), node('span', 'countdown-display quota-countdown', 'Hesaplanıyor…'));
      const timer = run.retry_timer || run.quota_timer;
      if (timer?.resets_at) timerChip.querySelector('.quota-countdown').dataset.resetAt = timer.resets_at;
      statsRow.append(timerChip);
    } else if (activeWorkers.length) {
      const activeChip = node('div', 'stat-chip');
      activeChip.innerHTML = `<span class="worker-pulse"></span> <strong>${activeWorkers.length} Alt Ajan Çalışıyor</strong> (${activeWorkers.join(', ')})`;
      statsRow.append(activeChip);
    }

    // Token Usage Stat
    const tokenChip = node('div', 'stat-chip');
    tokenChip.id = 'run-token-chip';
    tokenChip.innerHTML = `<span>⚡</span> Ölçülen token: <strong>${formatTokenCount(state.runTokenBreakdowns?.[run.id]?.provider_reported_tokens)}</strong>`;
    tokenChip.title = tokenAccountingNote(run.id);
    statsRow.append(tokenChip);


    overviewBar.append(statsRow);

    // Progress Bar
    const progressWrap = node('div', 'progress-bar-wrap');
    const progressFill = node('div', 'progress-bar-fill');
    progressFill.style.width = `${progressPercent}%`;
    progressWrap.append(progressFill);
    overviewBar.append(progressWrap);

    container.append(overviewBar);
  }


    if (!steps.length) {
      container.append(node('p', 'empty-note', activeStatuses.has(run.status) ? 'Plan hazırlanıyor…' : 'Kaydedilmiş adım bulunmuyor.'));
    }
    for (const step of steps) {
      const item = button('', 'flow-step', () => {
        state.step = state.step === step.id ? null : step.id;
        renderInspector();
      });
      item.classList.toggle('selected', state.step === step.id);
      item.append(badge(stepStatus(step)), node('strong', '', step.title || step.id));
      const sub = node('div', 'flow-step-sub');
      sub.append(node('small', '', `🤖 ${run.results?.[step.id]?.model_id || step.model_id || 'Otomatik model'}`));
      if (step.persona) sub.append(node('small', 'badge-persona', ` · 👤 ${step.persona}`));
      item.append(sub);
      if (step.dependencies?.length) item.append(node('small', '', `↳ Bağımlılıklar: ${step.dependencies.join(', ')}`));
      container.append(item);
      if (state.step === step.id) {
        container.append(node('p', 'detail-prompt', step.prompt || step.title || step.id));
      }
    }

  // 4. ÇIKTI & TOKEN TAB
  } else if (state.tab === 'output') {
    container.append(node('h3', 'inspector-title', 'Çıktı & Token Kullanımı'));
    const results = Object.entries(run.results || {}).filter(([id]) => !state.step || id === state.step);
    if (state.step) container.append(button('← Tüm çıktılar', 'outline-button', () => { state.step = null; renderInspector(); }));
    if (!results.length) container.append(node('p', 'empty-note', 'Adımlar tamamlandıkça çıktıları burada görünür.'));
    for (const [id, result] of results) {
      container.append(node('h3', 'inspector-title', run.plan?.steps?.find(s => s.id === id)?.title || id));
      if (result.persona) container.append(node('p', 'empty-note', `Atanan Persona: ${result.persona}`));
      if (result.token_usage?.source === 'provider_reported') container.append(node('p', 'empty-note', `Ölçülen adım tokenı: ${result.token_usage.step_tokens}`));
      if (result.cao_terminal_id) container.append(node('p', 'empty-note', `CAO ConPTY Terminali: ${result.cao_terminal_id}`));
      const output = node('div', 'markdown-body inspector-markdown');
      output.innerHTML = renderMarkdownHtml(result.output || result.error || 'Henüz çıktı yok.');
      container.append(output);
      for (const worker of result.worker_results || []) {
        container.append(node('h4', 'inspector-title', `${worker.role} · ${worker.model_id}`), node('pre', 'inspector-output', worker.output || ''));
      }
      if (result.awaiting_input) {
        const questionBox = node('div', 'question-box');
        questionBox.append(node('b', '', 'Ajan Kullanıcıdan Yanıt Bekliyor:'));
        const respInput = node('input', 'text-input');
        respInput.placeholder = 'Yanıtınızı buraya yazın...';
        questionBox.append(respInput, button('Cevapla & Devam Et', 'primary-button', async () => {
          const val = respInput.value.trim();
          if (!val) return;
          await api(`/api/runs/${encodeURIComponent(run.id)}/steps/${encodeURIComponent(id)}/respond`, {
            method: 'POST',
            body: JSON.stringify({ response: val })
          });
          toast('Yanıt iletildi.');
        }));
        container.append(questionBox);
      }
    }

  // 5. ORKESTRATÖR SOHBET TAB
  } else if (state.tab === 'chat') {
    container.append(node('h3', 'inspector-title', 'Canlı Orkestratör Sohbeti'));
    container.append(node('p', 'page-description', 'CAO ve LangGraph orkestratörüyle canlı konuşun. Plan üzerinde revizyonlar isteyebilir, yeni alt ajanlar tanımlatabilirsiniz.'));
    const chatBox = node('div', 'orchestrator-chat-box');
    const chatLog = node('div', 'chat-log');

    // Show past messages if any
    (run.orchestrator_messages || []).forEach(msg => {
      chatLog.append(node('div', 'chat-msg-user', `Siz: ${msg}`));
    });

    const inputRow = node('form', 'chat-input-row');
    const chatInput = node('input', 'chat-input');
    chatInput.placeholder = activeStatuses.has(run.status)
      ? 'Çalışan işe ek talimat yazın...'
      : 'Orkestratöre talimat veya soru yazın...';
    chatInput.required = true;
    const inspectorDelivery = node('select', 'chat-delivery-select');
    if (activeStatuses.has(run.status)) {
      for (const [value, label] of Object.entries(instructionDeliveryLabels)) {
        const option = node('option', '', label); option.value = value;
        inspectorDelivery.append(option);
      }
      inspectorDelivery.value = $('#instruction-delivery').value;
      inspectorDelivery.setAttribute('aria-label', 'Ek talimatın iletilme zamanı');
      inputRow.append(inspectorDelivery);
    }
    inputRow.append(chatInput, button('Gönder', 'primary-button'));
    inputRow.addEventListener('submit', async e => {
      e.preventDefault();
      const msg = chatInput.value.trim();
      if (!msg) return;
      if (activeStatuses.has(run.status)) {
        try {
          await submitActiveInstruction(run, msg, inspectorDelivery.value);
          chatInput.value = '';
        } catch (err) { toast(errorText(err)); }
        return;
      }
      chatInput.value = '';
      chatLog.append(node('div', 'chat-msg-user', `Siz: ${msg}`));
      const pending = node('div', 'chat-msg-ai', 'Ajan çalışıyor… 0 sn');
      chatLog.append(pending);
      chatLog.scrollTop = chatLog.scrollHeight;
      const started = Date.now();
      const pendingTimer = setInterval(() => {
        pending.textContent = `Ajan çalışıyor… ${Math.floor((Date.now() - started) / 1000)} sn`;
      }, 1000);
      try {
        const requestedModel = $('#chat-model').value;
        const res = await api(`/api/runs/${encodeURIComponent(run.id)}/chat`, {
          method: 'POST',
          body: JSON.stringify({ message: msg, model_id: requestedModel,
            allow_writes: $('#allow-writes').checked }),
          timeoutMs: 630000
        });
        if (requestedModel && res.model_id !== requestedModel) {
          toast(`Seçilen ${requestedModel} yerine ${res.model_id || 'bilinmeyen model'} yanıtladı. Piko sunucusunu yeniden başlatın.`);
        }
        pending.textContent = `Orkestratör: ${res.reply || 'Anlaşıldı.'}`;
        chatLog.scrollTop = chatLog.scrollHeight;
      } catch (err) {
        pending.textContent = `Yanıt alınamadı: ${errorText(err)}`;
        toast(errorText(err));
      } finally {
        clearInterval(pendingTimer);
      }
    });
    chatBox.append(chatLog, inputRow);
    container.append(chatBox);

  // 6. ETKİNLİKLER TAB
  } else if (state.tab === 'events') {
    container.append(node('h3', 'inspector-title', 'Son Etkinlikler'));
    if (!state.events.length) container.append(node('p', 'empty-note', 'Henüz kayıtlı etkinlik yok.'));
    for (const event of state.events.slice(-100).reverse()) {
      const item = node('div', 'event');
      const time = node('time', '', date(event.created_at, true));
      if (event.created_at) time.dateTime = event.created_at;
      item.append(time, node('div', '', event.message || event.kind));
      container.append(item);
    }

  // 7. İŞ KAYDI TAB
  } else if (state.tab === 'work') {
    const job = workState.jobs[run.id];
    container.append(node('h3', 'inspector-title', 'İş kaydı ve denetim'));
    if (!job?.work) {
      container.append(node('p', 'empty-note', 'Bu iş için kalıcı kayıt henüz yüklenmedi.'));
      loadWorkForRun(run);
    } else {
      const work = job.work;
      const card = node('div', 'work-status-card');
      card.append(node('b', '', 'Hedef'), node('div', '', work.goal || ''));
      card.append(node('b', '', 'Durum'), node('div', '', `${work.status} · öncelik: ${work.priority}`));
      if (work.due_at) card.append(node('b', '', 'Son tarih'), node('div', '', new Date(work.due_at).toLocaleString('tr-TR')));
      if (work.dependencies?.length) card.append(node('b', '', 'Bağımlılıklar'), node('div', '', work.dependencies.join(', ')));
      card.append(node('div', 'work-next', `Sıradaki adım: ${work.next_action || '—'}`));
      container.append(card);

      container.append(node('h3', 'inspector-title', 'Teslim sürümleri'));
      if (!job.deliverables?.length) container.append(node('p', 'empty-note', 'Henüz teslim kaydı yok.'));
      for (const deliverable of job.deliverables || []) container.append(deliveryCard(deliverable));

      const audit = node('div', 'worker-card');
      audit.append(node('div', 'worker-card-title', 'Ajanlar ve modeller'));
      for (const step of run.plan?.steps || []) {
        const result = run.results?.[step.id] || {};
        audit.append(node('small', '', `${step.title}: ${result.status || 'bekliyor'} · ${result.model_id || step.model_id || 'auto'}${result.independent_review ? ' · denetim: ' + (result.independent_review.status || result.independent_review.verdict || 'var') : ''}`));
      }
      container.append(audit);
      container.append(runtimeMetricsCard());
    }
    if (!state.runtimeMetrics) fetchRuntimeMetrics();
  }

  // 8. KÖPRÜ PAKETLERİ TAB
  else if (state.tab === 'bridges') {
    renderBridgeInspector(container);
  }

  if (archivedPlanSelected()) {
    container.querySelectorAll('.primary-button, .retry-btn, .danger-text, form button, input, select, textarea').forEach(control => control.disabled = true);
  }
  container.scrollTop = previousScroll;
}

let terminalOutputTarget = null;
let terminalOutputTimer = null;

function stopTerminalOutput() {
  clearTimeout(terminalOutputTimer);
  terminalOutputTimer = null;
  terminalOutputTarget = null;
  state.activeTerminalId = null;
}

function usesCliOutput(run, step = {}, result = {}) {
  if (result?.cao_terminal_id) return false;
  const id = result?.model_id || step.model_id || run.planner_model_actual || run.planner_model || '';
  const model = (state.bootstrap?.models || []).find(row => row.id === id);
  return run.execution_backend !== 'cao' || ['evren', 'opencode', 'claude-code'].includes(model?.provider)
    || ['opencode', 'claude-code'].includes(model?.runtime) || /^(opencode:|evren\/|claude-code:)/.test(id);
}

function openStepOutput(run, step, result = {}) {
  const target = usesCliOutput(run, step, result) ? {runId: run.id, stepId: step.id} : null;
  openTerminalModal(step.title || step.id, result?.output || '', result?.cao_terminal_id, target);
}

function openTerminalModal(title, output, terminalId = null, cliTarget = null) {
  stopTerminalOutput();
  state.activeTerminalId = terminalId;
  terminalOutputTarget = cliTarget ? {...cliTarget, initialOutput: output || '', busy: false} : null;
  $('#terminal-modal-title').textContent = `${title} · ${cliTarget ? 'Canlı CLI çıktısı' : 'ConPTY Terminal Çıktısı'}`;
  $('#terminal-modal-subtitle').textContent = cliTarget ? 'Salt okunur stdout · süreç durumu yükleniyor…' : terminalId ? `Terminal ID: ${terminalId}` : 'Kaydedilmiş çıktı';
  $('#terminal-modal-output').textContent = output || (cliTarget ? 'Süreç çıktısı sorgulanıyor…' : 'Terminal çıktısı bulunamadı.');
  if (!$('#terminal-modal').open) $('#terminal-modal').showModal();
  if (terminalId || cliTarget) refreshTerminalOutput();
}

let liveTerminalConnection = null;
let liveTerminalTarget = null;

function disconnectLiveTerminal() {
  const connection = liveTerminalConnection;
  liveTerminalConnection = null;
  if (!connection) return;
  connection.observer?.disconnect();
  connection.input?.dispose();
  connection.resize?.dispose();
  connection.socket.onopen = connection.socket.onmessage = connection.socket.onclose = connection.socket.onerror = null;
  connection.socket.close(1000, 'Viewer closed');
  connection.terminal.dispose();
}

function openLiveTerminal(title, terminalId) {
  if (!terminalId) { toast('Terminal henüz hazırlanıyor.'); return; }
  disconnectLiveTerminal();
  liveTerminalTarget = {title, terminalId};
  const dialog = $('#live-terminal-modal');
  const status = $('#live-terminal-status');
  const host = $('#live-terminal-host');
  $('#live-terminal-title').textContent = `${title} · Canlı terminal`;
  status.textContent = 'Çalışan terminale bağlanıyor…';
  host.replaceChildren();
  if (!dialog.open) dialog.showModal();
  if (!window.Terminal || !window.FitAddon?.FitAddon) {
    status.textContent = 'Terminal bileşeni yüklenemedi. Sayfayı yenileyin.';
    return;
  }
  const terminal = new window.Terminal({cursorBlink: true, disableStdin: true, fontSize: 13,
    fontFamily: 'Consolas, monospace', scrollback: 10000,
    theme: {background: '#101725', foreground: '#dce6f4', cursor: '#8cbfa4'}});
  const fit = new window.FitAddon.FitAddon();
  terminal.loadAddon(fit);
  terminal.open(host);
  fit.fit();
  const url = new URL(`/api/terminals/${encodeURIComponent(terminalId)}/live`, location.href);
  url.protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
  const remoteToken = new URL(location.href).searchParams.get('token');
  if (remoteToken) url.searchParams.set('token', remoteToken);
  const socket = new WebSocket(url);
  socket.binaryType = 'arraybuffer';
  const connection = {terminal, socket, observer: null, input: null, resize: null};
  liveTerminalConnection = connection;
  const sendSize = () => {
    if (socket.readyState === WebSocket.OPEN) socket.send(JSON.stringify({type: 'resize', rows: terminal.rows, cols: terminal.cols}));
  };
  connection.input = terminal.onData(data => {
    if (socket.readyState === WebSocket.OPEN) socket.send(JSON.stringify({type: 'input', data}));
  });
  connection.resize = terminal.onResize(sendSize);
  connection.observer = new ResizeObserver(() => { if (liveTerminalConnection === connection) fit.fit(); });
  connection.observer.observe(host);
  socket.onopen = () => {
    status.textContent = `Bağlı · ${terminalId} · Doğrudan çalışan oturum`;
    terminal.options.disableStdin = false;
    fit.fit(); sendSize(); terminal.focus();
  };
  socket.onmessage = event => terminal.write(event.data instanceof ArrayBuffer ? new Uint8Array(event.data) : event.data);
  socket.onerror = () => { status.textContent = 'Terminal bağlantısı kurulamadı. Sunucu sürümünü ve oturum durumunu kontrol edin.'; };
  socket.onclose = event => {
    terminal.options.disableStdin = true;
    status.textContent = event.code === 1000 ? 'Terminal bağlantısı kapandı.' : `Terminale bağlanılamadı veya oturum sona erdi${event.reason ? ': ' + event.reason : '.'}`;
  };
}

async function refreshTerminalOutput() {
  const target = terminalOutputTarget;
  const terminalId = state.activeTerminalId;
  const dialog = $('#terminal-modal');
  if (!dialog.open || (!target && !terminalId) || target?.busy) return;
  clearTimeout(terminalOutputTimer);
  if (target) target.busy = true;
  let keepPolling = !!target;
  try {
    const path = target
      ? `/api/runs/${encodeURIComponent(target.runId)}/steps/${encodeURIComponent(target.stepId)}/live-output`
      : `/api/terminals/${encodeURIComponent(terminalId)}/output`;
    let res;
    res = await api(path, {timeoutMs: 8000});
    if (target && res.state === 'waiting' && /canli cikti kaydi etkinlesmeden/.test(res.output || '')) {
      res.output = 'Bu adımın canlı çıktı akışı henüz mevcut değil; çalışma sırasını bekliyor olabilir. Çalışan diğer ajanın çıktısını açabilirsiniz.';
      res.pid = null; res.elapsed_seconds = null; res.idle_seconds = null; res.received_bytes = null;
    }
    if (!dialog.open || target !== terminalOutputTarget || terminalId !== state.activeTerminalId) return;
    $('#terminal-modal-output').textContent = res.output || target?.initialOutput || (target ? 'Henüz stdout alınmadı. Süreç durumu takip ediliyor.' : 'Terminal çıktısı boş.');
    if (target) {
      const labels = {running: 'Çalışıyor', completed: 'Tamamlandı', timeout: 'Süre sınırı', cancelled: 'İptal edildi', failed: 'Hata', waiting: 'Çıktı kaydı bekleniyor'};
      const details = ['Salt okunur stdout', labels[res.state] || res.state || 'Durum bilinmiyor'];
      if (res.pid) details.push(`PID ${res.pid}`);
      for (const [field, label] of [['elapsed_seconds', 'Süre'], ['idle_seconds', 'Son çıktıdan beri']]) {
        if (Number.isFinite(res[field])) details.push(`${label}: ${Math.floor(res[field])} sn`);
      }
      if (Number.isFinite(res.received_bytes)) details.push(`${res.received_bytes} bayt`);
      if (res.source_step_id) details.push(`Kaynak: ${res.source_step_id}`);
      $('#terminal-modal-subtitle').textContent = details.join(' · ');
      keepPolling = activeStatuses.has(res.run_state) || !['completed', 'timeout', 'cancelled', 'failed'].includes(res.state);
    }
  } catch (error) {
    if (!dialog.open || target !== terminalOutputTarget || terminalId !== state.activeTerminalId) return;
    if (target) $('#terminal-modal-subtitle').textContent = `Salt okunur stdout · ${errorText(error)} · Yeniden denenecek`;
    else toast(errorText(error));
  } finally {
    if (target) target.busy = false;
    if (target && keepPolling && target === terminalOutputTarget && dialog.open) {
      terminalOutputTimer = setTimeout(refreshTerminalOutput, 2000);
    }
  }
}

function renderPersonasPage() {
  const container = $('#data-page');
  container.replaceChildren(
    node('h2', '', 'Personalar ve Beceriler'),
    node('p', 'page-description', 'CAO ve LangGraph orkestratörünün görevlere atayabildiği uzman ajan personaları, proje becerileri (skills) ve MCP araçları.')
  );

  // Section 1: Personas
  container.append(node('h3', 'inspector-title', 'Kayıtlı Ajan Personaları'));
  const personasGrid = node('div', 'persona-grid');
  const personas = state.personasData?.personas || [];
  if (!personas.length) {
    personasGrid.append(node('p', 'empty-note', 'Persona listesi yükleniyor…'));
  }
  personas.forEach(p => {
    const card = node('div', 'persona-card');
    const top = node('div', 'persona-card-top');
    top.append(node('div', 'persona-avatar', '👤'));
    const col = node('div');
    col.append(node('h4', 'persona-name', p.name), node('small', 'persona-source', `Kaynak: ${p.source || 'agent-store'}`));
    top.append(col);
    card.append(top);

    if (p.skills?.length || p.mcp_servers?.length) {
      const tags = node('div', 'persona-tags');
      (p.skills || []).forEach(sk => tags.append(node('span', 'skill-pill', `🛠️ ${sk}`)));
      (p.mcp_servers || []).forEach(m => tags.append(node('span', 'mcp-pill', `🔌 ${m}`)));
      card.append(tags);
    }
    if (p.instructions) {
      card.append(node('div', 'persona-prompt-preview', p.instructions));
    }
    personasGrid.append(card);
  });
  container.append(personasGrid);

  // Section 2: Skills
  container.append(node('h3', 'inspector-title', 'Kullanılabilir Beceriler (Project Skills)'));
  const skillsList = node('div', 'provider-list');
  const skills = state.personasData?.skills || [];
  skills.slice(0, 30).forEach(sk => {
    const item = node('section', 'provider');
    item.append(node('h3', '', sk.name), node('small', '', sk.description || 'Proje becerisi'));
    skillsList.append(item);
  });
  container.append(skillsList);

  // Section 3: MCP Servers
  container.append(node('h3', 'inspector-title', 'Bağlı MCP Araçları'));
  const mcpsList = node('div', 'provider-list');
  const mcps = state.personasData?.mcps || [];
  mcps.forEach(m => {
    const item = node('section', 'provider');
    item.append(node('h3', '', m.id), badge('completed'), node('small', '', m.capabilities || 'MCP Sunucusu'));
    mcpsList.append(item);
  });
  container.append(mcpsList);
}

function renderModelsLegacy() {
  const container = $('#data-page');
  container.replaceChildren(
    node('h2', '', 'Modeller ve CLI'),
    node('p', 'page-description', 'Yerel kurulumdan alınan model kataloğu ve CLI bulunabilirlik durumu.')
  );
  const providers = node('div', 'provider-list');
  for (const provider of state.bootstrap?.providers || []) {
    const item = node('section', 'provider');
    item.append(node('h3', '', provider.id), badge(provider.available ? 'completed' : 'failed'), node('small', '', provider.available ? 'CLI bulundu' : 'CLI bulunamadı'));
    if (provider.executable) item.append(node('small', '', Array.isArray(provider.executable) ? provider.executable.join(' ') : provider.executable));
    providers.append(item);
  }
  const cao = state.bootstrap?.cao || {};
  const caoItem = node('section', 'provider');
  caoItem.append(node('h3', '', 'Gömülü CAO · Windows ConPTY'), badge(cao.available ? 'completed' : 'failed'), node('small', '', cao.detail || 'CAO durumu bilinmiyor'));
  providers.append(caoItem);
  container.append(providers);

  if (!state.bootstrap?.models?.length) container.append(node('p', 'empty-note', 'Model kataloğu yüklenemedi.'));
  for (const model of state.bootstrap?.models || []) {
    const row = node('div', 'model-row');
    const content = node('div');
    content.append(node('b', '', model.id), node('small', '', `${model.provider}${model.capabilities?.length ? ' · ' + model.capabilities.join(', ') : ''}`));
    row.append(content, node('span', 'status-badge', model.default_effort || 'Varsayılan'));
    container.append(row);
  }
}

function isModelCalibrated(model) {
  return !!(model?.role_weights && (model.provenance?.calibrated || model.provenance?.provenance === 'auto_calibrated'));
}

function modelTripleLabel(model) {
  return `${model.model || model.base_model || model.id} (${model.runtime || 'direct'})`;
}

function findModelByTriple(triple) {
  return (state.bootstrap?.models || []).find(model => (model.triple_key || `${model.model || model.id}::${model.runtime || 'direct'}::${model.account || 'default'}`) === triple);
}

function modelQuotaBar(model) {
  const wrap = node('div', 'model-quota');
  wrap.style.cssText = 'min-width:120px;display:flex;flex-direction:column;gap:3px';
  const cooldown = (state.bootstrap?.model_cooldowns || {})[model.id];
  const track = node('div', 'quota-track');
  track.style.cssText = 'height:6px;border-radius:99px;background:#e6ecf5;overflow:hidden';
  const fill = node('span', 'quota-fill');
  const pct = cooldown ? 18 : 100;
  fill.style.cssText = `display:block;height:100%;width:${pct}%;background:${cooldown ? '#d96b5f' : '#5aa37a'}`;
  track.append(fill);
  const label = cooldown
    ? `Kota: ${cooldown.error_kind || 'bekleme'}`
    : (model.availability?.quota_status === 'unknown' ? 'Kota: bilinmiyor' : `Kota: ${model.availability?.quota_status || 'bilinmiyor'}`);
  wrap.append(track, node('small', '', label));
  return wrap;
}

async function toggleModelHidden(model) {
  try {
    const res = await api('/api/models/hide', {
      method: 'POST', body: JSON.stringify({ id: model.id, hidden: !model.hidden })
    });
    state.bootstrap.models = res.models;
    populateModels();
    renderModels();
  } catch (error) {
    toast(errorText(error));
  }
}

async function calibrateSelection(triples) {
  if (!triples.length) return toast('Kalibre edilecek üçlü seçilmedi.');
  if (state.calibrationAbort) return toast('Önce çalışan kalibrasyonu iptal edin.');
  const controller = new AbortController();
  state.calibrationAbort = controller;
  state.calibrationProgress = {};
  try {
    const response = await fetch('/api/models/calibrate', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ triples }),
      signal: controller.signal
    });
    if (!response.ok || !response.body) throw new Error(`Kalibrasyon başlatılamadı (${response.status}).`);
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      let index;
      while ((index = buffer.indexOf('\n')) >= 0) {
        const line = buffer.slice(0, index).trim();
        buffer = buffer.slice(index + 1);
        if (!line) continue;
        let event;
        try { event = JSON.parse(line); } catch { continue; }
        if (event.type === 'start') {
          state.calibrationToken = event.token;
        } else if (event.type === 'triple_start') {
          state.calibrationProgress[event.id] = 'Kalibrasyon başladı';
        } else if (event.type === 'task_done') {
          const model = findModelByTriple(event.triple);
          if (model) state.calibrationProgress[model.id] = event.passed ? 'Görev geçti' : 'Görev kaldı';
        } else if (event.type === 'triple_done') {
          state.calibrationProgress[event.id] = 'Kalibre edildi';
        } else if (event.type === 'triple_cancelled') {
          state.calibrationProgress[event.id] = 'İptal edildi';
        } else if (event.type === 'done') {
          if (Array.isArray(event.models)) state.bootstrap.models = event.models;
          const report = event.report || {};
          toast(`Kalibrasyon: ${report.calibrated?.length || 0} tamam, ${report.cancelled?.length || 0} iptal, ${report.skipped?.length || 0} atlandı.`);
        } else if (event.type === 'error') {
          toast(event.error);
        }
        if (state.page === 'models') renderModels();
      }
    }
    if (state.page === 'models') renderModels();
    populateModels();
  } catch (error) {
    if (error.name !== 'AbortError') toast(errorText(error));
  } finally {
    state.calibrationAbort = null;
    state.calibrationToken = null;
    if (state.page === 'models') renderModels();
  }
}

function renderModels() {
  const container = $('#data-page');
  container.replaceChildren(
    node('h2', '', 'Modeller ve CLI'),
    node('p', 'page-description', 'Her satır bir (model, runtime, hesap) üçlüsüdür; kotalar ayrıdır.')
  );
  const providers = node('div', 'provider-list');
  for (const provider of state.bootstrap?.providers || []) {
    const item = node('section', 'provider');
    item.append(node('h3', '', provider.id), badge(provider.available ? 'completed' : 'failed'), node('small', '', provider.available ? 'CLI bulundu' : 'CLI bulunamadı'));
    if (provider.executable) item.append(node('small', '', Array.isArray(provider.executable) ? provider.executable.join(' ') : provider.executable));
    providers.append(item);
  }
  const cao = state.bootstrap?.cao || {};
  const caoItem = node('section', 'provider');
  caoItem.append(node('h3', '', 'Gömülü CAO / Windows ConPTY'), badge(cao.available ? 'completed' : 'failed'), node('small', '', cao.detail || 'CAO durumu bilinmiyor'));
  providers.append(caoItem);
  container.append(providers);

  const all = state.bootstrap?.models || [];
  const visible = all.filter(model => state.hiddenFilter ? true : !model.hidden);
  const toolbar = node('div', 'model-toolbar');
  toolbar.style.cssText = 'display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin:12px 0';
  toolbar.append(
    button(state.hiddenFilter ? 'Gizlileri gizle' : 'Gizli', 'outline-button', () => {
      state.hiddenFilter = !state.hiddenFilter;
      renderModels();
    }),
    button('Kalibre edilmemişleri çalıştır', 'primary-button', () => {
      const triples = visible.filter(model => !model.hidden && !isModelCalibrated(model)).map(model => ({ id: model.id }));
      calibrateSelection(triples);
    })
  );
  if (state.calibrationAbort) {
    toolbar.append(button('Kalibrasyonu iptal et', 'outline-button', async () => {
      const token = state.calibrationToken;
      if (token) api('/api/models/calibrate/cancel', { method: 'POST', body: JSON.stringify({ token }) }).catch(() => {});
      state.calibrationAbort?.abort();
    }));
  }
  container.append(toolbar);
  container.append(node('small', '', `${visible.length} / ${all.length} üçlü gösteriliyor · ${visible.filter(isModelCalibrated).length} kalibre`));

  if (!visible.length) container.append(node('p', 'empty-note', 'Model kataloğu yüklenemedi.'));
  const calibrated = visible.filter(isModelCalibrated);
  const devTop = calibrated.reduce((best, model) => ((model.role_weights?.development || 0) > (best?.role_weights?.development || 0) ? model : best), null);
  const archTop = calibrated.reduce((best, model) => ((model.role_weights?.architect || 0) > (best?.role_weights?.architect || 0) ? model : best), null);
  const groups = new Map();
  for (const model of visible) {
    const key = model.base_model || model.model || model.id;
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(model);
  }
  for (const [base, models] of groups) {
    const section = node('section', 'provider model-group');
    section.append(node('h3', '', base));
    for (const model of models) {
      const row = node('div', 'model-row');
      if (model.hidden) row.style.opacity = '0.55';
      const content = node('div');
      content.style.cssText = 'display:flex;flex-direction:column;gap:2px;min-width:220px';
      const title = node('b', '', model.model || model.id);
      const runtime = node('span', 'status-badge', model.runtime || 'direct');
      runtime.style.marginLeft = '6px';
      title.append(runtime);
      content.append(title, node('small', '', `${model.provider}${model.capabilities?.length ? ' · ' + model.capabilities.join(', ') : ''}`));
      if (isModelCalibrated(model)) {
        const roles = node('small', '', Object.entries(model.role_weights || {})
          .filter(([role]) => ['development', 'architect', 'reviewer'].includes(role))
          .map(([role, weight]) => `${role} ${Number(weight).toFixed(2)}`).join(' · '));
        content.append(roles);
        if (devTop && model.id === devTop.id) content.append(node('small', 'status-badge', 'birincil kodcu'));
        if (archTop && model.id === archTop.id) content.append(node('small', 'status-badge', 'birincil plancı'));
      } else {
        content.append(node('small', '', 'kalibre edilmedi'));
      }
      const actions = node('div');
      actions.style.cssText = 'display:flex;gap:6px;align-items:center;flex-wrap:wrap';
      actions.append(modelQuotaBar(model));
      const progress = state.calibrationProgress?.[model.id];
      if (progress) actions.append(node('small', '', progress));
      actions.append(
        button(isModelCalibrated(model) ? 'Yeniden kalibre et' : 'Kalibre et', 'outline-button', () => calibrateSelection([{ id: model.id }])),
        button(model.hidden ? 'Göster' : 'Gizle', 'outline-button', () => toggleModelHidden(model))
      );
      row.append(content, actions);
      section.append(row);
    }
    container.append(section);
  }
}

function renderHistory() {
  if (state.page !== 'history') return;
  const container = $('#data-page');
  container.replaceChildren(
    node('h2', '', 'Görev Geçmişi'),
    node('p', 'page-description', 'Kaydedilen planları, adım çıktılarını ve kesilen görevleri yeniden açın.'),
    button('Listeyi Yenile', 'outline-button', () => refreshRuns().then(renderHistory).catch(e => toast(errorText(e))))
  );
  if (!state.runs.length) container.append(node('p', 'empty-note', 'Henüz kaydedilmiş görev yok. İlk görevinizi sohbet alanından başlatın.'));
  for (const run of groupedRuns(state.runs)) {
    const row = button('', 'history-row', () => selectRun(run.id));
    const content = node('div');
    content.append(node('b', '', (run.prompt || 'İsimsiz görev').slice(0, 160)), node('small', '', date(run.created_at)));
    row.append(content, badge(run.status));
    const wrapper = node('div', 'history-run-row');
    wrapper.append(row, deleteConversationButton(run));
    container.append(wrapper);
  }
}

async function renderRemotePage() {
  if (state.page !== 'remote') return;
  const container = $('#data-page');
  container.replaceChildren(
    node('h2', '', 'Mobil ve Uzaktan Erişim (Tailscale & Android APK)'),
    node('p', 'page-description', 'Piko’yu telefonunuzdan veya dış ağdan (4G/5G) kontrol edin. Tailscale ile güvenli WireGuard tüneli üzerinden çalışır.')
  );

  let remoteData = { token: 'Yükleniyor…', interfaces: [], tailscale_ips: [], port: 8765 };
  try {
    remoteData = await api('/api/remote-info');
  } catch (e) {
    console.warn('remote-info error', e);
  }

  const apkCard = node('div', 'settings-group');
  apkCard.append(
    node('h3', '', '📱 Piko Mobil (Android APK)'),
    node('p', 'setting-help', 'Codex Remote benzeri, telefonunuzdan canlı terminal takibi, plan onaylama ve yeni görev başlatma sağlayan özel mobil uygulama.')
  );
  const dlRow = node('div', 'button-row');
  const dlBtn = node('a', 'primary-button', '📥 AgentRemote.apk İndir (~25 KB)');
  dlBtn.href = '/ui/AgentRemote.apk';
  dlBtn.setAttribute('download', 'AgentRemote.apk');
  dlBtn.style.textDecoration = 'none';
  dlBtn.style.display = 'inline-flex';
  dlBtn.style.alignItems = 'center';
  dlBtn.style.gap = '8px';
  dlRow.append(dlBtn);
  apkCard.append(dlRow);

  const connCard = node('div', 'settings-group');
  connCard.append(
    node('h3', '', '🔑 Bağlantı Bilgileri & Mobil Token'),
    node('p', 'setting-help', 'Bu bilgileri Android uygulamasındaki Bağlantı sekmesine girin:')
  );

  const tokenRow = node('div', 'token-info-row');
  tokenRow.style.margin = '10px 0';
  tokenRow.style.padding = '12px';
  tokenRow.style.background = 'var(--bg-card, #121826)';
  tokenRow.style.borderRadius = '8px';
  tokenRow.style.border = '1px solid var(--border, #263045)';

  const tokenVal = remoteData.token || '—';
  tokenRow.innerHTML = `
    <div style="margin-bottom: 8px;">
      <b>Mobil Bearer Token:</b><br>
      <code style="font-family: monospace; font-size: 13px; color: #10B981; word-break: break-all;">${tokenVal}</code>
    </div>
    <div style="margin-bottom: 8px;">
      <b>Tailscale IP'leri:</b><br>
      ${remoteData.tailscale_ips && remoteData.tailscale_ips.length > 0 ? remoteData.tailscale_ips.map(ip => `<code style="font-family: monospace; color: #38BDF8;">http://${ip}:8765</code>`).join('<br>') : '<span style="color:#F59E0B;">Tailscale henüz bağlı değil (veya algılanamadı)</span>'}
    </div>
    <div>
      <b>Yerel Ağ (Wi-Fi) IP'si:</b><br>
      ${(remoteData.interfaces || []).map(i => `<code style="font-family: monospace; color: #94A3B8;">http://${i.ip}:8765 (${i.interface})</code>`).join('<br>')}
    </div>
  `;
  connCard.append(tokenRow);

  const guideCard = node('div', 'settings-group');
  guideCard.append(
    node('h3', '', '🚀 3 Adımda Dış Ağdan Bağlanma (Rehber)'),
    node('div', 'guide-content', '')
  );
  guideCard.querySelector('.guide-content').innerHTML = `
    <ol style="padding-left: 20px; line-height: 1.8; font-size: 13px;">
      <li><b>Bilgisayarda Uzaktan Modu Açın:</b> Proje klasöründeki <code>remote.cmd</code> dosyasını çalıştırın (sunucu 0.0.0.0 üzerinde dış ağa açılır).</li>
      <li><b>Tailscale ile Eşleyin:</b> Bilgisayarınızda ve telefonunuzda <b>Tailscale</b>'i açıp aynı hesapla giriş yapın. Her iki cihaz da güvenli bir VPN ağına dahil olur.</li>
      <li><b>APK ile Bağlanın:</b> Yukarıdan indirdiğiniz <b>Piko Mobil</b> uygulamasını telefonunuzda açın, bilgisayarınızın Tailscale IP'sini (100.x.y.z:8765) ve Mobil Token'ı girip <b>Bağlan</b>'a basın!</li>
    </ol>
  `;

  container.append(apkCard, connCard, guideCard);
}


async function renderMemoryPage() {
  const container = $('#data-page');
  container.replaceChildren(
    node('h2', '', 'Kalıcı Bellek (Compounding Long-Term Memory)'),
    node('p', 'page-description', 'OpenAI Dots ve Grok Bot benzeri kümülatif hafıza: Projenize ve kodlama tercihlerinize dair kurallar, mimari kararlar ve otonom çıkarılan dersler. Yeni görevler ve planlar bu kuralları otomatik dikkate alır.')
  );

  let data = { facts: [], stats: {}, workspace: '' };
  try {
    data = await api('/api/memory');
    state.memoryData = data;
    const countEl = $('#memory-count');
    if (countEl) countEl.textContent = (data.facts || []).length;
  } catch (e) {
    console.warn('Memory fetch error', e);
  }

  const actionsBar = node('div', 'memory-actions-bar');
  const searchBox = node('div', 'memory-search-box');
  searchBox.append(node('span', '', '🔍'));
  const searchInput = node('input');
  searchInput.placeholder = 'Kalıcı hafızada kural veya ders ara…';
  searchBox.append(searchInput);
  actionsBar.append(searchBox);

  const filterWrap = node('div', 'memory-filter-pills');
  let activeFilter = 'all';
  const filterBtns = [
    { id: 'all', label: 'Tümü' },
    { id: 'rule', label: '📋 Kurallar' },
    { id: 'preference', label: '⭐ Tercihler' },
    { id: 'architecture', label: '🏛️ Mimari' },
    { id: 'insight', label: '💡 Çıkarılan Dersler' }
  ].map(f => {
    const btn = node('button', `memory-pill-btn ${f.id === activeFilter ? 'active' : ''}`, f.label);
    btn.onclick = () => {
      activeFilter = f.id;
      filterWrap.querySelectorAll('.memory-pill-btn').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      renderGrid();
    };
    return btn;
  });
  filterWrap.append(...filterBtns);
  actionsBar.append(filterWrap);

  const addBtn = node('button', 'primary-button', '＋ Yeni Bilgi / Kural Ekle');
  actionsBar.append(addBtn);
  container.append(actionsBar);

  const formBox = node('div', 'settings-group');
  formBox.style.display = 'none';
  formBox.style.marginBottom = '20px';
  formBox.innerHTML = `
    <h3>Yeni Kalıcı Hafıza Kaydı</h3>
    <p class="setting-help">Ajanların ve planlayıcının daima hatırlamasını istediğiniz bir kural, tercih veya mimari karar ekleyin.</p>
    <div style="display:flex;gap:10px;margin:10px 0;flex-wrap:wrap">
      <select id="new-fact-category" style="padding:8px;border-radius:6px;border:1px solid #dce4f1;background:#fff;font-size:12px">
        <option value="rule">📋 Kural (Strict Constraint)</option>
        <option value="preference">⭐ Tercih (User Preference)</option>
        <option value="architecture">🏛️ Mimari (Architecture Decision)</option>
        <option value="insight">💡 Ders / Çözüm (Insight & Lesson)</option>
      </select>
      <input id="new-fact-tags" type="text" placeholder="Etiketler (virgülle ayırın, örn: ui, css, react)" style="flex:1;min-width:200px;padding:8px 12px;border:1px solid #dce4f1;border-radius:6px;font-size:12px">
    </div>
    <textarea id="new-fact-content" placeholder="Örnek: 'Tüm frontend bileşenlerinde CSS modülleri kullanılmalı, inline stil yazılmamalıdır.' veya 'Asla vendor/ altındaki dosyaları elle düzenleme.'" rows="3" style="width:100%;border:1px solid #dce4f1;border-radius:6px;padding:10px;font-size:12px;line-height:1.6;font-family:inherit;margin-bottom:10px"></textarea>
    <div style="display:flex;gap:8px">
      <button id="save-fact-btn" class="primary-button">Hafızaya Kaydet</button>
      <button id="cancel-fact-btn" class="outline-button">Vazgeç</button>
    </div>
  `;
  container.append(formBox);

  addBtn.onclick = () => {
    formBox.style.display = formBox.style.display === 'none' ? 'block' : 'none';
    if (formBox.style.display === 'block') {
      formBox.querySelector('#new-fact-content')?.focus();
    }
  };
  formBox.querySelector('#cancel-fact-btn').onclick = () => { formBox.style.display = 'none'; };
  formBox.querySelector('#save-fact-btn').onclick = async () => {
    const cat = formBox.querySelector('#new-fact-category').value;
    const content = formBox.querySelector('#new-fact-content').value.trim();
    const tagsStr = formBox.querySelector('#new-fact-tags').value.trim();
    if (!content) {
      toast('Lütfen kural veya bilgi içeriğini girin.');
      return;
    }
    const tags = tagsStr ? tagsStr.split(',').map(s => s.trim().toLowerCase()).filter(Boolean) : [];
    try {
      await api('/api/memory/facts', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ category: cat, content, tags })
      });
      toast('Kalıcı hafızaya kaydedildi! 🧠');
      formBox.style.display = 'none';
      formBox.querySelector('#new-fact-content').value = '';
      renderMemoryPage();
    } catch (e) {
      toast(errorText(e));
    }
  };

  const grid = node('div', 'memory-cards-grid');
  container.append(grid);

  function renderGrid() {
    grid.replaceChildren();
    let facts = data.facts || [];
    const q = searchInput.value.toLowerCase().trim();

    if (activeFilter !== 'all') {
      facts = facts.filter(f => f.category === activeFilter);
    }
    if (q) {
      facts = facts.filter(f => (f.content || '').toLowerCase().includes(q) || (f.tags || []).some(t => t.toLowerCase().includes(q)));
    }

    if (!facts.length) {
      grid.append(node('p', 'empty-note', q ? 'Aramaya uygun kural veya bilgi bulunamadı.' : 'Henüz bu projede kayıtlı kalıcı hafıza veya kural yok. Yukarıdaki düğmeyle ilk kuralı ekleyin veya bir görev çalıştırın; ajanlar öğrendiklerini buraya kümülatif olarak kaydedecektir.'));
      return;
    }

    facts.forEach(f => {
      const card = node('div', 'memory-card');
      const header = node('div', 'memory-card-header');
      const badgeCls = `memory-badge memory-badge-${f.category || 'insight'}`;
      const badgeLabel = { rule: '📋 Kural', preference: '⭐ Tercih', architecture: '🏛️ Mimari', insight: '💡 Ders' }[f.category] || f.category;
      header.append(node('span', badgeCls, badgeLabel));

      const sourceLabel = f.source === 'auto_reflection' ? '🤖 Otonom Öğrenildi' : '👤 Kullanıcı';
      header.append(node('span', 'memory-source-pill', sourceLabel));
      card.append(header);

      card.append(node('div', 'memory-card-content', f.content));

      if (f.tags?.length) {
        const tagBox = node('div', 'memory-card-tags');
        f.tags.forEach(t => tagBox.append(node('span', 'memory-tag', `#${t}`)));
        card.append(tagBox);
      }

      const footer = node('div', 'memory-card-footer');
      const dateStr = f.created_at ? new Date(f.created_at).toLocaleDateString('tr-TR', { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' }) : '';
      footer.append(node('span', 'memory-date', dateStr));

      const delBtn = node('button', 'memory-del-btn', '🗑️');
      delBtn.title = 'Hafızadan sil';
      delBtn.onclick = async () => {
        if (!confirm('Bu kuralı kalıcı hafızadan silmek istediğinize emin misiniz?')) return;
        try {
          await api(`/api/memory/facts/${f.id}`, { method: 'DELETE' });
          toast('Kayıt silindi.');
          renderMemoryPage();
        } catch (e) {
          toast(errorText(e));
        }
      };
      footer.append(delBtn);
      card.append(footer);

      grid.append(card);
    });
  }

  searchInput.oninput = renderGrid;
  renderGrid();
}

async function renderTriggersPage() {
  const container = $('#data-page');
  container.replaceChildren(
    node('h2', '', 'Proaktif Tetikleyiciler (Autonomous Triggers & Daemons)'),
    node('p', 'page-description', 'OpenAI Dots ve Grok Bot çalışma felsefesi: Kullanıcının komut vermesini beklemeden arka planda zamanlanmış (cron/interval) veya dosya değişikliklerine duyarlı olarak otonom çalışan görevler.')
  );

  let data = { triggers: [], templates: [] };
  try {
    data = await api('/api/triggers');
    state.triggersData = data;
    const activeCount = (data.triggers || []).filter(t => t.enabled).length;
    const countEl = $('#trigger-count');
    if (countEl) countEl.textContent = activeCount;
  } catch (e) {
    console.warn('Triggers fetch error', e);
  }

  container.append(node('h3', 'triggers-section-title', '⚡ Hazır Otonom Görev Şablonları (Tek Tıkla Ekle)'));
  const tplGrid = node('div', 'templates-grid');
  (data.templates || []).forEach(tpl => {
    const card = node('div', 'template-card');
    card.append(node('h4', '', tpl.name));
    card.append(node('p', '', tpl.description));
    const btn = node('button', 'outline-button', '＋ Bu Görevi Aktif Et');
    btn.onclick = async () => {
      try {
        await api('/api/triggers/from-template', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ template_id: tpl.template_id })
        });
        toast(`'${tpl.name}' tetikleyicisi eklendi ve aktif edildi!`);
        renderTriggersPage();
      } catch (e) {
        toast(errorText(e));
      }
    };
    card.append(btn);
    tplGrid.append(card);
  });
  container.append(tplGrid);

  container.append(node('h3', 'triggers-section-title', '📋 Aktif Otonom Tetikleyiciler'));
  const trgGrid = node('div', 'triggers-list-grid');

  const triggers = data.triggers || [];
  if (!triggers.length) {
    trgGrid.append(node('p', 'empty-note', 'Henüz aktif bir proaktif tetikleyici yok. Yukarıdaki hazır şablonlardan birini ekleyebilir veya aşağıdaki formdan yeni bir özel tetikleyici oluşturabilirsiniz.'));
  } else {
    triggers.forEach(t => {
      const card = node('div', `trigger-card ${t.last_status === 'running' ? 'trigger-running' : ''}`);

      const top = node('div', 'trigger-card-top');
      top.append(node('h4', 'trigger-title', t.name));

      const intervalText = t.type === 'once'
        ? `Bir kez · ${date(t.run_at || t.next_run_at) || 'zaman bekleniyor'}`
        : t.type === 'file_watch'
          ? `Watcher (${t.interval_seconds}s sessizlik)`
          : `Her ${Math.round(t.interval_seconds / 3600) || Math.round(t.interval_seconds / 60) + ' dk'} saat`;
      top.append(node('span', 'trigger-interval-pill', intervalText));
      card.append(top);

      if (t.description) {
        card.append(node('p', 'trigger-desc', t.description));
      }

      card.append(node('div', 'trigger-prompt-box', t.prompt));

      const statusRow = node('div', 'trigger-status-row');
      const statusBadge = node('span', `status-badge badge-status-${t.last_status === 'running' ? 'running' : t.last_status === 'error' ? 'error' : t.last_status === 'completed' ? 'completed' : 'idle'}`,
        t.last_status === 'running' ? '⚡ Çalışıyor' : t.last_status === 'completed' ? '✓ Başarılı' : t.last_status === 'error' ? '⚠ Hata' : '💤 Beklemede'
      );
      statusRow.append(statusBadge);

      const runCountInfo = `Toplam: ${t.run_count || 0} kez çalıştı`;
      statusRow.append(node('span', '', runCountInfo));
      card.append(statusRow);

      const actions = node('div', 'trigger-actions');
      const toggleLabel = node('label', 'trigger-toggle-label');
      const check = node('input');
      check.type = 'checkbox';
      check.checked = !!t.enabled;
      check.onchange = async () => {
        try {
          await api(`/api/triggers/${t.id}`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ enabled: check.checked })
          });
          toast(check.checked ? 'Tetikleyici aktif edildi.' : 'Tetikleyici duraklatıldı.');
          renderTriggersPage();
        } catch (e) {
          toast(errorText(e));
        }
      };
      toggleLabel.append(check, node('span', '', check.checked ? 'Aktif' : 'Pasif'));
      actions.append(toggleLabel);

      const runNowBtn = node('button', 'trigger-run-now-btn', '⚡ Şimdi Çalıştır');
      runNowBtn.onclick = async () => {
        try {
          runNowBtn.disabled = true;
          runNowBtn.textContent = 'Başlatılıyor…';
          const res = await api(`/api/triggers/${t.id}/run-now`, { method: 'POST' });
          toast(`Tetikleyici otonom görev başlattı! (Görev ID: ${res.run_id})`);
          setTimeout(renderTriggersPage, 1500);
        } catch (e) {
          toast(errorText(e));
          runNowBtn.disabled = false;
          runNowBtn.textContent = '⚡ Şimdi Çalıştır';
        }
      };
      actions.append(runNowBtn);

      const delBtn = node('button', 'memory-del-btn', '🗑️');
      delBtn.title = 'Tetikleyiciyi sil';
      delBtn.onclick = async () => {
        if (!confirm(`'${t.name}' tetikleyicisini silmek istediğinize emin misiniz?`)) return;
        try {
          await api(`/api/triggers/${t.id}`, { method: 'DELETE' });
          toast('Tetikleyici silindi.');
          renderTriggersPage();
        } catch (e) {
          toast(errorText(e));
        }
      };
      actions.append(delBtn);

      card.append(actions);
      trgGrid.append(card);
    });
  }
  container.append(trgGrid);

  container.append(node('h3', 'triggers-section-title', '＋ Yeni zamanlanmış görev veya tetikleyici oluştur'));
  const customBox = node('div', 'settings-group');
  customBox.innerHTML = `
    <div style="display:flex;gap:12px;margin:10px 0;flex-wrap:wrap">
      <input id="custom-trg-name" type="text" placeholder="Tetikleyici Adı (Örn: Gece Testi)" style="flex:2;min-width:200px;padding:8px 12px;border:1px solid #dce4f1;border-radius:6px;font-size:12px">
      <select id="custom-trg-type" style="padding:8px;border-radius:6px;border:1px solid #dce4f1;background:#fff;font-size:12px">
        <option value="once">Belirli zamanda bir kez</option>
        <option value="interval">Zaman Aralığı (Interval)</option>
        <option value="file_watch">Dosya Değişikliği (File Watcher)</option>
      </select>
      <input id="custom-trg-run-at" type="datetime-local" aria-label="Görevin yerel başlangıç zamanı" style="padding:8px;border:1px solid #dce4f1;border-radius:6px;font-size:12px">
      <input id="custom-trg-interval" type="number" value="3600" min="10" placeholder="Aralık (saniye)" style="width:120px;padding:8px;border:1px solid #dce4f1;border-radius:6px;font-size:12px" hidden>
    </div>
    <textarea id="custom-trg-prompt" placeholder="Ajanın otonom uyanınca yürüteceği talimat/görev metni..." rows="3" style="width:100%;border:1px solid #dce4f1;border-radius:6px;padding:10px;font-size:12px;line-height:1.6;font-family:inherit;margin-bottom:10px"></textarea>
    <button id="create-custom-trg-btn" class="primary-button">Tetikleyiciyi Oluştur ve Başlat</button>
  `;
  const typeInput = customBox.querySelector('#custom-trg-type');
  const intervalInput = customBox.querySelector('#custom-trg-interval');
  const runAtInput = customBox.querySelector('#custom-trg-run-at');
  typeInput.onchange = () => {
    intervalInput.hidden = typeInput.value === 'once';
    runAtInput.hidden = typeInput.value !== 'once';
  };
  customBox.querySelector('#create-custom-trg-btn').onclick = async () => {
    const name = customBox.querySelector('#custom-trg-name').value.trim();
    const type = typeInput.value;
    const interval_seconds = parseInt(intervalInput.value) || 3600;
    const prompt = customBox.querySelector('#custom-trg-prompt').value.trim();
    const runAt = type === 'once' && runAtInput.value ? new Date(runAtInput.value) : null;
    if (!prompt || (type !== 'once' && !name) || (type === 'once' && (!runAt || Number.isNaN(runAt.valueOf()) || runAt <= new Date()))) {
      toast(type === 'once' ? 'Görev metni ve gelecekte bir yerel saat seçin.' : 'Tetikleyici adı ve görev metnini girin.');
      return;
    }
    try {
      await api('/api/triggers', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(type === 'once'
          ? {name, type, run_at: runAt.toISOString(), prompt, enabled: true}
          : {name, type, interval_seconds, prompt, enabled: true})
      });
      toast(type === 'once' ? 'Görev seçilen zamana kaydedildi.' : `'${name}' tetikleyicisi oluşturuldu!`);
      renderTriggersPage();
    } catch (e) {
      toast(errorText(e));
    }
  };
  container.append(customBox);
}

async function loadMemoryStats() {
  try {
    const data = await api('/api/memory');
    state.memoryData = data;
    const el = $('#memory-count');
    if (el) el.textContent = (data.facts || []).length;
  } catch {}
}

async function loadTriggersStats() {
  try {
    const data = await api('/api/triggers');
    state.triggersData = data;
    const el = $('#trigger-count');
    if (el) el.textContent = (data.triggers || []).filter(t => t.enabled).length;
  } catch {}
}


async function pollEvents(generation = state.generation) {
  const id = inspectedPlan()?.id || state.selectedId;
  if (!id) return;
  const data = await api(`/api/runs/${encodeURIComponent(id)}/events?after=${state.cursor}`);
  if (generation !== state.generation || id !== (inspectedPlan()?.id || state.selectedId)) return;
  if (Array.isArray(data.events)) {
    const known = new Set(state.events.map(e => e.id));
    state.events.push(...data.events.filter(e => !known.has(e.id)));
    state.events = state.events.slice(-150);
  }
  state.cursor = data.cursor ?? state.cursor;
  if (state.tab === 'events' || state.tab === 'work' || state.tab === 'bridges') {
    renderInspector();
  }
}

function schedulePoll(delay = 1000) {
  clearTimeout(state.timer);
  if (document.hidden || state.page !== 'chat' || !state.selectedId || (state.run && !conversationList().some(run => activeStatuses.has(run.status)))) return;
  state.timer = setTimeout(poll, delay);
}

async function poll() {
  if (state.polling) { schedulePoll(); return; }
  if (!state.selectedId || document.hidden) return;
  const id = state.selectedId, generation = state.generation;
  state.polling = true;
  try {
    await loadConversation(id, generation);
    if (generation !== state.generation) return;
    renderRun();
    fetchRunTokens(id);
    await pollEvents(generation);
    if (!state.notificationsAt || Date.now() - state.notificationsAt > 8000) {
      state.notificationsAt = Date.now();
      refreshNotifications();
    }
    connected(true);
  } catch (error) {
    if (generation === state.generation) {
      connected(false);
      $('#footer-state').textContent = 'Bağlantı hatası · Yeniden deneniyor';
    }
  } finally {
    state.polling = false;
    schedulePoll();
  }
}

async function mutateRun(action, runId = inspectedPlan()?.id || state.selectedId) {
  if (state.busy || !state.selectedId) return;
  const id = runId, generation = state.generation;
  setBusy(true);
  try {
    const run = await api(`/api/runs/${encodeURIComponent(id)}/${action}`, { method: 'POST', body: '{}' });
    if (generation !== state.generation) return;
    storeRun(run);
    await loadConversation(state.selectedId, generation);
    state.fingerprint = '';
    renderRun();
    await pollEvents(generation);
    schedulePoll();
  } catch (error) {
    toast(errorText(error));
  } finally {
    setBusy(false);
  }
}

async function retryStep(runId, stepId) {
  if (state.busy) return;
  setBusy(true);
  try {
    const run = await api(`/api/runs/${encodeURIComponent(runId)}/steps/${encodeURIComponent(stepId)}/retry`, {
      method: 'POST',
      body: '{}'
    });
    state.run = run;
    storeRun(run);
    state.fingerprint = '';
    renderRun();
    toast(`'${stepId}' adımı için tekrar deneme başlatıldı.`);
    await pollEvents(state.generation);
    schedulePoll();
  } catch (error) {
    toast(errorText(error));
  } finally {
    setBusy(false);
  }
}

let currentSwitchTarget = null;

async function autoSwitchStepModel(runId, stepId) {
  if (state.busy) return;
  setBusy(true);
  try {
    toast(`'${stepId}' adımı için alternatif model otomatik atanıyor...`);
    const run = await api(`/api/runs/${encodeURIComponent(runId)}/steps/${encodeURIComponent(stepId)}/switch-model`, {
      method: 'POST',
      body: JSON.stringify({ model_id: 'auto' })
    });
    state.run = run;
    storeRun(run);
    state.fingerprint = '';
    renderRun();
    const newM = run.plan?.steps?.find(s => s.id === stepId)?.model_id || run.results?.[stepId]?.model_id || 'yeni model';
    toast(`'${stepId}' adımı otomatik olarak '${newM}' modeline aktarıldı ve başlatıldı.`);
    await pollEvents(state.generation);
    schedulePoll();
  } catch (error) {
    toast(errorText(error));
  } finally {
    setBusy(false);
  }
}

async function retryQuotaStep(runId, stepId) {
  if (state.busy) return;
  setBusy(true);
  try {
    const run = await api(`/api/runs/${encodeURIComponent(runId)}/steps/${encodeURIComponent(stepId)}/retry-quota`, {
      method: 'POST',
      body: JSON.stringify({})
    });
    state.run = run;
    storeRun(run);
    state.fingerprint = '';
    renderRun();
    toast('Kota kaydı temizlendi; adım yeniden deneniyor.');
    await pollEvents(state.generation);
    schedulePoll();
  } catch (error) {
    toast(errorText(error));
  } finally {
    setBusy(false);
  }
}

function renderModelOptgroups(selectElement, models, {
  currentModel = null,
  cooldowns = {},
  includeAuto = false,
  autoLabel = '⚡ Varsayılan Model (Otomatik Yönlendirme)'
} = {}) {
  selectElement.replaceChildren();

  if (includeAuto) {
    const autoOption = node('option', '', autoLabel);
    autoOption.value = '';
    selectElement.appendChild(autoOption);
  }

  const groups = [
    { label: 'Google Antigravity (Gemini & Claude)', match: m => m.provider === 'antigravity' },
    { label: 'Claude Code CLI (Anthropic)', match: m => m.provider === 'claude-code' || m.runtime === 'claude-code' },
    { label: 'Ollama (Yerel Modeller)', match: m => m.provider === 'ollama' || m.runtime === 'ollama' },
    { label: 'OpenCode CLI', match: m => m.provider === 'opencode' },
    { label: 'Evren (OpenCode)', match: m => m.provider === 'evren' },
    { label: 'OpenAI Codex', match: m => m.provider === 'codex' },
    { label: 'Diğer Modeller', match: () => true }
  ];

  const handled = new Set();
  let firstAvailableId = null;

  for (const group of groups) {
    const groupModels = models.filter(m => !handled.has(m.id) && group.match(m));
    if (!groupModels.length) continue;

    const optgroup = document.createElement('optgroup');
    optgroup.label = group.label;

    for (const m of groupModels) {
      handled.add(m.id);
      const isCurrent = m.id === currentModel;
      const cd = cooldowns[m.id];
      const isCooldown = Boolean(cd && cd.remaining_seconds > 0);

      let label = m.id;
      if (isCurrent) label += ' (Mevcut)';
      if (isCooldown) {
        const rem = cd.remaining_seconds;
        const hours = Math.floor(rem / 3600);
        const mins = Math.floor((rem % 3600) / 60);
        const timeStr = hours > 0 ? `${hours} sa ${mins} dk` : `${mins} dk`;
        label += ` · Kota bekliyor (${timeStr})`;
      }

      const opt = node('option', '', label);
      opt.value = m.id;
      if (isCurrent || isCooldown) {
        opt.disabled = true;
      } else if (!firstAvailableId) {
        firstAvailableId = m.id;
      }
      optgroup.appendChild(opt);
    }
    selectElement.appendChild(optgroup);
  }

  return firstAvailableId;
}

async function openModelSwitchModal(runId, step) {
  currentSwitchTarget = { runId, stepId: step.id };
  const modal = $('#model-switch-dialog');
  if (!modal) return;

  $('#model-switch-title').textContent = `Ajanı / Modeli Değiştir · ${step.title || step.id}`;
  const select = $('#switch-model-select');
  select.replaceChildren();

  // Canlı kota hafızasını (cooldowns) sunucudan al
  let cooldowns = {};
  try {
    cooldowns = await api('/api/models/quota-status');
  } catch {
    cooldowns = state.bootstrap?.model_cooldowns || {};
  }

  const currentModel = state.run?.results?.[step.id]?.model_id || step.model_id;
  const models = state.bootstrap?.models || [];
  // Kullanıcının sistemdeki tüm desteklenen modelleri seçebilmesini sağla (Ollama, OpenCode, Claude Code dahil)
  const eligible = models.filter(isSelectableModel);

  const firstAvailableId = renderModelOptgroups(select, eligible, {
    currentModel,
    cooldowns
  });

  // Kotası dolu olmayan ve mevcut olmayan öncelikli modeli seç (Gemini / Claude / Ollama vb.)
  const preferred = eligible.find(m => m.id !== currentModel && !cooldowns[m.id] &&
    (m.provider === 'antigravity' || m.id.includes('gemini') || m.id.includes('claude') || m.provider === 'ollama'));
  if (preferred) {
    select.value = preferred.id;
  } else if (firstAvailableId) {
    select.value = firstAvailableId;
  } else {
    const opt = node('option', '', 'Şu anda uygun model yok; sayaç dolunca otomatik denenecek');
    opt.value = '';
    opt.disabled = true;
    opt.selected = true;
    select.prepend(opt);
  }
  $('#confirm-model-switch-btn').disabled = !firstAvailableId;

  modal.showModal();
}

async function submitModelSwitch(e) {
  if (e) e.preventDefault();
  if (!currentSwitchTarget || state.busy) return;
  const select = $('#switch-model-select');
  const newModel = select.value;
  if (!newModel) return;

  const { runId, stepId } = currentSwitchTarget;
  const modal = $('#model-switch-dialog');
  if (modal) modal.close();

  setBusy(true);
  try {
    toast(`'${stepId}' adımı için '${newModel}' modeli seçildi, işlem başlatılıyor...`);
    const run = await api(`/api/runs/${encodeURIComponent(runId)}/steps/${encodeURIComponent(stepId)}/switch-model`, {
      method: 'POST',
      body: JSON.stringify({ model_id: newModel })
    });
    state.run = run;
    storeRun(run);
    state.fingerprint = '';
    renderRun();
    toast(`'${stepId}' adımı '${newModel}' modeli ile yeniden başlatıldı.`);
    await pollEvents(state.generation);
    schedulePoll();
  } catch (error) {
    toast(errorText(error));
  } finally {
    setBusy(false);
  }
}

function downloadRun() {
  if (!state.run) return;
  const blob = new Blob([JSON.stringify({ ...state.run, events: state.events }, null, 2)], { type: 'application/json;charset=utf-8' });
  const url = URL.createObjectURL(blob), link = node('a');
  link.href = url;
  link.download = `agent-manager-${state.run.id}.json`;
  document.body.append(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

function updateBackendNote() {
  const backend = $('#execution-backend').value;
  $('#backend-readiness').textContent = backend === 'cao' ? (state.bootstrap?.cao?.detail || 'CAO hazır') : 'Mevcut CLI araçları doğrudan kullanılır.';
  $('#backend-note').textContent = backend === 'cao'
    ? 'Gömülü CAO motoru Windows üzerinde Codex ve Antigravity modellerini ConPTY terminalleriyle yönetir. LangGraph planı denetler.'
    : 'Doğrudan CLI modu adımları doğrudan terminalden yürütür.';
}

function applySettings() {
  $('#workspace-path').value = state.settings.workspace || state.bootstrap?.workspace_default || '';
  $('#execution-backend').value = state.settings.execution_backend || 'cao';
  updateBackendNote();
  $('#timeout-seconds').value = state.settings.timeout_seconds || 900;
  $('#max-parallel').value = state.settings.max_parallel || 4;
  const workspaceSummary = $('#workspace-summary');
  if (workspaceSummary) {
    workspaceSummary.textContent = state.settings.workspace || state.bootstrap?.workspace_default || 'Proje klasörünü ayarlayın';
    workspaceSummary.title = workspaceSummary.textContent;
  }
}

function openSettings() {
  applySettings();
  $('#settings-dialog').showModal();
  checkForUpdate();
}

let updateStatus = null;
let updateBusy = false;

function renderUpdateStatus(data) {
  updateStatus = data;
  const current = data?.current;
  const remote = data?.remote;
  $('#update-current').textContent = current ? current.slice(0, 8) : '—';
  $('#update-remote').textContent = remote ? remote.slice(0, 8) : '—';
  const available = Number(data?.behind || 0) > 0;
  $('#update-badge').textContent = available ? 'Yeni sürüm var' : 'Güncel';
  $('#update-badge').classList.toggle('available', available);
  if (data?.last_update?.state === 'dependencies_failed') {
    $('#update-badge').textContent = 'Kurulum uyarısı';
  }
  $('#update-state').textContent = data?.last_update?.state === 'dependencies_failed'
    ? data.last_update.detail
    : data?.reason || (available
    ? `${data.behind} yeni commit hazır.`
    : 'Bu kurulum güncel.');
  $('#apply-update').disabled = updateBusy || !data?.eligible;
}

async function checkForUpdate() {
  if (updateBusy) return;
  updateBusy = true;
  $('#check-update').disabled = true;
  $('#apply-update').disabled = true;
  $('#update-state').textContent = 'GitHub sürümü kontrol ediliyor…';
  try {
    const data = await api('/api/update/status?refresh=1', { timeoutMs: 30000 });
    renderUpdateStatus(data);
  } catch (error) {
    updateStatus = null;
    $('#update-badge').textContent = 'Kontrol edilemedi';
    $('#update-badge').classList.remove('available');
    $('#update-state').textContent = errorText(error);
  } finally {
    updateBusy = false;
    $('#check-update').disabled = false;
    $('#apply-update').disabled = !updateStatus?.eligible;
  }
}

async function applyUpdate() {
  if (updateBusy || !updateStatus?.eligible) return;
  if (state.pendingAttachments.some(item => item.uploading)) {
    $('#update-state').textContent = 'Dosya yüklemesi bitince güncellemeyi başlatın.';
    return;
  }
  updateBusy = true;
  $('#check-update').disabled = true;
  $('#apply-update').disabled = true;
  $('#update-state').textContent = 'Güncelleme uygulanıyor…';
  try {
    const result = await api('/api/update/apply', { method: 'POST', timeoutMs: 120000 });
    renderUpdateStatus(result);
    $('#update-badge').textContent = 'Yeniden başlatılıyor';
    $('#update-badge').classList.remove('available');
    $('#update-state').textContent = 'Dosyalar güncellendi. Uygulama yeni sürümle yeniden açılıyor…';
    if (result.restart_required) {
      let restarted = false;
      const deadline = Date.now() + 600000;
      while (Date.now() < deadline) {
        await new Promise(resolve => setTimeout(resolve, 1000));
        try {
          const status = await api('/api/update/status', { timeoutMs: 3500 });
          if (status.server_started_at && status.server_started_at !== result.server_started_at) {
            renderUpdateStatus(status);
            restarted = true;
            break;
          }
        } catch { /* Sunucu yeniden başlarken kısa bağlantı kesilmesi beklenir. */ }
      }
      if (restarted) {
        try {
          sessionStorage.setItem('agent-manager-update-draft', $('#prompt').value);
          sessionStorage.setItem('agent-manager-update-attachments', JSON.stringify(
            state.pendingAttachments.filter(item => item.id).map(({id, name, mime}) => ({id, name, mime}))));
        } catch {}
        location.reload();
      } else {
        $('#update-badge').textContent = 'Yeniden açılma bekleniyor';
        $('#update-state').textContent = 'Dosyalar güncellendi; sunucu henüz dönmedi. Masaüstü başlatıcısını yeniden açın.';
      }
    } else {
      $('#update-badge').textContent = 'Güncel';
      $('#update-state').textContent = result.reason || 'Güncelleme tamamlandı.';
    }
  } catch (error) {
    updateStatus = null;
    $('#update-badge').textContent = 'Güncelleme başarısız';
    $('#update-badge').classList.remove('available');
    $('#update-state').textContent = errorText(error);
  } finally {
    updateBusy = false;
    $('#check-update').disabled = false;
    $('#apply-update').disabled = !updateStatus?.eligible;
  }
}

$('#check-update').addEventListener('click', checkForUpdate);
$('#apply-update').addEventListener('click', applyUpdate);
function openPlanEditor(run = inspectedPlan()) {
  state.editPlanRunId = run?.id;
  $('#plan-json').value = JSON.stringify(run?.plan?.steps || [], null, 2);
  $('#plan-error').hidden = true;
  $('#plan-dialog').showModal();
}

function isSelectableModel(model) {
  const policy = state.bootstrap?.execution_policy;
  return model.execution_supported !== false && !model.hidden &&
    (!policy || policy.allowed_providers.includes(model.provider));
}

function populateModels() {
  const models = state.bootstrap?.models || [];
  // Sistemden çekilen tüm desteklenen modelleri dahil et (Ollama, OpenCode, Claude Code, Antigravity, Codex)
  const offered = models.filter(isSelectableModel);

  const select = $('#planner-model');
  renderModelOptgroups(select, offered, {
    includeAuto: true,
    autoLabel: '⚡ Varsayılan model (Otomatik yönlendirme)'
  });
  if (offered.some(m => m.id === state.settings.planner_model)) {
    select.value = state.settings.planner_model;
  }
  $('#model-count').textContent = models.length;

  const chatSelect = $('#chat-model');
  const cds = state.bootstrap?.model_cooldowns || {};
  renderModelOptgroups(chatSelect, offered, { cooldowns: cds });

  // Kota cooldown kontrolü: Eğer Codex veya seçili model kotada ise Gemini, Claude veya yerel modele devret
  const isCooled = id => Boolean(cds[id] && cds[id].remaining_seconds > 0);
  let preferred = state.settings.chat_model;
  if (!preferred || isCooled(preferred) || !offered.some(m => m.id === preferred)) {
    preferred = offered.find(m => m.id === 'gemini-3.8-flash-medium' && !isCooled(m.id))?.id
      || offered.find(m => m.provider === 'antigravity' && !isCooled(m.id))?.id
      || offered.find(m => (m.provider === 'claude-code' || m.runtime === 'claude-code') && !isCooled(m.id))?.id
      || offered.find(m => m.provider === 'ollama' && !isCooled(m.id))?.id
      || offered.find(m => !isCooled(m.id))?.id
      || offered[0]?.id;
  }
  if (preferred) chatSelect.value = preferred;
}

async function loadPersonas() {
  try {
    const data = await api('/api/personas');
    state.personasData = data;
    $('#persona-count').textContent = (data.personas || []).length;
  } catch {}
}

// Event Listeners
$('#attach-button').addEventListener('click', () => $('#attachment-input').click());
$('#attachment-input').addEventListener('change', event => {
  queueAttachments([...event.target.files]);
  event.target.value = '';
});
$('#prompt').addEventListener('paste', event => {
  const files = [...(event.clipboardData?.files || [])];
  if (files.length) {
    event.preventDefault();
    queueAttachments(files);
  }
});
function setComposerCollapsed(collapsed, focus = false) {
  const form = $('#composer');
  const toggle = $('#toggle-composer');
  if (!form || !toggle) return;
  form.hidden = collapsed;
  $('#composer-hints').hidden = collapsed;
  form.closest('.composer-zone').classList.toggle('composer-collapsed', collapsed);
  toggle.setAttribute('aria-expanded', String(!collapsed));
  toggle.textContent = collapsed ? ($('#prompt').value.trim() ? 'Taslağı aç ▴' : 'Mesaj yaz ▴') : 'Mesaj kutusunu küçült ▾';
  save('piko-composer-collapsed', collapsed);
  if (!collapsed && focus) $('#prompt').focus();
}

$('#toggle-composer').addEventListener('click', () => {
  setComposerCollapsed(!$('#composer').hidden, $('#composer').hidden);
});
setComposerCollapsed(load('piko-composer-collapsed', false));

$('#composer').addEventListener('dragover', event => {
  if ([...(event.dataTransfer?.types || [])].includes('Files')) event.preventDefault();
});
$('#composer').addEventListener('drop', event => {
  const files = [...(event.dataTransfer?.files || [])];
  if (files.length) {
    event.preventDefault();
    queueAttachments(files);
  }
});
$('#instruction-delivery').addEventListener('change', () => renderRun());
$('#composer').addEventListener('submit', async event => {
  event.preventDefault();
  if (state.busy) return;
  if (state.pendingAttachments.some(item => item.uploading)) {
    toast('Dosyaların yüklenmesi tamamlanınca gönderebilirsiniz.');
    return;
  }
  const attachments = attachmentIds();
  const prompt = $('#prompt').value.trim() || (attachments.length ? 'Eklediğim dosyaları incele ve özetle.' : '');
  if (!prompt) return;
  let startFresh = false;
  if (state.run && activeStatuses.has(state.run.status)) {
    if (/^(neredesin|nerede|ne durumda|durum ne|nerelere geldin)\b/i.test(prompt.trim())) {
      $('#prompt').value = '';
      await answerWhere(state.run);
      return;
    }
    if (attachments.length) { toast('Çalışan göreve ek talimatta dosya eki desteklenmiyor.'); return; }
    let delivery = 'amend';
    if (looksLikeNewRequest(prompt)) {
      delivery = await askNewOrCorrection();
      if (delivery === null) return;
    }
    if (delivery === 'amend') {
      setBusy(true);
      try {
        await submitActiveInstruction(state.run, prompt, $('#instruction-delivery').value);
        $('#prompt').value = '';
      } catch (error) { toast(errorText(error)); }
      finally { setBusy(false); }
      return;
    }
    startFresh = true;
  }
  if (!state.bootstrap) {
    toast('Önce yerel sunucu bağlantısının kurulmasını bekleyin.');
    return;
  }
  const previousId = startFresh ? '' : state.selectedId;
  const clientRunId = crypto.randomUUID().replaceAll('-', '');
  const requestedModel = $('#chat-model').value;
  const generation = ++state.generation;
  const startedAt = Date.now();
  const live = beginChatLive(prompt, requestedModel, previousId || clientRunId, [...state.pendingAttachments]);
  setBusy(true);
  $('#prompt').value = '';
  const applyReply = async res => {
    const run = res.run || (previousId ? await api(`/api/runs/${encodeURIComponent(previousId)}`) : null);
    if (!run) throw new Error('Sohbet kaydı alınamadı.');
    storeRun(run);
    live.finish();
    clearPendingAttachments();
    if (generation !== state.generation) return;
    state.planViewId = null;
    state.selectedId = run.id;
    state.run = run;
    state.events = [];
    state.cursor = 0;
    state.step = null;
    state.fingerprint = '';
    save('agent-manager-selected', run.id);
    await loadConversation(run.id, generation);
    setPage('chat');
    renderRun();
    $('#messages').scrollTop = $('#messages').scrollHeight;
    renderRecent();
    schedulePoll();
    connected(true);
  };
  try {
    const res = await api('/api/assist', {
      method: 'POST',
      body: JSON.stringify({
        message: prompt, model_id: requestedModel, planner_model: state.settings.planner_model || 'auto',
        planning_level: $('#planning-level').value,
        mode: 'orchestrate',
        conversation_run_id: previousId || '', client_run_id: clientRunId,
        workspace: state.activeProject?.path || state.settings.workspace || state.bootstrap.workspace_default,
        project_id: state.activeProject?.id || '', allow_writes: $('#allow-writes').checked,
        attachments, timeout_seconds: Number(state.settings.timeout_seconds || 1200),
        max_parallel: $('#planning-level').value === 'parallel' ? 6 : Number(state.settings.max_parallel || 4),
        execution_backend: state.settings.execution_backend || 'cao'
      }),
      timeoutMs: 630000
    });
    await applyReply(res);
  } catch (error) {
    let recovered = false;
    try {
      const latest = await api(`/api/runs/${encodeURIComponent(previousId || clientRunId)}`);
      const history = latest.chat_history || [];
      const index = history.findLastIndex(item => item.role === 'user' && item.text === prompt && Date.parse(item.created_at) >= startedAt - 2000);
      if (index >= 0 && history.slice(index + 1).some(item => item.role === 'assistant')) {
        await applyReply({run: latest});
        recovered = true;
      }
    } catch {}
    if (!recovered) {
      $('#prompt').value = prompt;
      live.finish(errorText(error));
      toast(errorText(error));
    }
  } finally {
    setBusy(false);
  }
});

$('#settings-form').addEventListener('submit', event => {
  event.preventDefault();
  state.settings = {
    ...state.settings,
    workspace: $('#workspace-path').value.trim(),
    execution_backend: $('#execution-backend').value,
    timeout_seconds: Number($('#timeout-seconds').value),
    max_parallel: Number($('#max-parallel').value)
  };
  save('agent-manager-settings', state.settings);
  applySettings();
  $('#settings-dialog').close();
  toast('Yeni görevler için ayarlar kaydedildi.');
});

$('#execution-backend').addEventListener('change', () => {
  updateBackendNote();
  populateModels();
});

$('#plan-form').addEventListener('submit', async event => {
  event.preventDefault();
  const error = $('#plan-error');
  error.hidden = true;
  if (state.busy) return;
  const id = state.editPlanRunId || state.selectedId, generation = state.generation;
  setBusy(true);
  try {
    const steps = JSON.parse($('#plan-json').value);
    if (!Array.isArray(steps) || !steps.length) throw new Error('Plan, en az bir adım içeren bir JSON dizisi olmalıdır.');
    const run = await api(`/api/runs/${encodeURIComponent(id)}/plan`, { method: 'PUT', body: JSON.stringify({ steps }) });
    if (generation !== state.generation) return;
    state.run = run;
    storeRun(run);
    state.fingerprint = '';
    renderRun();
    $('#plan-dialog').close();
    toast('Plan kaydedildi.');
  } catch (e) {
    error.textContent = errorText(e);
    error.hidden = false;
  } finally {
    setBusy(false);
  }
});

$$('[data-page]').forEach(el => el.addEventListener('click', () => setPage(el.dataset.page)));
$$('[data-tab]').forEach(el => el.addEventListener('click', () => {
  state.tab = el.dataset.tab;
  el.closest('details')?.removeAttribute('open');
  if (state.tab === 'work' && state.run) loadWorkForRun(state.run);
  if (state.tab === 'bridges') loadBridgeStatus(true);
  renderInspector();
}));
$$('[data-example]').forEach(el => el.addEventListener('click', () => {
  $('#prompt').value = el.dataset.example;
  setComposerCollapsed(false);
  $('#prompt').focus();
}));
$$('.settings-open').forEach(el => el.addEventListener('click', openSettings));
$$('.dialog-close').forEach(el => el.addEventListener('click', () => el.closest('dialog').close()));
$('#new-chat').addEventListener('click', newChat);
$('#cancel-job').addEventListener('click', () => mutateRun('cancel', state.run?.id));
$('#toggle-inspector').addEventListener('click', () => {
  $('#inspector').classList.toggle('open');
  $('#toggle-inspector').setAttribute('aria-expanded', String($('#inspector').classList.contains('open')));
});
$('#close-inspector').addEventListener('click', () => {
  $('#inspector').classList.remove('open');
  $('#toggle-inspector').setAttribute('aria-expanded', 'false');
});

// Terminal modal buttons
const refreshBtn = $('#refresh-terminal-btn');
if (refreshBtn) refreshBtn.addEventListener('click', refreshTerminalOutput);
$('#terminal-modal').addEventListener('close', stopTerminalOutput);
window.addEventListener('pagehide', stopTerminalOutput);
const closeTermBtn = $('#close-terminal-modal');
if (closeTermBtn) closeTermBtn.addEventListener('click', () => $('#terminal-modal').close());
const dismissTermBtn = $('#dismiss-terminal-btn');
if (dismissTermBtn) dismissTermBtn.addEventListener('click', () => $('#terminal-modal').close());
$('#close-live-terminal').addEventListener('click', () => $('#live-terminal-modal').close());
$('#live-terminal-modal').addEventListener('close', disconnectLiveTerminal);
$('#reconnect-live-terminal').addEventListener('click', () => {
  if (liveTerminalTarget) openLiveTerminal(liveTerminalTarget.title, liveTerminalTarget.terminalId);
});
window.addEventListener('pagehide', disconnectLiveTerminal);

// Model switch modal buttons
const closeSwitchBtn = $('#close-model-switch');
if (closeSwitchBtn) closeSwitchBtn.addEventListener('click', () => $('#model-switch-dialog').close());
const cancelSwitchBtn = $('#cancel-model-switch');
if (cancelSwitchBtn) cancelSwitchBtn.addEventListener('click', () => $('#model-switch-dialog').close());
const switchForm = $('#model-switch-form');
if (switchForm) switchForm.addEventListener('submit', submitModelSwitch);

// Enter / Ctrl+Enter handler: Enter alone in single-line submits, but inside multi-paragraph pastes allow Shift+Enter or Ctrl+Enter
$('#prompt').addEventListener('keydown', event => {
  if (event.key === 'Enter' && (event.ctrlKey || event.metaKey)) {
    event.preventDefault();
    $('#composer').requestSubmit();
  }
});

$('#prompt').addEventListener('input', () => {
  const val = $('#prompt').value.trim();
  if (val) {
    if (mascot) mascot.dispatch({ event: 'chat.message' });
    if (window.PikoConstellation) window.PikoConstellation.setMood('listening', 'Sizi dinliyorum...');
  } else {
    updateMascot(state.run);
    if (window.PikoConstellation && !state.run) window.PikoConstellation.setMood('idle');
  }
});

$('#prompt').addEventListener('focus', () => {
  if (window.PikoConstellation && !state.run) {
    window.PikoConstellation.setMood('listening', 'Sizi dinliyorum...');
  }
});

$('#prompt').addEventListener('blur', () => {
  if (window.PikoConstellation && !state.run && !$('#prompt').value.trim()) {
    window.PikoConstellation.setMood('idle');
  }
});

document.addEventListener('keydown', event => {
  if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'n') {
    event.preventDefault();
    newChat();
  }
  if (event.key === 'Escape') {
    $('#inspector').classList.remove('open');
    $('#toggle-inspector').setAttribute('aria-expanded', 'false');
  }
});
document.addEventListener('visibilitychange', () => {
  if (document.hidden) clearTimeout(state.timer);
  else { schedulePoll(0); refreshRecentInBackground(); }
});

$('#planner-model').addEventListener('change', () => {
  state.settings.planner_model = $('#planner-model').value;
  save('agent-manager-settings', state.settings);
});
$('#planning-level').addEventListener('change', () => {
  state.settings.planning_level = $('#planning-level').value;
  save('agent-manager-settings', state.settings);
});
$('#chat-model').addEventListener('change', () => {
  state.settings.chat_model = $('#chat-model').value;
  save('agent-manager-settings', state.settings);
});

function updateQuotaCountdowns() {
  document.querySelectorAll('.quota-countdown').forEach(el => {
    const resetsAt = Date.parse(el.dataset.resetAt || '');
    if (!Number.isFinite(resetsAt)) {
      el.textContent = 'Yenilenme saati bekleniyor';
      return;
    }
    const remaining = Math.max(0, Math.ceil((resetsAt - Date.now()) / 1000));
    if (!remaining) {
      el.textContent = 'Yeniden deneniyor…';
      return;
    }
    const hours = Math.floor(remaining / 3600);
    const mins = Math.floor((remaining % 3600) / 60);
    const secs = remaining % 60;
    el.textContent = `${String(hours).padStart(2, '0')}:${String(mins).padStart(2, '0')}:${String(secs).padStart(2, '0')}`;
  });
}

setInterval(updateQuotaCountdowns, 1000);

$('#sidebar-project-card')?.addEventListener('click', openProjectModal);
$('#metric-proj-badge')?.addEventListener('click', openProjectModal);
$('#metric-refresh-btn')?.addEventListener('click', async () => {
  try {
    toast("Modeller ve bağlı CLI'lar taranıyor...");
    const res = await api('/api/models/refresh', { method: 'POST' });
    if (Array.isArray(res.models)) {
      populateModels();
      toast(`Tarama tamamlandı: ${res.models.length} model algılandı.`);
    } else {
      toast("Modeller güncellendi.");
    }
    await loadProjectMetrics();
  } catch (e) {
    toast(errorText(e));
  }
});
$('#close-project-dialog')?.addEventListener('click', () => $('#project-dialog')?.close());

$('#project-dialog')?.addEventListener('click', e => {
  if (e.target === $('#project-dialog')) {
    $('#project-dialog').close();
  }
});

$('#btn-toggle-new-proj')?.addEventListener('click', () => {
  const form = $('#create-project-form');
  if (form) {
    if (form.hasAttribute('hidden')) {
      form.removeAttribute('hidden');
      $('#new-proj-name')?.focus();
    } else {
      form.setAttribute('hidden', '');
    }
  }
});

$('#btn-cancel-new-proj')?.addEventListener('click', () => {
  $('#create-project-form')?.setAttribute('hidden', '');
});

$('#create-project-form')?.addEventListener('submit', async e => {
  e.preventDefault();
  const name = $('#new-proj-name').value.trim();
  const path = $('#new-proj-path').value.trim();
  if (!name || !path) return;
  try {
    const proj = await api('/api/projects', {
      method: 'POST',
      body: JSON.stringify({ name, path })
    });
    state.projects.unshift(proj);
    setActiveProject(proj);
    $('#project-dialog')?.close();
    $('#new-proj-name').value = '';
    $('#new-proj-path').value = '';
    toast(`'${proj.name}' projesi oluşturuldu ve aktif edildi!`);
    newChat();
  } catch (err) {
    toast(errorText(err));
  }
});

// Delegated click for Copy Code buttons
document.addEventListener('click', e => {
  const btn = e.target.closest('.copy-code-btn');
  if (btn && btn.dataset.code) {
    copyToClipboard(decodeURIComponent(btn.dataset.code), btn);
  }
});


$('#topbar-project-btn')?.addEventListener('click', openProjectModal);
$('#sidebar-project-switcher')?.addEventListener('click', openProjectModal);
$('#sidebar-proj-switch-btn')?.addEventListener('click', openProjectModal);
$('#composer-project-pill')?.addEventListener('click', openProjectModal);
$('#ctx-memory-btn')?.addEventListener('click', () => switchPage('memory'));

$('#scope-project-btn')?.addEventListener('click', () => {
  state.recentScope = 'project';
  $('#scope-project-btn')?.classList.add('active');
  $('#scope-all-btn')?.classList.remove('active');
  renderRecent();
});

$('#scope-all-btn')?.addEventListener('click', () => {
  state.recentScope = 'all';
  $('#scope-all-btn')?.classList.add('active');
  $('#scope-project-btn')?.classList.remove('active');
  renderRecent();
});

async function initialize() {
  try {
    state.bootstrap = await api('/api/bootstrap');
    const parallelOption = $('#planning-level option[value="parallel"]');
    parallelOption.disabled = state.bootstrap.features?.isolated_parallel_planning !== true;
    parallelOption.title = parallelOption.disabled ? 'Sunucu güncellemesi bekleniyor' : 'Bağımsız işleri gerektiğinde ortak kapasiteyle yürüt';
    if (parallelOption.disabled && $('#planning-level').value === 'parallel') {
      $('#planning-level').value = 'balanced';
      state.settings.planning_level = 'balanced';
      save('agent-manager-settings', state.settings);
    }

    connected(true);
    startMascotHeartbeat();
    $('#version-label').textContent = `Piko ${state.bootstrap.version || ''}`;
    applySettings();
    bindWorkUi();
    refreshNotifications();
    await fetchProjects();
    populateModels();
    await loadPersonas();
    await loadMemoryStats();
    await loadTriggersStats();
    $('#footer-state').textContent = 'Hazır';
    await refreshRuns();
    const linkedId = new URL(location.href).searchParams.get('run');
    const id = /^[a-f0-9]{32}$/.test(linkedId || '') ? linkedId : load('agent-manager-selected', null);
    if (linkedId && id === linkedId) {
      state.recentScope = 'all';
      $('#scope-all-btn')?.classList.add('active');
      $('#scope-project-btn')?.classList.remove('active');
      renderRecent();
    }
    startRecentRefresh();
    if (id) await selectRun(id);
    else updateMascot(null);
  } catch (error) {
    connected(false);
    $('#footer-state').textContent = 'Sunucuya bağlanılamadı';
    toast(errorText(error));
    const retry = button('Bağlantıyı Yeniden Dene', 'outline-button', () => {
      retry.remove();
      initialize();
    });
    $('#welcome').append(retry);
  }
}

initialize().finally(() => {
  try {
    const draft = sessionStorage.getItem('agent-manager-update-draft');
    if (draft !== null) $('#prompt').value = draft;
    sessionStorage.removeItem('agent-manager-update-draft');
    const attachments = JSON.parse(sessionStorage.getItem('agent-manager-update-attachments') || '[]');
    if (Array.isArray(attachments) && attachments.length) {
      state.pendingAttachments = attachments.filter(item => item && typeof item.id === 'string');
      renderPendingAttachments();
    }
    sessionStorage.removeItem('agent-manager-update-attachments');
  } catch {}
});

/* ---------- Ortak iş kaydı, teslim masası, bildirimler ve görünüm ---------- */
const workState = { jobs: {}, loading: {} };

const mascot = (() => {
  const stage = document.getElementById('piko-mascot-stage');
  if (!stage || typeof initMascot !== 'function') return null;
  return initMascot(stage, { spriteUrl: '#' });
})();

const mascotRunEvents = {
  queued: 'run.queued',
  planning: 'run.planning',
  running: 'run.running',
  completed: 'run.completed',
  cancelled: 'run.cancelled',
  failed: 'run.failed',
  interrupted: 'run.interrupted'
};

const mascotDeliverableStates = new Set([
  'draft', 'delivered', 'repair_requested', 'test_pending', 'test_passed', 'test_failed'
]);

function updateMascot(run) {
  if (window.PikoConstellation) {
    window.PikoConstellation.updateState({ run, job: workState.jobs[run?.id], state });
  }

  const dispatchBoth = payload => {
    if (window.PikoConstellation) {
      window.PikoConstellation.dispatch(payload);
    }
    if (mascot) {
      return mascot.dispatch(payload);
    }
  };

  if (!run) {
    return dispatchBoth({ event: 'idle' });
  }

  const job = workState.jobs[run.id];
  const work = job?.work;
  const deliverables = job?.deliverables || [];
  const latest = deliverables[deliverables.length - 1];
  const workId = work?.id || run.id;

  if (run.status === 'failed' || run.status === 'interrupted' || (work?.status === 'blocked' && !activeStatuses.has(run.status))) {
    const reason = run.error || work?.last_result || statusLabels[run.status] || 'Müdahale gerekiyor.';
    return dispatchBoth({ event: 'work.blocked', work_id: workId, reason });
  }

  if (latest && mascotDeliverableStates.has(latest.status)) {
    return dispatchBoth({
      event: 'deliverable.updated',
      work_id: workId,
      deliverable_status: latest.status,
      reason: latest.title || latest.summary || ''
    });
  }

  const waiting = decisionWaiting(work);
  if (waiting || ['planned', 'awaiting_review', 'awaiting_user_test', 'waiting_quota'].includes(run.status)) {
    return dispatchBoth({
      event: 'work.waiting',
      work_id: workId,
      reason: waiting?.question || statusLabels[run.status] || ''
    });
  }

  const event = mascotRunEvents[run.status];
  if (event) {
    const reason = activeStatuses.has(run.status) && run.current_steps?.length
      ? run.current_steps.join(', ') : undefined;
    return dispatchBoth({ event, work_id: workId, reason });
  }

  return dispatchBoth({ event: 'idle', work_id: workId });
}

let mascotHeartbeatTimer = null;

async function pollMascotHealth() {
  if (!mascot) return;
  try {
    const health = await api('/api/health', { timeoutMs: 5000 });
    const alive = health.daemon_alive !== false;
    mascot.setHeartbeatAlive(alive);
    const trouble = !alive || health.kill_switch
      || (Array.isArray(health.stale_runs) && health.stale_runs.length > 0);
    if (trouble) mascot.dispatch({ event: 'health_report', ...health });
  } catch {
    /* Ulaşılamayan sunucu canlılık sinyali üretmez; mascot.js 120 sn sonra uyur. */
  }
}

function startMascotHeartbeat() {
  if (!mascot || mascotHeartbeatTimer !== null) return;
  mascotHeartbeatTimer = setInterval(pollMascotHealth, 30000);
  setTimeout(pollMascotHealth, 5000);
}

function applyView(view = state.view || 'piko') {
  state.view = view === 'developer' ? 'developer' : 'piko';
  document.body.classList.toggle('view-piko', state.view === 'piko');
  document.body.classList.toggle('view-developer', state.view === 'developer');
  $('#view-piko')?.classList.toggle('active', state.view === 'piko');
  $('#view-dev')?.classList.toggle('active', state.view === 'developer');
  $('#view-piko')?.setAttribute('aria-pressed', String(state.view === 'piko'));
  $('#view-dev')?.setAttribute('aria-pressed', String(state.view === 'developer'));
  save('piko-view', state.view);
  if (state.view === 'developer') {
    $('#inspector')?.classList.add('open');
    $('#toggle-inspector')?.setAttribute('aria-expanded', 'true');
  }
}

async function loadWorkForRun(run) {
  if (!run?.id || workState.loading[run.id]) return;
  workState.loading[run.id] = true;
  try {
    const list = await api('/api/work?limit=200');
    const linked = (list.items || []).find(item => item.run_id === run.id)
      || (list.items || []).find(item => item.conversation_id === (run.conversation_id || run.id));
    if (!linked) return;
    const data = await api(`/api/work/${encodeURIComponent(linked.id)}`);
    workState.jobs[run.id] = data;
    if (state.run?.id === run.id) {
      state.fingerprint = '';
      renderRun();
    }
  } catch {} finally {
    workState.loading[run.id] = false;
  }
}

function decisionWaiting(work) {
  return (work?.decisions || []).find(item => item.status === 'waiting');
}

function evidenceBlock(deliverable) {
  const view = deliverable.evidence_view || {};
  const block = node('div', 'delivery-section evidence-block');
  block.append(node('strong', '', 'Kanıt'));
  if (view.complete === false) {
    block.append(node('span', 'status-badge evidence-missing',
      `eksik kanıt: ${(view.missing || []).join(', ') || 'kanıt yok'}`));
  } else if (view.bridge) {
    block.append(node('span', 'status-badge evidence-ok', 'kanıt tam'));
  }
  if (view.summary) block.append(node('div', 'evidence-summary', view.summary));
  if (view.branch) block.append(node('div', 'evidence-line', `dal: ${view.branch}`));
  for (const link of view.links || []) {
    const anchor = node('a', 'evidence-line evidence-link', link);
    anchor.href = link;
    anchor.target = '_blank';
    anchor.rel = 'noreferrer';
    block.append(anchor);
  }
  if ((view.short_hashes || []).length) {
    const hashes = node('div', 'worker-meta');
    for (const hash of view.short_hashes) hashes.append(node('span', 'worker-id-pill', `sha256 ${hash}`));
    block.append(hashes);
  }
  const lines = view.lines || deliverable.evidence || [];
  if (lines.length) {
    const list = node('ul', 'evidence-lines');
    for (const line of lines) list.append(node('li', '', String(line)));
    block.append(list);
  }
  return block;
}

function deliveryCard(deliverable) {
  const card = node('article', 'delivery-card');
  const head = node('div', 'delivery-card-head');
  const title = node('div', '');
  title.append(node('b', '', deliverable.title || 'Teslim'),
    node('span', 'delivery-version',
      deliverable.parent_version
        ? `v${deliverable.version} · v${deliverable.parent_version} üzerinden`
        : `v${deliverable.version}`));
  const labels = {
    accepted: 'Kabul edildi', repair_requested: 'Düzeltme istendi',
    test_failed: 'Test başarısız', test_passed: 'Test geçti', test_pending: 'Test bekleniyor'
  };
  head.append(title, node('span', `status-badge status-${deliverable.status}`,
    labels[deliverable.status] || 'İncelemeye hazır'));
  card.append(head);
  const clusterTag = node('div', 'chat-cluster-agents');
  const cBadge = node('span', 'cluster-badge', deliverable.cluster_title || 'Paralel Plan Kümesi: Doğrulama');
  const agent1 = node('span', 'agent-pill verifier', '🟢 Denetçi (Verifier)');
  const agent2 = node('span', 'agent-pill coder', '🔵 Geliştirici (Coder)');
  clusterTag.append(cBadge, agent1, agent2);
  card.append(clusterTag);
  card.append(node('div', 'delivery-summary', deliverable.summary || ''));
  if (deliverable.requirements?.length) {
    const block = node('div', 'delivery-section');
    block.append(node('strong', '', 'Karşılanan gereksinimler'));
    const list = node('ul', '');
    for (const item of deliverable.requirements) list.append(node('li', '', item));
    block.append(list);
    card.append(block);
  }
  card.append(evidenceBlock(deliverable));
  const details = node('details', 'delivery-details technical');
  details.append(node('summary', 'details-toggle', 'Ayrıntıları göster'));
  if (deliverable.open_points?.length) {
    const block = node('div', 'delivery-section');
    block.append(node('strong', '', 'Açık kalan noktalar'));
    const list = node('ul', '');
    for (const item of deliverable.open_points) list.append(node('li', '', item));
    block.append(list);
    details.append(block);
  }
  if (deliverable.files?.length) {
    const block = node('div', 'delivery-section');
    block.append(node('strong', '', 'Dosyalar'));
    for (const file of deliverable.files) block.append(node('div', '', String(file)));
    details.append(block);
  }
  if (details.children.length > 1) card.append(details);

  const actions = node('div', 'delivery-actions');
  if (deliverable.status !== 'accepted') {
    const accept = button('Kabul et', 'primary-button', () => actOnDelivery(deliverable, 'accept'));
    const incomplete = deliverable.evidence_view?.complete === false;
    const cannotAccept = incomplete || deliverable.can_accept === false || deliverable.is_latest === false;
    if (cannotAccept) {
      accept.disabled = true;
      accept.title = deliverable.accept_block_reason || (deliverable.is_latest === false
        ? 'Bu önceki bir teslim sürümü. Güncel teslimi inceleyin.'
        : 'Kanıt eksik olduğu için teslim kabul edilemez.');
    }
    actions.append(accept);
  }
  actions.append(button('Düzelt', 'outline-button', () => actOnDelivery(deliverable, 'repair')));
  actions.append(button('Şunu dene', 'outline-button', () => actOnDelivery(deliverable, 'try')));
  actions.append(button('Test sonucu bildir', 'outline-button', () => actOnDelivery(deliverable, 'test')));
  card.append(actions);
  return card;
}

async function actOnDelivery(deliverable, action) {
  let body = {};
  const prompts = {
    repair: 'Neyin düzeltilmesini istiyorsunuz?',
    try: 'Piko şunu denesin:',
    test: 'Test sonucunuzu kısaca yazın (geçti/kaldı ayrıntısı).'
  };
  if (prompts[action]) {
    const note = window.prompt(prompts[action], '');
    if (note === null) return;
    if (!note.trim()) { toast('Kısa bir açıklama gerekli.'); return; }
    body.note = note.trim();
    if (action === 'test') body.passed = !/kald[ıi]|ba[şs]ar[ıi]s[ıi]z|hata|çalışm[ıi]yor/i.test(note);
  }
  try {
    await api(`/api/deliverables/${encodeURIComponent(deliverable.id)}/${action}`, {
      method: 'POST', body: JSON.stringify(body)
    });
    toast(action === 'accept' ? 'Teslim kabul edildi.'
      : action === 'try' ? 'Öneri onarım kuyruğuna eklendi.'
      : action === 'repair' ? 'Düzeltme isteği kaydedildi.' : 'Test sonucu kaydedildi.');
    if (state.run) {
      delete workState.jobs[state.run.id];
      await loadWorkForRun(state.run);
    }
    refreshNotifications();
  } catch (error) { toast(errorText(error)); }
}

function renderWorkPanel(run, container) {
  if (!run || run.id !== state.run?.id) return;
  const job = workState.jobs[run.id];
  if (!job?.work) return;
  const work = job.work;
  const panel = node('article', 'message-bubble plan-overview-bubble work-panel');
  const head = node('div', 'plan-overview-head');
  const identity = node('div', 'plan-identity');
  identity.append(node('span', 'plan-emblem', '^'), node('b', '', work.title || 'İş kaydı'));
  const workLabel = work.status === 'done' ? 'Tamamlandı' : work.status === 'blocked'
    ? 'Engellendi' : work.status === 'waiting' ? 'Bekliyor' : 'Sürüyor';
  head.append(identity, node('span', `status-badge status-${work.status}`, workLabel));
  panel.append(head);

  const info = node('div', 'work-status-card');
  info.append(node('b', '', 'Ne istendi'), node('div', '', work.goal || ''));
  info.append(node('b', '', 'Ne yapıldı'), node('div', '', work.last_result || 'Henüz sonuç yok.'));
  info.append(node('div', 'work-next', `Sıradaki adım: ${work.next_action || 'Teslimi incele.'}`));
  if (work.due_at) info.append(node('div', '', `Son tarih: ${new Date(work.due_at).toLocaleString('tr-TR')}`));
  panel.append(info);

  const decision = decisionWaiting(work);
  if (decision) {
    const form = node('form', 'user-test-request');
    form.append(node('strong', '', 'Piko sizden karar bekliyor'), node('p', '', decision.question));
    const answer = node('textarea', 'user-test-answer');
    answer.rows = 2;
    answer.required = true;
    form.append(answer, button('Kararı gönder', 'primary-button'));
    form.addEventListener('submit', async event => {
      event.preventDefault();
      if (!answer.value.trim()) return;
      try {
        await api(`/api/work/${encodeURIComponent(work.id)}/decisions/${encodeURIComponent(decision.id)}/answer`, {
          method: 'POST', body: JSON.stringify({answer: answer.value.trim()})
        });
        toast('Karar kaydedildi.');
        delete workState.jobs[run.id];
        await loadWorkForRun(run);
        refreshNotifications();
      } catch (error) { toast(errorText(error)); }
    });
    panel.append(form);
  }

  for (const deliverable of (job.deliverables || []).slice(-2)) panel.append(deliveryCard(deliverable));
  container.append(panel);
}

async function answerWorkDecision(workId, decisionId, answer) {
  await api(`/api/work/${encodeURIComponent(workId)}/decisions/${encodeURIComponent(decisionId)}/answer`, {
    method: 'POST', body: JSON.stringify({answer})
  });
  toast('Karar kaydedildi.');
  if (state.run) {
    delete workState.jobs[state.run.id];
    await loadWorkForRun(state.run);
  }
  refreshNotifications();
  openNotifications();
}

function notificationActions(item) {
  const holder = node('div', 'notification-actions');
  if (!item.work_id) return holder;
  api(`/api/work/${encodeURIComponent(item.work_id)}`).then(job => {
    const work = job.work || {};
    const decision = decisionWaiting(work);
    const latest = (job.deliverables || []).slice(-1)[0];
    if (decision) {
      holder.append(button('Kabul et', 'primary-button',
        () => answerWorkDecision(work.id, decision.id, 'Kabul ediyorum').catch(e => toast(errorText(e)))));
      holder.append(button('Düzelt', 'outline-button', async () => {
        const note = window.prompt('Neyin düzeltilmesini istiyorsunuz?', '');
        if (!note?.trim()) return;
        answerWorkDecision(work.id, decision.id, `Düzelt: ${note.trim()}`).catch(e => toast(errorText(e)));
      }));
      holder.append(button('Şunu dene', 'outline-button', async () => {
        const note = window.prompt('Piko şunu denesin:', '');
        if (!note?.trim()) return;
        answerWorkDecision(work.id, decision.id, `Şunu dene: ${note.trim()}`).catch(e => toast(errorText(e)));
      }));
    } else if (latest && latest.status !== 'accepted') {
      holder.append(button('Kabul et', 'primary-button',
        () => actOnDelivery(latest, 'accept')));
      holder.append(button('Düzelt', 'outline-button',
        () => actOnDelivery(latest, 'repair')));
      holder.append(button('Şunu dene', 'outline-button',
        () => actOnDelivery(latest, 'try')));
    }
    if (!holder.children.length) holder.remove();
  }).catch(() => holder.remove());
  return holder;
}

function notificationItem(item) {
  const card = node('article', `notification-item${item.read ? '' : ' unread'}`);
  card.append(node('b', '', item.title || item.kind));
  if (item.body) card.append(node('p', '', item.body));
  card.append(node('time', '', new Date(item.created_at).toLocaleString('tr-TR')));
  card.append(notificationActions(item));
  if (!item.read) {
    card.append(button('Okundu', 'outline-button', async () => {
      try {
        await api(`/api/notifications/${encodeURIComponent(item.id)}/read`, {method: 'POST', body: '{}'});
        refreshNotifications();
        openNotifications();
      } catch (error) { toast(errorText(error)); }
    }));
  }
  return card;
}

async function refreshNotifications() {
  try {
    const data = await api('/api/notifications?unread=1');
    state.notifications = data.items || [];
    const count = state.notifications.length;
    const badge = $('#notification-count');
    badge.hidden = count === 0;
    badge.textContent = String(count);
    $('#notification-button').classList.toggle('has-unread', count > 0);
  } catch {}
}

async function openNotifications() {
  const dialog = $('#notifications-dialog');
  try {
    const [data, prefs] = await Promise.all([
      api('/api/notifications'),
      api('/api/notifications/preferences')
    ]);
    const list = $('#notification-list');
    list.replaceChildren();
    for (const item of data.items || []) list.append(notificationItem(item));
    $('#notification-empty').hidden = (data.items || []).length > 0;
    $('#notif-enabled').value = prefs.enabled ? '1' : '0';
    $('#notif-severity').value = prefs.min_severity || 'low';
    $('#notif-quiet-start').value = prefs.quiet_start || '22:00';
    $('#notif-quiet-end').value = prefs.quiet_end || '08:00';
    $('#notif-timezone').value = prefs.timezone || '';
    dialog.showModal();
  } catch (error) { toast(errorText(error)); }
}

function whereSummary(job) {
  const work = job?.work;
  if (!work) return 'Şu anda kayıtlı bir işim yok. Yeni bir hedef verirsen planlayıp başlayabilirim.';
  const latest = (job.deliverables || []).slice(-1)[0];
  const parts = [
    `İş: ${work.title || work.goal}`,
    `Durum: ${work.status === 'done' ? 'tamamlandı' : work.status === 'waiting' ? 'bekliyor' : work.status === 'blocked' ? 'engellendi' : 'sürüyor'}`,
    latest ? `Son teslim: v${latest.version} - ${latest.summary}` : '',
    `Sıradaki adım: ${work.next_action || 'Teslimi incele.'}`
  ].filter(Boolean);
  return parts.join('\n');
}

async function answerWhere(run) {
  if (!run?.id) return false;
  let job = workState.jobs[run.id];
  if (!job) { await loadWorkForRun(run); job = workState.jobs[run.id]; }
  const bubble = node('article', 'message-bubble chat-bubble-assistant where-answer');
  const body = node('div', 'markdown-body');
  body.textContent = whereSummary(job);
  bubble.append(node('div', 'chat-bubble-head', '^ Piko'), body);
  $('#run-content')?.append(bubble);
  return true;
}

function looksLikeNewRequest(message) {
  const text = message.trim().toLocaleLowerCase('tr');
  return /^(yeni|ayrıca|ayri|bundan bağımsız|farklı bir iş|yeni görev|yeni iş)/.test(text)
    || /(yeni bir görev|yeni bir iş|ayrı bir görev|ayrı bir iş|bundan bağımsız)/.test(text);
}

function askNewOrCorrection() {
  return new Promise(resolve => {
    const dialog = $('#choice-dialog');
    const finish = value => {
      dialog.removeEventListener('close', onClose);
      if (dialog.open) dialog.close();
      resolve(value);
    };
    const onClose = () => resolve(null);
    $('#choice-amend').onclick = () => finish('amend');
    $('#choice-new').onclick = () => finish('new');
    $('#choice-cancel').onclick = () => finish(null);
    dialog.addEventListener('close', onClose, {once: true});
    dialog.showModal();
  });
}

function bindWorkUi() {
  applyView(load('piko-view', 'piko'));
  $('#view-piko')?.addEventListener('click', () => applyView('piko'));
  $('#view-dev')?.addEventListener('click', () => applyView('developer'));
  $('#notification-button')?.addEventListener('click', openNotifications);
  $('#close-notifications')?.addEventListener('click', () => $('#notifications-dialog').close());
  $('#notification-read-all')?.addEventListener('click', async () => {
    try {
      await api('/api/notifications/read-all', {method: 'POST', body: '{}'});
      toast('Bildirimler okundu.');
      await openNotifications();
      refreshNotifications();
    } catch (error) { toast(errorText(error)); }
  });
  $('#notification-preferences-form')?.addEventListener('submit', async event => {
    event.preventDefault();
    try {
      await api('/api/notifications/preferences', {
        method: 'PUT',
        body: JSON.stringify({
          enabled: $('#notif-enabled').value === '1',
          min_severity: $('#notif-severity').value,
          quiet_start: $('#notif-quiet-start').value || '22:00',
          quiet_end: $('#notif-quiet-end').value || '08:00',
          timezone: $('#notif-timezone').value.trim()
        })
      });
      toast('Bildirim tercihleri kaydedildi.');
      refreshNotifications();
    } catch (error) { toast(errorText(error)); }
  });
}

let runtimeMetricsTimer = null;
async function fetchRuntimeMetrics(force = false) {
  if (!force && runtimeMetricsTimer && Date.now() - runtimeMetricsTimer < 5000) return;
  try {
    const data = await api('/api/metrics');
    state.runtimeMetrics = data;
    runtimeMetricsTimer = Date.now();
    if (state.tab === 'work') renderInspector();
  } catch {}
}

function runtimeMetricsCard() {
  const card = node('div', 'worker-card');
  card.append(node('div', 'worker-card-title', 'Sistem kapasitesi'));
  const metrics = state.runtimeMetrics;
  if (!metrics) {
    card.append(node('small', '', 'Kapasite ölçümü yükleniyor…'));
    return card;
  }
  const slots = metrics.slots || {};
  const runtime = metrics.runtime || {};
  const occupancy = runtime.slot_occupancy_percent ?? 0;
  const active = slots.slot_active ?? 0;
  const cap = slots.max_concurrent_runs ?? 2;
  const row = node('div', 'worker-meta');
  row.append(node('span', 'worker-id-pill', `slot ${active}/${cap}`));
  row.append(node('span', 'worker-id-pill', `doluluk %${occupancy}`));
  row.append(node('span', 'worker-id-pill', `bekleyen ${slots.slot_waiters ?? 0}`));
  row.append(node('span', 'worker-id-pill', `kabul edilen ${slots.slot_wait_count ?? 0}`));
  row.append(node('span', 'worker-id-pill',
    `ort. bekleme ${Number(runtime.slot_wait_avg_seconds ?? 0).toFixed(2)} sn`));
  card.append(row);
  card.append(node('small', '', `En yüksek eşzamanlılık: ${slots.slot_max_observed ?? 0}`));
  return card;
}


async function loadBridgeStatus(force = false) {
  if (!force && state.bridgeCards && Date.now() - (state.bridgeStatusAt || 0) < 5000) return;
  try {
    const data = await api('/api/bridges/status');
    state.bridgeCards = data.packs || [];
    state.bridgeStatusAt = Date.now();
  } catch (error) {
    state.bridgeCards = [];
    toast(errorText(error));
  }
}

function bridgeCard(status) {
  const card = node('article', 'bridge-card');
  const bridge = status.bridge || {};
  const head = node('div', 'delivery-card-head');
  const title = node('div', '');
  title.append(node('b', '', `${bridge.id || 'paket'} · v${bridge.version || '—'}`));
  head.append(title, node('span', 'status-badge', (bridge.levels || []).join(' · ')));
  card.append(head);
  if (bridge.description) card.append(node('p', 'delivery-section', bridge.description));

  const meta = node('div', 'worker-meta');
  for (const level of bridge.levels || []) {
    const needsApproval = level === 'apply' && bridge.approval?.apply === 'per_call_token';
    meta.append(node('span', 'worker-id-pill',
      needsApproval ? `${level} (tek kullanımlık onay)` : level));
  }
  meta.append(node('span', 'worker-id-pill', `kanıt: ${bridge.evidence_format || '—'}`));
  card.append(meta);

  const manager = status.personas?.manager || {};
  const bridgeId = bridge.id || 'office';
  const granted = (manager.bridges?.[bridgeId]?.levels || []).join(', ') || '—';
  const needsToken = bridge.approval?.apply === 'per_call_token';
  card.append(node('div', 'delivery-section',
    `Yönetici personası (${manager.role || 'scheduler'}): ${granted}. `
    + (needsToken ? 'apply ayrı, tek kullanımlık onay token’ı ister.'
                  : 'apply bu pakette tanımlı değil.')));

  card.append(node('h4', 'inspector-title', 'Son teslim'));
  const last = status.last_delivery;
  if (!last) {
    card.append(node('p', 'empty-note', 'Bu paketten henüz teslim yok.'));
    return card;
  }
  const lastHead = node('div', 'delivery-card-head');
  const lastTitle = node('div', '');
  lastTitle.append(node('b', '', last.title || 'Teslim'),
    node('span', 'delivery-version', `v${last.version}`));
  const lastLabels = {accepted: 'Kabul edildi', repair_requested: 'Düzeltme istendi'};
  lastHead.append(lastTitle,
    node('span', `status-badge status-${last.status}`,
      lastLabels[last.status] || last.status));
  card.append(lastHead);
  card.append(node('div', 'delivery-summary', last.summary || ''));

  if (last.files?.length) {
    const block = node('div', 'delivery-section');
    block.append(node('strong', '', 'Dosyalar'));
    const list = node('ul', '');
    for (const file of last.files) list.append(node('li', '', file));
    block.append(list);
    card.append(block);
  }
  if (last.evidence?.length || last.evidence_view) card.append(evidenceBlock(last));
  card.append(node('small', '', `Teslim ${new Date(last.created_at).toLocaleString('tr-TR')}`));
  return card;
}

function renderBridgeInspector(container) {
  container.append(node('h3', 'inspector-title', 'Köprü paketleri'));
  const holder = node('div', 'bridge-cards');
  holder.append(node('p', 'empty-note', 'Paket durumu yükleniyor…'));
  container.append(holder);
  loadBridgeStatus().then(() => {
    holder.replaceChildren();
    const cards = state.bridgeCards || [];
    if (!cards.length) {
      holder.append(node('p', 'empty-note', 'Kayıtlı köprü paketi yok.'));
      return;
    }
    for (const card of cards) holder.append(bridgeCard(card));
  });
}
