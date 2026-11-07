import {useState} from 'react';
import {EngineClient,type Plan} from './api/client.ts';
import type {components} from './generated/api.js';

type Summary=components['schemas']['TaskRecoverySummary'];

export function TaskRecoveryPanel({client,runId,taskId,onUpdated}:{client:EngineClient;runId:string;taskId:string;onUpdated:(plan:Plan)=>void}) {
  const [summary,setSummary]=useState<Summary>();
  const [error,setError]=useState('');
  const [busy,setBusy]=useState(false);
  async function inspect() {
    setBusy(true);setError('');
    try {setSummary(await client.taskRecovery(runId,taskId));}
    catch(failure) {setError(String(failure));}
    finally {setBusy(false);}
  }
  async function decide(decision:'resume'|'review_candidate'|'fail') {
    setBusy(true);setError('');
    try {onUpdated(await client.decideTaskRecovery(runId,taskId,decision));}
    catch(failure) {setError(String(failure));}
    finally {setBusy(false);}
  }
  return <section aria-label={`Recovery for task ${taskId}`}>
    <p>The specialist stopped unexpectedly. Review its durable journal before choosing whether to continue or close this task.</p>
    {!summary?<button disabled={busy} onClick={()=>void inspect()}>Inspect saved task journal</button>:<>
      <p>{summary.saved_tool_results} saved tool results · {summary.consumed_iterations} inference attempts · {summary.run_measured_tokens} measured run tokens · {summary.reserved_tokens} tokens reserved</p>
      {summary.candidate&&<><h4>Saved candidate for review</h4><pre>{summary.candidate}</pre><p>This candidate is unverified. Open the ordinary evidence review before accepting it.</p><button disabled={busy||!summary.candidate_reviewable} onClick={()=>void decide('review_candidate')}>Review saved candidate</button>{!summary.candidate_reviewable&&<p>Later or unresolved work follows this candidate, so it cannot be reviewed yet.</p>}</>}
      {summary.pending_model_tool_calls.length>0&&<><h4>Proposed actions without a saved result</h4><ul>{summary.pending_model_tool_calls.map((call,index)=><li key={`${call.call_id}-${index}`}>{call.name??'Unknown tool'} ({call.call_id??'missing call ID'})</li>)}</ul><p>These actions may have been in flight. Reconcile their journals before continuing.</p></>}
      {summary.unresolved_tool_calls>0&&<p>{summary.unresolved_tool_calls} tool action(s) have an uncertain execution result. They must be reconciled before continuation.</p>}
      {summary.safe_to_resume&&<p>The saved history is at a safe boundary, with no unresolved action or candidate, and the shared run budget has room to continue.</p>}
      <div className="controls"><button disabled={busy||!summary.safe_to_resume} onClick={()=>void decide('resume')}>Resume from saved results</button><button disabled={busy} onClick={()=>void decide('fail')}>Mark task failed</button></div>
    </>}{error&&<p role="alert">{error}</p>}
  </section>;
}
