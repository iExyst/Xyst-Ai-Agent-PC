// Verify the real packaged Tauri WebView resolves deterministic specialist conflicts.
import { chromium, expect } from '@playwright/test';
import { spawn, execFileSync } from 'node:child_process';
import { createHash, randomUUID } from 'node:crypto';
import { createServer } from 'node:net';
import { mkdir, readFile, writeFile } from 'node:fs/promises';
import path from 'node:path';

const root=path.resolve('.acceptance',`native-conflict-review-${randomUUID()}`);
const appData=path.join(root,'appdata');
const localAppData=path.join(root,'localappdata');
const logDirectory=path.join(root,'logs');
await Promise.all([root,appData,localAppData,logDirectory].map(directory=>mkdir(directory,{recursive:true})));
const seed=JSON.parse(execFileSync('uv',['run','python','scripts/seed_native_conflict_review.py',root],{windowsHide:true,stdio:'pipe',encoding:'utf8',env:{...process.env,XAA_DATA_DIR:root}}));
if(seed.model_inference_requested!==false)throw new Error('Native conflict fixture unexpectedly requested model inference');
const executable=path.resolve(process.argv[2]||'build/native-release/xaa-desktop.exe');
const manifestPath=process.argv[3]||path.join(path.dirname(executable),'manifest.json');
const manifest=JSON.parse(await readFile(manifestPath,'utf8'));
const hash=data=>createHash('sha256').update(data).digest('hex');
for(const [name,digest] of Object.entries(manifest))if(hash(await readFile(path.join(path.dirname(manifestPath),name)))!==digest)throw new Error(`Native staged binary hash mismatch: ${name}`);
const listener=createServer();await new Promise(resolve=>listener.listen(0,'127.0.0.1',resolve));const allocatedPort=listener.address().port;await new Promise((resolve,reject)=>listener.close(error=>error?reject(error):resolve()));
const port=Number(process.env.XAA_NATIVE_DEBUG_PORT||allocatedPort);
if(!Number.isInteger(port)||port<1024||port>65535)throw new Error('Invalid native WebView debug port');
const logPath=path.join(logDirectory,'engine.log');
const priorLog=(await readFile(logPath,'utf8').catch(()=>'' )).length;
const nativeEnv={...process.env,APPDATA:appData,LOCALAPPDATA:localAppData,XAA_DATA_DIR:root,XAA_TEST_HIDDEN:'1',XAA_TEST_LOG_DIR:logDirectory,WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS:`--remote-debugging-port=${port}`};
const proc=spawn(executable,[],{windowsHide:true,stdio:'ignore',env:nativeEnv});
let browser,connection;
const report={passed:false,run_id:seed.run_id,desktop_sha256:manifest['xaa-desktop.exe'],engine_sha256:manifest['xaa-engine.exe'],model_inference_requested:false,scope:'Deterministic private specialist edits reviewed through the packaged Tauri WebView and frozen Windows filesystem worker.'};
try{
  for(let attempt=0;attempt<80;attempt++){
    try{browser=await chromium.connectOverCDP(`http://127.0.0.1:${port}`);break;}
    catch{if(proc.exitCode!==null)throw new Error(`Packaged desktop exited during startup (${proc.exitCode})`);await new Promise(resolve=>setTimeout(resolve,250));}
  }
  if(!browser)throw new Error('Packaged Tauri WebView did not expose its acceptance port');
  const page=browser.contexts()[0].pages()[0];
  await page.getByLabel('Workspace path').waitFor({timeout:30000});
  connection=await page.evaluate(()=>window.__TAURI_INTERNALS__.invoke('engine_connection'));
  if(typeof connection?.base_url!=='string'||typeof connection?.token!=='string')throw new Error('Packaged engine connection is unavailable');
  const headers={Authorization:`Bearer ${connection.token}`};
  async function get(route){const response=await fetch(connection.base_url+route,{headers,signal:AbortSignal.timeout(15000)});if(!response.ok)throw new Error(`Packaged API read failed (${response.status} ${route})`);return response.json();}
  const sourceFile=path.join(seed.workspace,'shared.py');
  expect(hash(await readFile(sourceFile))).toBe(seed.original_sha256);
  await page.getByRole('navigation',{name:'Main sections'}).getByRole('link',{name:/Saved runs/}).click();
  await page.getByRole('button',{name:`Open ${seed.objective}`,exact:true}).click();
  await page.getByRole('heading',{name:'Active run verifying',exact:true}).waitFor({timeout:15000});
  await page.getByRole('region',{name:'Current activity'}).getByText('Worker stopped',{exact:true}).waitFor({timeout:15000});
  await new Promise(resolve=>setTimeout(resolve,500));
  const plan=page.getByRole('region',{name:'Task plan'});
  for(const name of ['alpha','beta']){
    const panel=plan.getByRole('article',{name:`Task ${name}`,exact:true});
    await panel.getByRole('button',{name:'Review candidate',exact:true}).click();
    const privateChanges=panel.getByRole('region',{name:'Private specialist changes'});
    await expect(privateChanges).toBeVisible();
    await expect(privateChanges).toContainText(`return '${name}'`);
    if(name==='alpha')expect(hash(await readFile(sourceFile))).toBe(seed.original_sha256);
    for(const checkbox of await panel.getByRole('checkbox').all())await checkbox.check();
    await panel.getByLabel('Review rationale',{exact:true}).fill(`Inspected the ${name} private diff and read evidence in the packaged desktop.`);
    const accept=panel.getByRole('button',{name:'Integrate changes and accept task',exact:true});
    await expect(accept).toBeDisabled();
    await panel.getByRole('button',{name:'Check current project for integration',exact:true}).click();
    await expect(panel.getByText(/Current project checked\./)).toBeVisible();
    const authorize=panel.getByLabel('I reviewed the file changes and authorize these integration choices');
    if(name==='beta'){
      const diff=panel.getByLabel('Current-to-proposed conflict diff: shared.py');
      await expect(panel.getByRole('heading',{name:'Conflict: shared.py',exact:true})).toBeVisible();
      await expect(diff).toContainText("-    return 'alpha'");
      await expect(diff).toContainText("+    return 'beta'");
      await expect(panel.getByText('Current project contents (read-only)',{exact:true})).toBeVisible();
      await expect(authorize).toBeDisabled();
      await expect(accept).toBeDisabled();
      await panel.screenshot({path:path.join(root,'native-conflict-review.png')});
      await panel.getByRole('combobox',{name:'Resolve shared.py for beta',exact:true}).selectOption('proposed');
    }else await expect(panel.getByRole('heading',{name:'Conflict: shared.py',exact:true})).toHaveCount(0);
    await authorize.check();
    await expect(accept).toBeEnabled();
    await accept.click();
    await expect.poll(async()=>(await get(`/runs/${seed.run_id}/plan`)).tasks.find(task=>task.local_id===name).status,{timeout:20000}).toBe('completed');
    expect(hash(await readFile(sourceFile))).toBe(seed.proposed_sha256[name]);
  }
  const events=await get(`/runs/${seed.run_id}/events`);
  const reviews=events.filter(event=>event.event_type==='task.reviewed'&&event.payload.state==='completed');
  expect(reviews).toHaveLength(2);
  expect(reviews[0].payload.integrated_changes[0].choice).toBe('proposed');
  expect(reviews[1].payload.integrated_changes[0].choice).toBe('proposed');
  const finalRun=await get(`/runs/${seed.run_id}`);
  if(finalRun.state!=='verifying')throw new Error('The native fixture must not imply the parent run is complete.');
  report.passed=true;
  report.private_proposals_preserved_project_until_review=true;
  report.first_nonconflicting_specialist_integrated=true;
  report.current_to_proposed_conflict_diff_verified=true;
  report.conflict_choice_required_before_authorization=true;
  report.current_hash_rechecked_by_integration=true;
  report.final_workspace_sha256=hash(await readFile(sourceFile));
  report.owner_reviewed_task_count=reviews.length;
  report.parent_completion_claimed=false;
  await writeFile(path.join(root,'report.json'),JSON.stringify(report,null,2));
  await page.evaluate(async()=>{
    await window.__TAURI_INTERNALS__.invoke('set_close_behavior',{behavior:'exit_on_close'});
    await window.__TAURI_INTERNALS__.invoke('plugin:window|close',{label:'main'});
  }).catch(()=>{});
  for(let attempt=0;attempt<60&&proc.exitCode===null;attempt++)await new Promise(resolve=>setTimeout(resolve,250));
  if(proc.exitCode!==0)throw new Error(`Packaged desktop did not exit cleanly (${proc.exitCode})`);
  let stopped=false;
  for(let attempt=0;attempt<20;attempt++){try{await fetch(connection.base_url+'/runs',{signal:AbortSignal.timeout(500)});}catch{stopped=true;break;}await new Promise(resolve=>setTimeout(resolve,250));}
  if(!stopped)throw new Error('Packaged engine remained reachable after desktop exit');
  const shutdown=(await readFile(logPath,'utf8').catch(()=> '')).slice(priorLog.length);
  await writeFile(path.join(root,'native-shutdown.log'),shutdown);
  if(!shutdown.includes('Application shutdown complete.')||!shutdown.includes('Finished server process'))throw new Error('Packaged engine did not record graceful shutdown');
  report.engine_stopped=true;report.graceful_shutdown=true;
  await writeFile(path.join(root,'report.json'),JSON.stringify(report,null,2));
  console.log(JSON.stringify({report:path.join(root,'report.json'),passed:true}));
}catch(error){
  report.error=String(error);
  await browser?.contexts()[0]?.pages()[0]?.screenshot({path:path.join(root,'native-conflict-failure.png'),fullPage:true}).catch(()=>{});
  await writeFile(path.join(root,'report.json'),JSON.stringify(report,null,2)).catch(()=>{});
  throw error;
}finally{
  await browser?.close().catch(()=>{});
  if(proc.exitCode===null)execFileSync('taskkill',['/PID',String(proc.pid),'/T','/F'],{windowsHide:true,stdio:'ignore'});
}
