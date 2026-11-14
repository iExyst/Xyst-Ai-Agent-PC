import {useEffect,useState} from 'react';
import type {Run} from './api/client.ts';
import type {EngineClient} from './api/client.ts';
import {MCPReconciliationPanel} from './MCPReconciliationPanel.tsx';
import {WorkspaceWriteReconciliationPanel} from './WorkspaceWriteReconciliationPanel.tsx';

type Action='pause'|'resume'|'cancel';

export function ActiveRunPanel({run,answer,partial,activity,busy,client,onControl}:{run:Run;answer:string;partial:string;activity:string[];busy:boolean;client:EngineClient;onControl:(action:Action)=>void}) {
  const [workspaceRecoveryRunId,setWorkspaceRecoveryRunId]=useState(run.state==='blocked'?run.id:'');
  useEffect(()=>{if(run.state==='blocked')setWorkspaceRecoveryRunId(run.id);},[run.id,run.state]);
  const showWorkspaceRecovery=run.state==='blocked'||workspaceRecoveryRunId===run.id;
  function enabled(action:Action) {
    if(action==='resume')return ['paused','blocked'].includes(run.state);
    if(action==='pause')return ['queued','planning','running','verifying','waiting_for_approval'].includes(run.state);
    return !['completed','failed','cancelled'].includes(run.state);
  }
  return <section aria-label="Active run"><h2>Active run <small>{run.state}</small></h2><p className="active-run-objective-preview">{run.objective}</p><details className="active-run-objective-full"><summary>Show full objective</summary><p>{run.objective}</p></details>{run.command_grant&&<p>Isolated commands authorized for this run in {run.command_grant.distribution}, up to {run.command_grant.max_seconds} seconds each. Network denied; command changes stay in a private copy.</p>}{run.procedure_trial?.python_environment&&<p>Approved procedure Python environment: {run.procedure_trial.python_environment.environment_digest?.slice(0,12)??'unknown'}… Mounted read-only inside the WSL command sandbox.</p>}
    <div className="controls">{(['pause','resume','cancel'] as const).map(action=><button key={action} disabled={busy||!enabled(action)} onClick={()=>onControl(action)}>{action}</button>)}</div>
    {answer&&<article><h3>{run.state==='completed'?'Accepted result':'Candidate answer'}</h3><p style={{whiteSpace:'pre-wrap'}}>{answer}</p></article>}
    {partial&&<article><h3>Partial response</h3><p style={{whiteSpace:'pre-wrap'}}>{partial}</p></article>}
    <MCPReconciliationPanel client={client} runId={run.id}/>
    {showWorkspaceRecovery&&<WorkspaceWriteReconciliationPanel key={run.id} client={client} runId={run.id} enabled={run.state==='blocked'}/>}
    <h3>Activity</h3><ol aria-live="polite">{activity.map((item,index)=><li key={index}>{item}</li>)}</ol>
  </section>;
}
