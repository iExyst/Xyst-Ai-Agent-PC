// Frozen Tauri acceptance for the integrated-project validation and stale-result gate.
import {chromium,expect} from '@playwright/test';
import {spawn,execFileSync} from 'node:child_process';
import {mkdir,readFile,writeFile} from 'node:fs/promises';
import {createHash,randomUUID} from 'node:crypto';
import {createServer} from 'node:net';
import path from 'node:path';

const root=path.resolve('.acceptance',randomUUID());await mkdir(root,{recursive:true});
const fixture=JSON.parse(execFileSync('uv',['run','python','scripts/prepare_integrated_validation_fixture.py',root],{encoding:'utf8',windowsHide:true}));
const manifest=JSON.parse(await readFile('build/native-dev/manifest.json','utf8'));
for(const [name,digest] of Object.entries(manifest))if(createHash('sha256').update(await readFile(path.join('build/native-dev',name))).digest('hex')!==digest)throw new Error('Native staged binary hash mismatch');
const listener=createServer();await new Promise(resolve=>listener.listen(0,'127.0.0.1',resolve));const port=listener.address().port;await new Promise((resolve,reject)=>listener.close(error=>error?reject(error):resolve()));
const nativeEnv=Object.fromEntries(Object.entries(process.env).filter(([key])=>!['PATH','PYTHONPATH','VIRTUAL_ENV'].includes(key.toUpperCase())));nativeEnv.PATH=path.join(process.env.SystemRoot,'System32');
const proc=spawn(path.resolve('build/native-dev/xaa-desktop.exe'),[],{windowsHide:true,stdio:'ignore',env:{...nativeEnv,XAA_TEST_HIDDEN:'1',XAA_DATA_DIR:fixture.data,WEBVIEW2_USER_DATA_FOLDER:path.join(root,'webview'),WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS:`--remote-debugging-port=${port}`}});
let browser,connection,reviewResponse;const report={passed:false,scope:'Frozen development Tauri app and bundled engine, actual Ubuntu-24.04 WSL command; no model inference; not installer acceptance',build_sha256:manifest,run_id:fixture.run_id};
try{
  for(let attempt=0;attempt<100;attempt++){try{browser=await chromium.connectOverCDP(`http://127.0.0.1:${port}`);break;}catch{if(proc.exitCode!==null)throw new Error('Native app exited during boot');await new Promise(resolve=>setTimeout(resolve,250));}}
  if(!browser)throw new Error('Could not attach to the native WebView');
  const page=browser.contexts()[0].pages()[0];await expect(page.getByRole('heading',{name:'Xyst AI Agent',exact:true})).toBeVisible();connection=await page.evaluate(()=>window.__TAURI_INTERNALS__.invoke('engine_connection'));
  page.on('response',async response=>{if(new URL(response.url()).pathname.endsWith('/review'))reviewResponse={status:response.status(),body:await response.text().catch(()=> '')};});
  let ready=false;for(let attempt=0;attempt<120;attempt++){try{const response=await fetch(connection.base_url+`/runs/${fixture.run_id}`,{headers:{Authorization:`Bearer ${connection.token}`},signal:AbortSignal.timeout(500)});if(response.ok){ready=true;break;}}catch{}await new Promise(resolve=>setTimeout(resolve,250));}if(!ready)throw new Error('Seeded run unavailable through native API');
  await page.getByRole('button',{name:'Open Validate the integrated answer.py project',exact:true}).click();
  const panel=page.getByRole('region',{name:'Final run review'});await expect(panel.getByText(/Current project SHA-256/)).toBeVisible({timeout:30000});
  const argv=`["/usr/bin/python3","-I","-c","import runpy; value=runpy.run_path('answer.py')['answer'](); assert value == 42, value; print('NATIVE_INTEGRATED_ANSWER', value)"]`;
  await panel.getByLabel('Command arguments as a JSON string array').fill(argv);await panel.getByRole('button',{name:'Run check (15 second limit)',exact:true}).click();
  await expect(panel.getByText(/NATIVE_INTEGRATED_ANSWER 42/)).toBeVisible({timeout:60000});
  await writeFile(path.join(fixture.workspace,'answer.py'),'def answer():\n    return 42\n\n');
  await panel.getByRole('button',{name:'Refresh current project',exact:true}).click();
  await expect(panel.getByText(/Stale: integrated project changed after this command/)).toBeVisible({timeout:30000});
  const validationGroup=panel.getByRole('group',{name:'Integrated project validation'});report.stale_evidence_text=await validationGroup.innerText();
  const accept=panel.getByRole('button',{name:'Accept and complete run',exact:true});await expect(accept).toBeDisabled();
  await panel.getByRole('button',{name:'Run check (15 second limit)',exact:true}).click();
  await expect(panel.getByText(/NATIVE_INTEGRATED_ANSWER 42/).last()).toBeVisible({timeout:60000});
  await panel.getByRole('button',{name:'Refresh current project',exact:true}).click();
  const currentCheckbox=validationGroup.locator('input[type="checkbox"]:enabled').last();await expect(currentCheckbox).toBeVisible();await currentCheckbox.check();report.current_evidence_text=await validationGroup.innerText();
  await panel.getByLabel('I reviewed the objective, result, and available evidence',{exact:true}).check();await panel.getByLabel('Confirm criterion: answer() returns 42',{exact:true}).check();await panel.getByLabel('Final review rationale').fill('The current integrated project passed the restricted WSL check.');await expect(accept).toBeEnabled();
  await accept.click();await expect(page.getByRole('heading',{name:'Active run completed'})).toBeVisible({timeout:30000});
  const finalResponse=await fetch(connection.base_url+`/runs/${fixture.run_id}`,{headers:{Authorization:`Bearer ${connection.token}`}});const finalRun=await finalResponse.json();if(finalRun.state!=='completed')throw new Error('Native run did not complete');
  const eventsResponse=await fetch(connection.base_url+`/runs/${fixture.run_id}/events`,{headers:{Authorization:`Bearer ${connection.token}`}});const events=await eventsResponse.json();report.review_event=events.find(event=>event.event_type==='run.reviewed')?.payload;report.passed=true;report.final_state=finalRun.state;report.stale_validation_blocked=true;report.refreshed_validation_accepted=true;report.actual_wsl=true;report.model_inference=false;
  await writeFile(path.join(root,'report.json'),JSON.stringify(report,null,2));console.log(report,root);
}catch(error){report.error=String(error);report.review_response=reviewResponse;await writeFile(path.join(root,'report.json'),JSON.stringify(report,null,2));throw error;}
finally{await browser?.close().catch(()=>{});if(proc.exitCode===null){try{execFileSync('taskkill',['/PID',String(proc.pid),'/T','/F'],{windowsHide:true,stdio:'ignore'});}catch{}}}
