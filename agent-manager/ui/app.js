const $ = selector => document.querySelector(selector);
const $$ = selector => [...document.querySelectorAll(selector)];
const activeStatuses = new Set(['queued', 'planning', 'running', 'waiting_quota']);
const statusLabels = {
  queued: 'Sırada', planning: 'Planlanıyor', planned: 'Plan hazır',
  running: 'Çalışıyor', awaiting_review: 'İnceleme bekliyor',
  completed: 'Tamamlandı', failed: 'Başarısız', cancelled: 'İptal edildi',
  interrupted: 'Kesildi', pending: 'Bekliyor', blocked: 'Bağımlılık bekliyor',
  success: 'Tamamlandı', skipped: 'Atlandı', waiting_quota: '⏳ Kota Bekleniyor'
};
const unifiedHint = 'Sorunuzu veya yapmak istediğiniz işi yazın. Gerektiğinde planı inceleyip onaylayabilirsiniz.';

const state = {
  bootstrap: null, runs: [], run: null, selectedId: null,
  page: 'chat', mode: 'auto', tab: 'flow', step: null,
  events: [], cursor: 0, busy: false, timer: null, generation: 0,
  fingerprint: '', polling: false, settings: {},
  personasData: { personas: [], skills: [], mcps: [] },
  activeTerminalId: null, runTokens: 0,
  projects: [], activeProject: null, pendingAttachments: [], conversationRuns: [], runTokenTotals: {}
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
const planningLevelLabels = {fast: 'Hızlı', balanced: 'Dengeli', detailed: 'Ayrıntılı'};
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
  el.querySelector('span').textContent = ok ? 'Bağlı (CAO Native)' : 'Bağlantı kesildi';
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
  const timer = setInterval(() => {
    const seconds = Math.floor((Date.now() - started) / 1000);
    elapsed.textContent = `${seconds} sn`;
    status.textContent = `${phase}…`;
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
        assistant.classList.add('chat-live-error');
        head.querySelector('.chat-live-dot')?.remove();
        status.textContent = error;
        elapsed.textContent = 'Yanıt alınamadı';
      } else {
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
  try {
    const stats = await api(`/api/projects/${state.activeProject.id}/stats`);
    const tokEl = $('#metric-total-tokens');
    const stepEl = $('#metric-total-steps');
    const trigEl = $('#metric-active-triggers');
    const projEl = $('#metric-proj-name');
    if (tokEl) tokEl.textContent = stats.total_tokens > 1000 ? `${(stats.total_tokens / 1000).toFixed(1)}k` : stats.total_tokens;
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

async function fetchRunTokens(id) {
  try {
    const data = await api(`/api/runs/${encodeURIComponent(id)}/tokens`);
    state.runTokenTotals[id] = data.total_tokens || 0;
    state.runTokens = state.runTokenTotals[state.selectedId] || 0;
    if (inspectedPlan()?.id === id) renderRun();
  } catch {}
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

function renderRunMessages(run, container, questionDrafts, archived = false) {
  if (!archived) {
    for (const [index, previous] of (run.plan_history || []).entries()) {
      const section = node('section', 'archived-plan');
      section.append(button(`Önceki plan ${index + 1} · ${previous.prompt || 'Plan'} ↗`, 'archived-plan-select', () => choosePlan(previous.execution_id)));
      section.addEventListener('click', () => { state.planViewId = previous.execution_id; state.fingerprint = ''; renderRun(); });
      renderRunMessages({...previous, plan_history: [], conversation_history: [], chat_history: []}, section, [], true);
      section.querySelectorAll('.run-actions button, .inline-question button').forEach(button => button.disabled = true);
      container.append(section);
    }
  }

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
  if (!(run.mode === 'chat' && run.chat_history?.[0]?.text === run.prompt)) {
    const userBubble = node('article', 'message-bubble user-message');
    const userHead = node('div', 'user-head');
    userHead.append(node('span', 'user-avatar', '👤'), node('b', '', 'Siz'), node('time', 'time-meta', date(run.created_at, true)));
    userBubble.append(userHead, node('div', 'user-body', run.prompt));
    container.append(userBubble);
  }

  // 2. ORKESTRATÖR PLAN & MINI-DAG BALONU (Yalnızca gerçekten çok adımlı plan gerekiyorsa gösterilir)
  const steps = run.plan?.steps || [];
  const isMultiStep = steps.length > 1;

  if (isMultiStep || run.status === 'planned') {
    const planBubble = node('article', 'message-bubble plan-overview-bubble');
    const planHead = node('div', 'plan-overview-head');
    const planIdentity = node('div', 'plan-identity');
    planIdentity.append(node('span', 'plan-emblem', '◈'), node('b', '', 'Çalışma planı'));
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
    const result = run.results?.[step.id];
    const isRunning = (run.current_steps || []).includes(step.id);
    if (!result && !isRunning) continue;

    const personaKey = step.persona || 'developer';
    const personaIcon = personaIcons[personaKey] || personaIcons.default;
    const personaLabel = personaLabels[personaKey] || personaKey;

    const stepBubble = node('article', 'message-bubble agent-step-bubble');
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
        openTerminalModal(step.title || step.id, result?.output || 'Terminal oturumu aktif...', result?.cao_terminal_id);
      }));
    }
    if (isRunning && !archived) {
      const live = button(result?.cao_terminal_id ? 'Canlı terminali aç ↗' : 'Terminal hazırlanıyor…', 'output-open-button',
        () => openLiveTerminal(step.title || step.id, result?.cao_terminal_id));
      live.disabled = !result?.cao_terminal_id;
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

  if (run.orchestrator_answer && !run.chat_history?.length && isMultiStep) {
    const briefingBubble = node('article', 'message-bubble executive-briefing-bubble');
    const bHead = node('div', 'briefing-head');
    bHead.append(node('span', '', '◈'), node('span', '', 'Baş Yönetici Özeti (Executive Briefing)'));
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
        $('#prompt').focus();
        $('#composer').requestSubmit();
      };
      pills.append(pillBtn);
    });
    briefingBubble.append(pills);
    container.append(briefingBubble);
  }

  renderMermaidDiagrams();
}

