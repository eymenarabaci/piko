const fs=require('node:fs'),path=require('node:path'),cp=require('node:child_process');
const {chromium}=require(path.join(process.env.USERPROFILE,'.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright'));
const root=path.resolve(__dirname,'..'),out=path.join(root,'work/terminal-proof');fs.mkdirSync(out,{recursive:true});
const python=path.join(root,'.venv/Scripts/python.exe');let server,browser,entry;
function record(state){const code='import sys,json,psutil; from pathlib import Path; from manager.processes import _record; p=psutil.Process(int(sys.argv[1])); e={"resource_id":"visibility-browser-"+str(p.pid)+"-"+str(p.create_time()),"owner_task":"piko-visibility-planner-fix","pid":p.pid,"created_utc":__import__("datetime").datetime.fromtimestamp(p.create_time(),__import__("datetime").timezone.utc).isoformat(),"executable_path":p.exe(),"parent_pid":p.ppid(),"shared":False,"disposable":True,"state":"running"}; _record(Path.cwd(),e); print(json.dumps(e))';entry=JSON.parse(cp.execFileSync(python,['-B','-c',code,String(server.process().pid)],{cwd:root,encoding:'utf8'}));}
(async()=>{const errors=[],network=[],checks=[];try{
 server=await chromium.launchServer({headless:true,executablePath:'C:/Program Files/Google/Chrome/Application/chrome.exe'});record();
 browser=await chromium.connect(server.wsEndpoint());const page=await browser.newPage({viewport:{width:1440,height:1000}});
 page.on('pageerror',e=>errors.push(e.message));page.on('console',e=>{if(e.type()==='error')errors.push(e.text())});page.on('response',r=>{if(r.status()>=400)network.push([r.status(),r.url()])});
 const url='http://127.0.0.1:8765/?run=2c256f9cab3d4d2491883b56e1906d12';
 const rid='2c256f9cab3d4d2491883b56e1906d12';
 const original=await(await page.request.get('http://127.0.0.1:8765/api/runs/'+rid)).json();
 const fixture={...original,status:'waiting_retry',phase_status:'retrying',current_steps:[],quota_timer:null,retry_timer:{reason:'transient_error',resets_at:new Date(Date.now()+900000).toISOString()}};
 await page.route('**/api/runs/'+rid,route=>route.fulfill({json:fixture}));
 await page.route('**/api/runs/'+rid+'/conversation',route=>route.fulfill({json:{runs:[fixture]}}));
 await page.goto(url);await page.waitForLoadState('networkidle');
 await page.locator('#job-label').filter({hasText:'Geçici ağ/servis hatası'}).waitFor();
 if((await page.locator('#job-label').textContent()).includes('Kota bekleniyor'))throw Error('Network wait mislabeled quota');
 await page.screenshot({path:path.join(out,'network-backoff-label.png'),fullPage:true});
 console.log(JSON.stringify({networkWaitLabel:await page.locator('#job-label').textContent(),errors,network}));
 if(errors.length||network.length)process.exitCode=1;
 }catch(e){console.error(e.stack);process.exitCode=1;}finally{if(browser)await browser.close();if(server)await server.close();if(entry){entry.state='completed';cp.execFileSync(python,['-B','-c','import sys,json; from pathlib import Path; from manager.processes import _record; _record(Path.cwd(),json.loads(sys.argv[1]))',JSON.stringify(entry)],{cwd:root});}}})();
