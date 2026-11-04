import {useState} from 'react';
import {EngineClient, type Candidate, type Plan} from './api/client.ts';
import type {components} from './generated/api.js';

type Preview=components['schemas']['IntegrationPreview'];
type Choice='current'|'proposed';

export function TaskReviewPanel({client,runId,taskId,onReviewed}:{client:EngineClient;runId:string;taskId:string;onReviewed:(plan:Plan)=>void}) {
  const [candidate,setCandidate]=useState<Candidate>();
  const [criteria,setCriteria]=useState<number[]>([]);
  const [evidence,setEvidence]=useState<string[]>([]);
  const [rationale,setRationale]=useState('');
  const [preview,setPreview]=useState<Preview>();
  const [choices,setChoices]=useState<Record<string,Choice>>({});
  const [changesConfirmed,setChangesConfirmed]=useState(false);
  const [error,setError]=useState('');
  const [busy,setBusy]=useState(false);
  const hasChanges=!!candidate?.workspace_changes?.length;
  const unresolved=preview?.changes.some(change=>change.conflict&&!choices[change.path]);
  async function load() {
    setBusy(true);setError('');
    try {setCandidate(await client.candidate(runId,taskId));setPreview(undefined);setChoices({});setChangesConfirmed(false);}
    catch(failure) {setError(String(failure));}
    finally {setBusy(false);}
  }
  async function inspectIntegration() {
    setBusy(true);setError('');setChangesConfirmed(false);setChoices({});
    try {setPreview(await client.integrationPreview(runId,taskId));}
    catch {setPreview(undefined);setError('The project could not be checked for integration. Wait until the run is awaiting review, then refresh the preview.');}
    finally {setBusy(false);}
  }
  async function decide(decision:'accept'|'reject') {
    setBusy(true);setError('');
    const resolutions=preview?.changes.flatMap(change=>{const choice=choices[change.path];return change.conflict&&choice?[{path:change.path,expected_current_sha256:change.current_sha256,choice}]:[];})??[];
    try {onReviewed(await client.reviewCandidate(runId,taskId,{decision,rationale,confirmed_criteria:criteria,evidence_call_ids:evidence,changes_confirmed:changesConfirmed,...(hasChanges?{resolutions}:{})}));}
    catch(failure) {setError(hasChanges?'Integration was refused. The project may have changed or an earlier write may need reconciliation. Refresh the preview and inspect the saved evidence before retrying.':String(failure));setChangesConfirmed(false);}
    finally {setBusy(false);}
  }
  return <div className="task-review">
    {!candidate ? <button onClick={load} disabled={busy}>Review candidate</button> : <>
      <h4>Candidate result</h4><pre>{candidate.candidate ?? 'No candidate text recorded.'}</pre>
      {hasChanges&&<section aria-label="Private specialist changes"><h4>Proposed changes in the private copy</h4><p>The project has not received these changes. Review the complete differences, then check the current project before integration.</p>
        {candidate.workspace_changes?.map(change=><div key={change.path}><p>{change.path}</p><pre>{change.diff}{change.diff_truncated?'\n[Diff excerpt limited]':''}</pre></div>)}
        <button disabled={busy} onClick={inspectIntegration}>Check current project for integration</button>
        {preview&&<><p>Current project checked. Files are checked again before applying your choices.</p>{preview.changes.filter(change=>change.conflict).map(change=><div key={change.path}><h4>Conflict: {change.path}</h4><p>This file differs from the task's starting snapshot. Choose the version to retain.</p><pre>{change.current_sha256===null?'[File is missing from the project]':change.current_excerpt}</pre><label>Resolve {change.path} for {taskId}<select value={choices[change.path]??''} onChange={event=>{const choice=event.target.value as Choice;setChoices(previous=>({...previous,[change.path]:choice}));setChangesConfirmed(false);}}><option value="">Choose a version</option><option value="current" disabled={change.current_sha256===null}>Keep current project version</option><option value="proposed">Use proposed specialist version</option></select></label></div>)}
          <label><input type="checkbox" checked={changesConfirmed} disabled={!!unresolved} onChange={event=>setChangesConfirmed(event.target.checked)}/>I reviewed the file changes and authorize these integration choices</label></>}
      </section>}
      <p>Inspect the file evidence and confirm each criterion before accepting. Files are checked again when you accept.</p>
      <fieldset><legend>Criteria reviewed</legend>{candidate.acceptance_criteria.map((item,index)=><label key={index}><input type="checkbox" checked={criteria.includes(index)} onChange={event=>setCriteria(items=>event.target.checked?[...items,index]:items.filter(value=>value!==index))}/>{item}</label>)}</fieldset>
      <fieldset><legend>File evidence reviewed</legend>{candidate.evidence.length===0?<p>No complete file evidence was recorded. Acceptance is unavailable.</p>:candidate.evidence.map(item=><div key={item.call_id}><label><input type="checkbox" checked={evidence.includes(item.call_id)} onChange={event=>setEvidence(items=>event.target.checked?[...items,item.call_id]:items.filter(value=>value!==item.call_id))}/>Reviewed {item.path}</label><pre>{item.excerpt}{item.excerpt_truncated?'\n[Excerpt limited to 4096 characters]':''}</pre><p>SHA-256: {item.sha256}</p></div>)}</fieldset>
      <label>Review rationale<textarea value={rationale} maxLength={4000} onChange={event=>setRationale(event.target.value)}/></label>
      <div className="controls"><button disabled={busy||(hasChanges&&(!preview||!changesConfirmed||!!unresolved))||!rationale.trim()||criteria.length!==candidate.acceptance_criteria.length||criteria.length===0||evidence.length===0} onClick={()=>decide('accept')}>{hasChanges?'Integrate changes and accept task':'Accept task result'}</button><button disabled={busy||!rationale.trim()} onClick={()=>decide('reject')}>Reject task result</button></div>
    </>}{error&&<p role="alert">{error}</p>}
  </div>;
}
