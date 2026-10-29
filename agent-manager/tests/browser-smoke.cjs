const fs=require('node:fs');
const path=require('node:path');
const {spawnSync}=require('node:child_process');
const {chromium}=require('C:/Users/user/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const root=path.resolve(__dirname,'..');
const base=process.env.TEST_URL||'http://127.0.0.1:8766';
(async()=>{
 let server,browser;const errors=[],failures=[];const checks=[];
 const out=path.join(root,'work','visual');fs.mkdirSync(out,{recursive:true});
 try{
  server=await chromium.launchServer({headless:true,executablePath:'C:/Program Files/Google/Chrome/Application/chrome.exe'});
  const pid=server.process().pid;
  const code="import sys,psutil; from pathlib import Path; from datetime import datetime,timezone; from manager.processes import _record; p=psutil.Process(int(sys.argv[1])); _record(Path.cwd(),dict(resource_id='visual-browser-'+str(p.pid),owner_task='agent-manager-visual',pid=p.pid,created_utc=datetime.fromtimestamp(p.create_time(),timezone.utc).isoformat(),executable_path=p.exe(),parent_pid=p.ppid(),shared=False,disposable=True,state=sys.argv[2]))";
  spawnSync(path.join(root,'.venv/Scripts/python.exe'),['-c',code,String(pid),'running'],{cwd:root});
  browser=await chromium.connect(server.wsEndpoint());
  const page=await browser.newPage({viewport:{width:1440,height:1000}});
  page.on('pageerror',e=>errors.push(e.message));page.on('console',e=>{if(e.type()==='error')errors.push(e.text())});
  page.on('requestfailed',r=>failures.push(r.url()+': '+r.failure()?.errorText));
  page.on('response',r=>{if(r.status()>=400)failures.push(r.status()+': '+r.url())});
  await page.goto(base);await page.waitForLoadState('networkidle');
  await page.getByText('Bağlı',{exact:true}).waitFor();checks.push('bootstrap');
  await page.screenshot({path:path.join(out,'desktop-welcome.png'),fullPage:true});
  await page.locator('[data-page="models"]').click();await page.getByText('gpt-6.1-sol',{exact:true}).waitFor();checks.push('model-list');
  await page.screenshot({path:path.join(out,'desktop-models.png'),fullPage:true});
  await page.locator('[data-page="history"]').click();checks.push('history');
  await page.locator('#new-chat').click();
  await page.locator('.settings-open').first().click();await page.locator('#timeout-seconds').fill('60');await page.getByRole('button',{name:'Ayarları kaydet'}).click();checks.push('settings');
  await page.setViewportSize({width:390,height:844});await page.screenshot({path:path.join(out,'mobile-welcome.png'),fullPage:true});
  if(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth))throw Error('Mobile horizontal overflow');checks.push('mobile-no-overflow');
  await page.setViewportSize({width:1440,height:1000});
  await page.locator('[data-mode="plan"]').click();
  await page.locator('#planner-model').selectOption('gemini-3.8-flash-medium');
  await page.locator('#executor-model').selectOption('gpt-6.1-sol');
  await page.locator('#prompt').fill('2+2 sonucunu kısaca açıklamak için yalnız tek adımlık plan hazırla. Dosya veya araç gerekmiyor.');
  await page.locator('#send-button').click();
  await page.waitForFunction(()=>Boolean(JSON.parse(localStorage.getItem('agent-manager-selected')||'null')));
  const id=await page.evaluate(()=>JSON.parse(localStorage.getItem('agent-manager-selected')));
  console.log(JSON.stringify({phase:'submitted',run_id:id}));
  let run;
  const deadline=Date.now()+90000;
  while(Date.now()<deadline){const response=await page.request.get(base+'/api/runs/'+id);if(!response.ok())throw Error('Run API '+response.status());run=await response.json();if(!['queued','planning','running'].includes(run.status))break;await page.waitForTimeout(750)}
  if(['queued','planning','running'].includes(run.status))throw Error('UI live planning did not finish in deadline');
  await page.waitForTimeout(1500);checks.push('live-plan-status-'+run.status);
  if(run.status!=='planned')throw Error('Live planning failed: '+run.status+' '+run.error);
  if(run.status==='planned'){
   await page.getByRole('button',{name:'Planı düzenle',exact:true}).click();
   const plan=JSON.parse(await page.locator('#plan-json').inputValue());
   const steps=Array.isArray(plan)?plan:plan.steps;steps[0].title='Toplama sonucunu açıkla';
   await page.locator('#plan-json').fill(JSON.stringify(plan,null,2));await page.getByRole('button',{name:'Planı kaydet'}).click();
   await page.locator('#plan-dialog').waitFor({state:'hidden'});checks.push('edit-plan');
   const resumed=page.waitForResponse(r=>r.url().endsWith('/api/runs/'+id+'/resume')&&r.request().method()==='POST');
   await page.getByRole('button',{name:'Planı çalıştır',exact:true}).click();
   if(!(await resumed).ok())throw Error('Resume request failed');
   const finish=Date.now()+90000;
   while(Date.now()<finish){run=await (await page.request.get(base+'/api/runs/'+id)).json();if(!['queued','planning','running'].includes(run.status))break;await page.waitForTimeout(750)}
   checks.push('live-execution-status-'+run.status);
   if(run.status!=='completed')throw Error('Live execution failed: '+run.status+' '+run.error);
   await page.waitForTimeout(1500);
  }
  await page.screenshot({path:path.join(out,'desktop-run.png'),fullPage:true});
  await page.reload();await page.waitForLoadState('networkidle');await page.locator('#run-content .user-message').waitFor();checks.push('reload-persistence');
  await page.setViewportSize({width:390,height:844});await page.screenshot({path:path.join(out,'mobile-run.png'),fullPage:true});
  if(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth))throw Error('Mobile run overflow');
  await page.close();
  spawnSync(path.join(root,'.venv/Scripts/python.exe'),['-c',code,String(pid),'completed'],{cwd:root});
  fs.writeFileSync(path.join(out,'report.json'),JSON.stringify({checks,errors,network_errors:failures,run_id:id,status:run.status,error:run.error},null,2));
  if(errors.length||failures.length)throw Error(JSON.stringify({errors,failures}));
  console.log(JSON.stringify({checks,errors,network_errors:failures,status:run.status}));
 }finally{if(browser)await browser.close();if(server)await server.close()}
})().catch(e=>{console.error(e);process.exitCode=1});
