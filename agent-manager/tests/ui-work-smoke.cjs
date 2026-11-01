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
    checks.push('notifications-dialog');
    await page.locator('#notif-quiet-start').fill('23:00');
    await page.locator('#notif-quiet-start').dispatchEvent('input');
    await page.locator('#notification-preferences-form button[type=submit]').click();
    await page.waitForTimeout(600);
    await page.locator('#close-notifications').click();
    checks.push('notification-preferences');
    await page.screenshot({ path: path.join(out, 'notifications.png'), fullPage: true });

    const mascot = page.locator('#piko-mascot');
    await mascot.waitFor();
    const mascotState = await mascot.getAttribute('data-state');
    if (!mascotState) throw new Error('Mascot state missing');
    checks.push('mascot-' + mascotState);

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

    const accepted = await fetch(base + `/api/deliverables/${(await deliverableResponse.json()).id}/accept`, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ note: 'ui smoke' }),
    });
    if (!accepted.ok) throw new Error('Accept failed: ' + accepted.status);
    checks.push('delivery-accept-api');

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
