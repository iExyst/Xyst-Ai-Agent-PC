import {useState} from 'react';
import {EngineClient,type Run} from './api/client.ts';

export function RunReviewPanel({client,run,canAccept,onReviewed}:{client:EngineClient;run:Run;canAccept:boolean;onReviewed:(run:Run)=>void}) {
  const [objective,setObjective]=useState(false);
  const [criteria,setCriteria]=useState<number[]>([]);
  const [rationale,setRationale]=useState('');
  const [busy,setBusy]=useState(false);
  const [error,setError]=useState('');
  async function decide(decision:'accept'|'reject') {
    setBusy(true);setError('');
    try {onReviewed(await client.reviewRun(run.id,{decision,expected_revision:run.revision,rationale,objective_confirmed:objective,confirmed_criteria:criteria}));}
    catch(failure) {setError(String(failure));}
    finally {setBusy(false);}
  }
  return <section aria-label="Final run review" className="task-review"><h2>Final review</h2>
    <p>Record your decision after inspecting the candidate and available evidence. Acceptance repeats saved file checks and closes this run.</p>
    {!canAccept&&<p>Complete all specialist reviews before accepting the run.</p>}
    <label><input type="checkbox" checked={objective} onChange={event=>setObjective(event.target.checked)}/>I reviewed the objective, result, and available evidence</label>
    {run.acceptance_criteria.map((item,index)=><label key={index}><input type="checkbox" checked={criteria.includes(index)} onChange={event=>setCriteria(items=>event.target.checked?[...items,index]:items.filter(value=>value!==index))}/>Confirm criterion: {item}</label>)}
    <label>Final review rationale<textarea value={rationale} maxLength={4000} onChange={event=>setRationale(event.target.value)}/></label>
    <div className="controls"><button disabled={busy||!canAccept||!objective||!rationale.trim()||criteria.length!==run.acceptance_criteria.length} onClick={()=>decide('accept')}>Accept and complete run</button><button disabled={busy||!rationale.trim()} onClick={()=>decide('reject')}>Reject final result</button></div>
    {error&&<p role="alert">{error}</p>}
  </section>;
}
