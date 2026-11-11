import {chromium,expect} from '@playwright/test';
import {spawn,execFileSync} from 'node:child_process';
import {mkdir,readFile,unlink,writeFile} from 'node:fs/promises';
import {createHash,randomUUID} from 'node:crypto';
import path from 'node:path';

const executable=path.resolve(process.argv[2]??'build/native-release/xaa-desktop.exe');
const requestedRoot=process.env.XAA_NATIVE_ACCEPTANCE_ROOT;
const root=requestedRoot?path.resolve(requestedRoot):path.resolve('.acceptance',randomUUID());
const startupValueName=`XAA Acceptance ${randomUUID()}`;
if(requestedRoot){const acceptanceRoot=path.resolve('.acceptance')+path.sep;if(!root.startsWith(acceptanceRoot)||await readFile(path.join(root,'report.json')).then(()=>true).catch(()=>false))throw new Error('Requested native acceptance root must be new and inside .acceptance');}
await mkdir(root,{recursive:true});
const logDirectory=path.join(root,'logs');await mkdir(logDirectory,{recursive:true});
const logPath=path.join(logDirectory,'engine.log');
const previous=await readFile(logPath,'utf8').catch(()=>'');
const env=Object.fromEntries(Object.entries(process.env).filter(([key])=>!['PATH','PYTHONPATH','VIRTUAL_ENV'].includes(key.toUpperCase())));
const started=Date.now();let stdout='',stderr='';
const backupFixturePath=process.env.XAA_NATIVE_BACKUP_FIXTURE;
const backupFixture=backupFixturePath?JSON.parse(await readFile(backupFixturePath,'utf8')):null;
const recoverStartupRun=process.env.XAA_NATIVE_BOOT_RECOVERY==='1';
const localAppData=backupFixture?path.dirname(path.resolve(backupFixture.directory)):path.join(root,'local-app-data');
const testConfigDirectory=process.env.XAA_TEST_CONFIG_DIR||path.join(root,'config');
let ownerRecoveryAcceptance;
let desktopApiRequests=[];
if(recoverStartupRun){
  const recoveryReportPath=path.join(root,'startup-recovery-seed.json');
  const recoveryWorkspace=path.join(root,'startup-recovery-workspace');
  const recoveryDatabase=path.join(localAppData,'XAA','xaa.sqlite3');
  ownerRecoveryAcceptance=JSON.parse(execFileSync('uv',['run','python','scripts/native_recovery_fixture.py','--database',recoveryDatabase,'--workspace',recoveryWorkspace,'--report',recoveryReportPath],{windowsHide:true,encoding:'utf8',timeout:30000,env:process.env}));
  if(ownerRecoveryAcceptance.state_at_seed!=='blocked'||ownerRecoveryAcceptance.inference_attempts_at_seed!==0)throw new Error('Recovery fixture was not seeded as a blocked, inference-free run.');
}
const proc=spawn(executable,['--background'],{windowsHide:true,stdio:['ignore','pipe','pipe'],env:{...env,PATH:path.join(process.env.SystemRoot,'System32'),LOCALAPPDATA:localAppData,APPDATA:path.join(root,'roaming-app-data'),XAA_TEST_HIDDEN:'0',XAA_TEST_ACCEPTANCE:'1',XAA_TEST_AUTOSTART_VALUE_NAME:startupValueName,XAA_TEST_CONFIG_DIR:testConfigDirectory,XAA_TEST_LOG_DIR:logDirectory,XAA_DATA_DIR:localAppData,WEBVIEW2_USER_DATA_FOLDER:path.join(root,'webview-profile'),WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS:'--remote-debugging-port=19222'}});
proc.stdout.on('data',chunk=>{stdout=(stdout+chunk.toString()).slice(-32768);});
proc.stderr.on('data',chunk=>{stderr=(stderr+chunk.toString()).slice(-32768);});
let browser,connection,page;
const pageConsoleErrors=[];
const pageErrors=[];
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
  const pageDeadline=Date.now()+20000;
  while(Date.now()<pageDeadline){
    page=browser.contexts().flatMap(context=>context.pages())[0];
    if(page)break;
    if(proc.exitCode!==null)throw new Error('Packaged desktop exited before its WebView page appeared');
    await new Promise(resolve=>setTimeout(resolve,100));
  }
  if(!page)throw new Error('Packaged WebView debugging endpoint did not expose a page');
  page.on('console',message=>{if(message.type()==='error')pageConsoleErrors.push(message.text());});
  page.on('pageerror',error=>pageErrors.push(String(error)));
  await expect(page.getByRole('navigation',{name:'Main sections'})).toBeVisible({timeout:20000});
  const initiallyVisible=await page.evaluate(()=>window.__TAURI_INTERNALS__.invoke('plugin:window|is_visible',{label:'main'}));
  if(initiallyVisible)throw new Error('The --background launch argument opened the main window.');
  connection=await page.evaluate(()=>window.__TAURI_INTERNALS__.invoke('engine_connection'));
  let ready=false;
  for(let attempt=0;attempt<240;attempt++){
    try{const response=await fetch(`${connection.base_url}/runs`,{headers:{Authorization:`Bearer ${connection.token}`},signal:AbortSignal.timeout(500)});if(response.ok){const history=await response.json();if(backupFixture){if(!history.items.some(item=>item.id===backupFixture.run.id))throw new Error('Seeded installed backup run is absent from the engine database');}else if(recoverStartupRun){if(!history.items.some(item=>item.id===ownerRecoveryAcceptance.run_id))throw new Error('Seeded startup recovery run is absent from the engine database');}else if(history.items.length)throw new Error('Isolated boot unexpectedly has runs');ready=true;break;}}catch(error){if(String(error).includes('Seeded installed backup run')||String(error).includes('Seeded startup recovery run'))throw error;}
    await new Promise(resolve=>setTimeout(resolve,250));
  }
  if(!ready)throw new Error('Bundled engine API did not become ready');
  let backupAcceptance;
  if(backupFixture){
    const engine=path.join(path.dirname(executable),'xaa-engine.exe');
    const cliEnv={...process.env,XAA_API_TOKEN:connection.token,XAA_API_URL:connection.base_url};
    const backupText=execFileSync(engine,['--cli','--api-url',connection.base_url,'backup'],{windowsHide:true,encoding:'utf8',timeout:60000,env:cliEnv});
    const backupResult=JSON.parse(backupText);
    if(backupResult.status!=='verified')throw new Error('Installed xaa backup did not verify its bundle');
    const bundle=path.resolve(backupResult.path);
    const relativeBundle=path.relative(backupFixture.directory,bundle);
    if(relativeBundle.startsWith('..')||path.isAbsolute(relativeBundle)||!relativeBundle.toLowerCase().startsWith(`backups${path.sep}`.toLowerCase()))throw new Error('Installed backup path escaped the selected data directory');
    const restoreDirectory=path.join(root,'installed-backup-restored');
    const restoreText=execFileSync(engine,['--cli','restore-bundle',bundle,'--destination',restoreDirectory],{windowsHide:true,encoding:'utf8',timeout:60000,env:cliEnv});
    if(!restoreText.includes('Restored database and artifacts:'))throw new Error('Installed restore-bundle CLI did not report success');
    const verified=execFileSync('uv',['run','python','scripts/installed_backup_fixture.py','verify','--directory',restoreDirectory,'--fixture',backupFixturePath,'--report',path.join(root,'installed-backup-verification.json')],{windowsHide:true,encoding:'utf8',timeout:60000,env:process.env});
    backupAcceptance={backup_cli:true,restore_cli:true,bundle,restored_directory:restoreDirectory,verification:JSON.parse(verified)};
  }
  if(recoverStartupRun){
    page.on('request',request=>{if(request.url().startsWith(connection.base_url))desktopApiRequests.push(`${request.method()} ${new URL(request.url()).pathname}`);});
    page.on('dialog',dialog=>void dialog.accept());
    await expect(page.getByRole('heading',{name:'Active run blocked'})).toBeVisible({timeout:20000});
    await expect(page.getByRole('region',{name:'Active run'}).getByText(ownerRecoveryAcceptance.objective??'Review and recover interrupted workspace writes',{exact:true})).toBeVisible();
    await page.getByRole('button',{name:'Inspect uncertain workspace writes'}).click();
    await expect(page.getByText('Edit edit.txt',{exact:false})).toBeVisible();
    await expect(page.getByText('Create partial-create.txt',{exact:false})).toBeVisible();
    await expect(page.getByText('Delete deleted.txt',{exact:false})).toBeVisible();
    const createRow=page.locator('li').filter({hasText:'Create partial-create.txt'});
    const editRow=page.locator('li').filter({hasText:'Edit edit.txt'});
    const deleteRow=page.locator('li').filter({hasText:'Delete deleted.txt'});
    await expect(page.locator('section[aria-label="Workspace write recovery"] li')).toHaveCount(3);
    await unlink(ownerRecoveryAcceptance.create_path);
    await createRow.getByRole('button',{name:'Confirm write did not apply'}).click();
    await expect(createRow).toHaveCount(0);
    await editRow.getByRole('button',{name:'Confirm write did not apply'}).click();
    await expect(editRow).toHaveCount(0);
    await deleteRow.getByRole('button',{name:'Confirm write did not apply'}).click();
    await expect(deleteRow).toHaveCount(0);
    await expect(page.getByText('The interrupted write was recorded as not applied. Review the run, then use Resume to continue.',{exact:true})).toBeVisible();
    const runHeaders={Authorization:`Bearer ${connection.token}`};
    const [savedRunResponse,reconciliationsResponse,eventsResponse,resultResponses]=await Promise.all([
      fetch(`${connection.base_url}/runs/${encodeURIComponent(ownerRecoveryAcceptance.run_id)}`,{headers:runHeaders}),
      fetch(`${connection.base_url}/runs/${encodeURIComponent(ownerRecoveryAcceptance.run_id)}/workspace-write-reconciliations`,{headers:runHeaders}),
      fetch(`${connection.base_url}/runs/${encodeURIComponent(ownerRecoveryAcceptance.run_id)}/events`,{headers:runHeaders}),
      Promise.all(ownerRecoveryAcceptance.calls.map(async call=>({call,response:await fetch(`${connection.base_url}/runs/${encodeURIComponent(ownerRecoveryAcceptance.run_id)}/tools/${encodeURIComponent(call.call_id)}/result`,{headers:runHeaders})}))),
    ]);
    if(!savedRunResponse.ok||!reconciliationsResponse.ok||!eventsResponse.ok||resultResponses.some(item=>!item.response.ok))throw new Error('Could not verify the packaged owner recovery results.');
    const savedRun=await savedRunResponse.json();
    const reconciliations=await reconciliationsResponse.json();
    const events=await eventsResponse.json();
    const toolResults=await Promise.all(resultResponses.map(async item=>({call_id:item.call.call_id,result:await item.response.json()})));
    const restoredFileHashes=await Promise.all([ownerRecoveryAcceptance.edit_path,ownerRecoveryAcceptance.delete_path].map(async filePath=>createHash('sha256').update(await readFile(filePath)).digest('hex')));
    const createPathExists=await readFile(ownerRecoveryAcceptance.create_path).then(()=>true).catch(()=>false);
    if(savedRun.state!=='blocked'||reconciliations.length!==0||toolResults.some(item=>!String(item.result.result).includes('not_applied_after_review')))throw new Error('The installed desktop did not save all owner-reviewed recovery decisions while keeping the run blocked.');
    if(restoredFileHashes.some(fileHash=>fileHash!==ownerRecoveryAcceptance.original_sha256)||createPathExists)throw new Error('Owner recovery changed a restored file or retained the removed partial create.');
    if(events.some(event=>event.event_type==='model.inference.started'||event.event_type==='model.message'))throw new Error('The startup recovery acceptance unexpectedly requested model inference.');
    if(desktopApiRequests.some(request=>request.endsWith(`/runs/${ownerRecoveryAcceptance.run_id}/resume`)))throw new Error('The desktop resumed the run during recovery.');
    ownerRecoveryAcceptance={...ownerRecoveryAcceptance,passed:true,cold_desktop_start:true,saved_run_restored_from_database:true,latest_unfinished_run_opened:true,owner_recovery_confirmed_in_ui:true,resolved_call_ids:toolResults.map(item=>item.call_id),unresolved_intents:reconciliations.length,result_available:toolResults.every(item=>String(item.result.result).includes('not_applied_after_review')),run_state_after_recovery:savedRun.state,restored_workspace_sha256s:restoredFileHashes,partial_create_absent:!createPathExists,automatic_resume:false,model_inference_requested:false};
    await writeFile(path.join(root,'startup-recovery-acceptance.json'),JSON.stringify(ownerRecoveryAcceptance,null,2));
  }
  const startupMilliseconds=Date.now()-started;
  const denied=await fetch(`${connection.base_url}/runs`);if(denied.status!==401)throw new Error('Bundled API did not require authentication');
  await page.screenshot({path:path.join(root,'release-boot.png'),fullPage:true});
  await page.getByRole('link',{name:'Settings'}).click();
  const closeBehavior=page.getByLabel('When the main window closes');
  const initialCloseBehavior=await page.evaluate(()=>window.__TAURI_INTERNALS__.invoke('get_close_behavior'));
  const expectedInitialCloseBehavior=process.env.XAA_NATIVE_EXPECT_CLOSE_BEHAVIOR||'keep_running';
  if(initialCloseBehavior!==expectedInitialCloseBehavior)throw new Error(`Expected initial close behavior ${expectedInitialCloseBehavior}, received ${initialCloseBehavior}`);
  await expect(closeBehavior).toHaveValue(expectedInitialCloseBehavior);
  const startOnLogin=page.getByLabel('Start X.A.A. when I sign in');
  const initialStartOnLogin=await page.evaluate(()=>window.__TAURI_INTERNALS__.invoke('get_start_on_login'));
  const waitForStartOnLogin=async expected=>{
    const deadline=Date.now()+5000;
    while(Date.now()<deadline){if(await page.evaluate(()=>window.__TAURI_INTERNALS__.invoke('get_start_on_login'))===expected)return;await new Promise(resolve=>setTimeout(resolve,50));}
    const status=await page.getByRole('status').allTextContents();
    throw new Error(`Expected start-at-sign-in=${expected}; status: ${status.join(' | ')}`);
  };
  let startOnLoginVerified=false;
  try{
    await startOnLogin.click();
    await waitForStartOnLogin(true);
    await expect(startOnLogin).toBeChecked();
    await expect(page.getByText('X.A.A. will start hidden when you sign in.',{exact:true})).toBeVisible();
    await startOnLogin.click();
    await waitForStartOnLogin(false);
    await expect(startOnLogin).not.toBeChecked();
    await expect(page.getByText('Start at sign-in is off.',{exact:true})).toBeVisible();
    startOnLoginVerified=true;
  }finally{
    await page.evaluate(enabled=>window.__TAURI_INTERNALS__.invoke('set_start_on_login',{enabled}),initialStartOnLogin);
  }
  if(initialCloseBehavior!=='keep_running'){
    await closeBehavior.selectOption('keep_running');
    await expect(page.getByText('Close behavior saved.',{exact:true})).toBeVisible();
  }
  await page.evaluate(()=>window.__TAURI_INTERNALS__.invoke('restore_main_window'));
  const closeStarted=Date.now();
  await page.evaluate(()=>window.__TAURI_INTERNALS__.invoke('plugin:window|close',{label:'main'}));
  const visibilityDeadline=Date.now()+10000;
  const isWindowVisible=()=>page.evaluate(()=>window.__TAURI_INTERNALS__.invoke('plugin:window|is_visible',{label:'main'}));
  while(await isWindowVisible()&&Date.now()<visibilityDeadline)await new Promise(resolve=>setTimeout(resolve,100));
  const closeWaitMilliseconds=Date.now()-closeStarted;
  if(await isWindowVisible())throw new Error('Closing the window did not hide it');
  if(proc.exitCode!==null)throw new Error('Closing the window exited the desktop instead of keeping the engine running');
  const backgroundResponse=await fetch(`${connection.base_url}/runs`,{headers:{Authorization:`Bearer ${connection.token}`},signal:AbortSignal.timeout(1000)});
  if(!backgroundResponse.ok)throw new Error('The engine did not remain available after the window was hidden');
  await page.evaluate(()=>window.__TAURI_INTERNALS__.invoke('restore_main_window'));
  const restoreDeadline=Date.now()+10000;
  while(!await isWindowVisible()&&Date.now()<restoreDeadline)await new Promise(resolve=>setTimeout(resolve,100));
  if(!await isWindowVisible())throw new Error('The hidden window did not restore');
  if(await closeBehavior.inputValue()!=='keep_running')throw new Error('The default close behavior must keep the agent running');
  await closeBehavior.selectOption('exit_on_close');
  await expect(page.getByText('Close behavior saved.',{exact:true})).toBeVisible();
  const savedBehavior=await page.evaluate(()=>window.__TAURI_INTERNALS__.invoke('get_close_behavior'));
  if(savedBehavior!=='exit_on_close')throw new Error('The selected close behavior was not saved');
  await page.evaluate(()=>window.__TAURI_INTERNALS__.invoke('plugin:window|close',{label:'main'})).catch(()=>{});
  const shutdownDeadline=Date.now()+30000;
  while(proc.exitCode===null&&Date.now()<shutdownDeadline)await new Promise(resolve=>setTimeout(resolve,250));
  if(proc.exitCode===null)throw new Error('Selecting quit on close did not stop the desktop');
  let stopped=false;try{await fetch(`${connection.base_url}/runs`,{signal:AbortSignal.timeout(1000)});}catch{stopped=true;}
  if(!stopped)throw new Error('The engine survived the selected quit-on-close behavior');
  const log=(await readFile(logPath,'utf8')).slice(previous.length);
  if(!log.includes('Application shutdown complete.'))throw new Error('No graceful engine shutdown evidence');
  const report={passed:true,executable,startup_milliseconds:startupMilliseconds,close_wait_milliseconds:closeWaitMilliseconds,background_argument_supplied:true,background_launch_kept_window_hidden:!initiallyVisible,initial_close_behavior:initialCloseBehavior,start_at_sign_in_toggle_verified:startOnLoginVerified,initial_start_at_sign_in:initialStartOnLogin,startup_setting_restored:true,window_close_hides_by_default:true,engine_remains_available_while_hidden:true,window_restores:true,close_behavior_selectable:true,exit_on_close_stops_engine:true,restricted_runtime_path:true,bundled_engine_ready:true,authentication_required:true,no_runs_created:!backupFixture&&!ownerRecoveryAcceptance,installed_backup_restore_verified:Boolean(backupAcceptance),backup_acceptance:backupAcceptance,owner_reviewed_startup_recovery:ownerRecoveryAcceptance,model_inference_requested:false,desktop_exit:true,engine_stopped:true,graceful_shutdown:true};
  await writeFile(path.join(root,'report.json'),JSON.stringify(report,null,2));console.log(report,root);
}catch(error){
  let processDiagnostics;
  try{processDiagnostics=JSON.parse(execFileSync('pwsh.exe',['-NoProfile','-NonInteractive','-File',path.resolve('scripts/native_boot_diagnostics.ps1'),'-DesktopProcessId',String(proc.pid)],{windowsHide:true,encoding:'utf8',timeout:15000,maxBuffer:65536}));}catch(diagnosticError){processDiagnostics={error:String(diagnosticError)};}
  let pageDiagnostics;
  if(page){
    pageDiagnostics={url:page.url(),title:await page.title().catch(()=>''),body_text:(await page.locator('body').innerText({timeout:3000}).catch(()=>'' )).slice(0,12000),desktop_api_requests:desktopApiRequests,console_errors:pageConsoleErrors.slice(-40),page_errors:pageErrors.slice(-20)};
    await page.screenshot({path:path.join(root,'failure.png'),fullPage:true,timeout:5000}).catch(()=>{});
  }
  await writeFile(path.join(root,'desktop-stdout.log'),stdout);await writeFile(path.join(root,'desktop-stderr.log'),stderr);
  const engineLog=(await readFile(logPath,'utf8').catch(()=>''));await writeFile(path.join(root,'engine-startup.log'),engineLog.slice(previous.length));
  await writeFile(path.join(root,'report.json'),JSON.stringify({passed:false,error:String(error),desktop_exit_code:proc.exitCode,desktop_signal:proc.signalCode,process_diagnostics:processDiagnostics,page_diagnostics:pageDiagnostics,startup_recovery_seed:ownerRecoveryAcceptance,elapsed_milliseconds:Date.now()-started},null,2));throw error;
}
finally{await browser?.close().catch(()=>{});if(proc.exitCode===null)execFileSync('taskkill',['/PID',String(proc.pid),'/T','/F'],{windowsHide:true,stdio:'ignore'});try{execFileSync('reg.exe',['delete','HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run','/v',startupValueName,'/f'],{windowsHide:true,stdio:'ignore'});}catch{}}
