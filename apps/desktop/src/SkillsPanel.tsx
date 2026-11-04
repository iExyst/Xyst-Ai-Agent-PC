import {useEffect,useState} from 'react';
import {EngineClient} from './api/client.ts';
import type {components} from './generated/api.js';

type Summary=components['schemas']['SkillSummary'];
export type ProcedureTrialSelection=components['schemas']['SkillTrial']&{name:string};
export function SkillsPanel({client,onTrial}:{client:EngineClient;onTrial:(selection:ProcedureTrialSelection)=>void}){
  const [items,setItems]=useState<Summary[]>([]);
  const [selected,setSelected]=useState<Summary>();
  const [version,setVersion]=useState(1);
  const [name,setName]=useState('');
  const [content,setContent]=useState('');
  const [tools,setTools]=useState('');
  const [read,setRead]=useState(false),[write,setWrite]=useState(false);
  const [evidence,setEvidence]=useState<components['schemas']['SkillEvidence']>();
  const [trials,setTrials]=useState<components['schemas']['SkillTrialRun'][]>([]);
  const [trialOne,setTrialOne]=useState(''),[trialTwo,setTrialTwo]=useState('');
  const [caseOne,setCaseOne]=useState(''),[caseTwo,setCaseTwo]=useState('');
  const [criteria,setCriteria]=useState(''),[rationale,setRationale]=useState('');
  const [confirmed,setConfirmed]=useState(false);
  const [busy,setBusy]=useState(false),[error,setError]=useState('');
  async function refresh(){const rows=await client.skills();if(!Array.isArray(rows))throw new Error('Procedure library unavailable.');setItems(rows);}
  useEffect(()=>{let active=true;void client.skills().then(rows=>{if(!Array.isArray(rows))throw new Error('Procedure library unavailable.');if(active)setItems(rows);}).catch(failure=>{if(active)setError(String(failure));});return()=>{active=false;};},[client]);
  function reset(){setEvidence(undefined);setTrials([]);setSelected(undefined);setVersion(1);setName('');setContent('');setTools('');setRead(false);setWrite(false);setError('');}
  async function inspect(item:Summary,revision=item.latest_version){
    setBusy(true);setError('');
    try{const data=await client.skillVersion(item.id,revision);setEvidence(data.evaluation_evidence);setTrials([]);setTrialOne('');setTrialTwo('');setConfirmed(false);setSelected(item);setVersion(revision);setName(item.name);setContent(data.content);setTools(data.required_tools.map(tool=>`${tool.name}@${tool.version}`).join('\n'));setRead(data.required_permissions.includes('workspace.read'));setWrite(data.required_permissions.includes('workspace.write'));}
    catch(failure){setError(String(failure));}finally{setBusy(false);}
  }
  async function save(){
    setBusy(true);setError('');
    try{
      const dependencies=tools.split('\n').filter(line=>line.trim()).map(line=>{const parts=line.trim().split('@');if(parts.length!==2)throw new Error('Each tool dependency needs name@version.');return {name:parts[0]!,version:parts[1]!};});
      const permissions:('workspace.read'|'workspace.write')[]=[];if(read)permissions.push('workspace.read');if(write)permissions.push('workspace.write');
      const saved=await client.saveSkill({name,content,required_tools:dependencies,required_permissions:permissions},selected?.id,selected?.latest_version);
      await refresh();await inspect(saved);
    }catch(failure){setError(String(failure));}finally{setBusy(false);}
  }
  async function loadTrials(){if(!selected)return;setBusy(true);setError('');try{const rows=await client.skillTrials(selected.id,version);if(!Array.isArray(rows))throw new Error('Trial list unavailable.');setTrials(rows);}catch(failure){setError(String(failure));}finally{setBusy(false);}}
  async function evaluate(){if(!selected||!evidence)return;setBusy(true);setError('');try{
    const checks=criteria.split('\n').map(text=>text.trim()).filter(Boolean);
    await client.evaluateSkill(selected.id,version,{expected_evaluation_count:evidence.evaluations.length,trials:[{run_id:trialOne,case_description:caseOne},{run_id:trialTwo,case_description:caseTwo}],criteria:checks,confirmed_criteria:confirmed?checks.map((_,index)=>index):[],rationale});
    const rows=await client.skills();setItems(rows);const current=rows.find(item=>item.id===selected.id);if(current)await inspect(current,version);
  }catch(failure){setError(String(failure));}finally{setBusy(false);}}
  async function remove(){if(!selected)return;setBusy(true);setError('');try{await client.deleteSkill(selected.id);reset();await refresh();}catch(failure){setError(String(failure));}finally{setBusy(false);}}
  async function exportVersion(){if(!selected)return;setBusy(true);try{const data=await client.skillVersion(selected.id,version);const url=URL.createObjectURL(new Blob([JSON.stringify({name:selected.name,...data},null,2)],{type:'application/json'}));const link=document.createElement('a');link.href=url;link.download=`procedure-${selected.id}-v${version}.json`;link.click();setTimeout(()=>URL.revokeObjectURL(url),0);}catch(failure){setError(String(failure));}finally{setBusy(false);}}
  return <section aria-label="Procedure candidates"><h2>Procedure candidates</h2>
    <p>Draft reusable procedures here. Candidates are unverified. Select a version explicitly for an evaluation trial; declared tools and permissions grant no authority.</p>
    <div className="controls"><button disabled={busy} onClick={reset}>New procedure</button>
    <button disabled={busy} onClick={()=>{setBusy(true);void refresh().then(()=>reset()).catch(failure=>setError(String(failure))).finally(()=>setBusy(false));}}>Refresh procedure list</button></div>
    <ul>{items.map(item=><li key={item.id}><button disabled={busy} onClick={()=>void inspect(item)}>Inspect {item.name}</button> · {item.lifecycle_state} · version {item.latest_version}</li>)}</ul>
    {selected&&<label>Procedure version<select aria-label="Procedure version" value={version} disabled={busy} onChange={event=>void inspect(selected,Number(event.target.value))}>{Array.from({length:selected.latest_version},(_,i)=><option key={i+1} value={i+1}>{i+1}</option>)}</select></label>}
    <form onSubmit={event=>{event.preventDefault();void save();}}>
      <label>Procedure name<input aria-label="Procedure name" value={name} maxLength={80} disabled={busy} onChange={event=>setName(event.target.value)}/></label>
      <label>Procedure text<textarea aria-label="Procedure text" value={content} maxLength={16384} disabled={busy} onChange={event=>setContent(event.target.value)}/></label>
      <label>Tool dependencies (one name@version per line)<textarea aria-label="Tool dependencies (one name@version per line)" value={tools} maxLength={5200} disabled={busy} onChange={event=>setTools(event.target.value)}/></label>
      <label className="procedure-permission"><input type="checkbox" checked={read} disabled={busy} onChange={event=>setRead(event.target.checked)}/>Requires reading workspace files</label>
      <label className="procedure-permission"><input type="checkbox" checked={write} disabled={busy} onChange={event=>setWrite(event.target.checked)}/>Requires editing workspace files</label>
      <button disabled={busy||!name.trim()||!content.trim()||!!selected&&version!==selected.latest_version}>{selected?'Save new procedure version':'Save procedure candidate'}</button>
    </form>
    {selected&&<div className="controls"><button disabled={busy} onClick={()=>onTrial({skill_id:selected.id,version,name:selected.name})}>Select version for trial run</button><button disabled={busy} onClick={()=>void exportVersion()}>Export procedure version</button><button disabled={busy} onClick={()=>void remove()}>Delete procedure and all versions</button></div>}
    {selected&&version!==selected.latest_version&&<p>Historical versions are preserved. Select the latest version to revise this procedure.</p>}
    {selected&&evidence&&<section aria-label="Procedure evaluation"><h3>Version evaluation</h3>
      <p>Version {version}: {evidence.state}. Saved evaluations: {evidence.evaluations.length}. Recording a review does not activate this procedure.</p>
      {evidence.evaluations.map(entry=><details key={entry.id}><summary>Evaluation {new Date(entry.evaluated_at).toLocaleString()}</summary><p>{entry.rationale}</p><ul>{entry.trials.map(trial=><li key={trial.run_id}>{trial.case_description}: {trial.objective}</li>)}</ul><ul>{entry.criteria.map((criterion,index)=><li key={index}>{criterion}</li>)}</ul></details>)}
      <button disabled={busy} onClick={()=>void loadTrials()}>Find completed procedure trials</button>
      {trials.length<2?<p>At least two completed, explicitly accepted trials of this exact version are required. Describe distinct representative cases when reviewing them.</p>:<form onSubmit={event=>{event.preventDefault();void evaluate();}}>
        <label>First completed trial<select aria-label="First completed trial" value={trialOne} disabled={busy} onChange={event=>{setTrialOne(event.target.value);setConfirmed(false);}}><option value="">Choose a completed trial</option>{trials.map(trial=><option key={trial.id} value={trial.id}>{trial.objective} ({new Date(trial.created_at).toLocaleString()})</option>)}</select></label>
        <label>First representative case<input aria-label="First representative case" value={caseOne} maxLength={500} disabled={busy} onChange={event=>{setCaseOne(event.target.value);setConfirmed(false);}}/></label>
        <label>Second completed trial<select aria-label="Second completed trial" value={trialTwo} disabled={busy} onChange={event=>{setTrialTwo(event.target.value);setConfirmed(false);}}><option value="">Choose a completed trial</option>{trials.map(trial=><option key={trial.id} value={trial.id}>{trial.objective} ({new Date(trial.created_at).toLocaleString()})</option>)}</select></label>
        <label>Second representative case<input aria-label="Second representative case" value={caseTwo} maxLength={500} disabled={busy} onChange={event=>{setCaseTwo(event.target.value);setConfirmed(false);}}/></label>
        <label>Evaluation criteria (one per line)<textarea aria-label="Evaluation criteria (one per line)" value={criteria} maxLength={10000} disabled={busy} onChange={event=>{setCriteria(event.target.value);setConfirmed(false);}}/></label>
        <label>Evaluation rationale<textarea aria-label="Evaluation rationale" value={rationale} maxLength={4000} disabled={busy} onChange={event=>setRationale(event.target.value)}/></label>
        <label className="procedure-permission"><input type="checkbox" checked={confirmed} disabled={busy} onChange={event=>setConfirmed(event.target.checked)}/>I reviewed both trials and confirmed every evaluation criterion</label>
        <button disabled={busy||!confirmed||!trialOne||!trialTwo||trialOne===trialTwo||!caseOne.trim()||!caseTwo.trim()||!criteria.trim()||!rationale.trim()}>Record procedure evaluation</button>
      </form>}
    </section>}
    {error&&<p role="alert">{error}</p>}
  </section>;
}
