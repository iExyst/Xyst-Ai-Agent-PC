import {chromium,expect} from '@playwright/test';
import {spawn,execFileSync} from 'node:child_process';
import {mkdir,readFile,writeFile} from 'node:fs/promises';
import {randomUUID} from 'node:crypto';
import path from 'node:path';

const executable=path.resolve(process.argv[2]??'build/native-release/xaa-desktop.exe');
const requestedRoot=process.env.XAA_NATIVE_SCHEDULE_RESTART_ROOT;
const root=requestedRoot?path.resolve(requestedRoot):path.resolve('.acceptance',`native-schedule-restart-${randomUUID()}`);
if(requestedRoot){const acceptanceRoot=path.resolve('.acceptance')+path.sep;if(!root.startsWith(acceptanceRoot)||await readFile(path.join(root,'report.json')).then(()=>true).catch(()=>false))throw new Error('Requested schedule acceptance root must be new and inside .acceptance');}
await mkdir(root,{recursive:true});
const localAppData=path.join(root,'local-app-data');
const appData=path.join(root,'roaming-app-data');
const configDirectory=path.join(root,'config');
const logDirectory=path.join(root,'logs');
const webViewProfile=path.join(root,'webview-profile');
const secondWebViewProfile=path.join(root,'webview-profile-second');
await Promise.all([localAppData,appData,configDirectory,logDirectory,webViewProfile,secondWebViewProfile].map(dir=>mkdir(dir,{recursive:true})));
const database=path.join(localAppData,'XAA','xaa.sqlite3');
const fixturePath=path.join(root,'seed.json');
const fixture=JSON.parse(execFileSync('uv',['run','python','scripts/native_schedule_restart_fixture.py','--database',database,'--report',fixturePath],{windowsHide:true,encoding:'utf8',timeout:30000,env:process.env}));
const debugPortBase=Number(process.env.XAA_NATIVE_SCHEDULE_RESTART_DEBUG_PORT||19222);
if(!Number.isInteger(debugPortBase)||debugPortBase<1024||debugPortBase>65534)throw new Error('The schedule restart debug-port base must be an integer from 1024 through 65534.');
const environment={...process.env,LOCALAPPDATA:localAppData,APPDATA:appData,XAA_TEST_ACCEPTANCE:'1',XAA_TEST_HIDDEN:'0',XAA_TEST_CONFIG_DIR:configDirectory,XAA_TEST_LOG_DIR:logDirectory,WEBVIEW2_USER_DATA_FOLDER:webViewProfile,WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS:`--remote-debugging-port=${debugPortBase}`};
delete environment.XAA_DATA_DIR;delete environment.XAA_API_TOKEN;delete environment.XAA_API_PORT;delete environment.VIRTUAL_ENV;delete environment.PYTHONPATH;

const launch=(debugPort,profile)=>spawn(executable,['--background'],{windowsHide:true,stdio:['ignore','pipe','pipe'],env:{...environment,WEBVIEW2_USER_DATA_FOLDER:profile,WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS:`--remote-debugging-port=${debugPort}`}});
async function attach(proc,stage,debugPort){
  let browser,lastError='Debugging endpoint unavailable';
  const deadline=Date.now()+60000;
  while(Date.now()<deadline){
    try{
      const response=await fetch(`http://127.0.0.1:${debugPort}/json/version`,{signal:AbortSignal.timeout(750)});
      if(!response.ok)throw new Error(`Debugging metadata HTTP ${response.status}`);
      const metadata=await response.json();
      if(typeof metadata.webSocketDebuggerUrl!=='string')throw new Error('No WebSocket debugging URL');
      const endpoint=new URL(metadata.webSocketDebuggerUrl);
      if(endpoint.protocol!=='ws:'||!['localhost','127.0.0.1','[::1]'].includes(endpoint.hostname)||endpoint.port!==String(debugPort))throw new Error('Unexpected debugging endpoint');
      endpoint.hostname='127.0.0.1';
      browser=await chromium.connectOverCDP(endpoint.toString(),{timeout:3000});
      break;
    }catch(error){lastError=String(error)+(error?.cause?.code?` (${error.cause.code})`:'');if(proc.exitCode!==null)throw new Error(`Desktop exited during ${stage}: ${lastError}`);await new Promise(resolve=>setTimeout(resolve,250));}
  }
  if(!browser)throw new Error(`Could not attach to desktop during ${stage}: ${lastError}`);
  const page=browser.contexts().flatMap(context=>context.pages())[0];
  if(!page){await browser.close();throw new Error(`Desktop WebView did not open during ${stage}`);}
  await expect(page.getByRole('navigation',{name:'Main sections'})).toBeVisible({timeout:20000});
  const connection=await page.evaluate(()=>window.__TAURI_INTERNALS__.invoke('engine_connection'));
  const apiStarted=Date.now();
  let ready=false;
  for(let attempt=0;attempt<240;attempt++){
    try{const response=await fetch(`${connection.base_url}/schedules`,{headers:{Authorization:`Bearer ${connection.token}`},signal:AbortSignal.timeout(500)});if(response.ok){ready=true;break;}}catch{}
    if(proc.exitCode!==null)throw new Error(`Desktop exited before its engine became ready during ${stage}`);
    await new Promise(resolve=>setTimeout(resolve,250));
  }
  if(!ready){await browser.close();throw new Error(`Bundled engine API was not ready during ${stage} on debug port ${debugPort}`);}
  return {browser,page,connection,api_ready_milliseconds:Date.now()-apiStarted};
}

