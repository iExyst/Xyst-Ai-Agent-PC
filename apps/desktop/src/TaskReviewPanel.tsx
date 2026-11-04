import { useState } from 'react';
import { EngineClient, type Candidate, type Plan } from './api/client.ts';

export function TaskReviewPanel({client,runId,taskId,onReviewed}:{client:EngineClient;runId:string;taskId:string;onReviewed:(plan:Plan)=>void}) {
  const [candidate,setCandidate]=useState<Candidate>();
  const [criteria,setCriteria]=useState<number[]>([]);
  const [evidence,setEvidence]=useState<string[]>([]);
  const [rationale,setRationale]=useState('');
  const [error,setError]=useState('');
  const [busy,setBusy]=useState(false);
  async function load() {
    setBusy(true);setError('');
    try {setCandidate(await client.candidate(runId,taskId));}
    catch(failure) {setError(String(failure));}
    finally {setBusy(false);}
  }
  async function decide(decision:'accept'|'reject') {
    setBusy(true);setError('');
    try {onReviewed(await client.reviewCandidate(runId,taskId,{decision,rationale,confirmed_criteria:criteria,evidence_call_ids:evidence}));}
    catch(failure) {setError(String(failure));}
    finally {setBusy(false);}
  }
  return <div className="task-review">
    {!candidate ? <button onClick={load} disabled={busy}>Review candidate</button> : <>
      <h4>Candidate result</h4><pre>{candidate.candidate ?? 'No candidate text recorded.'}</pre>
      <p>Inspect the file evidence and confirm each criterion before accepting. Files are checked again when you accept.</p>
      <fieldset><legend>Criteria reviewed</legend>{candidate.acceptance_criteria.map((item,index)=><label key={index}><input type="checkbox" checked={criteria.includes(index)} onChange={event=>setCriteria(items=>event.target.checked?[...items,index]:items.filter(value=>value!==index))}/>{item}</label>)}</fieldset>
      <fieldset><legend>File evidence reviewed</legend>{candidate.evidence.length===0?<p>No complete file evidence was recorded. Acceptance is unavailable.</p>:candidate.evidence.map(item=><div key={item.call_id}><label><input type="checkbox" checked={evidence.includes(item.call_id)} onChange={event=>setEvidence(items=>event.target.checked?[...items,item.call_id]:items.filter(value=>value!==item.call_id))}/>Reviewed {item.path}</label><pre>{item.excerpt}{item.excerpt_truncated?'\n[Excerpt limited to 4096 characters]':''}</pre><p>SHA-256: {item.sha256}</p></div>)}</fieldset>
      <label>Review rationale<textarea value={rationale} maxLength={4000} onChange={event=>setRationale(event.target.value)}/></label>
      <div className="controls"><button disabled={busy||!rationale.trim()||criteria.length!==candidate.acceptance_criteria.length||criteria.length===0||evidence.length===0} onClick={()=>decide('accept')}>Accept task result</button><button disabled={busy||!rationale.trim()} onClick={()=>decide('reject')}>Reject task result</button></div>
    </>}{error&&<p role="alert">{error}</p>}
  </div>;
}
