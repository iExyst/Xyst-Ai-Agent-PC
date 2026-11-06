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
const started=Date.now();let stdout='',stderr='';
const proc=spawn(executable,[],{windowsHide:true,stdio:['ignore','pipe','pipe'],env:{...env,PATH:path.join(process.env.SystemRoot,'System32'),XAA_TEST_HIDDEN:'1',XAA_DATA_DIR:root,WEBVIEW2_USER_DATA_FOLDER:path.join(root,'webview-profile'),WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS:'--remote-debugging-port=19222'}});
proc.stdout.on('data',chunk=>{stdout=(stdout+chunk.toString()).slice(-32768);});
proc.stderr.on('data',chunk=>{stderr=(stderr+chunk.toString()).slice(-32768);});
let browser,connection;
try{
  const debuggingDeadline=Date.now()+60000;
  let lastDebuggingError='Debugging endpoint unavailable';
  while(Date.now()<debuggingDeadline){
    try{
      const response=await fetch('http://127.0.0.1:19222/json/version',{signal:AbortSignal.timeout(750)});
      if(!response.ok)throw new Error(`Debugging metadata HTTP ${response.status}`);
      const metadata=await response.json();
      if(typeof metadata.webSocketDebuggerUrl!=='string')throw new Error('No WebSocket debugging URL');
      const endpoint=new URL(metadata.webSocketDebuggerUrl);
      if(endpoint.protocol!=='ws:'||!['localhost','127.0.0.1','[::1]'].includes(endpoint.hostname)||endpoint.port!=='19222')throw new Error('Unexpected debugging endpoint address');
      endpoint.hostname='127.0.0.1';
      browser=await chromium.connectOverCDP(endpoint.toString(),{timeout:Math.min(3000,Math.max(1,debuggingDeadline-Date.now()))});
      break;
    }catch(error){lastDebuggingError=String(error)+(error?.cause?.code?` (${error.cause.code})`:'');if(proc.exitCode!==null)throw new Error(`Packaged desktop exited during boot: ${lastDebuggingError}`);await new Promise(resolve=>setTimeout(resolve,250));}
  }
  if(!browser)throw new Error(`Packaged debugging attachment failed: ${lastDebuggingError}`);
  let page;
  const pageDeadline=Date.now()+20000;
  while(Date.now()<pageDeadline){
    page=browser.contexts().flatMap(context=>context.pages())[0];
    if(page)break;
    if(proc.exitCode!==null)throw new Error('Packaged desktop exited before its WebView page appeared');
    await new Promise(resolve=>setTimeout(resolve,100));
  }
  if(!page)throw new Error('Packaged WebView debugging endpoint did not expose a page');
  await expect(page.getByRole('heading',{name:'Xyst AI Agent',exact:true})).toBeVisible({timeout:20000});
  connection=await page.evaluate(()=>window.__TAURI_INTERNALS__.invoke('engine_connection'));
  let ready=false;
  for(let attempt=0;attempt<240;attempt++){
    try{const response=await fetch(`${connection.base_url}/runs`,{headers:{Authorization:`Bearer ${connection.token}`},signal:AbortSignal.timeout(500)});if(response.ok){const history=await response.json();if(history.items.length)throw new Error('Isolated boot unexpectedly has runs');ready=true;break;}}catch{}
    await new Promise(resolve=>setTimeout(resolve,250));
  }
  if(!ready)throw new Error('Bundled engine API did not become ready');
  const startupMilliseconds=Date.now()-started;
  const denied=await fetch(`${connection.base_url}/runs`);if(denied.status!==401)throw new Error('Bundled API did not require authentication');
  await page.screenshot({path:path.join(root,'release-boot.png'),fullPage:true});
  await page.evaluate(()=>window.__TAURI_INTERNALS__.invoke('plugin:window|close',{label:'main'})).catch(()=>{});
  const closeStarted=Date.now();
  const closeDeadline=closeStarted+30000;
  while(proc.exitCode===null&&Date.now()<closeDeadline)await new Promise(resolve=>setTimeout(resolve,250));
  const closeWaitMilliseconds=Date.now()-closeStarted;
  if(proc.exitCode===null)throw new Error('Packaged desktop did not close');
  let stopped=false;try{await fetch(`${connection.base_url}/runs`,{signal:AbortSignal.timeout(1000)});}catch{stopped=true;}
  if(!stopped)throw new Error('Bundled engine survived desktop closure');
  const log=(await readFile(logPath,'utf8')).slice(previous.length);
  if(!log.includes('Application shutdown complete.'))throw new Error('No graceful engine shutdown evidence');
  const report={passed:true,executable,startup_milliseconds:startupMilliseconds,close_wait_milliseconds:closeWaitMilliseconds,restricted_runtime_path:true,bundled_engine_ready:true,authentication_required:true,no_runs_created:true,model_inference_requested:false,desktop_exit:true,engine_stopped:true,graceful_shutdown:true};
  await writeFile(path.join(root,'report.json'),JSON.stringify(report,null,2));console.log(report,root);
}catch(error){
  let processDiagnostics;
  try{processDiagnostics=JSON.parse(execFileSync('pwsh.exe',['-NoProfile','-NonInteractive','-File',path.resolve('scripts/native_boot_diagnostics.ps1'),'-DesktopProcessId',String(proc.pid)],{windowsHide:true,encoding:'utf8',timeout:15000,maxBuffer:65536}));}catch(diagnosticError){processDiagnostics={error:String(diagnosticError)};}
  await writeFile(path.join(root,'desktop-stdout.log'),stdout);await writeFile(path.join(root,'desktop-stderr.log'),stderr);
  const engineLog=(await readFile(logPath,'utf8').catch(()=>''));await writeFile(path.join(root,'engine-startup.log'),engineLog.slice(previous.length));
  await writeFile(path.join(root,'report.json'),JSON.stringify({passed:false,error:String(error),desktop_exit_code:proc.exitCode,desktop_signal:proc.signalCode,process_diagnostics:processDiagnostics,elapsed_milliseconds:Date.now()-started},null,2));throw error;
}
finally{await browser?.close().catch(()=>{});if(proc.exitCode===null)execFileSync('taskkill',['/PID',String(proc.pid),'/T','/F'],{windowsHide:true,stdio:'ignore'});}