async function getState(connection){
  const headers={Authorization:`Bearer ${connection.token}`};
  const [scheduleResponse,runsResponse]=await Promise.all([
    fetch(`${connection.base_url}/schedules`,{headers}),
    fetch(`${connection.base_url}/runs`,{headers}),
  ]);
  if(!scheduleResponse.ok||!runsResponse.ok)throw new Error('Could not read authenticated schedule/run state');
  const schedules=await scheduleResponse.json();
  const history=await runsResponse.json();
  const schedule=schedules.find(item=>item.id===fixture.schedule_id);
  if(!schedule)throw new Error('Seeded schedule did not survive desktop startup');
  const runs=Array.isArray(history)?history:history.items;
  return {schedule,runs};
}

async function waitForMissed(connection,stage){
  const deadline=Date.now()+30000;
  let state;
  while(Date.now()<deadline){
    state=await getState(connection);
    const executions=state.schedule.executions;
    if(executions.length===1&&executions[0].state==='missed')return state;
    await new Promise(resolve=>setTimeout(resolve,250));
  }
  throw new Error(`The packaged scheduler did not record one missed occurrence during ${stage}: ${JSON.stringify(state?.schedule?.executions)}`);
}

async function stop(session,proc){
  await session.page.evaluate(()=>window.__TAURI_INTERNALS__.invoke('quit_application')).catch(()=>{});
  await session.browser.close().catch(()=>{});
  const deadline=Date.now()+30000;
  while(proc.exitCode===null&&Date.now()<deadline)await new Promise(resolve=>setTimeout(resolve,100));
  if(proc.exitCode===null){execFileSync('taskkill',['/PID',String(proc.pid),'/T','/F'],{windowsHide:true,stdio:'ignore'});throw new Error('Packaged desktop did not stop cleanly during the cold-restart acceptance');}
}

let firstProcess,secondProcess,firstSession,secondSession;
const apiReadyMilliseconds=[];
try{
  firstProcess=launch(debugPortBase,webViewProfile);
  firstSession=await attach(firstProcess,'first cold start',debugPortBase);
  apiReadyMilliseconds.push(firstSession.api_ready_milliseconds);
  const first=await waitForMissed(firstSession.connection,'first cold start');
  const firstExecution=first.schedule.executions[0];
  if(first.runs.length!==0||firstExecution.run_id!==null)throw new Error('Skip-missed policy unexpectedly dispatched a run');
  await stop(firstSession,firstProcess);firstSession=null;

  const secondDebugPort=debugPortBase+1;
  secondProcess=launch(secondDebugPort,secondWebViewProfile);
  secondSession=await attach(secondProcess,'second cold start',secondDebugPort);
  apiReadyMilliseconds.push(secondSession.api_ready_milliseconds);
  const second=await waitForMissed(secondSession.connection,'second cold start');
  const secondExecution=second.schedule.executions[0];
  if(second.runs.length!==0||secondExecution.id!==firstExecution.id||secondExecution.occurrence_id!==firstExecution.occurrence_id)throw new Error('Cold restart duplicated or changed the missed occurrence');
  const denied=await fetch(`${secondSession.connection.base_url}/schedules`);
  if(denied.status!==401)throw new Error('Packaged schedule API did not require authentication');
  await stop(secondSession,secondProcess);secondSession=null;

  const report={passed:true,executable,full_desktop_process_restarts:2,debug_ports:[debugPortBase,secondDebugPort],api_ready_milliseconds:apiReadyMilliseconds,schedule_id:fixture.schedule_id,execution_id:firstExecution.id,occurrence_id:firstExecution.occurrence_id,state_after_both_starts:secondExecution.state,execution_count_after_both_starts:second.schedule.executions.length,run_count:second.runs.length,missed_policy:'skip',authenticated_api:true,model_inference_requested:false,telegram_delivery_requested:false,evidence:root};
  await writeFile(path.join(root,'report.json'),JSON.stringify(report,null,2));
  console.log(JSON.stringify(report));
}catch(error){
  const report={passed:false,error:String(error),executable,first_exit_code:firstProcess?.exitCode,second_exit_code:secondProcess?.exitCode,model_inference_requested:false,telegram_delivery_requested:false,evidence:root};
  await writeFile(path.join(root,'report.json'),JSON.stringify(report,null,2));
  throw error;
}finally{
  if(firstSession&&firstProcess?.exitCode===null)await stop(firstSession,firstProcess).catch(()=>{});
  if(secondSession&&secondProcess?.exitCode===null)await stop(secondSession,secondProcess).catch(()=>{});
  for(const proc of [firstProcess,secondProcess])if(proc&&proc.exitCode===null)execFileSync('taskkill',['/PID',String(proc.pid),'/T','/F'],{windowsHide:true,stdio:'ignore'});
}
