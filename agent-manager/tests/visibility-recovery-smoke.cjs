const fs=require('node:fs'),path=require('node:path'),cp=require('node:child_process');
const {chromium}=require(path.join(process.env.USERPROFILE,'.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright'));
const root=path.resolve(__dirname,'..'),out=path.join(root,'work/visibility-proof');fs.mkdirSync(out,{recursive:true});
const python=path.join(root,'.venv/Scripts/python.exe');let server,browser,entry;
function record(state){const code='import sys,json,psutil; from pathlib import Path; from manager.processes import _record; p=psutil.Process(int(sys.argv[1])); e={"resource_id":"visibility-browser-"+str(p.pid)+"-"+str(p.create_time()),"owner_task":"piko-visibility-planner-fix","pid":p.pid,"created_utc":__import__("datetime").datetime.fromtimestamp(p.create_time(),__import__("datetime").timezone.utc).isoformat(),"executable_path":p.exe(),"parent_pid":p.ppid(),"shared":False,"disposable":True,"state":"running"}; _record(Path.cwd(),e); print(json.dumps(e))';entry=JSON.parse(cp.execFileSync(python,['-B','-c',code,String(server.process().pid)],{cwd:root,encoding:'utf8'}));}
(async()=>{const errors=[],network=[],checks=[];try{
 server=await chromium.launchServer({headless:true,executablePath:'C:/Program Files/Google/Chrome/Application/chrome.exe'});record();
 browser=await chromium.connect(server.wsEndpoint());const page=await browser.newPage({viewport:{width:1440,height:1000}});
 page.on('pageerror',e=>errors.push(e.message));page.on('console',e=>{if(e.type()==='error')errors.push(e.text())});page.on('response',r=>{if(r.status()>=400)network.push([r.status(),r.url()])});page.on('requestfailed',r=>{if(!r.failure()?.errorText?.includes('ERR_ABORTED'))network.push(r.url()+': '+r.failure()?.errorText)});
 await page.goto('http://127.0.0.1:8765/?run=2c256f9cab3d4d2491883b56e1906d12');await page.waitForLoadState('networkidle');
 const selected=page.locator('.recent-run.selected');await selected.filter({hasText:'Piko, bu yeni sohbette tek emrim'}).waitFor({timeout:30000});checks.push('existing-autonomy-chat-visible-and-selected-by-link');
 const bootstrap=await (await page.request.get('http://127.0.0.1:8765/api/bootstrap')).json();
 const evren=new Set(bootstrap.models.filter(m=>m.provider==='evren').map(m=>m.id));
 for(const selector of ['#planner-model','#chat-model']){const values=await page.locator(selector+' option').evaluateAll(options=>options.map(o=>o.value));if(values.some(v=>v&&v!=='auto'&&!evren.has(v)))throw Error('Forbidden provider offered in '+selector);}
 checks.push('only-evren-models-offered');
 if((await page.locator('#topbar-project-name').textContent())!=='Piko 24 Saat Otonomi Laboratuvarı')throw Error('Wrong project header');checks.push('matching-lab-project-header');
 await page.getByText('0 Plan Adımı (0/0 Tamamlandı)',{exact:true}).waitFor();checks.push('no-phantom-worker-before-plan');
 await page.screenshot({path:path.join(out,'autonomy-chat-visible.png'),fullPage:true});
 const prior=await selected.textContent();const response=await page.request.get('http://127.0.0.1:8765/api/runs');const runs=await response.json();
 const fake={...runs[0],id:'bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb',conversation_id:'bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb',prompt:'Dış API sohbeti otomatik görünürlük testi',workspace:'C:/other-project',status:'planned',created_at:new Date().toISOString(),execution_created_at:new Date().toISOString(),project_id:''};
 await page.route('**/api/runs',route=>route.fulfill({json:[fake,...runs]}));
 await page.locator('.recent-run').filter({hasText:fake.prompt}).waitFor({timeout:23000});checks.push('external-chat-discovered-without-manual-reload');
 if((await page.locator('.recent-run.selected').textContent())!==prior)throw Error('Background refresh changed selected conversation');checks.push('selection-preserved');
 await page.locator('#scope-project-btn').click();if(await page.getByRole('button',{name:/Diğer projelerde.*Tümünü göster/}).count()){await page.getByRole('button',{name:/Diğer projelerde.*Tümünü göster/}).click();await page.locator('.recent-run').filter({hasText:fake.prompt}).waitFor();checks.push('project-filter-explains-hidden-chats');}
 const statusPage=await browser.newPage({viewport:{width:1440,height:1000}});statusPage.on('pageerror',e=>errors.push(e.message));
 let remoteStatus='failed';const existing=runs.find(r=>r.id==='2c256f9cab3d4d2491883b56e1906d12');
 const resumed=()=>({...existing,status:remoteStatus,updated_at:remoteStatus==='failed'?'2026-11-03T05:00:00Z':'2026-11-03T05:30:00Z',error:remoteStatus==='failed'?'fixture failure':null});
 await statusPage.route('**/api/runs',route=>route.fulfill({json:[resumed(),...runs.filter(r=>r.id!==existing.id)]}));
 await statusPage.route('**/api/runs/'+existing.id+'/conversation',route=>route.fulfill({json:{runs:[resumed()]}}));
 await statusPage.route('**/api/runs/'+existing.id,route=>route.fulfill({json:resumed()}));
 await statusPage.goto('http://127.0.0.1:8765/?run='+existing.id);await statusPage.waitForFunction(()=>document.querySelector('#footer-state')?.textContent==='Başarısız');
 remoteStatus='planning';await statusPage.waitForFunction(()=>document.querySelector('#footer-state')?.textContent==='Planlanıyor',{},{timeout:23000});checks.push('externally-resumed-chat-refreshes-and-restarts-polling');await statusPage.close();
 const report={checks,errors,network,fixture:'Synthetic chat is browser-route-only; no backend run created.'};fs.writeFileSync(path.join(out,'report.json'),JSON.stringify(report,null,2));console.log(JSON.stringify(report));if(errors.length||network.length)process.exitCode=1;
 }catch(e){console.error(e.stack);process.exitCode=1;}finally{if(browser)await browser.close();if(server)await server.close();if(entry){entry.state='completed';cp.execFileSync(python,['-B','-c','import sys,json; from pathlib import Path; from manager.processes import _record; _record(Path.cwd(),json.loads(sys.argv[1]))',JSON.stringify(entry)],{cwd:root});}}})();
