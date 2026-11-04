// Focused UI smoke for the work record, deliveries, notifications and view switch.
// Seeds state over the local API, then verifies the browser rendering. No model call.
const path = require('node:path');
const fs = require('node:fs');

function loadPlaywright() {
  const candidates = [
    process.env.PLAYWRIGHT_MODULE,
    path.join(process.env.USERPROFILE || '', '.cache', 'codex-runtimes',
              'codex-primary-runtime', 'dependencies', 'node', 'node_modules', 'playwright'),
    'playwright',
  ].filter(Boolean);
  for (const candidate of candidates) {
    try { return require(candidate); } catch {}
  }
  throw new Error('Playwright modülü bulunamadı. PLAYWRIGHT_MODULE ile yol verin.');
}

const { chromium } = loadPlaywright();
const root = path.resolve(__dirname, '..');
const base = process.env.TEST_URL || 'http://127.0.0.1:8766';
const chromePath = process.env.CHROME_PATH
  || 'C:/Program Files/Google/Chrome/Application/chrome.exe';

(async () => {
  let server, browser;
  const errors = [], failures = [], checks = [];
  const out = path.join(root, 'work', 'visual');
  fs.mkdirSync(out, { recursive: true });
  try {
    const seeded = await fetch(base + '/api/work', {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({
        goal: 'UI smoke için rapor hazırla',
        title: 'UI smoke raporu',
        acceptance: ['Teslim kartı görünür'],
        priority: 'high',
        due_at: new Date(Date.now() + 86_400_000).toISOString(),
        next_action: 'Teslimi incele.',
      }),
    });
    if (!seeded.ok) throw new Error('Seed work failed: ' + seeded.status);
    const work = await seeded.json();
    const deliverableResponse = await fetch(base + `/api/work/${work.id}/deliverables`, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({
        summary: 'Rapor hazır ve doğrulandı.',
        requirements: ['Teslim kartı görünür'],
        evidence: ['pytest: 162 passed'],
        open_points: [],
      }),
    });
    if (!deliverableResponse.ok) throw new Error('Seed deliverable failed: ' + deliverableResponse.status);
    await fetch(base + `/api/work/${work.id}/decisions`, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ question: 'Rapor biçimi doğru mu?' }),
    });

    server = await chromium.launchServer({ headless: true, executablePath: chromePath });
    browser = await chromium.connect(server.wsEndpoint());
    const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
    page.on('pageerror', e => errors.push(e.message));
    page.on('console', e => { if (e.type() === 'error') errors.push(e.text()); });
    page.on('requestfailed', r => failures.push(r.url() + ': ' + r.failure()?.errorText));
    page.on('response', r => { if (r.status() >= 400) failures.push(r.status() + ': ' + r.url()); });

    await page.goto(base);
    await page.waitForLoadState('networkidle');
    await page.locator('#connection.online').waitFor();
    checks.push('bootstrap');

    await page.locator('#view-piko').waitFor();
    await page.locator('#view-piko').click();
    if (!(await page.evaluate(() => document.body.classList.contains('view-piko')))) {
      throw new Error('Piko view class missing');
    }
    await page.locator('#view-dev').click();
    if (!(await page.evaluate(() => document.body.classList.contains('view-developer')))) {
      throw new Error('Developer view class missing');
    }
    await page.locator('#view-piko').click();
    checks.push('view-switch');
    await page.screenshot({ path: path.join(out, 'piko-view.png'), fullPage: true });

    await page.locator('#notification-button').click();
    await page.locator('#notifications-dialog').waitFor({ state: 'visible' });
    await page.locator('.notification-item').first().waitFor();
    await page.locator('.notification-actions button').first().waitFor({ timeout: 10000 });
    const notificationActions = await page.locator('.notification-actions button').allInnerTexts();
    if (!notificationActions.includes('Kabul et')) {
      throw new Error('Notification card missing one-click accept: ' + notificationActions);
    }
    if (!notificationActions.includes('Şunu dene')) {
      throw new Error('Notification card missing "Şunu dene": ' + notificationActions);
    }
    checks.push('notification-one-click:' + notificationActions.length);
    checks.push('notifications-dialog');
    await page.locator('#notif-quiet-start').fill('23:00');
    await page.locator('#notif-quiet-start').dispatchEvent('input');
    await page.locator('#notification-preferences-form button[type=submit]').click();
    await page.waitForTimeout(600);
    await page.locator('#close-notifications').click();
    checks.push('notification-preferences');
    await page.screenshot({ path: path.join(out, 'notifications.png'), fullPage: true });

    const mascot = page.locator('#piko-mascot-stage [data-state]');
    await mascot.waitFor();
    const mascotState = await mascot.getAttribute('data-state');
    if (!mascotState) throw new Error('Mascot state missing');
    if (mascotState !== 'idle') {
      throw new Error('Mascot should start idle, got ' + mascotState);
    }
    checks.push('mascot-initial-' + mascotState);

    const mascotSprite = page.locator('#piko-mascot-stage use');
    if (await mascotSprite.count() === 0) throw new Error('Mascot sprite <use> missing');
    const spriteHref = await mascotSprite.first().getAttribute('href');
    if (spriteHref !== '#piko-idle') throw new Error('Mascot sprite href unexpected: ' + spriteHref);
    checks.push('mascot-sprite-use');

    await page.locator('#prompt').fill('maske denemesi');
    await page.waitForFunction(() =>
      document.querySelector('#piko-mascot-stage [data-state]')?.dataset.state === 'konusuyor');
    checks.push('mascot-typing-konusuyor');
    await page.locator('#prompt').fill('');
    await page.waitForFunction(() =>
      document.querySelector('#piko-mascot-stage [data-state]')?.dataset.state === 'idle');

    const transitions = await page.evaluate(() => {
      const host = document.createElement('div');
      document.body.append(host);
      const mascot = window.initMascot(host, { spriteUrl: '#' });
      const read = () => host.querySelector('[data-state]').dataset.state;
      const initial = read();
      mascot.dispatch({ event: 'run.running', work_id: 'smoke-run' });
      const running = read();
      mascot.dispatch({ event: 'deliverable.updated', work_id: 'smoke-run',
                        deliverable_status: 'delivered', reason: 'Rapor hazır' });
      const delivered = read();
      mascot.dispatch({ event: 'work.blocked', work_id: 'smoke-run',
                        reason: 'Sahte engel' });
      const blocked = read();
      const workId = host.querySelector('[data-state]').dataset.workId;
      const reason = host.querySelector('.piko-mascot-reason').textContent;
      mascot.destroy();
      host.remove();
      return { initial, running, delivered, blocked, workId, reason };
    });
    if (transitions.initial !== 'idle') throw new Error('dispatch fixture did not start idle');
    if (transitions.running !== 'calisiyor') throw new Error('run.running did not map to calisiyor: ' + transitions.running);
    if (transitions.delivered !== 'teslim') throw new Error('delivered did not map to teslim: ' + transitions.delivered);
    if (transitions.blocked !== 'takildi') throw new Error('blocked did not map to takildi: ' + transitions.blocked);
    if (transitions.workId !== 'smoke-run') throw new Error('work id missing on mascot root');
    if (transitions.reason !== 'Sahte engel') throw new Error('blocked reason missing: ' + transitions.reason);
    checks.push('mascot-dispatch-transitions');

    const healthResponse = await fetch(base + '/api/health');
    if (!healthResponse.ok) throw new Error('health endpoint failed: ' + healthResponse.status);
    const health = await healthResponse.json();
    if (!('daemon_alive' in health)) throw new Error('health payload missing daemon_alive');
    checks.push('mascot-health-api');

    const workTab = page.locator('[data-tab="work"]');
    if (await workTab.count() === 0) throw new Error('İş kaydı sekmesi bulunamadı');
    await page.locator('details.inspector-more > summary').click().catch(() => {});
    await workTab.click({ force: true }).catch(() => {});
    await page.waitForTimeout(400);
    if (!(await page.locator('#inspector-content').innerText()).length) {
      throw new Error('Inspector did not render the work tab');
    }
    checks.push('inspector-work-tab');
    await page.screenshot({ path: path.join(out, 'inspector-work.png'), fullPage: true });

    const metrics = await (await fetch(base + '/api/metrics')).json();
    if (!metrics.slots || typeof metrics.slots.max_concurrent_runs !== 'number') {
      throw new Error('Slot metrics missing from /api/metrics');
    }
    if (!metrics.runtime || typeof metrics.runtime.slot_occupancy_percent !== 'number') {
      throw new Error('Runtime occupancy metrics missing from /api/metrics');
    }
    if (!('slot_wait_count' in metrics.slots) || !('slot_wait_avg_seconds' in metrics.runtime)) {
      throw new Error('Queue wait counters missing from /api/metrics');
    }
    checks.push('slot-metrics-api:' + metrics.slots.max_concurrent_runs);

    const bridges = await (await fetch(base + '/api/bridges')).json();
    const office = (bridges.bridges || []).find(item => item.id === 'office');
    if (!office) throw new Error('office pack not registered');
    if ((office.levels || []).join(',') !== 'read,draft,apply') {
      throw new Error('office levels unexpected: ' + office.levels);
    }
    checks.push('bridge-registry:office');

    const officeRequest = {
      title: 'UI smoke ofis raporu',
      sections: [{ heading: 'Özet', body: 'Köprü uçtan uca çalıştı.', bullets: ['kanıt üretildi'] }],
      table_headers: ['Ad', 'Durum'],
      rows: [['rapor', 'bitti']],
      formats: ['md', 'csv'],
    };
    const drafted = await (await fetch(base + '/api/bridges/office/draft', {
      method: 'POST', headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ request: officeRequest }),
    })).json();
    if ((drafted.files || []).length < 2) throw new Error('office draft produced no files');

    const approval = await (await fetch(base + '/api/bridges/office/approve', {
      method: 'POST', headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ request: officeRequest, target: 'work/office-smoke' }),
    })).json();

    const delivered = await (await fetch(base + '/api/bridges/office/deliver', {
      method: 'POST', headers: { 'content-type': 'application/json' },
      body: JSON.stringify({
        request: officeRequest,
        target: 'work/office-smoke',
        approval_token: approval.approval_token,
        idempotency_key: 'ui-smoke-office-1',
        work_id: work.id,
      }),
    })).json();
    if (!delivered.ok) throw new Error('office deliver failed: ' + JSON.stringify(delivered).slice(0, 200));
    if (!(delivered.evidence.files || []).every(item => item.sha256?.length === 64)) {
      throw new Error('office evidence missing sha256');
    }
    if (!delivered.evidence.summary || delivered.evidence.summary.split('\n').length < 1) {
      throw new Error('office evidence missing summary');
    }
    checks.push('office-delivery-evidence');

    const bridgeTab = page.locator('[data-tab="bridges"]');
    if (await bridgeTab.count() === 0) throw new Error('Köprüler sekmesi bulunamadı');
    await page.locator('details.inspector-more > summary').click().catch(() => {});
    await bridgeTab.click({ force: true }).catch(() => {});
    const cards = page.locator('.bridge-card');
    await cards.first().waitFor({ timeout: 15000 });
    const cardCount = await cards.count();
    const officeCard = page.locator('.bridge-card', { hasText: 'office' });
    const githubCard = page.locator('.bridge-card', { hasText: 'github' });
    const communicationCard = page.locator('.bridge-card', { hasText: 'communication' });
    await officeCard.first().waitFor();
    await githubCard.first().waitFor();
    await communicationCard.first().waitFor();
    const officeText = await officeCard.first().innerText();
    const githubText = await githubCard.first().innerText();
    const communicationText = await communicationCard.first().innerText();
    for (const text of [officeText, githubText, communicationText]) {
      if (!/read/.test(text)) throw new Error('Bridge card missing permission levels');
      if (!/Son teslim/i.test(text)) {
        throw new Error('Bridge card missing last delivery section');
      }
    }
    if (!/kanıt tam|eksik kanıt/.test(officeText)) {
      throw new Error('Office card missing the evidence badge');
    }
    if (!/sha256/.test(officeText)) {
      throw new Error('Office card missing the short sha256 evidence');
    }
    checks.push('bridge-evidence-badge');
    if (cardCount < 3) throw new Error('expected one card per shipped pack, got ' + cardCount);
    checks.push('bridge-cards-developer-view:' + cardCount);
    await page.screenshot({ path: path.join(out, 'bridge-card.png'), fullPage: true });

    const accepted = await fetch(base + `/api/deliverables/${(await deliverableResponse.json()).id}/accept`, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ note: 'ui smoke' }),
    });
    if (!accepted.ok) throw new Error('Accept failed: ' + accepted.status);
    checks.push('delivery-accept-api');

    // Repair loop: red -> v+1 (parent-linked) -> accept, without a model call.
    const redWork = await (await fetch(base + '/api/work', {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({
        goal: 'Onarım döngüsü smoke',
        title: 'Onarım döngüsü',
        due_at: new Date(Date.now() + 7 * 86_400_000).toISOString(),
      }),
    })).json();
    const redV1 = await (await fetch(base + `/api/work/${redWork.id}/deliverables`, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({
        summary: 'İlk sürüm',
        kind: 'office-report',
        bridge_id: 'office',
        files: ['rapor.md'],
        evidence: ['rapor.md · sha256 ' + 'a'.repeat(16) + '… (5 bayt)'],
        note: 'özet: ilk sürüm',
        idempotency_key: 'ui-smoke-repair-v1',
      }),
    })).json();
    if (redV1.evidence_view?.complete !== true) {
      throw new Error('Complete evidence was not recognized');
    }
    const rejected = await (await fetch(base + `/api/deliverables/${redV1.id}/repair`, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ note: 'Başlık eksik' }),
    })).json();
    if (rejected.status !== 'repair_requested') {
      throw new Error('Repair request not recorded: ' + rejected.status);
    }
    const redV2 = await (await fetch(base + `/api/work/${redWork.id}/deliverables`, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({
        summary: 'Düzeltilmiş sürüm',
        kind: 'office-report',
        bridge_id: 'office',
        files: ['rapor.md'],
        evidence: ['rapor.md · sha256 ' + 'b'.repeat(16) + '… (6 bayt)'],
        note: 'özet: başlık eklendi',
        parent_version: 1,
        idempotency_key: 'ui-smoke-repair-v2',
      }),
    })).json();
    if (redV2.version !== 2 || redV2.parent_version !== 1) {
      throw new Error('Repair version lineage broken: ' + JSON.stringify(redV2));
    }
    const redAccepted = await fetch(base + `/api/deliverables/${redV2.id}/accept`, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ note: 'ui smoke kabul' }),
    });
    if (!redAccepted.ok) throw new Error('Repair accept failed: ' + redAccepted.status);
    const redState = await (await fetch(base + `/api/work/${redWork.id}`)).json();
    if (redState.work.status !== 'done') {
      throw new Error('Work not done after accepting repair: ' + redState.work.status);
    }
    const deliveryMetrics = await (await fetch(base + '/api/metrics?task_kind=delivery')).json();
    const rejectedCount = (deliveryMetrics.models || [])
      .reduce((sum, model) => sum + (model.user_rejected || 0), 0);
    if (rejectedCount < 1) throw new Error('Rejection count missing from metrics');
    checks.push('repair-loop-red-v2-accept');

    // Evidence gate: a delivery without evidence cannot be accepted.
    const bareWork = await (await fetch(base + '/api/work', {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ goal: 'Kanıtsız teslim smoke' }),
    })).json();
    const bare = await (await fetch(base + `/api/work/${bareWork.id}/deliverables`, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ summary: 'Kanıtsız', kind: 'office-report',
                             bridge_id: 'office', files: [], evidence: [] }),
    })).json();
    if (bare.evidence_view?.complete !== false) {
      throw new Error('Missing evidence was not flagged');
    }
    const refused = await fetch(base + `/api/deliverables/${bare.id}/accept`, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ note: 'yok' }),
    });
    if (refused.status !== 400) throw new Error('Evidence gate did not refuse accept');
    checks.push('evidence-gate-refuses-accept');

    await page.setViewportSize({ width: 390, height: 844 });
    if (await page.evaluate(() => document.documentElement.scrollWidth > innerWidth)) {
      throw new Error('Mobile horizontal overflow');
    }
    checks.push('mobile-no-overflow');
    await page.screenshot({ path: path.join(out, 'mobile-work.png'), fullPage: true });
    await page.close();

    fs.writeFileSync(path.join(out, 'work-smoke-report.json'),
                     JSON.stringify({ checks, errors, network_errors: failures }, null, 2));
    if (errors.length || failures.length) {
      throw new Error(JSON.stringify({ errors, failures }));
    }
    console.log(JSON.stringify({ checks, errors, network_errors: failures }));
  } finally {
    if (browser) await browser.close();
    if (server) await server.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
