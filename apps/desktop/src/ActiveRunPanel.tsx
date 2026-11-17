import {useEffect,useState} from 'react';
import type {Run,RunEvent} from './api/client.ts';
import type {EngineClient} from './api/client.ts';
import {runElapsedSeconds} from './run-time.ts';
import {MCPReconciliationPanel} from './MCPReconciliationPanel.tsx';
import {WorkspaceWriteReconciliationPanel} from './WorkspaceWriteReconciliationPanel.tsx';

type Action='pause'|'resume'|'cancel';

const runStateLabels:Record<Run['state'],string>={
  queued:'Queued',planning:'Planning',running:'Running',verifying:'Checking results',
  waiting_for_approval:'Waiting for your approval',paused:'Paused',blocked:'Blocked, needs review',
  completed:'Completed',failed:'Failed',cancelled:'Cancelled',
};

type RunActivity=RunEvent&{description:string};

function PublicToolResult({client,runId,callId}:{client:EngineClient;runId:string;callId:string}) {
  const [expanded,setExpanded]=useState(false);
  const [result,setResult]=useState<string|null|undefined>(undefined);
  const [error,setError]=useState('');
  const [loading,setLoading]=useState(false);
  useEffect(()=>{
    if(!expanded||result!==undefined||loading)return;
    const controller=new AbortController();
    setLoading(true);
    void client.publicToolResult(runId,callId,controller.signal).then(setResult).catch(failure=>{
      if(!controller.signal.aborted)setError(failure instanceof Error?failure.message:'Could not load saved tool result.');
    }).finally(()=>{if(!controller.signal.aborted)setLoading(false);});
    return()=>{controller.abort();if(result===undefined)setLoading(false);};
  },[callId,client,expanded,result,runId]);
  return <div className="run-tool-result"><button type="button" aria-expanded={expanded} onClick={()=>{if(!expanded&&error)setError('');setExpanded(value=>!value);}}>{expanded?'Hide saved result':'View saved result'}</button>{expanded&&<section aria-label="Saved tool result"><p className="eyebrow">Untrusted saved tool result</p>{loading&&<p role="status">Loading saved result…</p>}{error&&<p role="alert">{error}</p>}{!loading&&!error&&result===null&&<p>No public result is available.</p>}{!loading&&!error&&typeof result==='string'&&<pre>{result}</pre>}</section>}</div>;
}

export function ActiveRunPanel({run,answer,partial,activity,busy,client,onControl}:{run:Run;answer:string;partial:string;activity:RunActivity[];busy:boolean;client:EngineClient;onControl:(action:Action)=>void}) {
  const [workspaceRecoveryRunId,setWorkspaceRecoveryRunId]=useState(run.state==='blocked'?run.id:'');
  const [clockNow,setClockNow]=useState(()=>Date.now());
  const isTerminal=['completed','failed','cancelled'].includes(run.state);
  useEffect(()=>{
    if(isTerminal)return;
    const timer=window.setInterval(()=>setClockNow(Date.now()),1000);
    return()=>window.clearInterval(timer);
  },[isTerminal,run.id]);
  useEffect(()=>{if(run.state==='blocked')setWorkspaceRecoveryRunId(run.id);},[run.id,run.state]);
  const elapsedSeconds=runElapsedSeconds(run,clockNow);
  const elapsedLabel=elapsedSeconds===null?'Unavailable':elapsedSeconds>=3600?`${Math.floor(elapsedSeconds/3600)}h ${Math.floor(elapsedSeconds%3600/60)}m ${elapsedSeconds%60}s`:elapsedSeconds>=60?`${Math.floor(elapsedSeconds/60)}m ${elapsedSeconds%60}s`:`${elapsedSeconds}s`;
  const currentActivity=activity[activity.length-1]?.description??'Waiting for the next saved update.';
  const showWorkspaceRecovery=run.state==='blocked'||workspaceRecoveryRunId===run.id;
  function enabled(action:Action) {
    if(action==='resume')return ['paused','blocked'].includes(run.state);
    if(action==='pause')return ['queued','planning','running','verifying','waiting_for_approval'].includes(run.state);
    return !['completed','failed','cancelled'].includes(run.state);
  }
  return <section aria-label="Active run"><h2>Active run <small>{run.state}</small></h2><p role="status" aria-label="Run status" className="sr-only">Run status: {runStateLabels[run.state]}</p><p className="active-run-elapsed">Elapsed wall time: <time aria-label="Elapsed wall time" aria-live="off">{elapsedLabel}</time></p><section className="active-run-current-activity" aria-label="Current activity"><p className="eyebrow">CURRENT ACTIVITY</p><p>{currentActivity}</p></section><p className="active-run-objective-preview">{run.objective}</p><details className="active-run-objective-full"><summary>Show full objective</summary><p>{run.objective}</p></details>{run.command_grant&&<p>Isolated commands authorized for this run in {run.command_grant.distribution}, up to {run.command_grant.max_seconds} seconds each. Network denied; command changes stay in a private copy.</p>}{run.procedure_trial?.python_environment&&<p>Approved procedure Python environment: {run.procedure_trial.python_environment.environment_digest?.slice(0,12)??'unknown'}… Mounted read-only inside the WSL command sandbox.</p>}
    <div className="controls">{(['pause','resume','cancel'] as const).map(action=><button key={action} disabled={busy||!enabled(action)} onClick={()=>onControl(action)}>{action}</button>)}</div>
    {answer&&<article><h3>{run.state==='completed'?'Accepted result':'Candidate answer'}</h3><p style={{whiteSpace:'pre-wrap'}}>{answer}</p></article>}
    {partial&&<article><h3>Partial response</h3><p style={{whiteSpace:'pre-wrap'}}>{partial}</p></article>}
    <MCPReconciliationPanel client={client} runId={run.id}/>
    {showWorkspaceRecovery&&<WorkspaceWriteReconciliationPanel key={run.id} client={client} runId={run.id} enabled={run.state==='blocked'}/>}
    <h3>Activity timeline</h3><ol className="run-activity-timeline" aria-label="Run activity history" aria-live="off">{activity.map(item=>{const callId=item.payload.call_id;const showResult=(item.event_type==='tool.completed'||item.event_type==='tool.failed')&&item.payload.internal===false&&typeof callId==='string';return <li key={item.sequence} className="run-activity-entry"><time dateTime={item.created_at}>{Number.isFinite(Date.parse(item.created_at))?new Date(item.created_at).toLocaleTimeString(): 'Time unavailable'}</time><span className="run-activity-type">{item.event_type}</span><span className="run-activity-description">{item.description}</span>{showResult&&<PublicToolResult key={`${run.id}:${callId}`} client={client} runId={run.id} callId={callId}/>}</li>})}</ol>
  </section>;
}
