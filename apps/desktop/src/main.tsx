import {ResourceLimits, parseLimits, type LimitInputs} from './ResourceLimits.tsx';
import {RunResources} from './RunResources.tsx';
import { useState, useRef, useEffect } from 'react';
import { createRoot } from 'react-dom/client';
import { EngineClient, EngineRequestError, type Run, type Artifact, type Plan } from './api/client.ts';
import './style.css';
import { invoke } from '@tauri-apps/api/core';
import { open } from '@tauri-apps/plugin-dialog';
import { SkillsPanel, type ProcedureTrialSelection } from './SkillsPanel.tsx';
import { MemoryPanel } from './MemoryPanel.tsx';
import { PlanPanel } from './PlanPanel.tsx';
import { RunReviewPanel } from './RunReviewPanel.tsx';
import { SavedRunsPanel } from './SavedRunsPanel.tsx';
import { AutomationsPanel } from './AutomationsPanel.tsx';
import { ChannelsPanel } from './ChannelsPanel.tsx';
import { ActiveRunPanel } from './ActiveRunPanel.tsx';
import { ExecutionSetup } from './ExecutionSetup.tsx';
import { ProviderSetup } from './ProviderSetup.tsx';
import { SearchProviderPanel } from './SearchProviderPanel.tsx';
import { MCPPanel } from './MCPPanel.tsx';
import type {components} from './generated/api.js';

