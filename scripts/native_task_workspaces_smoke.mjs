// Real native review/integration of private candidates produced by real source specialists.
import {chromium,expect} from '@playwright/test';
import {spawn,execFileSync} from 'node:child_process';
import {readFile,writeFile} from 'node:fs/promises';
import {createHash,randomUUID} from 'node:crypto';
import {createServer} from 'node:net';
import path from 'node:path';

const model=process.env.XAA_NATIVE_TASK_MODEL;
const distribution=process.env.XAA_NATIVE_TASK_DISTRIBUTION;
if(!model)throw new Error('Explicit installed XAA_NATIVE_TASK_MODEL is required');
const root=path.resolve('.acceptance',randomUUID());
execFileSync(path.resolve('.venv/Scripts/python.exe'),['scripts/live_task_workspaces_diagnostic.py','--root',root,'--model',model,'--prepare-only',...(distribution?['--distribution',distribution]:[])],{windowsHide:true,stdio:'inherit'});
const prepared=JSON.parse(await readFile(path.join(root,'report.json'),'utf8'));
if(!prepared.passed||!prepared.prepare_only||!prepared.private_variants_independently_validated)throw new Error('Source specialists did not produce independently checked private variants');
const sourceFile=path.join(root,'project','shared.py');
const hash=data=>createHash('sha256').update(data).digest('hex');
const originalHash=hash(await readFile(sourceFile));
const manifest=JSON.parse(await readFile('build/native-dev/manifest.json','utf8'));
for(const [name,digest] of Object.entries(manifest))if(hash(await readFile(path.join('build/native-dev',name)))!==digest)throw new Error('Native staged binary hash mismatch');
const listener=createServer();await new Promise(resolve=>listener.listen(0,'127.0.0.1',resolve));const port=listener.address().port;await new Promise((resolve,reject)=>listener.close(error=>error?reject(error):resolve()));
const logPath=path.join(process.env.LOCALAPPDATA,'dev.xyst.xaa','logs','engine.log');const priorLog=(await readFile(logPath,'utf8').catch(()=>'' )).length;
const nativeEnv=Object.fromEntries(Object.entries(process.env).filter(([key])=>!['PATH','PYTHONPATH','VIRTUAL_ENV'].includes(key.toUpperCase())));nativeEnv.PATH=path.join(process.env.SystemRoot,'System32');
const proc=spawn(path.resolve('build/native-dev/xaa-desktop.exe'),[],{windowsHide:true,stdio:'ignore',env:{...nativeEnv,XAA_TEST_HIDDEN:'1',XAA_DATA_DIR:path.join(root,'data'),WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS:`--remote-debugging-port=${port}`}});
let browser,connection;
const report={passed:false,run_id:prepared.run_id,model,build_sha256:manifest,fixture_review_automated:true,scope:'Real source-model private variants; native Tauri IPC/WebView review and frozen integration workers under System32-only runtime PATH. Not native model inference, autonomous review or full parent completion.'};
try{
  for(let attempt=0;attempt<80;attempt++){
    try{browser=await chromium.connectOverCDP(`http://127.0.0.1:${port}`);break;}
    catch{if(proc.exitCode!==null)throw new Error('Native process exited during startup');await new Promise(resolve=>setTimeout(resolve,250));}
  }
  if(!browser)throw new Error('Native WebView did not expose test connection');
  const page=browser.contexts()[0].pages()[0];await page.getByLabel('Local model').waitFor({timeout:30000});
  connection=await page.evaluate(()=>window.__TAURI_INTERNALS__.invoke('engine_connection'));
  const headers={Authorization:`Bearer ${connection.token}`};
  async function get(route){const response=await fetch(connection.base_url+route,{headers,signal:AbortSignal.timeout(15000)});if(!response.ok)throw new Error(`Native metadata read failed (${response.status})`);return response.json();}
  await page.getByRole('button',{name:/^Open Compare alternative implementations/}).click();
  await page.getByRole('heading',{name:'Active run verifying',exact:true}).waitFor();
  for(const name of ['alpha','beta']){
    const panel=page.getByRole('article',{name:`Task ${name}`,exact:true});
    await panel.getByRole('button',{name:'Review candidate',exact:true}).click();
    await expect(panel.getByRole('region',{name:'Private specialist changes'})).toBeVisible();
    await expect(panel.getByText(prepared.codes[name],{exact:false}).first()).toBeVisible();
    if(distribution){await expect(panel.getByText(/PRIVATE_VALIDATED/).first()).toBeVisible();await expect(panel.getByText(/Matches current private files/)).toBeVisible();await panel.getByRole('button',{name:'Inspect saved stdout',exact:true}).last().click();await expect(panel.getByText('Loading saved outputâ€¦',{exact:true})).toHaveCount(0);}
    expect(hash(await readFile(sourceFile))).toBe(name==='alpha'?originalHash:prepared.proposed_sha256.alpha);
    // The preparer statically validated the exact candidate bytes; review full native diff/evidence.
    for(const checkbox of await panel.getByRole('checkbox').all())await checkbox.check();
    await panel.getByLabel('Review rationale',{exact:true}).fill(`Automated native fixture review: inspected the actual private ${name} diff and file evidence; choose its validated implementation.`);
    const accept=panel.getByRole('button',{name:'Integrate changes and accept task',exact:true});await expect(accept).toBeDisabled();
    await panel.getByRole('button',{name:'Check current project for integration',exact:true}).click();
    const authorization=panel.getByLabel('I reviewed the file changes and authorize these integration choices');
    if(name==='beta'){
      await expect(panel.getByRole('heading',{name:'Conflict: shared.py',exact:true})).toBeVisible();
      await expect(authorization).toBeDisabled();await expect(accept).toBeDisabled();
      await panel.screenshot({path:path.join(root,'native-conflict.png')});
      await panel.getByRole('combobox',{name:'Resolve shared.py for beta',exact:true}).selectOption('proposed');
    }else await expect(panel.getByRole('heading',{name:'Conflict: shared.py',exact:true})).toHaveCount(0);
    await authorization.check();await expect(accept).toBeEnabled();await accept.click();
    await expect.poll(async()=>(await get(`/runs/${prepared.run_id}/plan`)).tasks.find(task=>task.local_id===name).status,{timeout:20000}).toBe('completed');
    expect(hash(await readFile(sourceFile))).toBe(prepared.proposed_sha256[name]);
  }
  const events=await get(`/runs/${prepared.run_id}/events`);
  expect(events.filter(event=>event.event_type==='task.integration.authorized')).toHaveLength(2);
  expect(events.filter(event=>event.event_type==='task.reviewed'&&event.payload.state==='completed')).toHaveLength(2);
  await page.screenshot({path:path.join(root,'native-integrated.png'),fullPage:true});
  report.native_integration_ui_verified=true;report.original_unchanged_before_review=true;report.private_variants_distinct=true;report.explicit_conflict_choice_verified=true;report.final_beta_sha256=hash(await readFile(sourceFile));report.parent_completion_claimed=false;
  report.private_validation_ui_verified=!!distribution;report.validation_distribution=distribution??null;report.private_command_execution_verified=!!prepared.private_command_validation_verified;
  await page.evaluate(()=>window.__TAURI_INTERNALS__.invoke('plugin:window|close',{label:'main'})).catch(()=>{});
  for(let attempt=0;attempt<40&&proc.exitCode===null;attempt++)await new Promise(resolve=>setTimeout(resolve,250));
  if(proc.exitCode===null)throw new Error('Native window close did not stop app');
  let stopped=false;for(let attempt=0;attempt<20;attempt++){try{await fetch(connection.base_url+'/runs',{signal:AbortSignal.timeout(500)});}catch{stopped=true;break;}await new Promise(resolve=>setTimeout(resolve,250));}
  if(!stopped)throw new Error('Native engine remains reachable after window exit');
  const shutdown=(await readFile(logPath,'utf8')).slice(priorLog);await writeFile(path.join(root,'native-shutdown.log'),shutdown);
  if(!shutdown.includes('Application shutdown complete.')||!shutdown.includes('Finished server process'))throw new Error('Graceful native engine shutdown was not observed');
  report.native_exit=true;report.engine_stopped=true;report.graceful_shutdown=true;report.passed=true;
}catch(error){report.error=String(error);await browser?.contexts()[0]?.pages()[0]?.screenshot({path:path.join(root,'native-failure.png'),fullPage:true}).catch(()=>{});throw error;}
finally{
  await browser?.close().catch(()=>{});
  if(proc.exitCode===null)execFileSync('taskkill',['/PID',String(proc.pid),'/T','/F'],{windowsHide:true,stdio:'ignore'});
  await writeFile(path.join(root,'native-report.json'),JSON.stringify(report,null,2));console.log(`Native private-workspace evidence: ${root}`);
}
