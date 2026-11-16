import {chromium,expect} from '@playwright/test';
import {spawn,execFileSync} from 'node:child_process';
import {mkdir,readFile,writeFile} from 'node:fs/promises';
import {randomUUID} from 'node:crypto';
import path from 'node:path';

const executable=path.resolve(process.argv[2]??'build/native-release/xaa-desktop.exe');
const root=path.resolve('.acceptance',`native-emergency-stop-${randomUUID()}`);
const localAppData=path.join(root,'local-app-data');
const logs=path.join(root,'logs');
const logPath=path.join(logs,'engine.log');
const webviewProfile=path.join(root,'webview-profile');
const recoveryWebviewProfile=path.join(root,'recovery-webview-profile');
const port=Number(process.env.XAA_NATIVE_EMERGENCY_STOP_DEBUG_PORT||19222);
const baseUrl=`http://127.0.0.1:${port}`;
const env=Object.fromEntries(Object.entries(process.env).filter(([key])=>!['PATH','PYTHONPATH','VIRTUAL_ENV','XAA_DATA_DIR'].includes(key.toUpperCase())));
await mkdir(logs,{recursive:true});
const previousLog=await readFile(logPath,'utf8').catch(()=> '');
const proc=spawn(executable,['--background'],{windowsHide:true,stdio:['ignore','pipe','pipe'],env:{...env,PATH:path.join(process.env.SystemRoot,'System32'),LOCALAPPDATA:localAppData,APPDATA:path.join(root,'roaming-app-data'),XAA_TEST_HIDDEN:'0',XAA_TEST_ACCEPTANCE:'1',XAA_TEST_CONFIG_DIR:path.join(root,'config'),XAA_TEST_LOG_DIR:logs,WEBVIEW2_USER_DATA_FOLDER:webviewProfile,WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS:`--remote-debugging-port=${port}`}});
let stdout='',stderr='',browser,page,connection,recoveryBrowser,recoveryPage,recoveryProc;
let stopResponse;
proc.stdout.on('data',chunk=>{stdout=(stdout+chunk.toString()).slice(-32768);});
proc.stderr.on('data',chunk=>{stderr=(stderr+chunk.toString()).slice(-32768);});
const waitFor=async(predicate,timeout,message)=>{
  const deadline=Date.now()+timeout;
  while(Date.now()<deadline){if(await predicate())return;await new Promise(resolve=>setTimeout(resolve,100));}
  throw new Error(message);
};
try{
  let endpoint;
  await waitFor(async()=>{
    if(proc.exitCode!==null)throw new Error(`Desktop exited during startup (${proc.exitCode}).`);
    try{
      const response=await fetch(`http://127.0.0.1:${port}/json/version`,{signal:AbortSignal.timeout(500)});
      if(!response.ok)return false;
      const metadata=await response.json();
      endpoint=new URL(metadata.webSocketDebuggerUrl);
      if(endpoint.protocol!=='ws:'||!['localhost','127.0.0.1','[::1]'].includes(endpoint.hostname)||endpoint.port!==String(port))throw new Error('Unexpected WebView debugging endpoint.');
      endpoint.hostname='127.0.0.1';
      return true;
    }catch(error){if(String(error).includes('Unexpected WebView'))throw error;return false;}
  },60000,'The desktop WebView debugging endpoint did not start.');
  browser=await chromium.connectOverCDP(endpoint.toString(),{timeout:10000});
  page=browser.contexts().flatMap(context=>context.pages())[0];
  if(!page)throw new Error('The desktop did not expose its WebView page.');
  await expect(page.getByRole('navigation',{name:'Main sections'})).toBeVisible({timeout:20000});
  connection=await page.evaluate(()=>window.__TAURI_INTERNALS__.invoke('engine_connection'));
  await waitFor(async()=>{
    try{
      const response=await fetch(`${connection.base_url}/runs`,{headers:{Authorization:`Bearer ${connection.token}`},signal:AbortSignal.timeout(750)});
      return response.ok;
    }catch{return false;}
  },60000,'The bundled engine did not become ready.');
  const runResponse=await fetch(`${connection.base_url}/runs`,{method:'POST',headers:{Authorization:`Bearer ${connection.token}`,'Content-Type':'application/json'},body:JSON.stringify({objective:'Emergency stop acceptance queued work',model:null}),signal:AbortSignal.timeout(5000)});
  if(runResponse.status!==201)throw new Error(`Could not create the queued recovery fixture (HTTP ${runResponse.status}).`);
  const queuedRun=await runResponse.json();
  if(queuedRun.state!=='queued')throw new Error(`Expected a queued fixture run; received ${queuedRun.state}.`);
  await page.evaluate(()=>window.__TAURI_INTERNALS__.invoke('restore_main_window'));
  await expect(page.getByRole('button',{name:'Emergency stop',exact:true})).toBeVisible({timeout:10000});
  await page.getByRole('button',{name:'Emergency stop',exact:true}).click();
  const confirmation=page.getByRole('group',{name:'Confirm emergency stop'});
  await expect(confirmation).toContainText('stops all queued and active runs');
  const stopResponsePromise=page.waitForResponse(response=>response.url()===`${connection.base_url}/emergency-stop`,{timeout:30000});
  await confirmation.getByRole('button',{name:'Stop all work and close X.A.A.'}).click();
  const response=await stopResponsePromise;
  stopResponse={status:response.status(),body:await response.json()};
  await waitFor(()=>proc.exitCode!==null,30000,'Emergency stop did not close the desktop process.');
  if(proc.exitCode!==0)throw new Error(`Emergency stop closed the desktop with exit code ${proc.exitCode}.`);
  if(stopResponse?.status!==200||stopResponse.body?.status!=='stopping')throw new Error('The authenticated emergency-stop request did not return its stopping result.');
  let engineStopped=false;
  try{await fetch(`${connection.base_url}/runs`,{headers:{Authorization:`Bearer ${connection.token}`},signal:AbortSignal.timeout(1000)});}catch{engineStopped=true;}
  if(!engineStopped)throw new Error('The bundled engine remained available after Emergency stop.');
  const database=path.join(localAppData,'XAA','xaa.sqlite3');
  const verification=JSON.parse(execFileSync('uv',['run','python','-c',"import json,sqlite3,sys; c=sqlite3.connect(sys.argv[1]); c.execute('pragma query_only=on'); r=c.execute('select state,lease_owner,lease_expires_at from runs where id=?',(sys.argv[2],)).fetchone(); e=c.execute(\"select payload from run_events where run_id=? and event_type='run.state_changed' order by sequence desc limit 1\",(sys.argv[2],)).fetchone(); print(json.dumps({'state':r[0] if r else None,'lease_owner':r[1] if r else None,'lease_expires_at':r[2] if r else None,'last_state_event':json.loads(e[0]) if e else None}))",database,queuedRun.id],{windowsHide:true,encoding:'utf8',timeout:30000,env:process.env}));
  if(verification.state!=='paused'||verification.lease_owner!==null||verification.lease_expires_at!==null||verification.last_state_event?.reason!=='emergency_stop')throw new Error(`The queued run was not durably paused for explicit recovery: ${JSON.stringify(verification)}`);
  const engineLog=(await readFile(logPath,'utf8')).slice(previousLog.length);
  if(!engineLog.includes('Application shutdown complete.'))throw new Error('The bundled engine did not log graceful shutdown completion.');
  await browser.close();browser=undefined;page=undefined;
  // The installed-package harness pins WebView2's debug argument to one port;
  // the first desktop has exited, so the cold recovery launch reuses that port.
  const recoveryPort=port;
  recoveryProc=spawn(executable,['--background'],{windowsHide:true,stdio:['ignore','pipe','pipe'],env:{...env,PATH:path.join(process.env.SystemRoot,'System32'),LOCALAPPDATA:localAppData,APPDATA:path.join(root,'roaming-app-data'),XAA_TEST_HIDDEN:'0',XAA_TEST_ACCEPTANCE:'1',XAA_TEST_CONFIG_DIR:path.join(root,'config'),XAA_TEST_LOG_DIR:logs,WEBVIEW2_USER_DATA_FOLDER:recoveryWebviewProfile,WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS:`--remote-debugging-port=${recoveryPort}`}});
  recoveryProc.stdout.on('data',chunk=>{stdout=(stdout+chunk.toString()).slice(-32768);});
  recoveryProc.stderr.on('data',chunk=>{stderr=(stderr+chunk.toString()).slice(-32768);});
  let recoveryEndpoint;
  await waitFor(async()=>{
    if(recoveryProc.exitCode!==null)throw new Error(`Desktop exited during recovery startup (${recoveryProc.exitCode}).`);
    try{
      const response=await fetch(`http://127.0.0.1:${recoveryPort}/json/version`,{signal:AbortSignal.timeout(500)});
      if(!response.ok)return false;
      const metadata=await response.json();
      recoveryEndpoint=new URL(metadata.webSocketDebuggerUrl);
      if(recoveryEndpoint.protocol!=='ws:'||!['localhost','127.0.0.1','[::1]'].includes(recoveryEndpoint.hostname)||recoveryEndpoint.port!==String(recoveryPort))throw new Error('Unexpected recovery WebView debugging endpoint.');
      recoveryEndpoint.hostname='127.0.0.1';
      return true;
    }catch(error){if(String(error).includes('Unexpected recovery WebView'))throw error;return false;}
  },60000,'The recovery WebView debugging endpoint did not start.');
  recoveryBrowser=await chromium.connectOverCDP(recoveryEndpoint.toString(),{timeout:10000});
  recoveryPage=recoveryBrowser.contexts().flatMap(context=>context.pages())[0];
  if(!recoveryPage)throw new Error('The recovery desktop did not expose its WebView page.');
  await expect(recoveryPage.getByRole('navigation',{name:'Main sections'})).toBeVisible({timeout:20000});
  const recoveryConnection=await recoveryPage.evaluate(()=>window.__TAURI_INTERNALS__.invoke('engine_connection'));
  await waitFor(async()=>{
    try{
      const response=await fetch(`${recoveryConnection.base_url}/runs/latest-unfinished`,{headers:{Authorization:`Bearer ${recoveryConnection.token}`},signal:AbortSignal.timeout(750)});
      return response.ok;
    }catch{return false;}
  },60000,'The restarted bundled engine did not become ready.');
  const recoveryRequests=[];
  recoveryPage.on('request',request=>{if(request.method()==='POST')recoveryRequests.push(new URL(request.url()).pathname);});
  await expect(recoveryPage.getByRole('heading',{name:'A saved run is paused'})).toBeVisible({timeout:20000});
  await recoveryPage.getByRole('button',{name:'Review saved run'}).click();
  await expect(recoveryPage.getByRole('heading',{name:/Active run Paused/i})).toBeVisible();
  await expect(recoveryPage.getByRole('button',{name:'resume',exact:true})).toBeEnabled();
  if(recoveryRequests.some(request=>request.endsWith('/resume')))throw new Error('The restarted desktop automatically resumed the emergency-stopped run.');
  await recoveryPage.evaluate(()=>window.__TAURI_INTERNALS__.invoke('quit_application'));
  await waitFor(()=>recoveryProc.exitCode!==null,30000,'The recovery desktop did not close cleanly.');
  if(recoveryProc.exitCode!==0)throw new Error(`Recovery desktop closed with exit code ${recoveryProc.exitCode}.`);
  const report={passed:true,desktop_exit_code:proc.exitCode,recovery_desktop_exit_code:recoveryProc.exitCode,emergency_stop_response:stopResponse,queued_run_id:queuedRun.id,persisted_run:verification,engine_stopped:engineStopped,graceful_shutdown:true,restarted_paused_run_visible:true,manual_resume_available:true,automatic_resume_requested:false,model_inference_requested:false};
  await writeFile(path.join(root,'report.json'),JSON.stringify(report,null,2));
  console.log(JSON.stringify({report,directory:root},null,2));
}catch(error){
  await writeFile(path.join(root,'desktop-stdout.log'),stdout);
  await writeFile(path.join(root,'desktop-stderr.log'),stderr);
  await writeFile(path.join(root,'engine-startup.log'),(await readFile(logPath,'utf8').catch(()=> '')).slice(previousLog.length));
  if(page)await page.screenshot({path:path.join(root,'failure.png'),fullPage:true,timeout:5000}).catch(()=>{});
  if(recoveryPage)await recoveryPage.screenshot({path:path.join(root,'recovery-failure.png'),fullPage:true,timeout:5000}).catch(()=>{});
  await writeFile(path.join(root,'report.json'),JSON.stringify({passed:false,error:String(error),desktop_exit_code:proc.exitCode,emergency_stop_response:stopResponse,database_path:path.join(localAppData,'XAA','xaa.sqlite3')},null,2));
  throw error;
}finally{
  await browser?.close().catch(()=>{});
  await recoveryBrowser?.close().catch(()=>{});
  if(proc.exitCode===null)try{execFileSync('taskkill',['/PID',String(proc.pid),'/T','/F'],{windowsHide:true,stdio:'ignore'});}catch{}
  if(recoveryProc?.exitCode===null)try{execFileSync('taskkill',['/PID',String(recoveryProc.pid),'/T','/F'],{windowsHide:true,stdio:'ignore'});}catch{}
}