function renderRun() {
  const run = state.run;
  const container = $('#run-content');
  $('#welcome').hidden = !!state.selectedId;
  container.hidden = !state.selectedId;

  const anyWaitingQuota = run?.status === 'waiting_quota' || Object.values(run?.results || {}).some(r => r.status === 'waiting_quota');
  $('#job-banner').hidden = !run || (!activeStatuses.has(run.status) && !anyWaitingQuota);
  $('#job-banner').classList.toggle('banner-waiting-quota', anyWaitingQuota);
  if (anyWaitingQuota) {
    const timer = run.quota_timer || Object.values(run.results || {}).find(r => r.status === 'waiting_quota');
    const waitingStepId = timer?.step_id || Object.keys(run.results || {}).find(k => run.results[k]?.status === 'waiting_quota');
    $('#job-label').innerHTML = `⏳ <strong>Kota Bekleniyor (Zamanlayıcı Aktif)</strong> · Kalan: <span class="countdown-badge" id="banner-countdown">...</span> · Model: <code>${timer?.model_id || ''}</code> <button type="button" class="banner-auto-switch-btn" id="banner-quick-switch">⚡ Otomatik Modeli Değiştir</button>`;
    const quickBtn = $('#banner-quick-switch');
    if (quickBtn && waitingStepId) {
      quickBtn.onclick = () => autoSwitchStepModel(run.id, waitingStepId);
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
    stats.append(node('span', 'stat-chip', `Adımlar ${done}/${total || '—'} · %${percent}`));
    stats.append(node('span', 'stat-chip', `⚡ ${(state.runTokenTotals[viewRun.id] || Object.values(viewRun.results || {}).reduce((sum, item) => sum + Number(item.token_usage?.step_tokens || item.tokens || 0), 0)).toLocaleString('tr-TR')} token`));
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

  const fingerprint = JSON.stringify([run.id, run.status, run.error, run.plan, run.results, run.current_steps, run.chat_history, run.orchestrator_answer, run.plan_history, state.conversationRuns, state.planViewId, state.events, state.runTokens]);
  if (fingerprint === state.fingerprint) {
    renderInspector();
    return;
  }
  state.fingerprint = fingerprint;

  const messages = $('#messages');
  const oldScroll = messages.scrollTop;
  const nearBottom = messages.scrollHeight - messages.scrollTop - messages.clientHeight < 80;
  const questionDrafts = [...container.querySelectorAll('.inline-question-input')].map(input => input.value);
  const focusedQuestion = document.activeElement?.classList?.contains('inline-question-input');
  container.replaceChildren();
  let planNumber = 0;
  for (const planRun of conversationList()) {
    const section = node('section', 'conversation-plan');
    section.dataset.runId = planRun.id;
    if (planRun.mode !== 'chat') {
      planNumber++;
      section.append(button(`Plan ${planNumber} · ${statusLabels[planRun.status] || planRun.status} · Ayrıntıları göster ↗`, 'conversation-plan-select', () => choosePlan(planRun.id)));
    }
    section.addEventListener('click', event => {
      if (event.target.closest('.archived-plan')) return;
      state.planViewId = planRun.id; state.fingerprint = ''; renderRun();
    });
    renderRunMessages(planRun, section, questionDrafts);
    container.append(section);
  }
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
  const tabNames = {flow: 'Plan', output: 'Çıktılar', terminals: 'Ajanlar', personas: 'Uzmanlıklar', chat: 'Sohbet', events: 'Etkinlik'};
  const moreLabel = $('#inspector-more-label');
  if (moreLabel) moreLabel.textContent = ['flow', 'output'].includes(state.tab) ? 'Diğer' : tabNames[state.tab] || 'Diğer';

  if (!run) {
    const empty = node('div', 'inspector-empty');
    empty.append(node('span', '', '⋈'), node('h3', '', 'Henüz bir görev yok'), node('p', '', 'Plan, alt ajanlar, persona kartları ve canlı sohbet burada görünür.'));
    container.append(empty);
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
          openTerminalModal(step.title || step.id, result?.output || 'Terminal oturumu aktif. Komutlar yürütülüyor...', result?.cao_terminal_id);
        }));
      }
      if (isRunning && !archivedPlanSelected()) {
        const live = button(result?.cao_terminal_id ? 'Canlı terminali aç' : 'Terminal hazırlanıyor…', 'outline-button',
          () => openLiveTerminal(step.title || step.id, result?.cao_terminal_id));
        live.disabled = !result?.cao_terminal_id;
        actions.append(live);
      }
      if (result?.status === 'waiting_quota') {
        actions.append(button('⚡ Otomatik Modeli Değiştir', 'primary-button', () => {
          autoSwitchStepModel(run.id, step.id);
        }));
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
    if (run.status === 'waiting_quota') {
      const timerChip = node('div', 'stat-chip quota-timer-chip');
      timerChip.innerHTML = `<span>⏳</span> <strong>Kota Zamanlayıcısı:</strong> <span class="countdown-display">Hesaplanıyor...</span> (Çalışma duraklatıldı)`;
      statsRow.append(timerChip);
    } else if (activeWorkers.length) {
      const activeChip = node('div', 'stat-chip');
      activeChip.innerHTML = `<span class="worker-pulse"></span> <strong>${activeWorkers.length} Alt Ajan Çalışıyor</strong> (${activeWorkers.join(', ')})`;
      statsRow.append(activeChip);
    }

    // Token Usage Stat
    const tokenChip = node('div', 'stat-chip');
    tokenChip.id = 'run-token-chip';
    tokenChip.innerHTML = `<span>⚡</span> Token: <strong>${(state.runTokenTotals[run.id] || Object.values(run.results || {}).reduce((sum, item) => sum + Number(item.token_usage?.step_tokens || item.tokens || 0), 0)).toLocaleString('tr-TR')} tokens</strong>`;
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
      if (result.token_usage) container.append(node('p', 'empty-note', `Adım Token: ${result.token_usage.step_tokens}`));
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
    (run.orchestrator_messages || []).forEach((msg, idx) => {
      chatLog.append(node('div', idx % 2 === 0 ? 'chat-msg-user' : 'chat-msg-ai', msg));
    });

    const inputRow = node('form', 'chat-input-row');
    const chatInput = node('input', 'chat-input');
    chatInput.placeholder = 'Orkestratöre talimat veya soru yazın...';
    chatInput.required = true;
    inputRow.append(chatInput, button('Gönder', 'primary-button'));
    inputRow.addEventListener('submit', async e => {
      e.preventDefault();
      const msg = chatInput.value.trim();
      if (!msg) return;
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
          toast(`Seçilen ${requestedModel} yerine ${res.model_id || 'bilinmeyen model'} yanıtladı. Agent Manager sunucusunu yeniden başlatın.`);
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
  }

  if (archivedPlanSelected()) {
    container.querySelectorAll('.primary-button, .retry-btn, .danger-text, form button, input, select, textarea').forEach(control => control.disabled = true);
  }
  container.scrollTop = previousScroll;
}

function openTerminalModal(title, output, terminalId = null) {
  state.activeTerminalId = terminalId;
  $('#terminal-modal-title').textContent = `${title} · ConPTY Terminal Çıktısı`;
  $('#terminal-modal-subtitle').textContent = terminalId ? `Terminal ID: ${terminalId}` : 'Terminal Akışı';
  $('#terminal-modal-output').textContent = output || 'Terminal çıktısı yükleniyor...';
  $('#terminal-modal').showModal();
  if (terminalId) {
    refreshTerminalOutput();
  }
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
  if (!state.activeTerminalId) return;
  try {
    const res = await api(`/api/terminals/${encodeURIComponent(state.activeTerminalId)}/output`);
    $('#terminal-modal-output').textContent = res.output || 'Terminal çıktısı boş.';
    toast('Terminal çıktısı güncellendi.');
  } catch (e) {
    toast(errorText(e));
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

function renderModels() {
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
    node('p', 'page-description', 'Agent Manager\'ı telefonunuzdan veya dış ağdan (4G/5G) kontrol edin. Tailscale ile güvenli WireGuard tüneli üzerinden çalışır.')
  );

  let remoteData = { token: 'Yükleniyor…', interfaces: [], tailscale_ips: [], port: 8765 };
  try {
    remoteData = await api('/api/remote-info');
  } catch (e) {
    console.warn('remote-info error', e);
  }

  const apkCard = node('div', 'settings-group');
  apkCard.append(
    node('h3', '', '📱 Android Uygulaması (Agent Remote APK)'),
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
      <li><b>APK ile Bağlanın:</b> Yukarıdan indirdiğiniz <b>Agent Remote</b> uygulamasını telefonunuzda açın, bilgisayarınızın Tailscale IP'sini (100.x.y.z:8765) ve Mobil Token'ı girip <b>Bağlan</b>'a basın!</li>
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

      const intervalText = t.type === 'file_watch'
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

  container.append(node('h3', 'triggers-section-title', '＋ Yeni Özel Otonom Tetikleyici Oluştur'));
  const customBox = node('div', 'settings-group');
  customBox.innerHTML = `
    <div style="display:flex;gap:12px;margin:10px 0;flex-wrap:wrap">
      <input id="custom-trg-name" type="text" placeholder="Tetikleyici Adı (Örn: Gece Testi)" style="flex:2;min-width:200px;padding:8px 12px;border:1px solid #dce4f1;border-radius:6px;font-size:12px">
      <select id="custom-trg-type" style="padding:8px;border-radius:6px;border:1px solid #dce4f1;background:#fff;font-size:12px">
        <option value="interval">Zaman Aralığı (Interval)</option>
        <option value="file_watch">Dosya Değişikliği (File Watcher)</option>
      </select>
      <input id="custom-trg-interval" type="number" value="3600" min="10" placeholder="Aralık (saniye)" style="width:120px;padding:8px;border:1px solid #dce4f1;border-radius:6px;font-size:12px">
    </div>
    <textarea id="custom-trg-prompt" placeholder="Ajanın otonom uyanınca yürüteceği talimat/görev metni..." rows="3" style="width:100%;border:1px solid #dce4f1;border-radius:6px;padding:10px;font-size:12px;line-height:1.6;font-family:inherit;margin-bottom:10px"></textarea>
    <button id="create-custom-trg-btn" class="primary-button">Tetikleyiciyi Oluştur ve Başlat</button>
  `;
  customBox.querySelector('#create-custom-trg-btn').onclick = async () => {
    const name = customBox.querySelector('#custom-trg-name').value.trim();
    const type = customBox.querySelector('#custom-trg-type').value;
    const interval_seconds = parseInt(customBox.querySelector('#custom-trg-interval').value) || 3600;
    const prompt = customBox.querySelector('#custom-trg-prompt').value.trim();
    if (!name || !prompt) {
      toast('Lütfen tetikleyici adı ve görev talimatını girin.');
      return;
    }
    try {
      await api('/api/triggers', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name, type, interval_seconds, prompt, enabled: true })
      });
      toast(`'${name}' tetikleyicisi oluşturuldu!`);
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
  if (state.tab === 'events') renderInspector();
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
  const eligible = models.filter(m => m.execution_supported !== false && m.provider !== 'opencode' && !m.hidden);

  let firstAvailableId = null;

  for (const m of eligible) {
    const isCurrent = m.id === currentModel;
    const cd = cooldowns[m.id];
    const isCooldown = Boolean(cd && cd.remaining_seconds > 0);

    let label = `${m.id} · ${m.provider.toUpperCase()}`;
    if (isCurrent) {
      label += ' (Mevcut Adım)';
    }
    if (isCooldown) {
      const rem = cd.remaining_seconds;
      const hours = Math.floor(rem / 3600);
      const mins = Math.floor((rem % 3600) / 60);
      const timeStr = hours > 0 ? `${hours} sa ${mins} dk` : `${mins} dk`;
      label += ` ⚠️ (Kotası Dolmuş Olabilir - ${timeStr} kaldı)`;
    }

    const opt = node('option', '', label);
    opt.value = m.id;
    if (isCurrent) {
      opt.disabled = true;
    } else if (!firstAvailableId) {
      firstAvailableId = m.id;
    }
    select.append(opt);
  }

  // Kotası dolu olmayan ve mevcut olmayan öncelikli modeli seç (Gemini / Claude vb.)
  const preferred = eligible.find(m => m.id !== currentModel && !cooldowns[m.id] &&
    (m.provider === 'antigravity' || m.id.includes('gemini') || m.id.includes('claude')));
  if (preferred) {
    select.value = preferred.id;
  } else if (firstAvailableId) {
    select.value = firstAvailableId;
  }

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
  $('#workspace-summary').textContent = state.settings.workspace || state.bootstrap?.workspace_default || 'Proje klasörünü ayarlayın';
  $('#workspace-summary').title = $('#workspace-summary').textContent;
}

function openSettings() {
  applySettings();
  $('#settings-dialog').showModal();
}
function openPlanEditor(run = inspectedPlan()) {
  state.editPlanRunId = run?.id;
  $('#plan-json').value = JSON.stringify(run?.plan?.steps || [], null, 2);
  $('#plan-error').hidden = true;
  $('#plan-dialog').showModal();
}

function populateModels() {
  const models = state.bootstrap?.models || [];
  const select = $('#planner-model');
  select.replaceChildren(node('option', '', 'Varsayılan model (Otomatik yönlendirme)'));
  select.firstChild.value = '';
  const offered = models.filter(model => model.execution_supported !== false && model.provider !== 'opencode');
  for (const model of offered) {
    const option = node('option', '', `${model.id} · ${model.provider}`);
    option.value = model.id;
    select.append(option);
  }
  if (offered.some(m => m.id === state.settings.planner_model)) {
    select.value = state.settings.planner_model;
  }
  $('#model-count').textContent = models.length;
  const chatSelect = $('#chat-model');
  chatSelect.replaceChildren();
  for (const model of offered) {
    const option = node('option', '', `${model.id} · ${model.provider}`);
    option.value = model.id;
    chatSelect.append(option);
  }
  const preferred = state.settings.chat_model || offered.find(model => model.id === 'gpt-6.1-sol')?.id
    || offered.find(model => model.provider === 'codex')?.id || offered[0]?.id;
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
  if (!state.bootstrap) {
    toast('Önce yerel sunucu bağlantısının kurulmasını bekleyin.');
    return;
  }
  const previousId = state.selectedId;
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
        message: prompt, model_id: requestedModel, planning_level: $('#planning-level').value,
        conversation_run_id: previousId || '', client_run_id: clientRunId,
        workspace: state.activeProject?.path || state.settings.workspace || state.bootstrap.workspace_default,
        project_id: state.activeProject?.id || '', allow_writes: $('#allow-writes').checked,
        attachments, timeout_seconds: Number(state.settings.timeout_seconds || 1200),
        max_parallel: Number(state.settings.max_parallel || 4),
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
  renderInspector();
}));
$$('[data-example]').forEach(el => el.addEventListener('click', () => {
  $('#prompt').value = el.dataset.example;
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
  else schedulePoll(0);
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
  const run = state.run;
  if (!run) return;
  const timer = run.quota_timer || Object.values(run.results || {}).find(r => r.status === 'waiting_quota');
  let resetsAtStr = timer?.resets_at;

  // Geçmiş veya aktif kayıtlarda resets_at yoksa output metninden ("2 saat 53 dakika") dinamik hesapla
  if (!resetsAtStr && timer?.output) {
    const m = timer.output.match(/(?:Yenilenmeye|kaldı|var)\s*(\d+)?\s*(?:saat)?\s*(\d+)\s*dakika/i);
    if (m) {
      const h = parseInt(m[1] || '0', 10);
      const min = parseInt(m[2] || '0', 10);
      const target = new Date(Date.now() + (h * 3600 + min * 60) * 1000);
      timer.resets_at = target.toISOString();
      resetsAtStr = timer.resets_at;
    }
  }
  if (!resetsAtStr) return;

  const resetsAt = new Date(resetsAtStr).getTime();
  const now = Date.now();
  const diffSec = Math.max(0, Math.floor((resetsAt - now) / 1000));

  const h = Math.floor(diffSec / 3600);
  const m = Math.floor((diffSec % 3600) / 60);
  const s = diffSec % 60;
  const formatted = `${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`;

  const bannerCd = $('#banner-countdown');
  if (bannerCd) {
    bannerCd.textContent = diffSec > 0 ? formatted : '00:00:00';
  }

  const countdownEls = document.querySelectorAll('.countdown-display');
  countdownEls.forEach(el => {
    el.textContent = diffSec > 0 ? `⏳ ${formatted}` : '⏳ Yenileniyor...';
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
    connected(true);
    $('#version-label').textContent = `Agent Manager ${state.bootstrap.version || ''}`;
    applySettings();
    await fetchProjects();
    populateModels();
    await loadPersonas();
    await loadMemoryStats();
    await loadTriggersStats();
    $('#footer-state').textContent = 'Hazır';
    await refreshRuns();
    const id = load('agent-manager-selected', null);
    if (id) await selectRun(id);
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

initialize();
