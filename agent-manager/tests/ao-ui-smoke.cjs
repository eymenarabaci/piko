const fs = require('node:fs');
const path = require('node:path');
const {chromium} = require('C:/Users/user/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');

(async () => {
  const base = process.env.TEST_URL || 'http://127.0.0.1:8767';
  const browser = await chromium.launch({headless:true, executablePath:'C:/Program Files/Google/Chrome/Application/chrome.exe'});
  const page = await browser.newPage({viewport:{width:1280,height:900}});
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  page.on('console', message => {if(message.type()==='error') errors.push(message.text());});
  page.on('requestfailed', request => errors.push(request.url()+': '+request.failure()?.errorText));
  try {
    await page.goto(base);
    await page.waitForLoadState('networkidle');
    await page.getByText('Bağlı',{exact:true}).waitFor();
    await page.locator('.settings-open').first().click();
    await page.locator('#execution-backend').selectOption('ao');
    await page.getByRole('button',{name:'Ayarları kaydet'}).click();
    if(await page.evaluate(() => JSON.parse(localStorage.getItem('agent-manager-settings')).execution_backend) !== 'ao') throw Error('AO backend setting was not saved');
    await page.locator('.settings-open').first().click();
    if(await page.locator('#execution-backend').inputValue() !== 'ao') throw Error('AO backend setting was not restored');
    const out = path.join(__dirname,'..','work','visual');
    fs.mkdirSync(out,{recursive:true});
    await page.screenshot({path:path.join(out,'ao-settings.png'),fullPage:true});
    await page.getByRole('button',{name:'Ayarları kapat'}).click();
    await page.locator('[data-page="models"]').click();
    await page.getByRole('heading',{name:'Agent Orchestrator'}).waitFor();
    if(errors.length) throw Error(JSON.stringify(errors));
    console.log(JSON.stringify({checks:['bootstrap','ao-settings','ao-status'],errors}));
  } finally {
    await page.close();
    await browser.close();
  }
})().catch(error => {console.error(error); process.exitCode=1;});
