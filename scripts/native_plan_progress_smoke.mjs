import { chromium, expect } from '@playwright/test';
import { spawn, execFileSync } from 'node:child_process';
import { mkdir, readFile, writeFile } from 'node:fs/promises';
import { randomUUID } from 'node:crypto';
import path from 'node:path';

const root=path.resolve('.acceptance',`native-plan-progress-${randomUUID()}`);
const appData=path.join(root,'appdata');
const localAppData=path.join(root,'localappdata');
const logDirectory=path.join(root,'logs');
const workspace=path.join(root,'workspace');
await Promise.all([appData,localAppData,logDirectory,workspace].map(directory=>mkdir(directory,{recursive:true})));
const port=Number(process.env.XAA_NATIVE_DEBUG_PORT||19231);
if(!Number.isInteger(port)||port<1024||port>65535)throw new Error('Invalid native WebView debug port');
const executable=path.resolve(process.argv[2]||'build/native-release/xaa-desktop.exe');
const manifestPath=process.argv[3]||path.join(path.dirname(executable),'manifest.json');
const manifest=JSON.parse(await readFile(manifestPath,'utf8'));
const desktopSha256=manifest['xaa-desktop.exe'];
const engineSha256=manifest['xaa-engine.exe'];
if(typeof desktopSha256!=='string'||typeof engineSha256!=='string')throw new Error('Native manifest is missing desktop or engine hashes');
const logPath=path.join(logDirectory,'engine.log');
const previousLog=await readFile(logPath,'utf8').catch(()=> '');
const nativeEnv={...process.env,APPDATA:appData,LOCALAPPDATA:localAppData,XAA_DATA_DIR:root,XAA_TEST_HIDDEN:'1',XAA_TEST_LOG_DIR:logDirectory,WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS:`--remote-debugging-port=${port}`};
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
  // A clean Windows profile can spend up to 60 seconds extracting and starting
  // the bundled engine. Allow the UI's full startup retry window before
  // treating a missing workspace field as a packaged-app failure.
  await page.getByLabel('Workspace path').waitFor({timeout:75000});
  connection=await page.evaluate(()=>window.__TAURI_INTERNALS__.invoke('engine_connection'));
  if(typeof connection?.base_url!=='string'||typeof connection?.token!=='string')throw new Error('Native engine connection is unavailable');
  await page.locator('details.saved-conversations > summary').click();
  await expect(page.getByRole('region',{name:'Shared conversation sessions'}).getByRole('heading',{name:'Shared conversation'})).toBeVisible();
  await page.getByRole('link',{name:'Chats',exact:true}).click();
  await expect(page.getByRole('heading',{name:'Chats',exact:true})).toBeVisible();
  await expect(page.getByRole('region',{name:'Shared conversation sessions'})).toBeVisible();
  await page.getByRole('link',{name:'Tools & integrations',exact:true}).click();
  await expect(page.getByRole('heading',{name:'Tools & integrations',exact:true})).toBeVisible();
  await page.locator('summary').filter({hasText:'Web search integration'}).click();
  await expect(page.getByRole('heading',{name:'Brave Search'})).toBeVisible();
  await page.locator('summary').filter({hasText:'Specialist identities and model routes'}).click();
  await expect(page.getByRole('heading',{name:'Specialist identities and model routes'})).toBeVisible();
  await page.locator('summary').filter({hasText:'Messaging channels'}).click();
  await expect(page.getByRole('heading',{name:'Telegram (private chats)'})).toBeVisible();
  await page.locator('summary').filter({hasText:'MCP integrations'}).click();
  await expect(page.getByRole('heading',{name:'MCP servers'})).toBeVisible();
  await page.locator('summary').filter({hasText:'Agent plugin discovery'}).click();
  await expect(page.getByRole('heading',{name:'Agent plugin packages'})).toBeVisible();
  await page.locator('summary').filter({hasText:'Command sandbox setup'}).click();
  await expect(page.getByRole('button',{name:'Find installed distributions'})).toBeVisible();
  const objective='Native nested specialist plan fixture';
  const created=await fetch(`${connection.base_url}/runs`,{method:'POST',headers:{Authorization:`Bearer ${connection.token}`,'Content-Type':'application/json'},body:JSON.stringify({objective,workspace,mode:'observe',model:null,max_iterations:8,max_seconds:120})});
  if(!created.ok)throw new Error(`Could not create model-free native fixture run (${created.status})`);
  const run=await created.json();
  execFileSync('uv',['run','python','scripts/seed_native_plan_progress.py',run.id],{cwd:process.cwd(),windowsHide:true,stdio:'pipe',env:{...process.env,XAA_DATA_DIR:root}});
  await page.reload();
  await page.getByRole('link',{name:/Saved runs/}).click();
  await expect(page.getByRole('region',{name:'Saved runs'}).getByRole('heading',{name:'Saved runs'})).toBeVisible();
  await page.getByRole('button',{name:`Open ${objective}`,exact:true}).click();
  await page.getByRole('heading',{name:'Active run verifying'}).waitFor({timeout:15000});
  const savedRunResponse=await fetch(`${connection.base_url}/runs/${run.id}`,{headers:{Authorization:`Bearer ${connection.token}`}});
  if(!savedRunResponse.ok)throw new Error('Could not read the saved native run timestamps.');
  const savedRun=await savedRunResponse.json();
  if(Date.parse(savedRun.started_at)-Date.parse(savedRun.created_at)<4*60*1000)throw new Error('The native run start time did not exclude its simulated queue delay.');
  const elapsedText=await page.getByLabel('Elapsed wall time').textContent();
  const elapsedMatch=elapsedText?.match(/^(\d+)s$/);
  if(!elapsedMatch||Number(elapsedMatch[1])>=60)throw new Error('The native elapsed display did not start at planning.');
  const resources=page.getByRole('region',{name:'Run resources'});
  await expect(resources).toContainText('8 iterations · 120 seconds');
  await expect(resources).toContainText('Inference iterations remaining: 7 of 8');
  await expect(resources).toContainText(/Wall-clock allowance remaining: 1m \d+s/);
  const usageResponse=await fetch(`${connection.base_url}/runs/${run.id}/usage`,{headers:{Authorization:`Bearer ${connection.token}`}});
  if(!usageResponse.ok||(await usageResponse.json()).inference_attempts!==1)throw new Error('Native resource API did not report the saved inference attempt.');
  const plan=page.getByRole('region',{name:'Task plan'});
  await expect(plan.getByRole('button',{name:'Run ready specialists',exact:true})).toBeDisabled();
  await expect(plan).toContainText('Choose a model before running ready specialists.');
  const dependencyMap=plan.getByRole('region',{name:'Task dependency map'});
  await expect(dependencyMap).toContainText('8 tasks · 2 stages');
  const graphInspect=dependencyMap.getByRole('link',{name:'Open task inspect, stage 1, Ready'});
  await expect(graphInspect).toContainText('Inspect the primary fixture without making changes.');
  await expect(graphInspect).toContainText('1 expected output');
  await expect(dependencyMap.getByRole('link',{name:'Open task review, stage 2, Waiting'})).toBeVisible();
  await expect(dependencyMap.locator('path[marker-end]')).toHaveCount(7);
  const graphViewport=dependencyMap.getByLabel('Scrollable task dependency map');
  if(!await graphViewport.evaluate(element=>element.scrollHeight>element.clientHeight))throw new Error('Native dependency map did not scroll within its bounded viewport.');
  const inspectTask=plan.getByRole('article',{name:'Task inspect'});
  await expect(inspectTask.getByRole('button',{name:'Cancel inspect',exact:true})).toBeEnabled();
  await expect(inspectTask).toContainText('1 iteration · 1 second');
  await expect(inspectTask).toContainText('Iterations: 0 used · 1 remaining');
  await expect(inspectTask).toContainText('Wall time: 0s elapsed · 1s remaining');
  await expect(inspectTask).toContainText('Tokens: shared run threshold');
  const taskDetails=inspectTask.locator('details.plan-task-details');
  if(await taskDetails.getAttribute('open')!==null)throw new Error('Native task details should start collapsed');
  await expect(inspectTask.locator('.plan-task-output-preview')).toContainText('Fixture state summary');
  const stageOne=plan.getByRole('group',{name:'Dependency stage 1'});
  const stageTwo=plan.getByRole('group',{name:'Dependency stage 2'});
  await expect(stageOne.getByRole('progressbar',{name:'Stage 1 completion'})).toHaveAttribute('max','7');
  await expect(stageOne.getByRole('progressbar',{name:'Stage 1 completion'})).toHaveAttribute('value','0');
  await expect(stageOne).toContainText('7 waiting');
  await expect(stageTwo.getByRole('progressbar',{name:'Stage 2 completion'})).toHaveAttribute('max','1');
  await expect(stageTwo).toContainText('1 waiting');
  const reviewTask=plan.getByRole('article',{name:'Task review'});
  await expect(reviewTask).toContainText('queued · Waiting for reviewed subtasks');
  const reviewDetails=reviewTask.locator('details.plan-task-details');
  await page.screenshot({path:path.join(root,'plan-progress.png'),fullPage:true});
  await reviewDetails.locator('summary').click();
  await expect(reviewDetails).toContainText('Parent waits for owner review of each subtask');
  await expect(reviewDetails.locator('dd').nth(0).locator('a[href^="#plan-task-"]')).toHaveCount(7);
  await taskDetails.locator('summary').click();
  await expect(taskDetails).toContainText('Subtask of');
  await expect(taskDetails).toContainText('review');
  await expect(taskDetails).toContainText('Acceptance criteria');
  await expect(taskDetails).toContainText('Expected outputs');
  await page.getByRole('link',{name:'Memory',exact:true}).click();
  await expect(page.getByRole('heading',{name:'Memory, with you in control'})).toBeVisible();
  await page.getByRole('link',{name:'Skills',exact:true}).click();
  await expect(page.getByRole('heading',{name:'Procedure candidates'})).toBeVisible();
  await page.getByRole('link',{name:'Automations',exact:true}).click();
  await expect(page.getByRole('heading',{name:'Automations',level:1,exact:true})).toBeVisible();
  await page.getByRole('link',{name:'System monitor',exact:true}).click();
  await expect(page.getByRole('heading',{name:'System monitor',exact:true})).toBeVisible();
  const report={passed:true,run_id:run.id,task_ids:['inspect','inventory',...Array.from({length:5},(_,index)=>`extra-${index+1}`),'review'],dependency_stages:2,stage_one_tasks:7,stage_two_tasks:1,nested_parent_id:'review',nested_child_count:7,parent_waits_for_owner_review:true,subtask_parent_links_verified:true,child_to_parent_graph_edges:7,stage_progress_bars_verified:true,task_purpose_and_expected_outputs_visible_in_graph:true,task_details_collapsed_by_default:true,expected_output_visible_in_task_summary:true,task_details_expand_verified:true,large_graph_viewport_scroll_verified:true,all_tasks_waiting:true,inference_allowance_verified:true,saved_inference_attempts:1,iterations_remaining:'7 of 8',run_wall_clock_begins_at_planning:true,queue_delay_excluded_seconds:300,elapsed_and_remaining_wall_time_verified:true,singular_task_budget_label_verified:true,specialist_resource_usage_display_verified:true,on_demand_screens_verified:['Memory','Skills','Automations','System monitor','Saved runs','Conversations'],on_demand_integration_panels_verified:['Search','Specialist profiles','Channels','MCP','Agent Plugins','Command sandbox'],dispatch_disabled_without_model:true,queued_cancel_remains_enabled:true,exit_on_close_behavior_selected:true,model_inference_requested:false,desktop_sha256:desktopSha256,engine_sha256:engineSha256};
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
  if(!gracefulShutdownLogObserved||!shutdownLog.includes('Finished server process'))throw new Error('Native desktop did not record graceful engine shutdown');
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
