// Native Tauri/frozen-engine command permission acceptance. Explicit installed dependencies only.
import {chromium,expect} from '@playwright/test';
import {spawn,execFileSync} from 'node:child_process';
import {mkdir,readFile,writeFile,readdir} from 'node:fs/promises';
import {randomUUID,createHash} from 'node:crypto';
import {createServer} from 'node:net';
import path from 'node:path';

const distribution=process.env.XAA_NATIVE_COMMAND_DISTRO;
const model=process.env.XAA_NATIVE_COMMAND_MODEL;
if(!distribution||!model)throw new Error('Explicit installed distro and model required: XAA_NATIVE_COMMAND_DISTRO / XAA_NATIVE_COMMAND_MODEL');
const root=path.resolve('.acceptance',randomUUID());await mkdir(root,{recursive:true});
const project=path.join(root,'project');await mkdir(project);
const fixProject=process.env.XAA_NATIVE_PROJECT_FIX==='1';
const code='NATIVE-CHECK-'+randomUUID().replaceAll('-','');
const script=fixProject?`import runpy\nf=runpy.run_path('calculator.py')['percentage']\nfor amount,percent,expected in [(200,15,30),(50,10,5),(0,15,0),(-80,25,-20),(1,12.5,0.125)]:\n    assert abs(f(amount,percent)-expected)<1e-9, (amount,percent,expected,f(amount,percent))\nprint('${code}')\n`:`from pathlib import Path\nPath('private-generated.txt').write_text('private only')\nprint('${code}')\n`;
await writeFile(path.join(project,'check.py'),script);
const broken='def percentage(amount, percent):\n    return amount * percent\n';
if(fixProject)await writeFile(path.join(project,'calculator.py'),broken);
const manifest=JSON.parse(await readFile('build/native-dev/manifest.json','utf8'));
for(const [name,digest] of Object.entries(manifest)){
  if(createHash('sha256').update(await readFile(path.join('build/native-dev',name))).digest('hex')!==digest)throw new Error('Staged native manifest does not match binary bytes');
}
const listener=createServer();await new Promise(resolve=>listener.listen(0,'127.0.0.1',resolve));
const port=listener.address().port;await new Promise((resolve,reject)=>listener.close(error=>error?reject(error):resolve()));
const logPath=path.join(process.env.LOCALAPPDATA,'dev.xyst.xaa','logs','engine.log');
const priorLog=(await readFile(logPath,'utf8').catch(()=>'' )).length;
const nativeEnv=Object.fromEntries(Object.entries(process.env).filter(([key])=>!['PATH','PYTHONPATH','VIRTUAL_ENV'].includes(key.toUpperCase())));
nativeEnv.PATH=path.join(process.env.SystemRoot,'System32');
const proc=spawn(path.resolve('build/native-dev/xaa-desktop.exe'),[],{windowsHide:true,stdio:'ignore',env:{...nativeEnv,XAA_TEST_HIDDEN:'1',XAA_DATA_DIR:path.join(root,'data'),XAA_EXACT_CONTEXT:'1',WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS:`--remote-debugging-port=${port}`}});
let browser,connection,instance;
const report={passed:false,distribution,model,build_sha256:manifest,scope:'Native development Tauri WebView + bundled frozen engine + real LM Studio/WSL; no connection shim; not installed/clean-machine/upgrade/reboot acceptance',fixture_review_automated:true,project_fix_requested:fixProject};
try{
  for(let attempt=0;attempt<80;attempt++){
    try{browser=await chromium.connectOverCDP(`http://127.0.0.1:${port}`);break;}
    catch{if(proc.exitCode!==null)throw new Error('Native app exited during startup');await new Promise(resolve=>setTimeout(resolve,250));}
  }
  if(!browser)throw new Error('No native WebView debugging connection');
  const page=browser.contexts()[0].pages()[0];
  await page.getByLabel('Local model').waitFor({timeout:30000});
  await expect(page.getByLabel('Local model')).toHaveValue('');
  connection=await page.evaluate(()=>window.__TAURI_INTERNALS__.invoke('engine_connection'));
  const headers={Authorization:`Bearer ${connection.token}`,'Content-Type':'application/json'};
  async function api(route,method='GET',data){
    const response=await fetch(connection.base_url+route,{method,headers,body:data===undefined?undefined:JSON.stringify(data),signal:AbortSignal.timeout(60000)});
    if(!response.ok)throw new Error(`Native API ${route} failed (${response.status})`);
    return response.json();
  }
  if((await fetch(connection.base_url+'/runs')).status!==401)throw new Error('Native API did not require authentication');
  const inventory=await api('/models');if(!inventory.some(item=>item.id===model))throw new Error('Requested model is not installed; no download is permitted');
  instance=(await api('/models/load','POST',{model,context_length:32768})).instance_id;
  await page.getByText('Command sandbox setup',{exact:true}).click();
  await page.getByRole('button',{name:'Find installed distributions',exact:true}).click();
  await page.getByLabel('Execution distribution').selectOption(distribution);
  const readiness=page.waitForResponse(response=>new URL(response.url()).pathname==='/execution/readiness');
  await page.getByRole('button',{name:'Check sandbox',exact:true}).click();
  const ready=await(await readiness).json();if(ready.state!=='ready')throw new Error('Native frozen sandbox readiness failed');
  report.profile_sha256=ready.profile_sha256;report.checks=ready.checks;
  await page.getByLabel('Workspace permissions').selectOption('workspace');
  await page.getByLabel('Workspace path').fill(project);
  await page.getByLabel('Local model').selectOption(model);
  await page.getByLabel('Objective',{exact:true}).fill(fixProject?'The project percentage calculator returns the wrong answer: percentage(200,15) should be 30. Inspect calculator.py and check.py, diagnose and fix calculator.py using the workspace file tools, then execute the existing check.py exactly once using workspace_run_command with argv ["/usr/bin/python3", "-I", "check.py"] and max_seconds 10. Do not edit check.py, create extra files or save a plan. After the check passes, report the changed filename, the fix and the entire NATIVE-CHECK- stdout line. All retained edits must use workspace file tools because command changes are discarded.':'Please execute the existing project check.py using workspace_run_command with argv ["/usr/bin/python3", "-I", "check.py"] and max_seconds 10. Tell me what it printed in stdout. Include the entire NATIVE-CHECK- line in your final answer. Use only this one command. Do not read or edit files or save a plan.');
  await page.getByText('Run limits',{exact:true}).click();
  await page.getByLabel('Iteration limit',{exact:true}).fill(fixProject?'10':'4');
  await page.getByLabel('Time limit (seconds)',{exact:true}).fill(fixProject?'300':'180');
  await page.getByLabel('Output tokens per request (optional)',{exact:true}).fill('2048');
  await page.getByLabel('Allow isolated commands for this next run').check();
  const creation=page.waitForResponse(response=>new URL(response.url()).pathname==='/runs'&&response.request().method()==='POST');
  await page.getByRole('button',{name:'Start objective',exact:true}).click();
  const response=await creation;if(response.status()!==201)throw new Error(`Native grant/run creation failed (${response.status()})`);
  const created=await response.json();report.run_id=created.id;
  if(created.command_grant?.distribution!==distribution||created.command_grant?.profile_sha256!==ready.profile_sha256)throw new Error('Saved grant differs from explicitly selected sandbox');
  await expect.poll(async()=>(await api(`/runs/${created.id}`)).state,{timeout:240000}).toMatch(/^(verifying|blocked|failed|cancelled)$/);
  await page.getByRole('heading',{name:'Active run verifying',exact:true}).waitFor({timeout:5000});
  await expect(page.getByRole('article')).toContainText(code,{timeout:5000});
  await expect(page.getByText(/Isolated commands authorized for this run in /)).toBeVisible();
  await expect(page.getByLabel('Allow isolated commands for this next run')).not.toBeChecked();
  const events=await api(`/runs/${created.id}/events`);
  const messages=events.filter(event=>event.event_type==='model.message').map(event=>event.payload.message);
  const proposals=messages.flatMap(message=>message.tool_calls??[]).filter(call=>call.function.name==='workspace_run_command');
  expect(proposals).toHaveLength(1);expect(JSON.parse(proposals[0].function.arguments).argv).toEqual(['/usr/bin/python3','-I','check.py']);
  expect(events.filter(event=>event.event_type==='tool.executing'&&event.payload.execution_id)).toHaveLength(1);
  expect(events.filter(event=>event.event_type==='execution.completed')).toHaveLength(1);
  const artifacts=await api(`/runs/${created.id}/artifacts`);
  const stdout=artifacts.find(item=>item.provenance.startsWith('execution.stdout:'));
  if(!stdout)throw new Error('No registered command stdout');
  const output=await(await fetch(connection.base_url+'/artifacts/'+stdout.id,{headers})).text();
  if(output!==code+'\n')throw new Error('Actual stdout artifact differs from fixture result');
  expect(await readFile(path.join(project,'check.py'),'utf8')).toBe(script);
  await expect(readFile(path.join(project,'private-generated.txt'))).rejects.toThrow();
  if(fixProject){
    expect((await readdir(project)).sort()).toEqual(['calculator.py','check.py']);
    const corrected=await readFile(path.join(project,'calculator.py'));
    if(corrected.toString('utf8')===broken)throw new Error('Calculator source was not changed');
    const snapshot=artifacts.find(item=>item.provenance.startsWith('execution.snapshot:'));
    if(!snapshot)throw new Error('No command snapshot manifest');
    const manifest=await(await fetch(connection.base_url+'/artifacts/'+snapshot.id,{headers})).json();
    const bound=manifest.files.find(item=>item.path==='calculator.py');
    if(bound?.sha256!==createHash('sha256').update(corrected).digest('hex'))throw new Error('Tested snapshot differs from retained calculator source');
    const changes=messages.flatMap(message=>message.tool_calls??[]).filter(call=>['workspace_write_file','workspace_replace_text','workspace_apply_patch'].includes(call.function.name));
    if(!changes.length)throw new Error('No model-proposed durable workspace edit');
    const answer=messages.at(-1)?.content??'';if(!answer.includes('calculator.py')||!answer.includes(code))throw new Error('Final candidate omitted changed file or actual test evidence');
    report.project_fix_verified=true;report.changed_files=['calculator.py'];report.corrected_sha256=bound.sha256;report.tested_snapshot_matches_retained_file=true;report.project_test_cases=5;
  }

  // Fixture-only review after independently checking artifacts, not a fabricated user decision.
  await page.getByLabel('I reviewed the objective, result, and available evidence').check();
  await page.getByLabel('Final review rationale').fill('Automated acceptance fixture: exact registered stdout, one command launch, original check.py and private write absence were independently checked. In project-fix mode, the tested snapshot hash must match the retained corrected calculator.');
  await page.getByRole('button',{name:'Accept and complete run',exact:true}).click();
  await page.getByRole('heading',{name:'Active run completed',exact:true}).waitFor({timeout:20000});
  const saved=await api(`/runs/${created.id}`);if(saved.state!=='completed')throw new Error('Fixture review did not complete');
  await page.screenshot({path:path.join(root,'native.png'),fullPage:true});
  await api('/models/unload','POST',{instance_id:instance});instance=undefined;report.owned_model_unloaded=true;
  report.command_proposals=1;report.launch_events=1;report.completed_commands=1;report.exact_stdout_artifact_verified=true;report.original_project_unchanged=!fixProject;report.state=saved.state;report.native_grant_ui_verified=true;report.restricted_runtime_path=true;
  await page.evaluate(()=>window.__TAURI_INTERNALS__.invoke('plugin:window|close',{label:'main'})).catch(()=>{});
  for(let attempt=0;attempt<40&&proc.exitCode===null;attempt++)await new Promise(resolve=>setTimeout(resolve,250));
  if(proc.exitCode===null)throw new Error('Native window close did not stop desktop');
  let stopped=false;for(let attempt=0;attempt<20;attempt++){
    try{await fetch(connection.base_url+'/runs',{signal:AbortSignal.timeout(500)});}catch{stopped=true;break;}
    await new Promise(resolve=>setTimeout(resolve,250));
  }
  if(!stopped)throw new Error('Owned native engine remains reachable');
  const shutdown=(await readFile(logPath,'utf8')).slice(priorLog);await writeFile(path.join(root,'shutdown.log'),shutdown);
  if(!shutdown.includes('Application shutdown complete.')||!shutdown.includes('Finished server process'))throw new Error('Native engine graceful shutdown was not observed');
  report.native_exit=true;report.engine_stopped=true;report.graceful_shutdown=true;report.passed=true;
}catch(error){report.error=String(error);await browser?.contexts()[0]?.pages()[0]?.screenshot({path:path.join(root,'failure.png'),fullPage:true}).catch(()=>{});throw error;}
finally{
  if(instance&&connection)await fetch(connection.base_url+'/models/unload',{method:'POST',headers:{Authorization:`Bearer ${connection.token}`,'Content-Type':'application/json'},body:JSON.stringify({instance_id:instance}),signal:AbortSignal.timeout(20000)}).catch(()=>{});
  await browser?.close().catch(()=>{});
  if(proc.exitCode===null)execFileSync('taskkill',['/PID',String(proc.pid),'/T','/F'],{windowsHide:true,stdio:'ignore'});
  await writeFile(path.join(root,'report.json'),JSON.stringify(report,null,2));console.log(`Native command evidence: ${root}`);
}