function App() {
  const [token, setToken] = useState('');
  const [client, setClient] = useState<EngineClient>();
  const [models, setModels] = useState<string[]>([]);
  const [model, setModel] = useState('');
  const [diagnosticPassed, setDiagnosticPassed] = useState(false);
  const [workspace, setWorkspace] = useState('');
  const [sandbox,setSandbox]=useState<components['schemas']['ReadinessResult']>();
  const [commandAllowed,setCommandAllowed]=useState(false);
  const [limits, setLimits] = useState<LimitInputs>({max_iterations:'8', max_seconds:'900', max_total_tokens:'', max_output_tokens:''});
  const parsedLimits = parseLimits(limits);
  const [procedureTrial,setProcedureTrial]=useState<ProcedureTrialSelection>();
  const [usageRefresh, setUsageRefresh] = useState(0);
  const [mode, setMode] = useState<'observe' | 'workspace'>('observe');
  useEffect(()=>setCommandAllowed(false),[sandbox,workspace,mode,client]);
  const [checkPath, setCheckPath] = useState('');
  const [approvedWebHosts, setApprovedWebHosts] = useState('');
  const [approvedWebSearch, setApprovedWebSearch] = useState(false);
  const [approvedBrowser, setApprovedBrowser] = useState(false);
  const [approvedMCPTools, setApprovedMCPTools] = useState<string[]>([]);
  const [expectedContent, setExpectedContent] = useState('');
  const [objective, setObjective] = useState('');
  const [run, setRun] = useState<Run>();
  const [activity, setActivity] = useState<string[]>([]);
  const [answer, setAnswer] = useState('');
  const [partial,setPartial]=useState('');
  const [artifacts, setArtifacts] = useState<Artifact[]>([]);
  const [plan, setPlan] = useState<Plan>();
  const [historyRefresh,setHistoryRefresh]=useState(0);
  const [preview, setPreview] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const stream = useRef<AbortController | null>(null);
  useEffect(() => () => stream.current?.abort(), []);
  useEffect(() => {
    if (!('__TAURI_INTERNALS__' in window)) return;
    let disposed = false;
    setBusy(true);
    void (async () => {
      try {
        const connection = await invoke<{base_url: string; token: string}>('engine_connection');
        const next = new EngineClient(connection.base_url, connection.token);
        for (let attempt = 0; attempt < 40 && !disposed; attempt++) {
          try {
            await next.history();
            if (!disposed) { setModel(''); setClient(next); }
            const available = await next.models().catch(()=>undefined);
            if (!disposed) { setModels(available?.map(item => item.id)??[]);if(!available)setError('The engine is connected, but LM Studio models are unavailable. Sandbox setup and saved data remain accessible.'); }
            return;
          } catch { await new Promise(resolve => setTimeout(resolve, 250)); }
        }
        if (!disposed) setError('The local engine did not become ready. Inspect the engine log and restart the app.');
      } catch (failure) { if (!disposed) setError(String(failure)); }
      finally { if (!disposed) setBusy(false); }
    })();
    return () => { disposed = true; };
  }, []);
  async function connect() {
    setBusy(true); setError('');
    try {
      const next = new EngineClient('http://127.0.0.1:8765', token);
      await next.history();
      setClient(next); setToken('');
      const available = await next.models().catch(()=>undefined);
      setModels(available?.map(item => item.id)??[]); setModel('');
      if(!available)setError('The engine is connected, but LM Studio models are unavailable. Sandbox setup and saved data remain accessible.');
    } catch (failure) { setError(String(failure)); } finally { setBusy(false); }
  }
  async function watch(next: Run) {
    if (!client) return;
    stream.current?.abort();
    const controller = new AbortController(); stream.current = controller;
    setActivity([]);setAnswer('');setPartial('');setArtifacts([]);setPreview('');setPlan(undefined);
    try {
      const initialPlan = await client.plan(next.id, controller.signal);
      if (!controller.signal.aborted) setPlan(initialPlan);
      for await (const event of client.events(next.id, 0, controller.signal)) {
        if(controller.signal.aborted)break;
        if(['tokens.reserved','model.inference.started','task.inference.started','model.message','model.incomplete','task.model.message','diagnostic.inference.started','diagnostic.inference.completed'].includes(event.event_type))setUsageRefresh(value=>value+1);
        if(event.event_type==='model.inference.started'){setPartial('');setAnswer('');}
        if(event.event_type==='model.delta'&&typeof event.payload.text==='string'){setPartial(text=>(text+event.payload.text).slice(0,65536));continue;}
        if (event.event_type.startsWith('plan.') || event.event_type.startsWith('task.')) {
          const updatedPlan = await client.plan(next.id, controller.signal);
          if (!controller.signal.aborted) setPlan(updatedPlan);
        }
        if (event.event_type === 'artifact.created') {const saved=await client.artifacts(next.id,controller.signal);if(!controller.signal.aborted)setArtifacts(saved);}
        if(controller.signal.aborted)break;
        const labels: Record<string, string> = { 'run.queued': 'Objective queued', 'tool.intended': 'Workspace operation started', 'tool.completed': 'Workspace operation saved', 'model.message': 'Model response saved', 'run.lease_acquired': 'Worker started', 'run.lease_released': 'Worker stopped', 'verification.feedback': 'File check failed; correction requested', 'verification.passed': 'File checks passed; review pending' };
        Object.assign(labels, { 'tool.failed': 'Workspace operation needs review', 'plan.rejected': 'Plan proposal rejected; correction needed', 'plan.saved': 'Task plan saved', 'task.claimed': 'Specialist task started', 'task.blocked': 'Specialist task blocked', 'task.result': 'Specialist result submitted', 'task.verification': 'Specialist file evidence checked' });
        Object.assign(labels, {'execution.granted':'Isolated commands authorized for this run','execution.reserved':'Sandbox command reserved','execution.snapshot':'Private project copy captured','tool.executing':'Workspace operation executing','execution.completed':'Sandbox command result saved','execution.reconciled':'Interrupted command reconciled; inspect verification status','model.answer_requested':'Public answer requested; tools disabled for this legacy continuation','model.response_requested':'Model response requested; saved progress retained','diagnostic.inference.started':'Checking model tool support','diagnostic.inference.completed':'Model tool check response saved'});
        const reasons: Record<string,string> = {
          model_no_public_response: 'The model returned another empty response without progress. Inspect the saved evidence before retrying.',
          model_no_public_answer: 'The model returned no public answer after one answer-only retry. Inspect the saved evidence; command results were not replayed.',
          saved_candidate_has_no_public_text: 'The saved response has no public candidate text. Inspect the evidence before starting a new objective.',
          context_budget_exceeded: 'The estimated request and output allowance do not fit the loaded context. Reduce input or the output limit, or load a larger context.',
          specialist_tasks_blocked: 'Specialist work is blocked and no candidate or ready task remains. Inspect the task plan before starting a new objective.',
          diagnostic_output_limit: 'The tool-use check reached its output limit or was filtered. Tools remain disabled; choose another model or retry the objective.',
          model_length: 'The model reached its output limit. Start a new objective with a larger output limit.',
          token_budget_exhausted_or_unmeasured: 'The reported token threshold was reached, or usage could not be measured. Further work is blocked.',
          iteration_budget_exhausted: 'The shared iteration limit was reached.',
          time_budget_exhausted: 'The run time limit was reached.',
        };
        const reason = typeof event.payload.reason === 'string' ? reasons[event.payload.reason] : undefined;
        const description = event.event_type === 'run.state_changed' ? `Run is ${String(event.payload.to).replaceAll('_', ' ')}${reason ? `. ${reason}` : ''}` : (labels[event.event_type] ?? 'Progress saved');
        setActivity(items => [...items.slice(-199), description]);
        if (event.event_type === 'model.message') {
          setPartial('');
          const message = event.payload.message;
          if (message && typeof message === 'object' && 'content' in message && typeof message.content === 'string') setAnswer(message.content);
        }
        if(event.event_type==='run.state_changed')setHistoryRefresh(value=>value+1);
        if (event.event_type.startsWith('run.')) {const saved=await client.getRun(next.id,controller.signal);if(!controller.signal.aborted)setRun(saved);}
      }
    } catch (failure) { if (!controller.signal.aborted) setError(String(failure)); }
  }
  async function openSaved(id:string) {
    if(!client)return;
    stream.current?.abort();
    const controller=new AbortController();stream.current=controller;
    setBusy(true);setError('');
    try {const saved=await client.getRun(id,controller.signal);if(!controller.signal.aborted){setRun(saved);setBusy(false);void watch(saved);}}
    catch(failure){if(!controller.signal.aborted)setError(String(failure));}
    finally{if(stream.current===controller||!controller.signal.aborted)setBusy(false);}
  }
  async function submit() {
    if (!client || !parsedLimits) return;
    if(commandAllowed&&(!sandbox||sandbox.state!=='ready'||!sandbox.profile_sha256||mode!=='workspace'||Date.now()>=Date.parse(sandbox.expires_at))){setCommandAllowed(false);setError('Sandbox authorization expired. Check the sandbox and select the command permission again.');return;}
    setBusy(true); setError(''); setActivity([]); setAnswer(''); setArtifacts([]); setPreview(''); setPlan(undefined);
    try { const next = await client.createRun({ objective, model, workspace, mode, approved_web_search:approvedWebSearch, approved_browser:approvedBrowser, approved_web_hosts: approvedWebHosts.split(/[\s,]+/).map(host=>host.trim()).filter(Boolean), approved_mcp_tools:approvedMCPTools, command_grant:commandAllowed&&mode==='workspace'&&sandbox?.state==='ready'&&sandbox.profile_sha256&&Date.now()<Date.parse(sandbox.expires_at)?{backend:sandbox.backend,distribution:sandbox.distribution,profile_sha256:sandbox.profile_sha256,max_seconds:30}:null, procedure_trial:procedureTrial?{skill_id:procedureTrial.skill_id,version:procedureTrial.version,purpose:procedureTrial.purpose??'evaluation_trial'}:null, ...parsedLimits, file_checks: checkPath.trim() ? [{path: checkPath, expected_content: expectedContent}] : [] }); setRun(next);setCommandAllowed(false);setApprovedWebSearch(false);setApprovedBrowser(false);setApprovedMCPTools([]);setProcedureTrial(undefined);setHistoryRefresh(value=>value+1); void watch(next); }
    catch (failure) { setError(failure instanceof EngineRequestError&&failure.status===403&&commandAllowed?'Command authorization was refused. Check the sandbox again and confirm Workspace mode and the selected project.':String(failure)); } finally { setBusy(false); }
  }
  async function selectWorkspace() {
    try {
      const selected = await open({ directory: true, multiple: false, title: 'Choose a project workspace' });
      if (typeof selected === 'string') setWorkspace(selected);
    } catch (failure) { setError(`Could not open the workspace picker: ${String(failure)}`); }
  }
  async function startDemonstration() {
    if (!client || !workspace.trim() || !model || !parsedLimits) return;
    const demoObjective = 'Read the project overview and setup documentation in the selected workspace. Do not modify files or run commands. Summarize what the project does, how a developer starts it, and one useful next step. Cite the file paths you read.';
    setMode('observe');
    setBusy(true); setError(''); setActivity([]); setAnswer(''); setArtifacts([]); setPreview(''); setPlan(undefined);
    try {
      const next = await client.createRun({ objective: demoObjective, model, workspace, mode: 'observe', approved_web_search:false, approved_browser:false, command_grant: null, ...parsedLimits, file_checks: [] });
      setObjective(demoObjective); setRun(next); setCommandAllowed(false); setApprovedBrowser(false); setHistoryRefresh(value => value + 1); void watch(next);
    } catch (failure) { setError(String(failure)); }
    finally { setBusy(false); }
  }
  async function dispatchPlan() {
    if (!client || !run) return;
    setBusy(true); setError('');
    try { const next=await client.dispatchPlan(run.id);setRun(next);void watch(next); }
    catch(failure) {setError(String(failure));}
    finally {setBusy(false);}
  }
  async function control(action: 'pause' | 'resume' | 'cancel') {
    if (!client || !run) return;
    try { setRun(await client.control(run.id, action)); } catch (failure) { setError(String(failure)); }
  }
  return <main><header><span>X.A.A.</span><h1>Xyst AI Agent</h1><p>Local objectives. Visible execution.</p></header>
    {!client ? <section><h2>Connect to the engine</h2><p>Start the local engine, then enter its API token. The token stays in this window’s memory.</p><label>Engine token<input type="password" value={token} onChange={event => setToken(event.target.value)} /></label><button onClick={connect} disabled={busy || token.length < 32}>Connect</button></section> : <>
      <ProviderSetup client={client} models={models} model={model} onModelsChanged={setModels} onModelChanged={setModel} onDiagnosticPassed={setDiagnosticPassed}/>
      <section aria-labelledby="onboarding-title"><h2 id="onboarding-title">Get started</h2><ol><li>Connect LM Studio, choose a discovered model, and complete the harmless tool diagnostic above. {diagnosticPassed?'Tool support verified.':'The demonstration unlocks after a successful diagnostic.'}</li><li>{workspace ? <>Workspace selected: <code>{workspace}</code></> : 'Choose a project folder below.'}</li><li>Run a read-only demonstration to see the agent inspect and explain that project.</li></ol><button type="button" onClick={()=>void startDemonstration()} disabled={busy||!diagnosticPassed||!model||!workspace.trim()}>Start read-only onboarding demonstration</button></section>
      <section><details><summary>Memory preferences</summary><MemoryPanel client={client} /></details></section>
      <section><details><summary>Reusable procedures</summary><SkillsPanel client={client} onTrial={setProcedureTrial}/></details></section>
      <section><details><summary>Automations</summary><AutomationsPanel client={client} model={model} workspace={workspace}/></details></section>
      <section><details><summary>Messaging channels</summary><ChannelsPanel client={client}/></details></section>
      <section><details><summary>Web search integration</summary><SearchProviderPanel client={client}/></details></section>
      <section><details><summary>MCP integrations</summary><MCPPanel client={client} selectedRunWriteTools={approvedMCPTools} onRunWriteToolsChange={setApprovedMCPTools}/></details></section>
      <ExecutionSetup client={client} onReady={setSandbox}/>
      <SavedRunsPanel client={client} refreshKey={String(historyRefresh)} onOpen={id=>void openSaved(id)}/>
      <section><h2>Operating mode</h2><label>Workspace permissions<select value={mode} onChange={event => setMode(event.target.value as 'observe' | 'workspace')}><option value="observe">Observe — read files</option><option value="workspace">Workspace — read, create and edit files</option></select></label><p>{mode === 'workspace' ? 'New objectives may create files and edit existing files inside the selected workspace. Creation requires existing parent directories and never overwrites a destination.' : 'New objectives can read files without changing them.'}</p></section>
      <section><h2>File check (optional)</h2><p>Compare a file with the exact contents you supply, including whitespace. A mismatch asks the agent to correct its work within the run budget.</p><label>File to verify<input value={checkPath} onChange={event => setCheckPath(event.target.value)} placeholder="config.txt" /></label><label htmlFor="expected-file-content">Expected file contents</label><textarea id="expected-file-content" value={expectedContent} onChange={event => setExpectedContent(event.target.value)} /></section>
      {procedureTrial&&<section aria-label="Selected procedure"><p>{procedureTrial.purpose==='active_use'?'Active procedure':'Unverified procedure trial'}: {procedureTrial.name} &middot; version {procedureTrial.version}. Its requirements must fit the selected workspace and mode.</p><button onClick={()=>setProcedureTrial(undefined)}>Remove selected procedure</button></section>}
      <section><h2>New objective</h2><label className="procedure-permission"><input type="checkbox" checked={commandAllowed} disabled={busy||mode!=='workspace'||!workspace.trim()||!sandbox} onChange={event=>setCommandAllowed(event.target.checked)}/>Allow isolated commands for this next run</label><p>Requires Workspace mode and a current sandbox check. Commands receive a copy of the selected project, without network access, for up to 30 seconds each. Command changes are discarded. This grant stays with the created run when paused or resumed; each new run requires a new selection.</p><label className="procedure-permission"><input type="checkbox" checked={approvedWebSearch} disabled={busy} onChange={event=>setApprovedWebSearch(event.target.checked)}/>Allow Brave web search for this next run</label><p>Requires a saved Brave Search key. Queries are sent to Brave; results are untrusted. This does not authorize reading result pages.</p><label className="procedure-permission"><input type="checkbox" checked={approvedBrowser} disabled={busy||!approvedWebHosts.trim()} onChange={event=>setApprovedBrowser(event.target.checked)}/>Allow managed browser reads for this next run</label><p>Uses a fresh Microsoft Edge session to open pages, follow named links, read visible text, and optionally capture screenshots. It cannot submit forms, click buttons, download files, or use your saved browser profile. Exact hostnames below also limit browser navigation and page requests; site content is untrusted.</p><label htmlFor="approved-web-hosts">Public web hosts approved for this run (optional)</label><textarea id="approved-web-hosts" value={approvedWebHosts} onChange={event=>setApprovedWebHosts(event.target.value)} placeholder="docs.example.com, standards.example.org"/><p>Each exact hostname you enter authorizes public HTTPS reads for this run. Private and local addresses, other hosts, non-HTTPS links, and redirects outside this list are blocked. Page text is untrusted input. This grant does not permit form submissions or workspace file changes.</p><label>Workspace path<input value={workspace} onChange={event => setWorkspace(event.target.value)} placeholder="C:\Projects\example" /></label><button type="button" onClick={()=>void selectWorkspace()} disabled={busy}>Browse for project folder</button><label htmlFor="run-objective">Objective</label><textarea id="run-objective" value={objective} onChange={event => setObjective(event.target.value)} /><ResourceLimits inputs={limits} onChange={setLimits}/><p>Final answers await independent verification</p><button onClick={submit} disabled={busy || !parsedLimits || !objective.trim() || !model || !workspace.trim()}>Start objective</button></section>
      {run && <ActiveRunPanel run={run} answer={answer} partial={partial} activity={activity} busy={busy} onControl={action=>void control(action)}/>}
      {run && <RunResources client={client} run={run} refresh={usageRefresh}/>}
      {artifacts.length > 0 && <section><h2>Files and artifacts</h2><ul>{artifacts.map(item => <li key={item.id}><button onClick={async () => { try { setPreview(await client.artifactPreview(item.id)); } catch (failure) { setError(String(failure)); } }}>View {item.provenance}</button> {item.size_bytes} bytes</li>)}</ul>{preview.startsWith('data:image/png;base64,') ? <img src={preview} alt="Untrusted browser screenshot" style={{maxWidth:'100%',maxHeight:600}}/> : preview && <pre style={{whiteSpace:'pre-wrap',overflowWrap:'anywhere',maxHeight:400,overflow:'auto'}}>{preview}</pre>}</section>}
      {run && plan && <PlanPanel key={run.id} plan={plan} client={client} runId={run.id} onReviewed={setPlan} onDispatch={()=>void dispatchPlan()} canDispatch={!busy&&run.state==='verifying'} />}
      {run?.state==='verifying'&&<RunReviewPanel key={`${run.id}:${run.revision}`} client={client} run={run} canAccept={plan!==undefined&&plan.tasks.every(task=>task.status==='completed')} onReviewed={next=>{setRun(next);setHistoryRefresh(value=>value+1);}}/>}
    </>}{error && <p role="alert">{error}</p>}</main>;
}
createRoot(document.getElementById('root')!).render(<App />);
