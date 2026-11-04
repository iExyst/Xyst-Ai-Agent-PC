import {chromium,expect} from '@playwright/test';
import {spawn,execFileSync} from 'node:child_process';
import {mkdir,readFile,writeFile} from 'node:fs/promises';
import {randomUUID} from 'node:crypto';
import path from 'node:path';

const executable=path.resolve(process.argv[2]??'build/native-release/xaa-desktop.exe');
const root=path.resolve('.acceptance',randomUUID());await mkdir(root,{recursive:true});
const logPath=path.join(process.env.LOCALAPPDATA,'dev.xyst.xaa','logs','engine.log');
const previous=await readFile(logPath,'utf8').catch(()=>'');
const env=Object.fromEntries(Object.entries(process.env).filter(([key])=>!['PATH','PYTHONPATH','VIRTUAL_ENV'].includes(key.toUpperCase())));
const proc=spawn(executable,[],{windowsHide:true,stdio:'ignore',env:{...env,PATH:path.join(process.env.SystemRoot,'System32'),XAA_TEST_HIDDEN:'1',XAA_DATA_DIR:root,WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS:'--remote-debugging-port=19222'}});
let browser,connection;
try{
  for(let attempt=0;attempt<80;attempt++){
    try{browser=await chromium.connectOverCDP('http://127.0.0.1:19222');break;}catch{if(proc.exitCode!==null)throw new Error('Packaged desktop exited during boot');await new Promise(resolve=>setTimeout(resolve,250));}
  }
  if(!browser)throw new Error('Packaged WebView did not become ready');
  const page=browser.contexts()[0].pages()[0];
  await expect(page.getByRole('heading',{name:'Xyst AI Agent',exact:true})).toBeVisible({timeout:20000});
  connection=await page.evaluate(()=>window.__TAURI_INTERNALS__.invoke('engine_connection'));
  let ready=false;
  for(let attempt=0;attempt<80;attempt++){
    try{const response=await fetch(`${connection.base_url}/runs`,{headers:{Authorization:`Bearer ${connection.token}`},signal:AbortSignal.timeout(500)});if(response.ok){const history=await response.json();if(history.items.length)throw new Error('Isolated boot unexpectedly has runs');ready=true;break;}}catch{}
    await new Promise(resolve=>setTimeout(resolve,250));
  }
  if(!ready)throw new Error('Bundled engine API did not become ready');
  const denied=await fetch(`${connection.base_url}/runs`);if(denied.status!==401)throw new Error('Bundled API did not require authentication');
  await page.screenshot({path:path.join(root,'release-boot.png'),fullPage:true});
  await page.evaluate(()=>window.__TAURI_INTERNALS__.invoke('plugin:window|close',{label:'main'})).catch(()=>{});
  for(let attempt=0;attempt<60&&proc.exitCode===null;attempt++)await new Promise(resolve=>setTimeout(resolve,250));
  if(proc.exitCode===null)throw new Error('Packaged desktop did not close');
  let stopped=false;try{await fetch(`${connection.base_url}/runs`,{signal:AbortSignal.timeout(1000)});}catch{stopped=true;}
  if(!stopped)throw new Error('Bundled engine survived desktop closure');
  const log=(await readFile(logPath,'utf8')).slice(previous.length);
  if(!log.includes('Application shutdown complete.'))throw new Error('No graceful engine shutdown evidence');
  const report={passed:true,executable,restricted_runtime_path:true,bundled_engine_ready:true,authentication_required:true,no_runs_created:true,model_inference_requested:false,desktop_exit:true,engine_stopped:true,graceful_shutdown:true};
  await writeFile(path.join(root,'report.json'),JSON.stringify(report,null,2));console.log(report,root);
}catch(error){await writeFile(path.join(root,'report.json'),JSON.stringify({passed:false,error:String(error)},null,2));throw error;}
finally{await browser?.close().catch(()=>{});if(proc.exitCode===null)execFileSync('taskkill',['/PID',String(proc.pid),'/T','/F'],{windowsHide:true,stdio:'ignore'});}
