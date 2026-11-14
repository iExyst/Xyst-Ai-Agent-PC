import {useEffect,useState} from 'react';
import {EngineClient} from './api/client.ts';
import type {components} from './generated/api.js';

type RuntimeReadiness=components['schemas']['SkillNodeRuntimeReadinessResult'];

export function NodeRuntimeReadinessPanel({client,skillId,version,distribution}:{client:EngineClient;skillId:string;version:number;distribution?:string}){
  const [result,setResult]=useState<RuntimeReadiness>();
  const [busy,setBusy]=useState(false);
  const [error,setError]=useState('');
  useEffect(()=>{setResult(undefined);setError('');},[skillId,version,distribution]);
  async function check(){
    if(!distribution)return;
    setBusy(true);setError('');setResult(undefined);
    try{setResult(await client.skillNodeRuntimeReadiness(skillId,version,distribution));}
    catch(failure){setError(String(failure));}
    finally{setBusy(false);}
  }
  return <section aria-label="Restricted WSL Node.js runtime readiness">
    <h4>Node.js scripts in the selected WSL distribution</h4>
    <p>This checks Node.js and npm versions inside the restricted distribution. It does not run procedure scripts, resolve packages, or install dependencies.</p>
    <button disabled={busy||!distribution} onClick={()=>void check()}>{busy?'Checking…':'Check Node.js and npm'}</button>
    {!distribution&&<p>Run the sandbox check first to select a ready WSL distribution.</p>}
    {result&&<div role="status">
      <p>Check state: {result.state}{result.node_version?` · Node.js ${result.node_version}`:''}{result.npm_version?` · npm ${result.npm_version}`:''}</p>
      {result.state==='node_missing'&&<p>Node.js was not found in this distribution’s restricted PATH, so linked JavaScript scripts cannot run there.</p>}
      {result.state==='ready'&&!result.npm_available&&<p>Node.js scripts can run, but npm is unavailable. Package resolution and dependency installation are not supported yet.</p>}
      {result.state==='ready'&&result.npm_available&&<p>Runtime check passed. npm package resolution and dependency installation are not supported yet.</p>}
      {result.state==='unavailable'&&<p>The restricted runtime check did not complete. Review the selected distribution and sandbox readiness.</p>}
    </div>}
    {error&&<p role="alert">{error}</p>}
  </section>;
}
