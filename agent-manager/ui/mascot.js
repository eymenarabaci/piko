/* Piko mascot core.
 * initMascot(container) -> { dispatch, setHeartbeatAlive, getState, destroy }
 * No external dependencies. State is driven only by explicit events.
 */
(function (global) {
  'use strict';

  var HEARTBEAT_TIMEOUT_MS = 120000;
  var HEARTBEAT_CHECK_MS = 5000;
  var SVG_NS = 'http://www.w3.org/2000/svg';
  var XLINK_NS = 'http://www.w3.org/1999/xlink';

  var STATES = [
    'idle',
    'konusuyor',
    'yuruyor',
    'calisiyor',
    'dagitiyor',
    'teslim',
    'bekliyor',
    'takildi',
    'uyuyor'
  ];

  var LABELS = {
    idle: 'Hazır',
    konusuyor: 'Konuşuyor',
    yuruyor: 'Yürüyor',
    calisiyor: 'Çalışıyor',
    dagitiyor: 'Dağıtıyor',
    teslim: 'Teslim getiriyor',
    bekliyor: 'Onay bekliyor',
    takildi: 'Takıldı',
    uyuyor: 'Uyuyor'
  };

  var ICONS = {
    idle: '○',
    konusuyor: '…',
    yuruyor: '→',
    calisiyor: '◆',
    dagitiyor: '⇢',
    teslim: '✓',
    bekliyor: '◷',
    takildi: '!',
    uyuyor: 'z'
  };

  var DEFAULT_REASONS = {
    takildi: 'Bir iş takıldı, kontrol gerekiyor.',
    uyuyor: 'Sessizim, uyuyorum.'
  };

  var EVENT_STATES = {
    idle: 'idle',
    ready: 'idle',
    wake: 'idle',
    reset: 'idle',
    'run.idle': 'idle',
    'run.completed': 'idle',
    'run.cancelled': 'idle',
    'work.idle': 'idle',
    'work.cancelled': 'idle',
    'deliverable.accepted': 'idle',

    konusuyor: 'konusuyor',
    'chat.message': 'konusuyor',
    'chat.reply': 'konusuyor',
    'message.user': 'konusuyor',
    'message.assistant': 'konusuyor',
    'assistant.speaking': 'konusuyor',
    'assistant.message': 'konusuyor',

    yuruyor: 'yuruyor',
    'run.queued': 'yuruyor',
    'run.started': 'yuruyor',
    'work.started': 'yuruyor',
    'work.assigned': 'yuruyor',
    'bridge.visit': 'yuruyor',

    calisiyor: 'calisiyor',
    'run.planning': 'calisiyor',
    'run.running': 'calisiyor',
    'work.active': 'calisiyor',
    'work.working': 'calisiyor',
    'deliverable.draft': 'calisiyor',
    'deliverable.repair_requested': 'calisiyor',

    dagitiyor: 'dagitiyor',
    'run.fanout': 'dagitiyor',
    'bridge.dispatch': 'dagitiyor',
    'pack.dispatch': 'dagitiyor',
    'work.distribute': 'dagitiyor',
    'work.pack_started': 'dagitiyor',

    teslim: 'teslim',
    'deliverable.ready': 'teslim',
    'deliverable.delivered': 'teslim',
    'deliverable.test_passed': 'teslim',
    'work.done': 'teslim',

    bekliyor: 'bekliyor',
    'work.waiting': 'bekliyor',
    'work.awaiting_review': 'bekliyor',
    'run.waiting_quota': 'bekliyor',
    'deliverable.test_pending': 'bekliyor',
    'deliverable.awaiting_review': 'bekliyor',
    awaiting_review: 'bekliyor',
    awaiting_user_test: 'bekliyor',

    takildi: 'takildi',
    'work.blocked': 'takildi',
    'run.failed': 'takildi',
    'run.error': 'takildi',
    'run.interrupted': 'takildi',
    'watchdog.blocked': 'takildi',
    'health.blocked': 'takildi',
    'deliverable.test_failed': 'takildi',

    uyuyor: 'uyuyor',
    sleep: 'uyuyor',
    'heartbeat.silent': 'uyuyor',
    'heartbeat.stale': 'uyuyor',
    'daemon.stale': 'uyuyor',
    'daemon.offline': 'uyuyor'
  };

  var WORK_STATUS_STATES = {
    open: 'idle',
    active: 'calisiyor',
    waiting: 'bekliyor',
    blocked: 'takildi',
    done: 'teslim',
    cancelled: 'idle'
  };

  var DELIVERABLE_STATUS_STATES = {
    draft: 'calisiyor',
    delivered: 'teslim',
    accepted: 'idle',
    repair_requested: 'calisiyor',
    test_pending: 'bekliyor',
    test_passed: 'teslim',
    test_failed: 'takildi'
  };

  function hasOwn(object, key) {
    return Object.prototype.hasOwnProperty.call(object, key);
  }

  function normalise(raw) {
    if (raw === null || raw === undefined) return { event: '' };
    if (typeof raw === 'string') return { event: raw.toLowerCase() };
    if (typeof raw !== 'object') return { event: '' };
    return {
      event: String(raw.event || '').toLowerCase(),
      work_id: raw.work_id || raw.workId || '',
      reason: raw.reason,
      pack: raw.pack || '',
      status: raw.status || '',
      deliverable_status: raw.deliverable_status || raw.deliverableStatus || '',
      daemon_alive: raw.daemon_alive,
      stale_runs: raw.stale_runs,
      active_runs: raw.active_runs,
      kill_switch: raw.kill_switch
    };
  }

  function resolveState(payload) {
    var name = payload.event;
    if (!name) return null;

    if (name === 'health_report' || name === 'health.report' || name === 'healthreport') {
      if (payload.daemon_alive === false) {
        return { state: 'uyuyor', reason: 'Bağlantı sessiz.' };
      }
      if (payload.kill_switch) {
        return { state: 'takildi', reason: 'Watchdog durdurdu.' };
      }
      if (Array.isArray(payload.stale_runs) && payload.stale_runs.length) {
        return { state: 'takildi', reason: 'Bir iş sessiz kaldı.' };
      }
      if (Array.isArray(payload.active_runs) && payload.active_runs.length) {
        return { state: 'calisiyor' };
      }
      return { state: 'idle' };
    }

    if (name === 'work_record' || name === 'work.record' || name === 'work_record.updated') {
      if (payload.status && hasOwn(WORK_STATUS_STATES, payload.status)) {
        return { state: WORK_STATUS_STATES[payload.status] };
      }
      if (payload.deliverable_status && hasOwn(DELIVERABLE_STATUS_STATES, payload.deliverable_status)) {
        return { state: DELIVERABLE_STATUS_STATES[payload.deliverable_status] };
      }
      return null;
    }

    if (name === 'deliverable' || name === 'deliverable.updated') {
      if (payload.status && hasOwn(DELIVERABLE_STATUS_STATES, payload.status)) {
        return { state: DELIVERABLE_STATUS_STATES[payload.status] };
      }
      if (payload.deliverable_status && hasOwn(DELIVERABLE_STATUS_STATES, payload.deliverable_status)) {
        return { state: DELIVERABLE_STATUS_STATES[payload.deliverable_status] };
      }
      return null;
    }

    if (hasOwn(EVENT_STATES, name)) return { state: EVENT_STATES[name] };
    return null;
  }

  function detectSpriteBase() {
    try {
      if (global.document) {
        var current = global.document.currentScript;
        if (current && current.src) return current.src.replace(/[^\/]*$/, '');
        var scripts = global.document.getElementsByTagName('script');
        for (var i = 0; i < scripts.length; i += 1) {
          var src = scripts[i].src || '';
          if (/mascot\.js(\?|$)/.test(src)) return src.replace(/[^\/]*$/, '');
        }
      }
    } catch (error) {
      /* fall through to the default below */
    }
    return 'ui/';
  }

  function initMascot(container, options) {
    if (!container || typeof container.appendChild !== 'function') {
      throw new Error('initMascot(container): bir kapsayıcı öğe gerekli.');
    }
    if (typeof global.document === 'undefined') {
      throw new Error('initMascot(container): tarayıcı ortamı gerekli.');
    }

    options = options || {};
    var doc = global.document;
    var currentState = 'idle';
    var lastWorkId = '';
    var lastEvent = '';
    var lastBeatAt = Date.now();
    var timer = null;
    var autoSleep = options.autoSleep !== false;
    var timeoutMs = Number(options.heartbeatTimeoutMs) || HEARTBEAT_TIMEOUT_MS;
    var checkMs = Number(options.heartbeatCheckMs) || HEARTBEAT_CHECK_MS;
    var spriteBase = options.spriteUrl
      ? String(options.spriteUrl).replace(/#.*$/, '')
      : detectSpriteBase() + 'mascot-sprite.svg';

    var root = doc.createElement('div');
    root.className = 'piko-mascot-stage';
    root.setAttribute('data-state', 'idle');
    root.setAttribute('role', 'status');
    root.setAttribute('aria-live', 'polite');

    var figure = doc.createElement('span');
    figure.className = 'piko-mascot-figure';
    figure.setAttribute('aria-hidden', 'true');

    var svg = doc.createElementNS(SVG_NS, 'svg');
    svg.setAttribute('class', 'piko-mascot-svg');
    svg.setAttribute('viewBox', '0 0 64 64');
    svg.setAttribute('aria-hidden', 'true');
    svg.setAttribute('focusable', 'false');

    var use = doc.createElementNS(SVG_NS, 'use');
    svg.appendChild(use);
    figure.appendChild(svg);

    var icon = doc.createElement('span');
    icon.className = 'piko-state-icon';
    icon.setAttribute('aria-hidden', 'true');

    var text = doc.createElement('span');
    text.className = 'piko-mascot-text';

    var label = doc.createElement('span');
    label.className = 'piko-mascot-label';

    var reasonEl = doc.createElement('span');
    reasonEl.className = 'piko-mascot-reason';
    reasonEl.hidden = true;

    text.appendChild(label);
    text.appendChild(reasonEl);
    root.appendChild(figure);
    root.appendChild(icon);
    root.appendChild(text);
    container.appendChild(root);

    function spriteHref(state) {
      return spriteBase + '#piko-' + state;
    }

    function setState(state, reason, meta) {
      if (STATES.indexOf(state) === -1) return currentState;

      var resolvedReason;
      if (reason === undefined || reason === null) {
        resolvedReason = DEFAULT_REASONS[state] || '';
      } else {
        resolvedReason = String(reason);
      }

      currentState = state;
      lastEvent = (meta && meta.event) || '';
      if (meta && meta.work_id) lastWorkId = String(meta.work_id);

      root.setAttribute('data-state', state);
      root.setAttribute('data-event', lastEvent);
      if (lastWorkId) root.setAttribute('data-work-id', lastWorkId);
      root.setAttribute(
        'aria-label',
        'Piko durumu: ' + LABELS[state] + (resolvedReason ? '. ' + resolvedReason : '')
      );

      label.textContent = LABELS[state];
      icon.textContent = ICONS[state];
      reasonEl.textContent = resolvedReason;
      reasonEl.hidden = !resolvedReason;

      var href = spriteHref(state);
      use.setAttribute('href', href);
      use.setAttributeNS(XLINK_NS, 'xlink:href', href);

      return currentState;
    }

    function snapshot() {
      return {
        state: currentState,
        label: LABELS[currentState],
        work_id: lastWorkId,
        event: lastEvent
      };
    }

    function dispatch(raw) {
      var payload = normalise(raw);
      if (!payload.event) return snapshot();

      lastBeatAt = Date.now();
      var resolved = resolveState(payload);
      if (!resolved) return snapshot();

      var reason = payload.reason !== undefined ? payload.reason : resolved.reason;
      setState(resolved.state, reason, {
        event: payload.event,
        work_id: payload.work_id
      });
      return snapshot();
    }

    function setHeartbeatAlive(alive) {
      if (alive) {
        lastBeatAt = Date.now();
        if (currentState === 'uyuyor') {
          setState('idle', '', { event: 'heartbeat.alive' });
        }
      } else {
        setState('uyuyor', DEFAULT_REASONS.uyuyor, { event: 'heartbeat.silent' });
      }
      return snapshot();
    }

    function checkHeartbeat() {
      if (!autoSleep || currentState === 'uyuyor') return;
      if (Date.now() - lastBeatAt >= timeoutMs) {
        setState('uyuyor', DEFAULT_REASONS.uyuyor, { event: 'heartbeat.silent' });
      }
    }

    function destroy() {
      if (timer !== null) {
        global.clearInterval(timer);
        timer = null;
      }
      if (root.parentNode) root.parentNode.removeChild(root);
    }

    if (autoSleep && typeof global.setInterval === 'function') {
      timer = global.setInterval(checkHeartbeat, checkMs);
    }

    setState('idle', '', { event: 'init' });

    return {
      dispatch: dispatch,
      setHeartbeatAlive: setHeartbeatAlive,
      getState: snapshot,
      destroy: destroy,
      root: root
    };
  }

  initMascot.STATES = STATES;
  initMascot.LABELS = LABELS;
  initMascot.ICONS = ICONS;
  initMascot.EVENT_STATES = EVENT_STATES;
  initMascot.HEARTBEAT_TIMEOUT_MS = HEARTBEAT_TIMEOUT_MS;

  if (typeof module !== 'undefined' && module.exports) {
    module.exports = initMascot;
  }
  global.initMascot = initMascot;
})(typeof window !== 'undefined' ? window : this);
