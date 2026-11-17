import { chromium, expect } from '@playwright/test';
import { spawn, execFileSync } from 'node:child_process';
import { mkdir, readFile, writeFile } from 'node:fs/promises';
import { randomUUID } from 'node:crypto';
import path from 'node:path';

const root=path.resolve('.acceptance',`native-plan-progress-${randomUUID()}`);
const appData=path.join(root,'appdata');
const localAppData=path.join(root,'localappdata');
const workspace=path.join(root,'workspace');
await Promise.all([appData,localAppData,workspace].map(directory=>mkdir(directory,{recursive:true})));
const port=Number(process.env.XAA_NATIVE_DEBUG_PORT||19231);
if(!Number.isInteger(port)||port<1024||port>65535)throw new Error('Invalid native WebView debug port');
const executable=path.resolve(process.argv[2]||'build/native-release/xaa-desktop.exe');
const manifestPath=process.argv[3]||path.join(path.dirname(executable),'manifest.json');
const manifest=JSON.parse(await readFile(manifestPath,'utf8'));
const desktopSha256=manifest['xaa-desktop.exe'];
const engineSha256=manifest['xaa-engine.exe'];
if(typeof desktopSha256!=='string'||typeof engineSha256!=='string')throw new Error('Native manifest is missing desktop or engine hashes');
const logPath=path.join(localAppData,'dev.xyst.xaa','logs','engine.log');
const previousLog=await readFile(logPath,'utf8').catch(()=> '');
const nativeEnv={...process.env,APPDATA:appData,LOCALAPPDATA:localAppData,XAA_DATA_DIR:root,XAA_TEST_HIDDEN:'1',WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS:`--remote-debugging-port=${port}`};
const proc=spawn(executable,[],{windowsHide:true,stdio:'ignore',env:nativeEnv});
let browser;
let connection;
let passed=false;
try{
  for(let attempt=0;attempt<80;attempt++){
    try{browser=await chromium.connectOverCDP(`http://127.0.0.1:${port}`);break;}
    catch{if(proc.exitCode!==null)throw new Error(`Native desktop exited during startup with ${proc.exitCode}`);await new Promise(resolve=>setTimeout(resolve,250));}
  }
  if(!browser)throw new Error('Native WebView did not expose the acceptance port');
  const page=browser.contexts()[0].pages()[0];
  await page.getByLabel('Workspace path').waitFor({timeout:30000});
  connection=await page.evaluate(()=>window.__TAURI_INTERNALS__.invoke('engine_connection'));
  if(typeof connection?.base_url!=='string'||typeof connection?.token!=='string')throw new Error('Native engine connection is unavailable');
  const objective='Native dependency stage progress fixture';
  const created=await fetch(`${connection.base_url}/runs`,{method:'POST',headers:{Authorization:`Bearer ${connection.token}`,'Content-Type':'application/json'},body:JSON.stringify({objective,workspace,mode:'observe',model:null,max_iterations:8,max_seconds:120})});
  if(!created.ok)throw new Error(`Could not create model-free native fixture run (${created.status})`);
  const run=await created.json();
  execFileSync('uv',['run','python','scripts/seed_native_plan_progress.py',run.id],{cwd:process.cwd(),windowsHide:true,stdio:'pipe',env:{...process.env,XAA_DATA_DIR:root}});
  await page.reload();
  await page.getByRole('link',{name:/Saved runs/}).click();
  await page.getByRole('button',{name:`Open ${objective}`,exact:true}).click();
  await page.getByRole('heading',{name:'Active run verifying'}).waitFor({timeout:15000});
  const resources=page.getByRole('region',{name:'Run resources'});
  await expect(resources).toContainText('8 iterations · 120 seconds');
  await expect(resources).toContainText('Inference iterations remaining: 7 of 8');
  const usageResponse=await fetch(`${connection.base_url}/runs/${run.id}/usage`,{headers:{Authorization:`Bearer ${connection.token}`}});
  if(!usageResponse.ok||(await usageResponse.json()).inference_attempts!==1)throw new Error('Native resource API did not report the saved inference attempt.');
  const plan=page.getByRole('region',{name:'Task plan'});
  await expect(plan.getByRole('button',{name:'Run ready specialists',exact:true})).toBeDisabled();
  await expect(plan).toContainText('Choose a model before running ready specialists.');
  const inspectTask=plan.getByRole('article',{name:'Task inspect'});
  await expect(inspectTask.getByRole('button',{name:'Cancel inspect',exact:true})).toBeEnabled();
  await expect(inspectTask).toContainText('1 iteration · 1 second');
  const stageOne=plan.getByRole('group',{name:'Dependency stage 1'});
  const stageTwo=plan.getByRole('group',{name:'Dependency stage 2'});
  await expect(stageOne.getByRole('progressbar',{name:'Stage 1 completion'})).toHaveAttribute('max','2');
  await expect(stageOne.getByRole('progressbar',{name:'Stage 1 completion'})).toHaveAttribute('value','0');
  await expect(stageOne).toContainText('2 waiting');
  await expect(stageTwo.getByRole('progressbar',{name:'Stage 2 completion'})).toHaveAttribute('max','1');
  await expect(stageTwo).toContainText('1 waiting');
  await page.screenshot({path:path.join(root,'plan-progress.png'),fullPage:true});
  const report={passed:true,run_id:run.id,task_ids:['inspect','inventory','review'],dependency_stages:2,stage_one_tasks:2,stage_two_tasks:1,stage_progress_bars_verified:true,all_tasks_waiting:true,inference_allowance_verified:true,saved_inference_attempts:1,iterations_remaining:'7 of 8',singular_task_budget_label_verified:true,dispatch_disabled_without_model:true,queued_cancel_remains_enabled:true,exit_on_close_behavior_selected:true,model_inference_requested:false,desktop_sha256:desktopSha256,engine_sha256:engineSha256};
  await writeFile(path.join(root,'report.json'),JSON.stringify(report,null,2));
  passed=true;
  await page.evaluate(async()=>{
    await window.__TAURI_INTERNALS__.invoke('set_close_behavior',{behavior:'exit_on_close'});
    await window.__TAURI_INTERNALS__.invoke('plugin:window|close',{label:'main'});
  }).catch(()=>{});
  for(let attempt=0;attempt<60&&proc.exitCode===null;attempt++)await new Promise(resolve=>setTimeout(resolve,250));
  if(proc.exitCode!==0)throw new Error(`Native desktop did not close cleanly (exit ${proc.exitCode})`);
  let engineStopped=false;
  for(let attempt=0;attempt<20;attempt++){
    try{await fetch(`${connection.base_url}/models`,{signal:AbortSignal.timeout(500)});}
    catch{engineStopped=true;break;}
    await new Promise(resolve=>setTimeout(resolve,250));
  }
  if(!engineStopped)throw new Error('Owned engine remained reachable after native plan acceptance');
  const shutdownLog=(await readFile(logPath,'utf8').catch(()=> '')).slice(previousLog.length);
  const gracefulShutdownLogObserved=shutdownLog.includes('Application shutdown complete.');
  if(proc.exitCode!==0)throw new Error(`Native desktop exited with ${proc.exitCode}`);
  report.engine_stopped=true;
  report.graceful_shutdown_log_observed=gracefulShutdownLogObserved;
  await writeFile(path.join(root,'report.json'),JSON.stringify(report,null,2));
  console.log(JSON.stringify({report:path.join(root,'report.json'),passed:true}));
}catch(error){
  const report={passed:false,error:String(error),native_exit:proc.exitCode};
  await writeFile(path.join(root,'report.json'),JSON.stringify(report,null,2)).catch(()=>{});
  if(proc.exitCode===null){
    try{execFileSync('taskkill',['/PID',String(proc.pid),'/T','/F'],{windowsHide:true,stdio:'ignore'});}catch{}
  }
  throw error;
}finally{await browser?.close().catch(()=>{});}
