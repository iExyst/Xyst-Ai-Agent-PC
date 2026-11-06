import { chromium, expect } from '@playwright/test';
import { spawn, execFileSync } from 'node:child_process';
import { mkdir, readFile, writeFile, copyFile } from 'node:fs/promises';
import { randomUUID, createHash } from 'node:crypto';
import path from 'node:path';

const root = path.resolve('.acceptance', randomUUID());
await mkdir(root, { recursive: true });
const nativeLocalAppData = path.join(root, 'localappdata');
const nativeAppData = path.join(root, 'appdata');
await Promise.all([nativeLocalAppData, nativeAppData].map(directory => mkdir(directory, { recursive: true })));
if (process.env.XAA_NATIVE_SNAPSHOT) {
  await mkdir(path.join(root,'XAA'));
  await copyFile(process.env.XAA_NATIVE_SNAPSHOT,path.join(root,'XAA','xaa.sqlite3'));
}
const logPath = path.join(nativeLocalAppData, 'dev.xyst.xaa', 'logs', 'engine.log');
const previousLog = await readFile(logPath, 'utf8').catch(() => '');
const nativeEnv = Object.fromEntries(Object.entries(process.env).filter(([key]) => !['PATH', 'PYTHONPATH', 'VIRTUAL_ENV'].includes(key.toUpperCase())));
nativeEnv.PATH = path.join(process.env.SystemRoot, 'System32');
const manifest = JSON.parse(await readFile('build/native-dev/manifest.json', 'utf8'));
const proc = spawn(path.resolve('build/native-dev/xaa-desktop.exe'), [], { windowsHide: true, stdio: 'ignore', env: { ...nativeEnv, APPDATA: nativeAppData, LOCALAPPDATA: nativeLocalAppData, XAA_TEST_HIDDEN: '1', XAA_DATA_DIR: root, WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS: '--remote-debugging-port=19222' } });
let browser;
let connection;
let instance;
const finalReview = process.env.XAA_NATIVE_FINAL_REVIEW === '1';
const patchRequested=process.env.XAA_NATIVE_PATCH==='1';
let patchVerified=false;
const createRequested=process.env.XAA_NATIVE_CREATE==='1';
let createVerified=false;
const streamingRequested=process.env.XAA_STREAM_RESPONSES==='1';
let streamingVerified=false;
let partialUiObserved=false;
const skillLifecycleRequested=process.env.XAA_NATIVE_SKILL_LIFECYCLE==='1';
let skillLifecycleVerified=false;
const skillEvaluationRequested=process.env.XAA_NATIVE_SKILL_EVALUATION==='1';
let skillEvaluationVerified=false;
const skillsRequested=process.env.XAA_NATIVE_SKILLS==='1';
let skillsVerified=false;
const conversationSearchRequested=process.env.XAA_NATIVE_CONVERSATION_SEARCH==='1';
let conversationSearchVerified=false;
const reservationUiRequested=process.env.XAA_NATIVE_RESERVATION_UI==='1';
let reservationUiVerified=false;
const reopen = process.env.XAA_NATIVE_REOPEN === '1';
const tokenCountRequested=process.env.XAA_NATIVE_TOKEN_COUNT==='1';
let tokenCountVerified=false;
const providerSetupRequested=process.env.XAA_NATIVE_PROVIDER_SETUP==='1';
let providerSetupVerified=false;
const onboardingRequested=process.env.XAA_NATIVE_ONBOARDING==='1';
let onboardingVerified=false;
const limitsRequested=process.env.XAA_NATIVE_LIMITS==='1';
let limitsVerified=false;
const edit = process.env.XAA_NATIVE_EDIT === '1' || finalReview || patchRequested || createRequested;
const specialists = process.env.XAA_NATIVE_SPECIALISTS === '1';
let specialistStreamingVerified=false;
const exactContextRequested=process.env.XAA_EXACT_CONTEXT==='1';
const taskQuotaRequested=process.env.XAA_NATIVE_TASK_QUOTA==='1';
let taskQuotaVerified=false;
const plan = process.env.XAA_NATIVE_PLAN === '1' || specialists || taskQuotaRequested;
try {
  for (let attempt = 0; attempt < 60; attempt++) {
    try { browser = await chromium.connectOverCDP('http://127.0.0.1:19222'); break; }
    catch { if (proc.exitCode !== null) throw new Error('Native app exited during startup'); await new Promise(resolve => setTimeout(resolve, 250)); }
  }
  if (!browser) throw new Error('Native WebView did not expose test connection');
  const page = browser.contexts()[0].pages()[0];
  await page.getByLabel('Local model').waitFor({ timeout: 20000 });
  const count = await page.getByLabel('Local model').locator('option').evaluateAll(options=>options.filter(option=>option.value).length);
  if (!count) throw new Error('Native app did not discover models');
  if(await page.getByLabel('Local model').inputValue()!=='')throw new Error('Native app silently selected an unbenchmarked default model');
  connection = await page.evaluate(() => window.__TAURI_INTERNALS__.invoke('engine_connection'));
  const headers={Authorization:`Bearer ${connection.token}`};
  if(providerSetupRequested){
    const initial=await (await fetch(`${connection.base_url}/providers/lm-studio/config`,{headers})).json();
    if(initial.endpoint!=='http://127.0.0.1:1234')throw new Error('Native provider setup did not start with the default LM Studio endpoint');
    await page.getByLabel('LM Studio server address').fill('http://localhost:1234');
    await page.getByRole('button',{name:'Save address and connect',exact:true}).click();
    await expect(page.getByRole('status').filter({hasText:/Connected\. Found 29 models\./})).toBeVisible({timeout:15000});
    const saved=await (await fetch(`${connection.base_url}/providers/lm-studio/config`,{headers})).json();
    if(saved.endpoint!=='http://127.0.0.1:1234'||saved.selected_model!==null)throw new Error('Native provider endpoint was not persisted or did not clear the stale selection');
    const loaded=await fetch(`${connection.base_url}/models/load`,{method:'POST',headers:{...headers,'Content-Type':'application/json'},body:JSON.stringify({model:'qwen3-0.6b',context_length:4096})});
    if(!loaded.ok)throw new Error(`Could not load installed tool-diagnostic fixture: ${await loaded.text()}`);
    instance=(await loaded.json()).instance_id;
    await page.getByLabel('Local model').selectOption('qwen3-0.6b');
    await expect.poll(async()=>((await (await fetch(`${connection.base_url}/providers/lm-studio/config`,{headers})).json()).selected_model)).toBe('qwen3-0.6b');
    await page.getByRole('button',{name:'Run harmless tool-use diagnostic',exact:true}).click();
    await expect(page.getByText('The selected model completed a validated harmless tool round trip.',{exact:true})).toBeVisible({timeout:60000});
    await page.getByLabel('Local model').selectOption('');
    await expect.poll(async()=>((await (await fetch(`${connection.base_url}/providers/lm-studio/config`,{headers})).json()).selected_model)).toBeNull();
    providerSetupVerified=true;
    await writeFile(path.join(root,'provider-setup.json'),JSON.stringify({passed:true,initial_endpoint:initial.endpoint,saved_endpoint:saved.endpoint,discovered_model_count:count,selected_model_during_diagnostic:'qwen3-0.6b',real_tool_round_trip:true,selection_cleared:true},null,2));
  }
  if(onboardingRequested){
    const model='qwen/qwen3.5-9b';
    let diagnosticAttempts=0;
    let demoRequest;
    page.on('request',request=>{if(request.url()===`${connection.base_url}/runs`&&request.method()==='POST')demoRequest=JSON.parse(request.postData()||'{}');});
    const available=(await (await fetch(`${connection.base_url}/models`,{headers})).json()).map(item=>item.id);
    if(!available.includes(model))throw new Error(`Required real onboarding fixture is not installed: ${model}`);
    const workspace=path.join(root,'onboarding-project');
    await mkdir(workspace,{recursive:true});
    await writeFile(path.join(workspace,'README.md'),'# Aurora Ledger\n\nA desktop ledger for community gardens.\n\n## Setup\nInstall Python 3.12, then run the documented app entry point.\n\n## Development\nThe project currently has no automated test instructions.\n');
    const load=await fetch(`${connection.base_url}/models/load`,{method:'POST',headers:{...headers,'Content-Type':'application/json'},body:JSON.stringify({model,context_length:16384})});
    if(!load.ok)throw new Error(`Could not load installed onboarding model: ${await load.text()}`);
    instance=(await load.json()).instance_id;
    await page.getByLabel('Local model').selectOption(model);
    await expect.poll(async()=>((await (await fetch(`${connection.base_url}/providers/lm-studio/config`,{headers})).json()).selected_model)).toBe(model);
    for(diagnosticAttempts=1;diagnosticAttempts<=2;diagnosticAttempts++){
      await page.getByRole('button',{name:'Run harmless tool-use diagnostic',exact:true}).click();
      try{await page.getByText('The selected model completed a validated harmless tool round trip.',{exact:true}).waitFor({timeout:120000});break;}
      catch(error){if(diagnosticAttempts===2)throw error;}
    }
    await page.getByLabel('Workspace path').fill(workspace);
    const started=Date.now();
    await page.getByRole('button',{name:'Start read-only onboarding demonstration',exact:true}).click();
    await expect(page.getByRole('heading',{name:/Active run/})).toBeVisible({timeout:30000});
    let savedRun;
    for(let attempt=0;attempt<180;attempt++){
      const history=await (await fetch(`${connection.base_url}/runs?limit=1`,{headers})).json();
      savedRun=history.items?.[0];
      if(savedRun&&['verifying','completed','failed','blocked'].includes(savedRun.state))break;
      await new Promise(resolve=>setTimeout(resolve,1000));
    }
    if(!savedRun||savedRun.objective.indexOf('Do not modify files or run commands')<0||savedRun.workspace!==workspace||savedRun.state!=='verifying'||demoRequest?.mode!=='observe'||demoRequest?.command_grant!==null)throw new Error(`Read-only demonstration did not reach independent review in Observe mode: ${JSON.stringify({savedRun,demoRequest})}`);
    const events=await (await fetch(`${connection.base_url}/runs/${savedRun.id}/events`,{headers})).json();
    const readIntent=events.find(event=>event.event_type==='tool.intended'&&event.payload.tool==='workspace.read_file');
    const read=readIntent&&events.find(event=>event.event_type==='tool.completed'&&event.payload.call_id===readIntent.payload.call_id);
    const final=events.filter(event=>event.event_type==='model.message').map(event=>event.payload.message?.content).filter(value=>typeof value==='string').at(-1);
    const unchanged=(await readFile(path.join(workspace,'README.md'),'utf8')).startsWith('# Aurora Ledger');
    const readOnlyTools=new Set(['workspace.read_file','workspace.list_directory','workspace.search']);
    const writes=events.some(event=>event.event_type==='tool.intended'&&!readOnlyTools.has(event.payload.tool));
    if(!read||!final?.includes('Aurora Ledger')||!unchanged||writes)throw new Error('Demonstration lacks real README evidence, an accurate public answer or read-only source preservation');
    await page.getByLabel('I reviewed the objective, result, and available evidence').check();
    await page.getByLabel('Final review rationale').fill('Confirmed that the summary matches README.md and that the project files were not changed.');
    await page.getByRole('button',{name:'Accept and complete run',exact:true}).click();
    await expect(page.getByRole('heading',{name:'Active run completed'})).toBeVisible({timeout:30000});
    const completed=await (await fetch(`${connection.base_url}/runs/${savedRun.id}`,{headers})).json();
    if(completed.state!=='completed')throw new Error('The reviewed onboarding demonstration did not complete');
    onboardingVerified=true;
    await writeFile(path.join(root,'onboarding.json'),JSON.stringify({passed:true,model,diagnostic_passed:true,diagnostic_attempts:diagnosticAttempts,workspace_selected:true,mode:'observe',run_state:completed.state,final_review_accepted:true,actual_workspace_read:true,public_answer_contains_project_identity:true,source_unchanged:unchanged,write_tools_proposed:false,elapsed_ms:Date.now()-started,run_id:savedRun.id},null,2));
    const unload=await fetch(`${connection.base_url}/models/unload`,{method:'POST',headers:{...headers,'Content-Type':'application/json'},body:JSON.stringify({instance_id:instance})});
    if(!unload.ok)throw new Error('Could not unload onboarding model');
    instance=undefined;
  }
  if(skillLifecycleRequested){
    if(!process.env.XAA_NATIVE_SNAPSHOT)throw new Error('Lifecycle acceptance requires a real evaluated-version snapshot');
    const entries=await (await fetch(`${connection.base_url}/skills`,{headers})).json();
    const skill=entries[0];
    if(entries.length!==1||skill.latest_version!==2||skill.active_version!==null||skill.lifecycle_state!=='deprecated')throw new Error('Expected deprecated real lifecycle fixture with two evaluated versions');
    const history=await (await fetch(`${connection.base_url}/runs?limit=50`,{headers})).json();
    const before=await Promise.all(history.items.map(async run=>await (await fetch(`${connection.base_url}/runs/${run.id}/events`,{headers})).json()));
    await page.getByText('Reusable procedures',{exact:true}).click();
    const panel=page.getByRole('region',{name:'Procedure candidates',exact:true});
    await panel.getByRole('button',{name:`Inspect ${skill.name}`,exact:true}).click();
    await panel.getByLabel('Procedure version',{exact:true}).selectOption('1');
    const lifecycle=panel.getByRole('region',{name:'Procedure lifecycle',exact:true});
    await lifecycle.getByLabel('Lifecycle decision rationale',{exact:true}).fill('Automated native acceptance activates evaluated version one.');
    await lifecycle.getByRole('button',{name:'Activate this evaluated version',exact:true}).click();
    await expect(lifecycle).toContainText('Active version: 1.');
    await expect(panel.getByRole('button',{name:'Delete procedure and all versions',exact:true})).toBeDisabled();
    await panel.getByLabel('Procedure version',{exact:true}).selectOption('2');
    await lifecycle.getByLabel('Lifecycle decision rationale',{exact:true}).fill('Automated native acceptance upgrades to evaluated version two.');
    await lifecycle.getByRole('button',{name:'Activate this evaluated version',exact:true}).click();
    await expect(lifecycle).toContainText('Active version: 2.');
    await panel.getByLabel('Procedure version',{exact:true}).selectOption('1');
    await lifecycle.getByLabel('Lifecycle decision rationale',{exact:true}).fill('Automated native acceptance rolls back to evaluated version one.');
    await lifecycle.getByRole('button',{name:'Roll back to this evaluated version',exact:true}).click();
    await expect(lifecycle).toContainText('Active version: 1.');
    await panel.getByRole('button',{name:'Select active procedure for next run',exact:true}).click();
    await expect(page.getByRole('region',{name:'Selected procedure',exact:true})).toContainText('Active procedure:');
    await page.getByRole('button',{name:'Remove selected procedure',exact:true}).click();
    await panel.screenshot({path:path.join(root,'skill-lifecycle.png')});
    await lifecycle.getByLabel('Lifecycle decision rationale',{exact:true}).fill('Automated native acceptance deprecates the procedure.');
    await lifecycle.getByRole('button',{name:'Deprecate procedure',exact:true}).click();
    await expect(lifecycle).toContainText('Entry state: deprecated. Active version: none.');
    const final=await (await fetch(`${connection.base_url}/skills/${skill.id}`,{headers})).json();
    const audit=await (await fetch(`${connection.base_url}/skills/${skill.id}/lifecycle`,{headers})).json();
    const after=await Promise.all(history.items.map(async run=>await (await fetch(`${connection.base_url}/runs/${run.id}/events`,{headers})).json()));
    if(final.active_version!==null||audit.length!==8||JSON.stringify(before)!==JSON.stringify(after))throw new Error('Native lifecycle mutated trial histories or lost audit events');
    await writeFile(path.join(root,'skill-lifecycle.json'),JSON.stringify({passed:true,source:'evaluated real-model procedure versions; lifecycle decisions automated for acceptance',activate_upgrade_rollback_deprecate:true,active_use_selection:true,lifecycle_event_count:8,trial_journals_unchanged:true,final_state:final.lifecycle_state,active_version:final.active_version},null,2));
    skillLifecycleVerified=true;
  }
  if(skillEvaluationRequested){
    if(!process.env.XAA_NATIVE_SNAPSHOT)throw new Error('Evaluation acceptance requires an isolated real-trial snapshot');
    const headers={Authorization:`Bearer ${connection.token}`};
    const skills=await (await fetch(`${connection.base_url}/skills`,{headers})).json();
    const skill=skills[0];
    if(skills.length!==1||skill.lifecycle_state!=='evaluated'||skill.active_version!==null)throw new Error('Expected one evaluated inactive fixture procedure');
    const version=await (await fetch(`${connection.base_url}/skills/${skill.id}/versions/1`,{headers})).json();
    const prior=version.evaluation_evidence.evaluations;
    if(prior.length!==1)throw new Error('Expected one existing automated fixture evaluation');
    const chosen=prior[0].trials;
    const before=await Promise.all(chosen.map(async trial=>await (await fetch(`${connection.base_url}/runs/${trial.run_id}/events`,{headers})).json()));
    await page.getByText('Reusable procedures',{exact:true}).click();
    const panel=page.getByRole('region',{name:'Procedure candidates',exact:true});
    await panel.getByRole('button',{name:`Inspect ${skill.name}`,exact:true}).click();
    const evaluation=panel.getByRole('region',{name:'Procedure evaluation',exact:true});
    await expect(evaluation).toContainText('Version 1: evaluated. Saved evaluations: 1.');
    await evaluation.getByRole('button',{name:'Find completed procedure trials',exact:true}).click();
    await evaluation.getByLabel('First completed trial',{exact:true}).selectOption(chosen[0].run_id);
    await evaluation.getByLabel('Second completed trial',{exact:true}).selectOption(chosen[1].run_id);
    await evaluation.getByLabel('First representative case',{exact:true}).fill(chosen[0].case_description);
    await evaluation.getByLabel('Second representative case',{exact:true}).fill(chosen[1].case_description);
    await evaluation.getByLabel('Evaluation criteria (one per line)',{exact:true}).fill('Both saved trials report exact evidence and preserve their fixtures.');
    await evaluation.getByLabel('Evaluation rationale',{exact:true}).fill('Automated native acceptance records a second explicit fixture review of existing real-model trial evidence; this does not represent a human review or activation.');
    await evaluation.getByLabel('I reviewed both trials and confirmed every evaluation criterion',{exact:true}).check();
    await evaluation.getByRole('button',{name:'Record procedure evaluation',exact:true}).click();
    await expect(evaluation).toContainText('Version 1: evaluated. Saved evaluations: 2.');
    const afterVersion=await (await fetch(`${connection.base_url}/skills/${skill.id}/versions/1`,{headers})).json();
    const afterSummary=await (await fetch(`${connection.base_url}/skills/${skill.id}`,{headers})).json();
    const after=await Promise.all(chosen.map(async trial=>await (await fetch(`${connection.base_url}/runs/${trial.run_id}/events`,{headers})).json()));
    if(afterVersion.evaluation_evidence.evaluations.length!==2||afterSummary.active_version!==null||JSON.stringify(before)!==JSON.stringify(after))throw new Error('Evaluation changed trial execution or activated procedure');
    await panel.screenshot({path:path.join(root,'skill-evaluation.png')});
    await writeFile(path.join(root,'skill-evaluation.json'),JSON.stringify({passed:true,source:'two real local-model trials; review actions automated for acceptance',evaluation_count:2,active_version:null,trial_journals_unchanged:true,native_review_submission:true},null,2));
    skillEvaluationVerified=true;
  }
  if(skillsRequested){
    await page.getByText('Reusable procedures',{exact:true}).click();
    let panel=page.getByRole('region',{name:'Procedure candidates',exact:true});
    const name=`Read evidence ${randomUUID()}`;
    await panel.getByLabel('Procedure name',{exact:true}).fill(name);
    await panel.getByLabel('Procedure text',{exact:true}).fill('Read the requested files and report verified evidence.');
    await panel.getByLabel('Tool dependencies (one name@version per line)',{exact:true}).fill('workspace.read_file@1');
    await panel.getByLabel('Requires reading workspace files',{exact:true}).check();
    await panel.getByRole('button',{name:'Save procedure candidate',exact:true}).click();
    await panel.getByRole('button',{name:`Inspect ${name}`,exact:true}).waitFor();
    await panel.getByLabel('Procedure text',{exact:true}).fill('Revised: read files, check evidence, then report.');
    await panel.getByRole('button',{name:'Save new procedure version',exact:true}).click();
    await panel.getByLabel('Procedure version',{exact:true}).selectOption('1');
    await panel.getByRole('button',{name:'Save new procedure version',exact:true}).isDisabled().then(value=>{if(!value)throw new Error('Historical procedure can be overwritten');});
    await page.reload();await page.getByLabel('Local model').waitFor({timeout:20000});
    await page.getByText('Reusable procedures',{exact:true}).click();
    panel=page.getByRole('region',{name:'Procedure candidates',exact:true});
    await panel.getByRole('button',{name:`Inspect ${name}`,exact:true}).click();
    await panel.getByLabel('Procedure version',{exact:true}).selectOption('1');
    await expect(panel.getByLabel('Procedure text',{exact:true})).toHaveValue('Read the requested files and report verified evidence.');
    await panel.getByRole('button',{name:'Select version for trial run',exact:true}).click();
    await expect(page.getByRole('region',{name:'Selected procedure',exact:true})).toContainText(name);
    await page.getByRole('button',{name:'Remove selected procedure',exact:true}).click();
    const headers={Authorization:`Bearer ${connection.token}`};
    const entries=await (await fetch(`${connection.base_url}/skills`,{headers})).json();
    if(entries.length!==1||entries[0].active_version!==null||entries[0].lifecycle_state!=='candidate'||entries[0].latest_version!==2)throw new Error('Procedure candidate metadata invalid');
    await panel.screenshot({path:path.join(root,'skills.png')});
    await panel.getByRole('button',{name:'Delete procedure and all versions',exact:true}).click();
    await panel.getByRole('button',{name:'Save procedure candidate',exact:true}).waitFor();
    const after=await (await fetch(`${connection.base_url}/skills`,{headers})).json();
    const history=await (await fetch(`${connection.base_url}/runs`,{headers})).json();
    if(after.length||history.items.length)throw new Error('Procedure deletion failed or editor created an executable run');
    await writeFile(path.join(root,'skills.json'),JSON.stringify({passed:true,create_revise_inspect_delete:true,native_renderer_reload:true,historical_version_preserved:true,active_version:null,no_runs_created:true},null,2));
    skillsVerified=true;
  }
  if(conversationSearchRequested){
    const phrase=process.env.XAA_NATIVE_SEARCH_PHRASE;
    if(!process.env.XAA_NATIVE_SNAPSHOT||!phrase)throw new Error('Conversation search requires an isolated existing run snapshot and a phrase from its answer');
    const headers={Authorization:`Bearer ${connection.token}`};
    const literal=await (await fetch(`${connection.base_url}/runs?q=${encodeURIComponent(phrase)}`,{headers})).json();
    if(literal.items.length)throw new Error('Fixture phrase also occurs in objective; answer search not proven');
    const matched=await (await fetch(`${connection.base_url}/runs?scope=conversation&q=${encodeURIComponent(phrase)}`,{headers})).json();
    if(matched.items.length!==1)throw new Error('Conversation phrase did not identify one saved run');
    const saved=matched.items[0];
    const events=await (await fetch(`${connection.base_url}/runs/${saved.id}/events`,{headers})).json();
    for(let pass=0;pass<2;pass++){
      if(pass){await page.reload();await page.getByLabel('Local model').waitFor({timeout:20000});}
      await page.getByLabel('Search in',{exact:true}).selectOption('conversation');
      await page.getByLabel('Search conversation phrase',{exact:true}).fill(phrase);
      await page.getByRole('button',{name:'Search saved runs',exact:true}).click();
      await page.getByRole('button',{name:`Open ${saved.objective}`,exact:true}).click();
      await page.getByRole('heading',{name:`Active run ${saved.state}`,exact:true}).waitFor();
    }
    await page.getByRole('region',{name:'Saved runs',exact:true}).screenshot({path:path.join(root,'conversation-search.png')});
    const after=await (await fetch(`${connection.base_url}/runs/${saved.id}/events`,{headers})).json();
    if(JSON.stringify(events)!==JSON.stringify(after))throw new Error('Search/reopen changed execution journal');
    await writeFile(path.join(root,'conversation-search.json'),JSON.stringify({passed:true,source:'snapshot of an existing real model read run',matched_run:saved.id,objective_matches:0,conversation_matches:1,native_boot:true,renderer_reload:true,events_unchanged:true},null,2));
    conversationSearchVerified=true;
  }
  if(reservationUiRequested){
    if(!process.env.XAA_NATIVE_SNAPSHOT)throw new Error('Reservation UI requires an isolated paused fixture snapshot');
    const headers={Authorization:`Bearer ${connection.token}`};
    const saved=(await (await fetch(`${connection.base_url}/runs?limit=1`,{headers})).json()).items[0];
    if(saved?.state!=='paused')throw new Error('Reservation fixture must be paused');
    const events=await (await fetch(`${connection.base_url}/runs/${saved.id}/events`,{headers})).json();
    const usage=await (await fetch(`${connection.base_url}/runs/${saved.id}/usage`,{headers})).json();
    if(usage.outstanding_reserved_tokens!==300||usage.outstanding_reservations!==1||usage.total_tokens!==0||usage.unmeasured_calls!==1)throw new Error('Saved hold metadata changed');
    for(let pass=0;pass<2;pass++){
      if(pass){await page.reload();await page.getByLabel('Local model').waitFor({timeout:20000});}
      await page.getByRole('button',{name:`Open ${saved.objective}`,exact:true}).click();
      await page.getByRole('heading',{name:'Active run paused',exact:true}).waitFor();
      const resources=page.getByRole('region',{name:'Run resources'});
      await resources.getByText('Reserved tokens awaiting measured results: 300 across 1 request.',{exact:true}).waitFor();
      await resources.getByText('These holds include input and the output allowance. Interrupted requests keep their hold until a measured result is saved.',{exact:true}).waitFor();
    }
    const after=await (await fetch(`${connection.base_url}/runs/${saved.id}/events`,{headers})).json();
    if(JSON.stringify(events)!==JSON.stringify(after))throw new Error('Viewing reserved usage mutated events');
    await page.screenshot({path:path.join(root,'reservation-ui.png'),fullPage:true});
    await writeFile(path.join(root,'reservation-ui.json'),JSON.stringify({passed:true,fixture:'synthetic paused journal with interrupted inference; no real prediction',native_boot:true,renderer_reload:true,events_unchanged:true,...usage},null,2));
    reservationUiVerified=true;
  }
  if (reopen) {
    if (!process.env.XAA_NATIVE_SNAPSHOT) throw new Error('Reopen acceptance requires an isolated saved snapshot');
    const headers={Authorization:`Bearer ${connection.token}`};
    const response=await fetch(`${connection.base_url}/runs?limit=1`,{headers});
    if(!response.ok)throw new Error('Native history API failed');
    const saved=(await response.json()).items[0];
    if(!saved||saved.state!=='completed')throw new Error('Reopen fixture must contain a completed run');
    const eventsResponse=await fetch(`${connection.base_url}/runs/${encodeURIComponent(saved.id)}/events`,{headers});
    const events=await eventsResponse.json();
    const candidate=events.filter(event=>event.event_type==='model.message').at(-1)?.payload.message.content;
    if(!candidate)throw new Error('Saved fixture has no candidate text');
    for(let pass=0;pass<2;pass++){
      if(pass) {await page.reload();await page.getByLabel('Local model').waitFor({timeout:20000});}
      await page.getByRole('button',{name:`Open ${saved.objective}`,exact:true}).click();
      await page.getByRole('heading',{name:'Active run completed'}).waitFor();
      await page.getByRole('heading',{name:'Accepted result',exact:true}).waitFor();
      await page.getByText(candidate,{exact:true}).waitFor({timeout:10000});
      for(const action of ['pause','resume','cancel'])if(await page.getByRole('button',{name:action,exact:true}).isEnabled())throw new Error('Completed run exposes an active execution control');
    }
    const after=await fetch(`${connection.base_url}/runs/${encodeURIComponent(saved.id)}/events`,{headers});
    if(JSON.stringify(await after.json())!==JSON.stringify(events))throw new Error('Reopening history changed persisted execution events');
    await writeFile(path.join(root,'reopen.json'),JSON.stringify({passed:true,run_id:saved.id,event_count:events.length,native_process_boot:true,renderer_reload:true,events_unchanged:true},null,2));
  }
  if (plan) {
    const workspace = path.join(root, 'workspace');
    await mkdir(workspace);
    const filename = `project-${randomUUID()}.txt`;
    const fixture = (specialists||taskQuotaRequested) ? `XAA-${randomUUID()}\n` : 'native planning fixture\n';
    await writeFile(path.join(workspace, filename), fixture);
    const response = await fetch(connection.base_url + '/models/load', { method: 'POST', headers: { Authorization: `Bearer ${connection.token}`, 'Content-Type': 'application/json' }, body: JSON.stringify({ model: 'qwen/qwen3.5-9b', context_length: taskQuotaRequested ? 32768 : 16384 }) });
    if (!response.ok) throw new Error('Native planning model loading failed');
    instance = (await response.json()).instance_id;
    await page.getByLabel('Local model').selectOption('qwen/qwen3.5-9b');
    await page.getByLabel('Workspace path').fill(workspace);
    const specialistRequirements = (specialists||taskQuotaRequested) ? 'Both task objectives must tell the assigned specialist to use workspace_read_file on the actual discovered filename and report the exact code in that file. For both tasks, acceptance_criteria must be exactly ["Report the exact code read from the fixture file."]. The reviewer must independently read the file. ' : '';
    const reservations = ((specialists||taskQuotaRequested) ? 'Give inspect max_iterations 2 and review max_iterations 3. ' : 'Give each task max_iterations 2. ')+'The tasks[].id values must be exactly inspect and review, without suffixes. Set review dependencies to ["inspect"]. ';
    await page.getByLabel('Objective', { exact: true }).fill('First list the workspace root using workspace_list_directory. Then use plan_save to save exactly two tasks: inspect (researcher, no dependencies) and review (reviewer, depends on inspect). Include the actual discovered filename in the inspect objective. ' + reservations + 'Give both tasks max_seconds 60, permission workspace.read, and explicit acceptance_criteria. ' + specialistRequirements + (taskQuotaRequested?'Set inspect max_total_tokens to exactly 1 and max_output_tokens to exactly 256. Set review max_total_tokens and max_output_tokens to null. This is an acceptance test of quota blocking; the tiny inspect allowance is intentional and must not be expanded. ':'') + 'After plan_save succeeds, immediately finish with a plain text answer: the plan is saved and execution is pending. Do not call plan_run_ready or any further tool after saving. The user will dispatch manually. Do not read or modify files and do not execute the tasks during this planning request.');
    if(taskQuotaRequested){
      await page.getByText('Run limits',{exact:true}).click();
      await page.getByLabel('Reported token threshold (optional)',{exact:true}).fill('50000');
      await page.getByLabel('Output tokens per request (optional)',{exact:true}).fill('4096');
    }
    const created = page.waitForResponse(response => new URL(response.url()).pathname === '/runs' && response.request().method() === 'POST');
    await page.getByRole('button', { name: 'Start objective' }).click();
    const run = await (await created).json();
    const panel = page.getByRole('region', {name:'Task plan'});
    await panel.getByRole('article', {name:'Task inspect'}).waitFor({timeout:90000});
    await page.getByRole('heading', {name:'Active run verifying'}).waitFor({timeout:90000});
    const saved = await fetch(`${connection.base_url}/runs/${encodeURIComponent(run.id)}/plan`, {headers:{Authorization:`Bearer ${connection.token}`}});
    if (!saved.ok) throw new Error('Native plan API unavailable');
    const graph = await saved.json();
    await writeFile(path.join(root, 'plan.json'), JSON.stringify(graph, null, 2));
    const inspect = graph.tasks.find(task => task.local_id === 'inspect');
    const review = graph.tasks.find(task => task.local_id === 'review');
    if (graph.tasks.length !== 2 || !inspect?.objective.includes(filename) || JSON.stringify(review?.dependencies) !== '["inspect"]' || JSON.stringify(graph.ready) !== '["inspect"]' || graph.tasks.some(task => task.status !== 'queued')) throw new Error('Native plan graph acceptance failed');
    if (!(await panel.getByRole('article', {name:'Task review'}).textContent()).includes('Waiting for dependencies')) throw new Error('Native dependency display failed');
    if (await readFile(path.join(workspace, filename), 'utf8') !== fixture) throw new Error('Native planning changed fixture bytes');
    await writeFile(path.join(root, 'plan.json'), JSON.stringify(graph, null, 2));
    if(taskQuotaRequested){
      if(inspect.budgets.max_total_tokens!==1||inspect.budgets.max_output_tokens!==256)throw new Error('Model plan did not preserve specialist quotas');
      const headers={Authorization:`Bearer ${connection.token}`};
      const before=await (await fetch(`${connection.base_url}/runs/${run.id}/events`,{headers})).json();
      const cursor=before.at(-1)?.sequence??0;
      await panel.getByRole('button',{name:'Run ready specialists',exact:true}).click();
      await page.getByRole('heading',{name:'Active run blocked',exact:true}).waitFor({timeout:90000});
      await panel.getByRole('article',{name:'Task inspect'}).getByText('The reported token allowance was reached, or usage could not be measured. Start a new objective to change its allowance.',{exact:true}).waitFor();
      await panel.getByRole('article',{name:'Task review'}).getByText('A prerequisite task is blocked or failed. This task cannot proceed.',{exact:true}).waitFor();
      const final=await (await fetch(`${connection.base_url}/runs/${run.id}/plan`,{headers})).json();
      const events=(await (await fetch(`${connection.base_url}/runs/${run.id}/events`,{headers})).json()).filter(event=>event.sequence>cursor);
      const replies=events.filter(event=>event.event_type==='task.model.message'&&event.payload.task_id==='inspect');
      if(final.tasks.some(task=>task.status!=='blocked')||final.tasks.find(task=>task.local_id==='inspect')?.blocked_reason!=='token_budget_exhausted_or_unmeasured'||(exactContextRequested ? replies.length!==0 : replies.length!==1||replies[0].payload.usage?.total_tokens<=1)||events.some(event=>event.event_type==='tool.intended'||event.event_type==='task.inference.started'&&event.payload.task_id==='review')||await readFile(path.join(workspace,filename),'utf8')!==fixture)throw new Error('Native specialist quota containment failed');
      await writeFile(path.join(root,'task-quota.json'),JSON.stringify({passed:true,run_id:run.id,plan_source:'real local model',task_states:final.tasks.map(task=>({id:task.local_id,status:task.status,reason:task.blocked_reason,budgets:task.budgets})),specialist_usage:replies[0]?.payload.usage??null,counted_admission_prevented_specialist_prediction:exactContextRequested,post_dispatch_tool_intents:0,dependent_inference:false,fixture_unchanged:true,parent_state:'blocked',explanations_visible:true},null,2));
      taskQuotaVerified=true;
    }
    if (specialists) {
      const expectedCriteria = ['Report the exact code read from the fixture file.'];
      if (graph.tasks.some(task => JSON.stringify(task.acceptance_criteria) !== JSON.stringify(expectedCriteria))) throw new Error('Model plan did not preserve fixture acceptance criteria');
      for (const taskId of ['inspect', 'review']) {
        const article = panel.getByRole('article', {name:`Task ${taskId}`});
        const streamedDraft=streamingRequested
          ? article.getByRole('heading',{name:'Partial specialist draft · unverified',exact:true}).waitFor({timeout:60000}).then(()=>true,()=>false)
          : Promise.resolve(false);
        await page.getByRole('button', {name:'Run ready specialists',exact:true}).click();
        let candidateReady=false;
        for(let attempt=0;attempt<90;attempt++){
          const response=await fetch(`${connection.base_url}/runs/${encodeURIComponent(run.id)}/plan`,{headers:{Authorization:`Bearer ${connection.token}`}});
          if(!response.ok)throw new Error('Cannot observe native specialist state');
          const status=(await response.json()).tasks.find(task=>task.local_id===taskId)?.status;
          if(status==='verifying'){candidateReady=true;break;}
          if(['failed','blocked','cancelled'].includes(status))throw new Error(`Native ${taskId} entered ${status}`);
          await new Promise(resolve=>setTimeout(resolve,1000));
        }
        if(!candidateReady)throw new Error(`Native ${taskId} did not submit a candidate before the observation deadline`);
        const partialVisible=await streamedDraft;
        if(streamingRequested&&!partialVisible)throw new Error(`Native ${taskId} streamed draft was not visible in its task panel`);
        specialistStreamingVerified ||= partialVisible;
        await article.getByRole('button', {name:'Review candidate',exact:true}).waitFor({timeout:10000});
        await page.getByRole('heading', {name:'Active run verifying'}).waitFor({timeout:90000});
        const candidateResponse = await fetch(`${connection.base_url}/runs/${encodeURIComponent(run.id)}/tasks/${taskId}/candidate`, {headers:{Authorization:`Bearer ${connection.token}`}});
        if (!candidateResponse.ok) throw new Error('Native candidate retrieval failed');
        const candidate = await candidateResponse.json();
        const digest = createHash('sha256').update(fixture).digest('hex');
        if (!candidate.candidate?.includes(fixture.trim()) || !candidate.evidence.length || candidate.evidence.some(item=>item.path!==filename||item.excerpt!==fixture||item.sha256!==digest)) throw new Error(`Native ${taskId} failed independent fixture evidence checks`);
        await writeFile(path.join(root, `${taskId}-candidate.json`), JSON.stringify(candidate,null,2));
        await article.getByRole('button', {name:'Review candidate',exact:true}).click();
        await article.getByLabel(expectedCriteria[0], {exact:true}).check();
        await article.getByLabel(`Reviewed ${filename}`, {exact:true}).first().check();
        await article.getByLabel('Review rationale').fill('Automated acceptance fixture: independently compared the fresh code, complete file bytes and SHA-256 with the criterion.');
        await article.getByRole('button', {name:'Accept task result',exact:true}).click();
        await article.getByText('completed', {exact:true}).waitFor();
      }
      const finalResponse = await fetch(`${connection.base_url}/runs/${encodeURIComponent(run.id)}/plan`, {headers:{Authorization:`Bearer ${connection.token}`}});
      const finalGraph = await finalResponse.json();
      if (finalGraph.tasks.some(task => task.status !== 'completed') || finalGraph.ready.length) throw new Error('Native reviewed dependency graph did not complete');
      if (await readFile(path.join(workspace, filename),'utf8') !== fixture) throw new Error('Specialists changed the read-only fixture');
      await writeFile(path.join(root,'reviewed-plan.json'),JSON.stringify(finalGraph,null,2));
      if(streamingRequested){
        const events=await (await fetch(`${connection.base_url}/runs/${encodeURIComponent(run.id)}/events`,{headers:{Authorization:`Bearer ${connection.token}`}})).json();
        const deltas=events.filter(event=>event.event_type==='task.model.delta');
        const byTask=Object.fromEntries(['inspect','review'].map(taskId=>[taskId,deltas.filter(event=>event.payload.task_id===taskId)]));
        if(Object.values(byTask).some(items=>items.length===0)||deltas.some(event=>event.payload.text.length>128))throw new Error('Native specialist streaming events were missing, mis-scoped or unbounded');
        await writeFile(path.join(root,'specialist-streaming.json'),JSON.stringify({passed:true,run_id:run.id,model:'qwen/qwen3.5-9b',unverified_drafts_shown_per_task:true,all_drafts_remained_review_gated:true,task_event_counts:Object.fromEntries(Object.entries(byTask).map(([taskId,items])=>[taskId,items.length])),max_delta_characters:Math.max(...deltas.map(event=>event.payload.text.length)),native_task_panels:true},null,2));
        specialistStreamingVerified=true;
      }
    }
    await fetch(connection.base_url + '/models/unload', { method: 'POST', headers: { Authorization: `Bearer ${connection.token}`, 'Content-Type': 'application/json' }, body: JSON.stringify({ instance_id: instance }) });
    instance = undefined;
  }
  if (edit) {
    if(!createRequested)await writeFile(path.join(root, 'config.txt'), 'value=1\n');
    await writeFile(path.join(root, 'neighbor.txt'), 'preserve');
    const response = await fetch(connection.base_url + '/models/load', { method: 'POST', headers: { Authorization: `Bearer ${connection.token}`, 'Content-Type': 'application/json' }, body: JSON.stringify({ model: 'qwen/qwen3.5-9b', context_length: 16384 }) });
    if (!response.ok) throw new Error('Native model loading failed');
    instance = (await response.json()).instance_id;
    await page.getByLabel('Local model').selectOption('qwen/qwen3.5-9b');
    await page.getByLabel('Workspace permissions').selectOption('workspace');
    await page.getByLabel('Workspace path').fill(root);
    await page.getByLabel('File to verify').fill('config.txt');
    await page.getByLabel('Expected file contents').fill('value=2\n');
    const objective = process.env.XAA_NATIVE_REPLACE === '1' ? 'Read config.txt using workspace_read_file. Then use workspace_replace_text to replace old_text value=1 with new_text value=2 using the exact returned sha256. Preserve all other content including the final newline.' : 'Read config.txt with workspace_read_file. Use its returned sha256 and workspace_write_file to set the exact content to the JSON string "value=2\\n", including its final newline. Report the result.';
    const requestedObjective=patchRequested?'Read config.txt with workspace_read_file. Then call workspace_apply_patch with the returned sha256 and a single-file unified patch: headers --- a/config.txt and +++ b/config.txt, hunk @@ -1 +1 @@, remove value=1 and add value=2. Preserve the final newline. Report the result.':objective;
    const creationObjective='Use workspace_create_file to create the NEW file config.txt with content equal to the JSON string "value=2\\n", including the final newline. Read the file back with workspace_read_file, preserve neighbor.txt and report the result.';
    await page.getByLabel('Objective', { exact: true }).fill(createRequested?creationObjective:requestedObjective+(patchRequested?' The patch string must end with a newline after +value=2. Use exactly this JSON string for patch: "--- a/config.txt\\n+++ b/config.txt\\n@@ -1 +1 @@\\n-value=1\\n+value=2\\n".':''));
    const partialObservation=streamingRequested?page.getByRole('heading',{name:'Partial response',exact:true}).waitFor({timeout:60000}).then(()=>true,()=>false):Promise.resolve(false);
    const created = page.waitForResponse(response => new URL(response.url()).pathname === '/runs' && response.request().method() === 'POST');
    await page.getByRole('button', { name: 'Start objective' }).click();
    const run = await (await created).json();
    await page.getByText('File checks passed; review pending', { exact: true }).waitFor({ timeout: 60000 });
    if (await readFile(path.join(root, 'config.txt'), 'utf8') !== 'value=2\n' || await readFile(path.join(root, 'neighbor.txt'), 'utf8') !== 'preserve') throw new Error('Native edit artifact acceptance failed');
    if(createRequested){
      const response=await fetch(`${connection.base_url}/runs/${encodeURIComponent(run.id)}/events`,{headers:{Authorization:`Bearer ${connection.token}`}});
      if(!response.ok)throw new Error('Cannot inspect native creation events');
      const events=await response.json();
      const calls=events.filter(event=>event.event_type==='model.message').flatMap(event=>event.payload.message.tool_calls??[]);
      const creates=calls.filter(call=>call.function.name==='workspace_create_file');
      const intents=events.filter(event=>event.event_type==='tool.intended'&&event.payload.tool==='workspace.create_file');
      const edits=calls.filter(call=>['workspace_write_file','workspace_replace_text','workspace_apply_patch'].includes(call.function.name));
      if(creates.length!==1||intents.length!==1||intents[0].payload.call_id!==creates[0].id||edits.length)throw new Error('Native creation required exactly one create proposal and no edits');
      createVerified=true;
      await writeFile(path.join(root,'creation.json'),JSON.stringify({passed:true,create_proposals:creates.length,create_intents:intents.length,exact_file_bytes:true,neighbor_preserved:true},null,2));
    }
    if(patchRequested){
      const response=await fetch(`${connection.base_url}/runs/${encodeURIComponent(run.id)}/events`,{headers:{Authorization:`Bearer ${connection.token}`}});
      if(!response.ok)throw new Error('Cannot inspect native patch events');
      const events=await response.json();
      const calls=events.filter(event=>event.event_type==='model.message').flatMap(event=>event.payload.message.tool_calls??[]);
      const patches=calls.filter(call=>call.function.name==='workspace_apply_patch');
      const writes=events.filter(event=>event.event_type==='tool.intended'&&event.payload.tool==='workspace.write_file');
      if(patches.length!==1||writes.length!==1||writes[0].payload.call_id!==patches[0].id)throw new Error('Native patch did not normalize exactly one model proposal to one write');
      patchVerified=true;
      await writeFile(path.join(root,'patch.json'),JSON.stringify({passed:true,patch_proposals:patches.length,normalized_write_intents:writes.length,exact_file_bytes:true,neighbor_preserved:true},null,2));
    }
    if(streamingRequested){
      const response=await fetch(`${connection.base_url}/runs/${encodeURIComponent(run.id)}/events`,{headers:{Authorization:`Bearer ${connection.token}`}});
      if(!response.ok)throw new Error('Cannot inspect native stream events');
      const events=await response.json();
      const proposals=new Map();
      for(const event of events)if(event.event_type==='model.message')for(const call of event.payload.message.tool_calls??[])proposals.set(call.id,event.sequence);
      const deltas=events.filter(event=>event.event_type==='model.delta');
      const effects=events.filter(event=>event.event_type==='tool.executing');
      if(!deltas.length||!effects.length||effects.some(event=>!proposals.has(event.payload.call_id)||proposals.get(event.payload.call_id)>=event.sequence))throw new Error('Native streamed action was not preceded by a complete validated proposal');
      partialUiObserved=await Promise.race([partialObservation,new Promise(resolve=>setTimeout(()=>resolve(false),1000))]);
      streamingVerified=true;
      await writeFile(path.join(root,'streaming.json'),JSON.stringify({passed:true,content_delta_events:deltas.length,validated_proposals:proposals.size,effects_after_proposals:true,partial_ui_observed:partialUiObserved},null,2));
    }
    if (finalReview) {
      const review = page.getByRole('region', {name:'Final run review'});
      await review.getByLabel('I reviewed the objective, result, and available evidence', {exact:true}).check();
      await review.getByLabel('Final review rationale', {exact:true}).fill('Automated acceptance fixture: verified exact config bytes and preserved neighbor before accepting.');
      await review.getByRole('button', {name:'Accept and complete run',exact:true}).click();
      await page.getByRole('heading', {name:'Active run completed'}).waitFor({timeout:10000});
      const response=await fetch(`${connection.base_url}/runs/${encodeURIComponent(run.id)}`,{headers:{Authorization:`Bearer ${connection.token}`}});
      if(!response.ok||(await response.json()).state!=='completed')throw new Error('Native final review did not persist completion');
    }
    await fetch(connection.base_url + '/models/unload', { method: 'POST', headers: { Authorization: `Bearer ${connection.token}`, 'Content-Type': 'application/json' }, body: JSON.stringify({ instance_id: instance }) });
    instance = undefined;
  }
  if(limitsRequested){
    const workspace=path.join(root,'limit-workspace');
    await mkdir(workspace);
    const fixture=path.join(workspace,'input.txt');
    await writeFile(fixture,'unchanged native token fixture\n');
    const headers={Authorization:`Bearer ${connection.token}`,'Content-Type':'application/json'};
    const loaded=await fetch(`${connection.base_url}/models/load`,{method:'POST',headers,body:JSON.stringify({model:'qwen/qwen3.5-9b',context_length:16384})});
    if(!loaded.ok)throw new Error('Native limit model loading failed');
    instance=(await loaded.json()).instance_id;
    await page.getByLabel('Local model').selectOption('qwen/qwen3.5-9b');
    await page.getByLabel('Workspace path').fill(workspace);
    await page.getByLabel('Workspace permissions').selectOption('observe');
    await page.getByLabel('File to verify').fill('');
    await page.getByLabel('Objective',{exact:true}).fill('Read input.txt with workspace_read_file and report its exact contents.');
    if(!await page.getByLabel('Iteration limit',{exact:true}).isVisible())await page.getByText('Run limits',{exact:true}).click();
    await page.getByLabel('Iteration limit',{exact:true}).fill('3');
    await page.getByLabel('Time limit (seconds)',{exact:true}).fill('60');
    await page.getByLabel('Reported token threshold (optional)',{exact:true}).fill('5000');
    await page.getByLabel('Output tokens per request (optional)',{exact:true}).fill('1');
    const created=page.waitForResponse(response=>new URL(response.url()).pathname==='/runs'&&response.request().method()==='POST');
    await page.getByRole('button',{name:'Start objective',exact:true}).click();
    const run=await (await created).json();
    await page.getByRole('heading',{name:'Active run blocked',exact:true}).waitFor({timeout:90000});
    await page.getByText('Run is blocked. The model reached its output limit. Start a new objective with a larger output limit.',{exact:true}).waitFor();
    const saved=await (await fetch(`${connection.base_url}/runs/${run.id}`,{headers})).json();
    const usage=await (await fetch(`${connection.base_url}/runs/${run.id}/usage`,{headers})).json();
    const events=await (await fetch(`${connection.base_url}/runs/${run.id}/events`,{headers})).json();
    const resources=page.getByRole('region',{name:'Run resources'});
    await resources.getByText('Reported token threshold: 5000 · Output tokens per request: 1',{exact:true}).waitFor();
    await resources.getByText(`Measured tokens: ${usage.total_tokens} (${usage.prompt_tokens} input, ${usage.completion_tokens} output). Calls without measured usage: ${usage.unmeasured_calls}.`,{exact:true}).waitFor();
    const diagnosticResponses=events.filter(event=>event.event_type==='diagnostic.inference.completed');
    const diagnosticStarts=events.filter(event=>event.event_type==='diagnostic.inference.started');
    if(diagnosticStarts.length!==2||diagnosticStarts.some(event=>event.payload.max_output_tokens!==512)||diagnosticResponses.some(event=>event.payload.usage?.completion_tokens>512))throw new Error('Native diagnostic output cap missing or exceeded');
    const diagnosticTokens=diagnosticResponses.reduce((sum,event)=>sum+(event.payload.usage?.total_tokens??0),0);
    const taskTokens=events.filter(event=>['model.message','model.incomplete','task.model.message'].includes(event.event_type)).reduce((sum,event)=>sum+(event.payload.usage?.total_tokens??0),0);
    if(diagnosticResponses.length!==2||diagnosticTokens<=0||usage.total_tokens!==diagnosticTokens+taskTokens)throw new Error('Native usage omitted diagnostic inference');
    const expected={max_iterations:3,max_seconds:60,max_total_tokens:5000,max_output_tokens:1};
    if(Object.entries(expected).some(([key,value])=>saved.limits[key]!==value)||!events.some(event=>event.event_type==='model.incomplete'&&event.payload.usage?.completion_tokens===1)||events.some(event=>event.event_type==='tool.intended')||await readFile(fixture,'utf8')!=='unchanged native token fixture\n')throw new Error('Native resource limits were not preserved or enforced');
    limitsVerified=true;
    await writeFile(path.join(root,'limits.json'),JSON.stringify({passed:true,run_id:run.id,limits:saved.limits,usage,state:saved.state,tool_intents:0,fixture_unchanged:true,native_ui_configured:true,diagnostic_calls:diagnosticResponses.length,diagnostic_tokens:diagnosticTokens,diagnostic_output_cap:512},null,2));
    const unloaded=await fetch(`${connection.base_url}/models/unload`,{method:'POST',headers,body:JSON.stringify({instance_id:instance})});
    if(!unloaded.ok)throw new Error('Native limit model unload failed');
    instance=undefined;
  }
  if(tokenCountRequested){
    const headers={Authorization:`Bearer ${connection.token}`,'Content-Type':'application/json'};
    const loaded=await fetch(`${connection.base_url}/models/load`,{method:'POST',headers,body:JSON.stringify({model:'qwen/qwen3.5-9b',context_length:16384})});
    if(!loaded.ok)throw new Error('Native tokenizer model loading failed');
    instance=(await loaded.json()).instance_id;
    const tools=[{type:'function',function:{name:'xaa_echo',description:'Return a diagnostic string unchanged.',parameters:{type:'object',properties:{value:{type:'string'}},required:['value'],additionalProperties:false}}}];
    const cases=[{name:'plain',messages:[{role:'user',content:'Say hello.'}],expected:13},
      {name:'tool_result',messages:[{role:'user',content:'Call xaa_echo with value fixture.'},{role:'assistant',tool_calls:[{id:'fixture-call',type:'function',function:{name:'xaa_echo',arguments:'{"value":"fixture"}'}}]},{role:'tool',tool_call_id:'fixture-call',content:'fixture'}],tools,expected:324}];
    const counts=[];
    for(const item of cases){
      const response=await fetch(`${connection.base_url}/providers/lm-studio/token-count`,{method:'POST',headers,body:JSON.stringify({model:instance,messages:item.messages,tools:item.tools})});
      if(!response.ok)throw new Error(`Native tokenizer API failed (${response.status})`);
      const count=await response.json();
      if(count.prompt_tokens!==item.expected||count.context_length!==16384||count.sdk_version!=='1.6.0b1')throw new Error('Native tokenizer fixture count/metadata mismatch');
      counts.push({case:item.name,...count});
    }
    const cliCount=JSON.parse(execFileSync('uv',['run','xaa','tokens','Say hello.','--model',instance],{windowsHide:true,encoding:'utf8',env:{...process.env,XAA_API_TOKEN:connection.token,XAA_API_URL:connection.base_url}}));
    if(cliCount.prompt_tokens!==13||cliCount.model!==instance)throw new Error('CLI prompt count did not match bundled service');
    await writeFile(path.join(root,'token-count.json'),JSON.stringify({passed:true,counts,bundled_worker:true,restricted_path:true,inference_requested:false,development_cli_verified:true},null,2));
    tokenCountVerified=true;
    const unloaded=await fetch(`${connection.base_url}/models/unload`,{method:'POST',headers,body:JSON.stringify({instance_id:instance})});
    if(!unloaded.ok)throw new Error('Native tokenizer model unload failed');
    instance=undefined;
  }
  await page.screenshot({ path: path.join(root, 'native.png'), fullPage: true });
  const crash = process.env.XAA_CRASH === '1';
  if (crash) execFileSync('taskkill', ['/PID', String(proc.pid), '/F'], { windowsHide: true, stdio: 'ignore' });
  else await page.evaluate(() => window.__TAURI_INTERNALS__.invoke('plugin:window|close', { label: 'main' })).catch(() => {});
  for (let attempt = 0; attempt < 40 && proc.exitCode === null; attempt++) await new Promise(resolve => setTimeout(resolve, 250));
  if (proc.exitCode === null) throw new Error('Native app did not exit after closing its window');
  if (!crash && proc.exitCode !== 0) throw new Error(`Native app exited with status ${proc.exitCode}`);
  let stopped = false;
  for (let attempt = 0; attempt < 20; attempt++) {
    try { await fetch(connection.base_url + '/models', { signal: AbortSignal.timeout(500) }); }
    catch { stopped = true; break; }
    await new Promise(resolve => setTimeout(resolve, 250));
  }
  if (!stopped) throw new Error('Owned engine remained reachable after native exit');
  const shutdownLog = (await readFile(logPath, 'utf8').catch(() => '')).slice(previousLog.length);
  const graceful = shutdownLog.includes('Application shutdown complete.') && shutdownLog.includes('Finished server process');
  await writeFile(path.join(root, 'shutdown.log'), shutdownLog);
  const report = { passed: true, discovered_models: count, native_edit_verified: edit, native_streaming_verified:streamingVerified, partial_ui_observed:partialUiObserved, native_final_review_verified:finalReview, native_reopen_verified:reopen, native_plan_verified: plan, native_specialists_verified: specialists, native_specialist_streaming_verified:specialistStreamingVerified, fixture_review_automated: specialists||finalReview||skillEvaluationRequested||skillLifecycleRequested, snapshot_boot_test: Boolean(process.env.XAA_NATIVE_SNAPSHOT), restricted_runtime_path: true, build_sha256: manifest, native_exit: true, engine_stopped: true, crash_test: crash, graceful_shutdown_log_observed: graceful };
  report.native_patch_verified=patchVerified;
  report.native_creation_verified=createVerified;
  report.explicit_model_selection_required=true;
  report.native_limits_verified=limitsVerified;
  report.native_task_quota_verified=taskQuotaVerified;
  report.exact_context_enabled=exactContextRequested;
  report.native_reservation_ui_verified=reservationUiVerified;
  report.native_conversation_search_verified=conversationSearchVerified;
  report.native_skills_verified=skillsVerified;
  report.native_skill_evaluation_verified=skillEvaluationVerified;
  report.native_skill_lifecycle_verified=skillLifecycleVerified;
  report.native_token_count_verified=tokenCountVerified;
  report.native_provider_setup_verified=providerSetupVerified;
  report.native_onboarding_verified=onboardingVerified;
  await writeFile(path.join(root, 'report.json'), JSON.stringify(report, null, 2));
  console.log(report, root);
} catch (error) {
  if (browser) await browser.contexts()[0]?.pages()[0]?.screenshot({path:path.join(root,'failure.png'),fullPage:true}).catch(()=>{});
  await writeFile(path.join(root, 'report.json'), JSON.stringify({ passed: false, native_edit_requested: edit, native_specialists_requested: specialists, error: String(error) }, null, 2));
  throw error;
} finally {
  if (instance && connection) await fetch(connection.base_url + '/models/unload', { method: 'POST', headers: { Authorization: `Bearer ${connection.token}`, 'Content-Type': 'application/json' }, body: JSON.stringify({ instance_id: instance }) }).catch(() => {});
  await browser?.close().catch(() => {});
  if (proc.exitCode === null) execFileSync('taskkill', ['/PID', String(proc.pid), '/T', '/F'], { windowsHide: true, stdio: 'ignore' });
}
