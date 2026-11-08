import {useEffect,useState} from 'react';
import {EngineClient,type ProjectValidation,type Run} from './api/client.ts';

export function RunReviewPanel({client,run,canAccept,onReviewed}:{client:EngineClient;run:Run;canAccept:boolean;onReviewed:(run:Run)=>void}) {
  const [objective,setObjective]=useState(false);
  const [criteria,setCriteria]=useState<number[]>([]);
  const [rationale,setRationale]=useState('');
  const [busy,setBusy]=useState(false);
  const [error,setError]=useState('');
  const [validation,setValidation]=useState<ProjectValidation>();
  const [argv,setArgv]=useState('["/usr/bin/python3", "-I", "-m", "pytest", "-q"]');
  const [selected,setSelected]=useState<string[]>([]);
  const [output,setOutput]=useState<Record<string,string>>({});
  const [validationBusy,setValidationBusy]=useState(true);
  async function refreshValidation(){setValidationBusy(true);setError('');try{setValidation(await client.projectValidation(run.id));}catch(failure){setError(String(failure));}finally{setValidationBusy(false);}}
  useEffect(()=>{void refreshValidation();},[client,run.id]);
  async function validate(){setValidationBusy(true);setError('');try{const parsed:unknown=JSON.parse(argv);if(!Array.isArray(parsed)||parsed.some(item=>typeof item!=='string'))throw new Error('Enter a JSON array of command argument strings.');setValidation(await client.runProjectValidation(run.id,{request_id:crypto.randomUUID(),expected_revision:run.revision,argv:parsed as string[],max_seconds:15}));setSelected([]);}catch(failure){setError(String(failure));}finally{setValidationBusy(false);}}
  const validationMissing=!!validation?.required&&!selected.some(id=>validation.evidence.some(item=>item.call_id===id&&item.succeeded&&item.matches_current_project));
  async function decide(decision:'accept'|'reject') {
    setBusy(true);setError('');
    try {onReviewed(await client.reviewRun(run.id,{decision,expected_revision:run.revision,rationale,objective_confirmed:objective,confirmed_criteria:criteria,...(validation?.required?{validation_call_ids:selected}:{})}));}
    catch(failure) {setError(String(failure));}
    finally {setBusy(false);}
  }
  return <section aria-label="Final run review" className="task-review"><h2>Final review</h2>
    <p>Record your decision after inspecting the candidate and available evidence. Acceptance repeats saved file checks and closes this run.</p>
    {!canAccept&&<p>Complete all specialist reviews before accepting the run.</p>}
    {validation?.available&&<fieldset><legend>Integrated project validation</legend><p>Commands run against a captured copy of the current integrated project inside the saved restricted environment. Changes made by the command are discarded. Network access is disabled.</p><p>{validation.required?'Required for this edited project.':'Optional for this project.'} Current project SHA-256: {validation.current_snapshot_sha256??'unavailable'}</p><button disabled={validationBusy} onClick={()=>void refreshValidation()}>Refresh current project</button>{(validation.suggestions??[]).length>0&&<div><h3>Suggested checks from project files</h3><p>These commands are inferred from project manifests. Inspect the command and confirm its tools and dependencies are available before running.</p>{(validation.suggestions??[]).map((suggestion,index)=><button key={`${suggestion.label}-${index}`} type="button" disabled={validationBusy} onClick={()=>setArgv(JSON.stringify(suggestion.argv))}>{suggestion.label}: {suggestion.rationale}</button>)}</div>}<label>Command arguments as a JSON string array<textarea value={argv} maxLength={16384} onChange={event=>setArgv(event.target.value)}/></label><button disabled={validationBusy||run.state!=='verifying'||(validation.unresolved_call_ids??[]).length>0} onClick={()=>void validate()}>Run check (15 second limit)</button>{(validation.unresolved_call_ids??[]).map(callId=><div key={callId}><p>Validation {callId} has an uncertain result. Reconcile the saved execution before running another check.</p><button disabled={validationBusy} onClick={()=>{setValidationBusy(true);setError('');void client.reconcileProjectValidation(run.id,callId).then(setValidation).catch(failure=>setError(String(failure))).finally(()=>setValidationBusy(false));}}>Reconcile saved validation {callId}</button></div>)}{validation.evidence.map(item=><div key={item.call_id}><pre>{JSON.stringify(item.argv)}</pre><p>Exit: {item.exit_code??'unavailable'} · {item.termination_reason} · {item.matches_current_project?'Matches current integrated project':'Stale: integrated project changed after this command'}</p><p>Snapshot SHA-256: {item.snapshot_sha256}</p><pre>{item.stdout_excerpt}{item.stderr_excerpt?'\n'+item.stderr_excerpt:''}</pre>{[item.stdout_artifact_id,item.stderr_artifact_id].map((id,index)=><div key={id}><button onClick={()=>{void client.artifactPreview(id).then(text=>setOutput(previous=>({...previous,[id]:text}))).catch(failure=>setError(String(failure)));}}>Inspect saved {index===0?'stdout':'stderr'}</button>{output[id]!==undefined&&<pre>{output[id]}</pre>}</div>)}<label><input type="checkbox" disabled={!item.succeeded||!item.matches_current_project} checked={selected.includes(item.call_id)} onChange={event=>setSelected(previous=>event.target.checked?[...previous,item.call_id]:previous.filter(value=>value!==item.call_id))}/>Reviewed successful current validation {item.call_id}</label></div>)}</fieldset>}
    <label><input type="checkbox" checked={objective} onChange={event=>setObjective(event.target.checked)}/>I reviewed the objective, result, and available evidence</label>
    {run.acceptance_criteria.map((item,index)=><label key={index}><input type="checkbox" checked={criteria.includes(index)} onChange={event=>setCriteria(items=>event.target.checked?[...items,index]:items.filter(value=>value!==index))}/>Confirm criterion: {item}</label>)}
    <label>Final review rationale<textarea value={rationale} maxLength={4000} onChange={event=>setRationale(event.target.value)}/></label>
    <div className="controls"><button disabled={busy||validationBusy||validation===undefined||!canAccept||validationMissing||!objective||!rationale.trim()||criteria.length!==run.acceptance_criteria.length} onClick={()=>decide('accept')}>Accept and complete run</button><button disabled={busy||!rationale.trim()} onClick={()=>decide('reject')}>Reject final result</button></div>
    {error&&<p role="alert">{error}</p>}
  </section>;
}
