/**
 * Piko Living Multi-Agent Atelier & Dynamic Workflow Constellation
 *
 * Features:
 * - Truly dynamic: Sub-agents are created directly from the active plan's real steps & personas.
 * - Real plan progression: Steps accurately reflect backend execution and completion (100% completed = all 8 steps completed).
 * - Clean Idle State: "Yeni Sohbet" starts in a pristine listening state without ghost plans or fake bots.
 * - Single Team & Multi-Pod layouts: Orbiting Piko or clustering by active workflows.
 * - Pure Canvas2D + HTML Overlay. 60 FPS hardware accelerated, zero external dependencies.
 */
(function (global) {
  'use strict';

  const COLORS = {
    piko: { core: '#38bdf8', aura: 'rgba(56, 189, 248, 0.45)', blush: 'rgba(244, 114, 182, 0.65)' },
    supervisionBeam: 'rgba(251, 191, 36, 0.85)'
  };

  const PERSONA_LABELS = {
    developer: 'Geliştirici',
    firebase_specialist: 'Firebase Uzmanı',
    feature_developer: 'Özellik Geliştirici',
    frontend_architect: 'Ön Yüz Mimarı',
    architect: 'Sistem Mimarı',
    qa_tester: 'QA & Test Uzmanı',
    tester: 'Test Uzmanı',
    verifier: 'Denetçi',
    guard: 'Güvenlik',
    security_auditor: 'Güvenlik Denetçisi',
    bridge: 'Mobil Köprü',
    watchdog: 'Bekçi Servisi',
    devops: 'DevOps & Dağıtım',
    writer: 'Teknik Yazar',
    reporter: 'Teknik Raporlayıcı',
    image_generator: 'Görsel Üretici',
    video_generator: 'Video Üretici',
    reviewer: 'Kod İnceleyici'
  };

  const PERSONA_COLORS = {
    developer: { core: '#38bdf8', aura: 'rgba(56, 189, 248, 0.45)', blush: 'rgba(244, 114, 182, 0.5)' },
    feature_developer: { core: '#38bdf8', aura: 'rgba(56, 189, 248, 0.45)', blush: 'rgba(244, 114, 182, 0.5)' },
    firebase_specialist: { core: '#f59e0b', aura: 'rgba(245, 158, 11, 0.45)', blush: 'rgba(244, 114, 182, 0.5)' },
    frontend_architect: { core: '#c084fc', aura: 'rgba(192, 132, 252, 0.45)', blush: 'rgba(244, 114, 182, 0.5)' },
    architect: { core: '#c084fc', aura: 'rgba(192, 132, 252, 0.45)', blush: 'rgba(244, 114, 182, 0.5)' },
    qa_tester: { core: '#34d399', aura: 'rgba(52, 211, 153, 0.45)', blush: 'rgba(244, 114, 182, 0.5)' },
    tester: { core: '#34d399', aura: 'rgba(52, 211, 153, 0.45)', blush: 'rgba(244, 114, 182, 0.5)' },
    verifier: { core: '#34d399', aura: 'rgba(52, 211, 153, 0.45)', blush: 'rgba(244, 114, 182, 0.5)' },
    guard: { core: '#f87171', aura: 'rgba(248, 113, 113, 0.45)', blush: 'rgba(244, 114, 182, 0.5)' },
    security_auditor: { core: '#f87171', aura: 'rgba(248, 113, 113, 0.45)', blush: 'rgba(244, 114, 182, 0.5)' },
    watchdog: { core: '#f87171', aura: 'rgba(248, 113, 113, 0.45)', blush: 'rgba(244, 114, 182, 0.5)' },
    bridge: { core: '#fbbf24', aura: 'rgba(251, 191, 36, 0.45)', blush: 'rgba(244, 114, 182, 0.5)' },
    devops: { core: '#2dd4bf', aura: 'rgba(45, 212, 191, 0.45)', blush: 'rgba(244, 114, 182, 0.5)' },
    default: { core: '#818cf8', aura: 'rgba(129, 140, 248, 0.45)', blush: 'rgba(244, 114, 182, 0.5)' }
  };

  function getPersonaColors(type) {
    return PERSONA_COLORS[type] || PERSONA_COLORS.default;
  }

  class PikoAtelierStage {
    constructor(container) {
      this.container = container;
      this.canvas = null;
      this.ctx = null;
      this.overlay = null;
      this.tickerMsg = null;
      this.clusterBadge = null;
      this.modeToggleBtn = null;
      this.celebrationStars = [];
      this.thoughtSparkles = [];
      this.lastMouseX = 0;
      this.lastMouseY = 0;
      this.animId = null;

      this.width = 460;
      this.height = 310;
      this.dpr = window.devicePixelRatio || 1;
      this.mood = 'idle';
      this.time = 0;
      this.hoveredEntity = null;
      this.activeInspection = null;

      // Default layout mode: 'team' (orbiting around Piko)
      this.layoutMode = 'team';

      this.pikoBlink = 0;
      this.supervisionCycleTimer = 0;

      // Real dynamic data
      this.isLiveRun = false;
      this.activePlanIndex = 0;
      this.plans = [];
      this.agents = [];

      this.initDom();
      this.initEntities();
      this.renderPlanTabs();
      this.renderPlanStepper();
      this.bindEvents();
      this.startLoop();
    }

    initDom() {
      this.canvas = this.container.querySelector('#constellation-canvas');
      this.overlay = this.container.querySelector('#constellation-overlay');
      this.tickerMsg = this.container.querySelector('#ticker-message');
      this.clusterBadge = this.container.querySelector('#stage-cluster-badge');
      this.modeToggleBtn = this.container.querySelector('#stage-toggle-cluster-btn');

      if (this.modeToggleBtn) {
        this.modeToggleBtn.textContent = '👥 Piko & Ajanlar';
        this.modeToggleBtn.title = 'Ajan Dizilimini Değiştir';
      }
      if (this.clusterBadge) {
        this.clusterBadge.textContent = 'Piko & Ajan Ekibi';
      }

      if (this.canvas) {
        this.ctx = this.canvas.getContext('2d');
        this.resize();
      }
    }

    resize() {
      if (!this.canvas) return;
      const rect = this.canvas.parentElement.getBoundingClientRect();
      this.width = rect.width || 460;
      this.height = rect.height || 310;
      this.dpr = window.devicePixelRatio || 1;

      this.canvas.width = this.width * this.dpr;
      this.canvas.height = this.height * this.dpr;
      this.ctx.setTransform(1, 0, 0, 1, 0, 0);
      this.ctx.scale(this.dpr, this.dpr);

      this.updatePositions();
    }

    initEntities() {
      // Central Cute Piko Core
      this.piko = {
        id: 'piko-core',
        name: 'Piko',
        role: 'Merkezi Orkestratör',
        x: this.width * 0.5,
        y: this.height * 0.5,
        radius: 26,
        color: COLORS.piko,
        status: 'Dinliyor'
      };

      // Real sub-agents are populated dynamically via updateState()
      this.agents = [];
      this.updatePositions();
    }

    updatePositions() {
      if (!this.piko) return;

      const count = this.agents.length;

      if (count === 0 || this.layoutMode === 'team' || this.plans.length <= 1) {
        // Mode 1: Central Piko with sub-agents orbiting around
        this.piko.x = this.width * 0.5;
        this.piko.y = this.height * 0.5;

        if (count > 0) {
          const rx = Math.min(155, Math.max(75, this.width * 0.36));
          const ry = Math.min(95, Math.max(55, this.height * 0.34));
          const bubbleRadius = count > 8 ? 10.5 : (count > 5 ? 12.5 : 14.5);

          this.agents.forEach((ag, i) => {
            const angle = (i / count) * Math.PI * 2 - Math.PI / 2;
            ag.x = this.piko.x + Math.cos(angle) * rx;
            ag.y = this.piko.y + Math.sin(angle) * ry;
            ag.radius = bubbleRadius;
          });
        }
      } else {
        // Mode 2: Multi-Plan Workflow Clusters
        this.piko.x = this.width * 0.5;
        this.piko.y = 48;

        const planCount = Math.max(1, this.plans.length);
        const podWidth = this.width / planCount;

        this.plans.forEach((plan, pIdx) => {
          const podCenterX = podWidth * (pIdx + 0.5);
          const podCenterY = this.height * 0.56;
          const planAgents = this.agents.filter(a => a.planId === plan.id);
          const pAgentCount = planAgents.length;

          planAgents.forEach((ag, aIdx) => {
            if (pAgentCount === 1) {
              ag.x = podCenterX;
              ag.y = podCenterY;
            } else {
              const span = Math.min(podWidth * 0.75, (pAgentCount - 1) * 32);
              const startX = podCenterX - span / 2;
              ag.x = startX + (aIdx / (pAgentCount - 1)) * span;
              ag.y = podCenterY + (aIdx % 2 === 1 ? 12 : -12);
            }
            ag.radius = 12;
          });
        });
      }
    }

    renderPlanTabs() {
      const tracker = document.querySelector('.workbench-plan-tracker');
      if (!tracker) return;

      let tabsRow = tracker.querySelector('.plan-tabs-row');
      if (!tabsRow) {
        tabsRow = document.createElement('div');
        tabsRow.className = 'plan-tabs-row';
        tracker.insertBefore(tabsRow, tracker.firstChild);
      }

      tabsRow.innerHTML = '';
      if (!this.plans || this.plans.length <= 1) {
        tabsRow.style.display = 'none';
        return;
      }

      tabsRow.style.display = 'flex';
      this.plans.forEach((plan, idx) => {
        const btn = document.createElement('button');
        btn.type = 'button';
        btn.className = `plan-tab-btn ${idx === this.activePlanIndex ? 'active' : ''}`;
        btn.innerHTML = `<span class="plan-tab-dot" style="background:${plan.color || '#38bdf8'}"></span> ${plan.tabName || `Plan ${idx + 1}`}`;
        btn.addEventListener('click', () => {
          this.switchPlan(idx);
        });
        tabsRow.appendChild(btn);
      });
    }

    switchPlan(index) {
      this.activePlanIndex = index;
      const plan = this.plans[index];
      if (!plan) return;

      const buttons = document.querySelectorAll('.plan-tab-btn');
      buttons.forEach((b, i) => b.classList.toggle('active', i === index));

      this.renderPlanStepper();
      this.setTicker(`◈ Piko: ${plan.title} (${plan.badge})`);
    }

    renderPlanStepper() {
      const titleEl = document.getElementById('plan-tracker-title');
      const goalEl = document.getElementById('plan-current-goal');
      const stepPill = document.getElementById('plan-step-pill');
      const fill = document.getElementById('stepper-progress-fill');
      const nodesContainer = document.getElementById('stepper-nodes');

      if (!this.plans || this.plans.length === 0 || !this.isLiveRun) {
        if (titleEl) titleEl.textContent = 'Piko Hazır • Yeni Sohbet';
        if (stepPill) {
          stepPill.textContent = 'Dinlemede';
          stepPill.className = 'step-pill';
        }
        if (goalEl) goalEl.textContent = 'Sohbet alanından talimatınızı yazın; Piko iş akışını planlayacaktır.';
        if (fill) fill.style.width = '0%';
        if (nodesContainer) nodesContainer.innerHTML = '';
        return;
      }

      const plan = this.plans[this.activePlanIndex] || this.plans[0];
      if (!plan) return;

      if (titleEl) titleEl.textContent = plan.title;
      if (stepPill) {
        stepPill.textContent = plan.badge;
        stepPill.className = `step-pill ${plan.isCompleted ? 'step-pill-completed' : (plan.isFailed ? 'step-pill-failed' : 'step-pill-running')}`;
      }

      const steps = plan.steps || [];
      const total = Math.max(1, steps.length);
      const completedSteps = steps.filter(s => s.status === 'completed');
      const activeStep = steps.find(s => s.status === 'active');

      if (goalEl) {
        if (plan.isCompleted) {
          goalEl.textContent = `✓ Tüm ${total} adım başarıyla tamamlandı.`;
        } else if (plan.isFailed) {
          goalEl.textContent = `✕ İş akışında hata oluştu. Müdahale bekleniyor.`;
        } else if (activeStep) {
          goalEl.textContent = `⚡ İşlemde: ${activeStep.name} (${activeStep.agentName || activeStep.persona})`;
        } else {
          goalEl.textContent = `${completedSteps.length} / ${total} adım tamamlandı.`;
        }
      }

      const progressPercent = plan.isCompleted
        ? 100
        : (plan.isFailed
            ? Math.round((completedSteps.length / total) * 100)
            : Math.round(((completedSteps.length + (activeStep ? 0.5 : 0)) / total) * 100));
      if (fill) fill.style.width = `${progressPercent}%`;

      if (nodesContainer) {
        nodesContainer.innerHTML = '';
        steps.forEach((step, idx) => {
          const isDone = step.status === 'completed';
          const isActive = step.status === 'active';
          const isFailed = step.status === 'failed';

          const node = document.createElement('div');
          node.className = `step-node ${isDone ? 'completed' : ''} ${isActive ? 'active' : ''} ${isFailed ? 'failed' : 'pending'}`;
          node.title = `${step.name} (${step.agentName || step.persona})`;

          const bubble = document.createElement('div');
          bubble.className = 'step-bubble';
          bubble.textContent = isDone ? '✓' : (isFailed ? '✕' : (idx + 1));

          const label = document.createElement('div');
          label.className = 'step-node-label';
          label.textContent = step.name;

          node.append(bubble, label);
          node.addEventListener('click', () => {
            this.setTicker(`Adım ${idx + 1}: ${step.name} (${step.agentName || step.persona}) — Durum: ${isDone ? 'Tamamlandı' : (isActive ? 'Yürütülüyor' : (isFailed ? 'Başarısız' : 'Bekliyor'))}`);
            if (this.agents[idx]) {
              this.activeInspection = this.agents[idx].id;
              this.updateLiveCard(this.agents[idx]);
            }
          });

          nodesContainer.append(node);
        });
      }
    }

    bindEvents() {
      window.addEventListener('resize', () => this.resize());

      if (this.modeToggleBtn) {
        this.modeToggleBtn.addEventListener('click', () => {
          this.layoutMode = this.layoutMode === 'team' ? 'workflows' : 'team';
          this.modeToggleBtn.textContent = this.layoutMode === 'team' ? '👥 Piko & Ajanlar' : '⑂ İş Akışları';
          this.modeToggleBtn.title = this.layoutMode === 'team'
            ? 'Şu an: Piko & Ajanlar (Tek Takım). Tıklayın: Kümeler'
            : 'Şu an: Kümeler. Tıklayın: Tek Takım';
          if (this.clusterBadge) {
            this.clusterBadge.textContent = this.layoutMode === 'team'
              ? 'Piko & Ajan Ekibi'
              : 'İş Akışı Kümeleri';
          }
          this.updatePositions();
        });
      }

      if (this.canvas) {
        this.canvas.addEventListener('mousemove', e => this.onMouseMove(e));
        this.canvas.addEventListener('mouseleave', () => this.onMouseLeave());
        this.canvas.addEventListener('click', () => this.onClick());
      }
    }

    onMouseMove(e) {
      const rect = this.canvas.getBoundingClientRect();
      const mouseX = e.clientX - rect.left;
      const mouseY = e.clientY - rect.top;
      this.lastMouseX = mouseX;
      this.lastMouseY = mouseY;

      let found = null;
      for (const ag of this.agents) {
        const d = Math.hypot(mouseX - ag.x, mouseY - ag.y);
        if (d <= ag.radius + 6) {
          found = { type: 'agent', entity: ag };
          break;
        }
      }

      if (!found && this.piko) {
        const dPiko = Math.hypot(mouseX - this.piko.x, mouseY - this.piko.y);
        if (dPiko <= this.piko.radius + 8) {
          found = { type: 'piko', entity: this.piko };
        }
      }

      this.hoveredEntity = found;
      this.updateTooltip(mouseX, mouseY, found);
    }

    onMouseLeave() {
      this.hoveredEntity = null;
      if (this.overlay) this.overlay.innerHTML = '';
    }

    onClick() {
      if (this.hoveredEntity) {
        const ent = this.hoveredEntity.entity;
        if (ent.id !== 'piko-core') {
          this.activeInspection = ent.id;
          this.updateLiveCard(ent);
          this.setTicker(`◈ Piko odaklandı: ${ent.name} (${ent.model}) — ${ent.workflow}`);
        } else {
          this.updateLiveCardPiko();
          this.setTicker(`◈ Piko: Tüm sistem ve alt ajanlar senkronize.`);
        }
      }
    }

    setMood(mood, tickerText = null) {
      this.mood = mood;
      if (tickerText) {
        this.setTicker(`◈ Piko: ${tickerText}`);
      } else {
        const moodMessages = {
          idle: 'Tüm ajanlar hazır ve dinlemede.',
          listening: 'Sizi dinliyorum...',
          thinking: 'Talimatınızı düşünüyorum ve plan hazırlıyorum...',
          speaking: 'Sohbetten yanıt veriyorum...',
          working: 'İş akışı yürütülüyor, alt ajanlar çalışıyor.',
          waiting_decision: 'Onay kapısı aktif. İncelemenizi bekliyorum!',
          celebrating: 'Tebrikler! İş başarıyla tamamlandı.',
          worried: 'Bir aksaklık tespit edildi, denetliyorum.'
        };
        if (moodMessages[mood]) this.setTicker(`◈ Piko: ${moodMessages[mood]}`);
      }

      if (mood === 'celebrating') {
        if (this.piko) this.spawnCelebration(this.piko.x, this.piko.y, '#fbbf24');
      } else if (mood === 'thinking') {
        if (this.piko) this.spawnThought(this.piko.x, this.piko.y - this.piko.radius - 12);
      }
    }

    spawnThought(x, y) {
      for (let i = 0; i < 8; i++) {
        this.thoughtSparkles.push({
          x: x + (Math.random() - 0.5) * 20,
          y: y,
          vx: (Math.random() - 0.5) * 1.5,
          vy: -1.0 - Math.random() * 1.8,
          size: 1.5 + Math.random() * 2.5,
          color: Math.random() > 0.5 ? '#38bdf8' : '#818cf8',
          life: 1.0,
          decay: 0.02 + Math.random() * 0.02
        });
      }
    }

    spawnCelebration(x, y, color = '#fbbf24') {
      for (let i = 0; i < 24; i++) {
        const angle = Math.random() * Math.PI * 2;
        const speed = 1.2 + Math.random() * 3.8;
        this.celebrationStars.push({
          x: x,
          y: y,
          vx: Math.cos(angle) * speed,
          vy: Math.sin(angle) * speed,
          size: 2.0 + Math.random() * 2.5,
          color: color,
          life: 1.0,
          decay: 0.015 + Math.random() * 0.025
        });
      }
    }

    updateTooltip(x, y, target) {
      if (!this.overlay) return;
      if (!target) {
        this.overlay.innerHTML = '';
        return;
      }

      const ent = target.entity;
      this.overlay.innerHTML = `
        <div class="agent-tooltip-card" style="left: ${x}px; top: ${y}px;">
          <div class="agent-tooltip-head">
            <span class="agent-tooltip-name">${ent.name}</span>
            <span class="agent-tooltip-role">${ent.status}</span>
          </div>
          <div style="font-size:10px; color:#cbd5e1; margin-bottom:4px;">${ent.role}</div>
          <div style="font-size:9px; color:#38bdf8;">${ent.workflow || 'Merkezi Orkestratör'}</div>
          <div style="font-size:9px; color:#94a3b8; margin-top:2px;">${ent.model || 'Piko Core'}</div>
        </div>
      `;
    }

    setTicker(msg) {
      if (this.tickerMsg) this.tickerMsg.textContent = msg;
    }

    updateLiveCardPiko() {
      const nameEl = document.getElementById('live-card-agent-name');
      const modelEl = document.getElementById('live-card-agent-model');
      const actionEl = document.getElementById('live-card-action');
      const statusEl = document.getElementById('live-card-status');
      const avatarEl = document.getElementById('live-card-avatar');

      if (nameEl) nameEl.textContent = 'Piko (Merkezi Orkestratör)';
      if (modelEl) modelEl.textContent = 'Canlı Sistem • Hazır';
      if (actionEl) actionEl.textContent = 'Yeni talimatınız bekleniyor...';
      if (statusEl) statusEl.textContent = 'Dinliyor';
      if (avatarEl) avatarEl.textContent = '✨';
    }

    updateLiveCard(agent) {
      if (!agent) {
        this.updateLiveCardPiko();
        return;
      }
      const nameEl = document.getElementById('live-card-agent-name');
      const modelEl = document.getElementById('live-card-agent-model');
      const actionEl = document.getElementById('live-card-action');
      const statusEl = document.getElementById('live-card-status');
      const avatarEl = document.getElementById('live-card-avatar');

      if (nameEl) nameEl.textContent = agent.name;
      if (modelEl) modelEl.textContent = `${agent.model} • ${agent.workflow}`;
      if (actionEl) actionEl.textContent = agent.currentFile || agent.role || agent.status;
      if (statusEl) statusEl.textContent = agent.status;
      if (avatarEl) {
        const t = (agent.type || '').toLowerCase();
        avatarEl.textContent = t.includes('firebase') ? '🔥' :
                               t.includes('architect') || t.includes('mimar') ? '📐' :
                               t.includes('qa') || t.includes('test') || t.includes('verifier') ? '🛡️' :
                               t.includes('security') || t.includes('guard') ? '🔒' :
                               t.includes('bridge') || t.includes('mobil') ? '🌉' :
                               t.includes('devops') ? '⚡' : '💻';
      }
    }

    startLoop() {
      const loop = () => {
        this.time += 0.016;

        // Cute natural blinking
        if (Math.sin(this.time * 1.6) > 0.985) this.pikoBlink = 1;
        else this.pikoBlink = 0;

        // Spawn thought sparkles while thinking
        if (this.mood === 'thinking' && Math.random() < 0.12 && this.piko) {
          this.spawnThought(this.piko.x, this.piko.y - this.piko.radius - 12);
        }

        this.draw();
        this.animId = requestAnimationFrame(loop);
      };
      this.animId = requestAnimationFrame(loop);
    }

    draw() {
      if (!this.ctx) return;
      const ctx = this.ctx;
      ctx.clearRect(0, 0, this.width, this.height);

      // 1. Draw neural links & energy flow
      this.drawLinks(ctx);

      // 2. Draw Workflow Pod Envelopes ONLY if multiple plans exist in workflows mode
      if (this.layoutMode === 'workflows' && this.plans.length > 1) {
        this.drawWorkflowPods(ctx);
      }

      // 3. Draw Sub-Agents
      this.agents.forEach((ag, idx) => {
        this.drawCuteAgent(ctx, ag, idx);
      });

      // 4. Draw Cute Central Piko
      this.drawCutePiko(ctx);

      // 5. Draw supervision beam
      this.drawSupervisionBeam(ctx);

      // 6. Draw celebration sparkles
      this.drawCelebrationStars(ctx);

      // 7. Draw thought sparkles
      this.drawThoughtSparkles(ctx);
    }

    drawThoughtSparkles(ctx) {
      if (!this.thoughtSparkles || !this.thoughtSparkles.length) return;
      ctx.save();
      for (let i = this.thoughtSparkles.length - 1; i >= 0; i--) {
        const s = this.thoughtSparkles[i];
        s.x += s.vx;
        s.y += s.vy;
        s.life -= s.decay;

        if (s.life <= 0) {
          this.thoughtSparkles.splice(i, 1);
          continue;
        }

        ctx.fillStyle = s.color;
        ctx.globalAlpha = Math.max(0, s.life);
        ctx.shadowColor = s.color;
        ctx.shadowBlur = 10;
        ctx.beginPath();
        ctx.arc(s.x, s.y, s.size * Math.max(0.2, s.life), 0, Math.PI * 2);
        ctx.fill();
      }
      ctx.restore();
    }

    drawCelebrationStars(ctx) {
      if (!this.celebrationStars || !this.celebrationStars.length) return;
      ctx.save();
      for (let i = this.celebrationStars.length - 1; i >= 0; i--) {
        const s = this.celebrationStars[i];
        s.x += s.vx;
        s.y += s.vy;
        s.vy += 0.04;
        s.life -= s.decay;

        if (s.life <= 0) {
          this.celebrationStars.splice(i, 1);
          continue;
        }

        ctx.fillStyle = s.color;
        ctx.globalAlpha = Math.max(0, s.life);
        ctx.shadowColor = s.color;
        ctx.shadowBlur = 8;
        ctx.beginPath();
        ctx.arc(s.x, s.y, s.size * Math.max(0.2, s.life), 0, Math.PI * 2);
        ctx.fill();
      }
      ctx.restore();
    }

    drawLinks(ctx) {
      if (!this.piko || !this.agents.length) return;
      ctx.save();
      const piko = this.piko;

      this.agents.forEach((ag, i) => {
        ctx.beginPath();
        ctx.strokeStyle = ag.stepStatus === 'completed' ? 'rgba(52, 211, 153, 0.28)' :
                          ag.stepStatus === 'active' ? 'rgba(56, 189, 248, 0.5)' :
                          'rgba(148, 163, 184, 0.15)';
        ctx.lineWidth = ag.stepStatus === 'active' ? 1.8 : 1.2;
        ctx.setLineDash([3, 4]);
        ctx.lineDashOffset = -this.time * 8;

        ctx.moveTo(piko.x, piko.y);
        ctx.lineTo(ag.x, ag.y);
        ctx.stroke();

        // Traveling energy particle
        const t = (this.time * 0.45 + i * 0.15) % 1;
        const px = piko.x + (ag.x - piko.x) * t;
        const py = piko.y + (ag.y - piko.y) * t;

        ctx.beginPath();
        ctx.fillStyle = ag.color.core;
        ctx.shadowColor = ag.color.core;
        ctx.shadowBlur = ag.stepStatus === 'active' ? 8 : 4;
        ctx.arc(px, py, ag.stepStatus === 'active' ? 2.4 : 1.8, 0, Math.PI * 2);
        ctx.fill();
        ctx.shadowBlur = 0;
      });

      ctx.restore();
    }

    drawWorkflowPods(ctx) {
      ctx.save();
      const planCount = this.plans.length;
      const podWidth = this.width / planCount;

      this.plans.forEach((plan, idx) => {
        const isPlanActive = idx === this.activePlanIndex;
        const podX = podWidth * (idx + 0.5);
        const podY = this.height * 0.56;

        ctx.fillStyle = isPlanActive ? 'rgba(30, 41, 59, 0.4)' : 'rgba(15, 23, 42, 0.3)';
        ctx.strokeStyle = isPlanActive ? (plan.color || '#38bdf8') : 'rgba(255, 255, 255, 0.12)';
        ctx.lineWidth = isPlanActive ? 1.6 : 1;
        ctx.setLineDash([4, 4]);
        ctx.beginPath();
        ctx.roundRect(podX - podWidth * 0.42, podY - 40, podWidth * 0.84, 80, 10);
        ctx.fill();
        ctx.stroke();

        ctx.font = '600 9.5px Inter, sans-serif';
        ctx.fillStyle = isPlanActive ? (plan.color || '#38bdf8') : '#94a3b8';
        ctx.textAlign = 'center';
        ctx.fillText(plan.tabName || plan.title, podX, podY - 48);
      });

      ctx.restore();
    }

    drawCutePiko(ctx) {
      if (!this.piko) return;
      ctx.save();
      const p = this.piko;
      const t = this.time;
      const mood = this.mood || 'idle';

      let floatSpeed = 2.5;
      let floatAmp = 3;
      if (mood === 'listening') { floatSpeed = 3.5; floatAmp = 1.8; }
      else if (mood === 'thinking') { floatSpeed = 4.0; floatAmp = 4; }
      else if (mood === 'speaking') { floatSpeed = 5.2; floatAmp = 2.5; }
      else if (mood === 'celebrating') { floatSpeed = 7.0; floatAmp = 6; }

      const floatY = Math.sin(t * floatSpeed) * floatAmp;
      const headTilt = mood === 'listening' ? -0.08 : (mood === 'thinking' ? 0.05 : 0);

      const cx = p.x;
      const cy = p.y + floatY;

      // Aura
      let auraPulse = Math.sin(t * (mood === 'thinking' ? 6 : 3)) * 4;
      let auraRadius = p.radius * 1.8 + auraPulse;
      if (mood === 'thinking') auraRadius += 6;

      const auraGrad = ctx.createRadialGradient(cx, cy, p.radius * 0.4, cx, cy, auraRadius);
      auraGrad.addColorStop(0, p.color.aura);
      auraGrad.addColorStop(0.7, 'rgba(56, 189, 248, 0.15)');
      auraGrad.addColorStop(1, 'rgba(0, 0, 0, 0)');

      ctx.fillStyle = auraGrad;
      ctx.beginPath();
      ctx.arc(cx, cy, auraRadius, 0, Math.PI * 2);
      ctx.fill();

      // Energy Orbit Ring
      ctx.strokeStyle = mood === 'thinking' ? 'rgba(192, 132, 252, 0.7)' : 'rgba(56, 189, 248, 0.45)';
      ctx.lineWidth = 1.4;
      ctx.setLineDash([4, 6]);
      ctx.lineDashOffset = t * 14;
      ctx.beginPath();
      ctx.arc(cx, cy, p.radius + 6 + Math.sin(t * 3) * 2, 0, Math.PI * 2);
      ctx.stroke();
      ctx.setLineDash([]);

      // Head Tilt Transformation
      ctx.translate(cx, cy);
      ctx.rotate(headTilt);
      ctx.translate(-cx, -cy);

      // Cute Antenna
      const antH = 13 + Math.sin(t * 4) * 2;
      const tipX = cx + Math.sin(t * 2.5) * 3;
      const tipY = cy - p.radius - antH;

      ctx.beginPath();
      ctx.strokeStyle = '#38bdf8';
      ctx.lineWidth = 2.5;
      ctx.lineCap = 'round';
      ctx.moveTo(cx, cy - p.radius);
      ctx.quadraticCurveTo(cx, cy - p.radius - 8, tipX, tipY);
      ctx.stroke();

      // Antenna Star Gem
      ctx.fillStyle = mood === 'thinking' ? '#c084fc' : '#fbbf24';
      ctx.shadowColor = ctx.fillStyle;
      ctx.shadowBlur = 10;
      ctx.beginPath();
      ctx.arc(tipX, tipY, 4, 0, Math.PI * 2);
      ctx.fill();
      ctx.shadowBlur = 0;

      // Spirit Body
      const bodyGrad = ctx.createRadialGradient(cx - 5, cy - 6, 3, cx, cy, p.radius);
      bodyGrad.addColorStop(0, '#ffffff');
      bodyGrad.addColorStop(0.45, '#38bdf8');
      bodyGrad.addColorStop(1, '#0284c7');

      ctx.fillStyle = bodyGrad;
      ctx.beginPath();
      ctx.arc(cx, cy, p.radius, 0, Math.PI * 2);
      ctx.fill();

      // Glass Edge
      ctx.strokeStyle = '#ffffff';
      ctx.lineWidth = 1.8;
      ctx.stroke();

      // Cheeks (Blush)
      ctx.fillStyle = p.color.blush;
      ctx.beginPath();
      ctx.arc(cx - 11, cy + 5, 4.2, 0, Math.PI * 2);
      ctx.arc(cx + 11, cy + 5, 4.2, 0, Math.PI * 2);
      ctx.fill();

      // Eyes
      const eyeDist = 8;
      const eyeY = cy - 1;
      let lookOffsetX = mood === 'listening' ? -2.2 : (mood === 'thinking' ? 1.5 : 0);
      let lookOffsetY = mood === 'thinking' ? -2.5 : 0;

      if (this.pikoBlink) {
        ctx.strokeStyle = '#082f49';
        ctx.lineWidth = 2;
        ctx.beginPath();
        ctx.moveTo(cx - eyeDist - 3, eyeY);
        ctx.lineTo(cx - eyeDist + 3, eyeY);
        ctx.moveTo(cx + eyeDist - 3, eyeY);
        ctx.lineTo(cx + eyeDist + 3, eyeY);
        ctx.stroke();
      } else if (mood === 'celebrating') {
        ctx.strokeStyle = '#082f49';
        ctx.lineWidth = 2.4;
        ctx.lineCap = 'round';
        ctx.beginPath();
        ctx.arc(cx - eyeDist, eyeY, 4.5, 1.1 * Math.PI, 1.9 * Math.PI, false);
        ctx.stroke();
        ctx.beginPath();
        ctx.arc(cx + eyeDist, eyeY, 4.5, 1.1 * Math.PI, 1.9 * Math.PI, false);
        ctx.stroke();
      } else {
        ctx.fillStyle = '#082f49';
        ctx.beginPath();
        ctx.ellipse(cx - eyeDist + lookOffsetX, eyeY + lookOffsetY, 3.2, 4.2, 0, 0, Math.PI * 2);
        ctx.ellipse(cx + eyeDist + lookOffsetX, eyeY + lookOffsetY, 3.2, 4.2, 0, 0, Math.PI * 2);
        ctx.fill();

        ctx.fillStyle = '#ffffff';
        ctx.beginPath();
        ctx.arc(cx - eyeDist + lookOffsetX + 1, eyeY + lookOffsetY - 1.5, 1.4, 0, Math.PI * 2);
        ctx.arc(cx + eyeDist + lookOffsetX + 1, eyeY + lookOffsetY - 1.5, 1.4, 0, Math.PI * 2);
        ctx.fill();

        ctx.beginPath();
        ctx.arc(cx - eyeDist + lookOffsetX - 1, eyeY + lookOffsetY + 1.8, 0.8, 0, Math.PI * 2);
        ctx.arc(cx + eyeDist + lookOffsetX - 1, eyeY + lookOffsetY + 1.8, 0.8, 0, Math.PI * 2);
        ctx.fill();
      }

      // Mouth
      ctx.strokeStyle = '#082f49';
      ctx.fillStyle = '#082f49';
      ctx.lineWidth = 1.6;
      ctx.lineCap = 'round';

      if (mood === 'speaking') {
        const mouthOpen = 2 + Math.abs(Math.sin(t * 15)) * 3.5;
        ctx.beginPath();
        ctx.ellipse(cx, cy + 6, 2.5, mouthOpen, 0, 0, Math.PI * 2);
        ctx.fill();
      } else if (mood === 'listening') {
        ctx.beginPath();
        ctx.ellipse(cx - 0.5, cy + 5.5, 1.6, 2.0, 0, 0, Math.PI * 2);
        ctx.stroke();
      } else if (mood === 'celebrating') {
        ctx.beginPath();
        ctx.arc(cx, cy + 5, 4.2, 0.1 * Math.PI, 0.9 * Math.PI, false);
        ctx.stroke();
      } else {
        ctx.beginPath();
        ctx.arc(cx, cy + 5.5, 3.2, 0.1 * Math.PI, 0.9 * Math.PI, false);
        ctx.stroke();
      }

      // Name Label
      ctx.font = '700 11px Inter, sans-serif';
      ctx.fillStyle = '#f8fafc';
      ctx.textAlign = 'center';
      const moodLabel = mood === 'listening' ? 'PİKO (Dinliyor)' :
                         mood === 'thinking' ? 'PİKO (Düşünüyor)' :
                         mood === 'speaking' ? 'PİKO (Konuşuyor)' :
                         mood === 'working' ? 'PİKO (Yönetiyor)' :
                         mood === 'waiting_decision' ? 'PİKO (Onay Bekliyor)' :
                         mood === 'celebrating' ? 'PİKO (Kutluyor)' : 'PİKO';
      ctx.fillText(moodLabel, cx, cy + p.radius + 14);

      ctx.restore();
    }

    drawCuteAgent(ctx, ag, idx) {
      ctx.save();
      const t = this.time;
      const isInspected = this.activeInspection === ag.id;
      const isHovered = this.hoveredEntity?.entity?.id === ag.id;
      const isDone = ag.stepStatus === 'completed';
      const isActive = ag.stepStatus === 'active';
      const isFailed = ag.stepStatus === 'failed';

      const floatY = Math.sin(t * 2.2 + idx * 1.2) * 2.2;
      const cx = ag.x;
      const cy = ag.y + floatY;

      // Aura
      const aRadius = ag.radius * (isInspected || isHovered ? 2.2 : (isActive ? 2.0 : 1.6));
      const aGrad = ctx.createRadialGradient(cx, cy, ag.radius * 0.4, cx, cy, aRadius);
      const auraColor = isDone ? 'rgba(52, 211, 153, 0.45)' : (isFailed ? 'rgba(248, 113, 113, 0.45)' : ag.color.aura);
      aGrad.addColorStop(0, auraColor);
      aGrad.addColorStop(1, 'rgba(0,0,0,0)');
      ctx.fillStyle = aGrad;
      ctx.beginPath();
      ctx.arc(cx, cy, aRadius, 0, Math.PI * 2);
      ctx.fill();

      // Cute Body
      const bGrad = ctx.createRadialGradient(cx - 3, cy - 3, 2, cx, cy, ag.radius);
      bGrad.addColorStop(0, '#ffffff');
      bGrad.addColorStop(0.5, isDone ? '#34d399' : (isFailed ? '#f87171' : ag.color.core));
      bGrad.addColorStop(1, '#0f172a');
      ctx.fillStyle = bGrad;
      ctx.beginPath();
      ctx.arc(cx, cy, ag.radius, 0, Math.PI * 2);
      ctx.fill();

      ctx.strokeStyle = isInspected ? '#fbbf24' : (isActive ? '#38bdf8' : (isHovered ? '#ffffff' : (isDone ? '#34d399' : 'rgba(255,255,255,0.4)')));
      ctx.lineWidth = isInspected ? 2.2 : (isActive ? 2.0 : 1.2);
      ctx.stroke();

      // Cheeks (Blush)
      ctx.fillStyle = ag.color.blush;
      ctx.beginPath();
      ctx.arc(cx - 5, cy + 2.5, 1.8, 0, Math.PI * 2);
      ctx.arc(cx + 5, cy + 2.5, 1.8, 0, Math.PI * 2);
      ctx.fill();

      // Eyes
      const eyeDist = 4;
      const eyeY = cy - 0.5;

      if (isDone) {
        // Happy Anime Eyes (^ ^)
        ctx.strokeStyle = '#0f172a';
        ctx.lineWidth = 1.6;
        ctx.lineCap = 'round';
        ctx.beginPath();
        ctx.arc(cx - eyeDist, eyeY, 2.5, 1.1 * Math.PI, 1.9 * Math.PI, false);
        ctx.stroke();
        ctx.beginPath();
        ctx.arc(cx + eyeDist, eyeY, 2.5, 1.1 * Math.PI, 1.9 * Math.PI, false);
        ctx.stroke();
      } else {
        // Round Eyes
        ctx.fillStyle = '#0f172a';
        ctx.beginPath();
        ctx.ellipse(cx - eyeDist, eyeY, 1.4, 1.9, 0, 0, Math.PI * 2);
        ctx.ellipse(cx + eyeDist, eyeY, 1.4, 1.9, 0, 0, Math.PI * 2);
        ctx.fill();

        ctx.fillStyle = '#ffffff';
        ctx.beginPath();
        ctx.arc(cx - eyeDist + 0.4, eyeY - 0.5, 0.7, 0, Math.PI * 2);
        ctx.arc(cx + eyeDist + 0.4, eyeY - 0.5, 0.7, 0, Math.PI * 2);
        ctx.fill();
      }

      // Smile
      ctx.strokeStyle = '#0f172a';
      ctx.lineWidth = 1.2;
      ctx.beginPath();
      ctx.arc(cx, cy + 2.5, 1.5, 0.1 * Math.PI, 0.9 * Math.PI, false);
      ctx.stroke();

      // Step Number Badge (above spirit)
      const badgeText = isDone ? '✓' : `#${idx + 1}`;
      ctx.font = '700 8.5px Inter, sans-serif';
      ctx.fillStyle = isDone ? '#34d399' : (isActive ? '#38bdf8' : '#94a3b8');
      ctx.textAlign = 'center';
      ctx.fillText(badgeText, cx, cy - ag.radius - 4);

      // Name label (below spirit)
      ctx.font = '600 9px Inter, sans-serif';
      ctx.fillStyle = isInspected ? '#fef08a' : (isDone ? '#34d399' : '#cbd5e1');
      ctx.textAlign = 'center';
      const displayName = ag.name.length > 14 ? ag.name.slice(0, 13) + '…' : ag.name;
      ctx.fillText(displayName, cx, cy + ag.radius + 11);

      ctx.restore();
    }

    drawSupervisionBeam(ctx) {
      if (!this.activeInspection || !this.piko) return;
      const targetAgent = this.agents.find(a => a.id === this.activeInspection);
      if (!targetAgent) return;

      ctx.save();
      const piko = this.piko;

      ctx.beginPath();
      ctx.strokeStyle = COLORS.supervisionBeam;
      ctx.lineWidth = 2.0;
      ctx.shadowColor = '#fbbf24';
      ctx.shadowBlur = 8;
      ctx.moveTo(piko.x, piko.y);
      ctx.lineTo(targetAgent.x, targetAgent.y);
      ctx.stroke();

      const midX = (piko.x + targetAgent.x) / 2;
      const midY = (piko.y + targetAgent.y) / 2 - 8;

      const tagText = `◈ Piko ➜ ${targetAgent.name}`;
      ctx.font = '600 8.5px Inter, sans-serif';
      const textWidth = ctx.measureText(tagText).width + 10;

      ctx.fillStyle = 'rgba(15, 23, 42, 0.92)';
      ctx.strokeStyle = '#fbbf24';
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.roundRect(midX - textWidth / 2, midY - 8, textWidth, 16, 4);
      ctx.fill();
      ctx.stroke();

      ctx.fillStyle = '#fef08a';
      ctx.textAlign = 'center';
      ctx.fillText(tagText, midX, midY + 4);

      ctx.restore();
    }

    // Bridge for external state from app.js
    updateState({ run, job, state }) {
      if (!run) {
        this.isLiveRun = false;
        this.plans = [];
        this.agents = [];
        this.activePlanIndex = 0;
        this.activeInspection = null;
        this.setMood('idle', 'Tüm ajanlar hazır ve dinlemede.');
        if (this.clusterBadge) this.clusterBadge.textContent = 'Piko & Ajan Ekibi';
        this.renderPlanTabs();
        this.renderPlanStepper();
        this.updatePositions();
        this.updateLiveCardPiko();
        return;
      }

      this.isLiveRun = true;
      const steps = run.plan?.steps || [];
      const results = run.results || {};
      const currentSteps = Array.isArray(run.current_steps) ? run.current_steps : [];
      const isRunCompleted = run.status === 'completed' || run.status === 'success';
      const isRunFailed = run.status === 'failed';
      const isRunRunning = run.status === 'running' || run.status === 'planning';

      // Map real steps
      const mappedSteps = steps.map((st, i) => {
        const res = results[st.id];
        let status = 'pending';
        if (currentSteps.includes(st.id)) {
          status = 'active';
        } else if (res?.status === 'completed' || res?.status === 'success' || (isRunCompleted && res?.status !== 'failed')) {
          status = 'completed';
        } else if (res?.status === 'failed') {
          status = 'failed';
        } else if (currentSteps.includes(st.id)) {
          status = 'active';
        }

        const personaKey = st.persona || (st.model ? st.model.split('/')[0] : 'developer');
        return {
          id: st.id || `step_${i + 1}`,
          index: i,
          name: st.title || st.action || `Adım ${i + 1}`,
          persona: personaKey,
          agentName: PERSONA_LABELS[personaKey] || personaKey,
          status: status,
          detail: res?.output ? 'Çıktı üretildi' : (st.description || st.title || '')
        };
      });

      const total = mappedSteps.length;
      const completedCount = mappedSteps.filter(s => s.status === 'completed').length;
      const percent = total ? Math.round((completedCount / total) * 100) : 0;

      const planTitle = run.title || run.prompt?.slice(0, 45) || `Görev: ${run.id.slice(0, 8)}`;
      const planBadge = isRunCompleted ? '✓ %100 (Tamamlandı)' :
                        isRunFailed ? '✕ Başarısız' :
                        `⚡ %${percent} (${completedCount}/${total} Adım)`;

      this.plans = [
        {
          id: run.id,
          tabName: 'Aktif Plan',
          title: planTitle,
          clusterTag: 'Canlı İş Akışı',
          badge: planBadge,
          color: isRunCompleted ? '#34d399' : (isRunFailed ? '#f87171' : '#38bdf8'),
          isCompleted: isRunCompleted,
          isFailed: isRunFailed,
          totalSteps: total,
          steps: mappedSteps
        }
      ];

      // DYNAMIC SUB-AGENTS GENERATED FROM REAL STEPS!
      this.agents = mappedSteps.map((st, i) => {
        const colors = getPersonaColors(st.persona);
        return {
          id: `agent-step-${i}`,
          stepId: st.id,
          stepIndex: i,
          planId: run.id,
          name: `${i + 1}. ${st.agentName}`,
          role: st.name,
          model: st.persona,
          type: st.persona,
          color: colors,
          status: st.status === 'completed' ? 'Tamamlandı' : (st.status === 'active' ? 'Çalışıyor' : (st.status === 'failed' ? 'Başarısız' : 'Bekliyor')),
          stepStatus: st.status,
          currentFile: st.name,
          workflow: `Adım ${i + 1} / ${total}`
        };
      });

      if (this.clusterBadge) {
        this.clusterBadge.textContent = `${total} Plan Adımı (${completedCount}/${total} Tamamlandı)`;
      }

      this.activePlanIndex = 0;
      this.renderPlanTabs();
      this.renderPlanStepper();
      this.updatePositions();

      // Find active or inspected agent
      const activeStep = mappedSteps.find(s => s.status === 'active') || (isRunCompleted ? mappedSteps[mappedSteps.length - 1] : mappedSteps[0]);
      if (activeStep) {
        const ag = this.agents[activeStep.index];
        if (ag) {
          this.activeInspection = ag.id;
          this.updateLiveCard(ag);
        }
        if (isRunCompleted) {
          this.setMood('celebrating', 'Tüm plan adımları başarıyla tamamlandı!');
        } else if (isRunFailed) {
          this.setMood('worried', 'Bir adımda hata oluştu, denetleniyor.');
        } else if (activeStep.status === 'active') {
          this.setMood('working', `"${activeStep.name}" yürütülüyor (${activeStep.agentName})`);
        }
      }
    }

    dispatch(payload) {
      const ev = payload.event || '';
      const reason = payload.reason || '';
      if (ev === 'run.running' || ev === 'run.started') {
        this.setMood('working', reason || 'İş akışı başladı, alt ajanlar görevlendirildi.');
      } else if (ev === 'deliverable.updated' || ev === 'work.waiting') {
        this.setMood('waiting_decision', reason || 'Onay kapısı aktif! Kararınızı bekliyorum.');
      } else if (ev === 'run.completed') {
        this.setMood('celebrating', 'Tüm adımlar başarıyla tamamlandı!');
      } else if (ev === 'work.blocked' || ev === 'run.failed') {
        this.setMood('worried', reason || 'Bir hata oluştu, müdahale bekleniyor.');
      } else if (ev === 'idle') {
        this.setMood('idle', 'Tüm ajanlar hazır ve dinlemede.');
      } else if (ev === 'chat.typing') {
        this.setMood('listening', 'Sizi dinliyorum...');
      } else if (ev === 'chat.thinking') {
        this.setMood('thinking', reason || 'Düşünüyorum...');
      } else if (ev === 'chat.speaking') {
        this.setMood('speaking', reason || 'Piko yanıtlıyor...');
      }
    }
  }

  function initConstellation(container) {
    if (!container) return null;
    const instance = new PikoAtelierStage(container);
    global.PikoConstellation = instance;
    return instance;
  }

  global.initConstellation = initConstellation;

  if (typeof document !== 'undefined') {
    document.addEventListener('DOMContentLoaded', () => {
      const stage = document.getElementById('piko-constellation-stage');
      if (stage) initConstellation(stage);
    });
  }

})(typeof window !== 'undefined' ? window : this);
