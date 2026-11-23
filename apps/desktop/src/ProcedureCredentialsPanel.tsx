import {useEffect,useState} from 'react';
import type {EngineClient} from './api/client.ts';
import type {components} from './generated/api.js';

type Status=components['schemas']['SkillCredentialStatus'];

export function ProcedureCredentialsPanel({client,skillId,version}:{client:EngineClient;skillId:string;version:number}){
  const [status,setStatus]=useState<Status>();
  const [values,setValues]=useState<Record<string,string>>({});
  const [busy,setBusy]=useState(false);
  const [error,setError]=useState('');

  useEffect(()=>{
    let active=true;
    setStatus(undefined);setValues({});setError('');
    void client.skillCredentialStatus(skillId,version)
      .then(result=>{if(active)setStatus(result);})
      .catch(failure=>{if(active)setError(String(failure));});
    return()=>{active=false;};
  },[client,skillId,version]);

  async function save(envName:string){
    const value=values[envName]??'';
    if(!value)return;
    setBusy(true);setError('');
    try{
      setStatus(await client.setSkillCredential(skillId,version,envName,value));
      setValues(current=>({...current,[envName]:''}));
    }catch(failure){setError(String(failure));}
    finally{setBusy(false);}
  }

  async function clear(envName:string){
    setBusy(true);setError('');
    try{setStatus(await client.clearSkillCredential(skillId,version,envName));}
    catch(failure){setError(String(failure));}
    finally{setBusy(false);}
  }

  if(status?.credentials.length===0)return null;

  return <section aria-label="Procedure credential readiness">
    <h4>Procedure credentials</h4>
    <p>Credential values are stored in Windows Credential Manager and never returned by the API. Saving a value records availability only; procedures do not receive these credentials.</p>
    {!status&&<p role="status">Loading declared credential names…</p>}
    {status&&!status.credential_store_available&&<p role="status">Windows Credential Manager is unavailable.</p>}
    {status?.credentials.map(item=><div className="procedure-credential-row" key={item.env_name}>
      <label>{item.env_name} {item.required?'(required)':'(optional)'}
        <input type="password" autoComplete="new-password" maxLength={4096} value={values[item.env_name]??''} disabled={busy||!status.credential_store_available} onChange={event=>setValues(current=>({...current,[item.env_name]:event.target.value}))}/>
      </label>
      <p>{item.configured?'Saved in Windows Credential Manager.':'Not configured.'}</p>
      <div className="controls">
        <button type="button" disabled={busy||!status.credential_store_available||!values[item.env_name]} onClick={()=>void save(item.env_name)}>Save securely</button>
        <button type="button" disabled={busy||!status.credential_store_available||!item.configured} onClick={()=>void clear(item.env_name)}>Clear saved credential</button>
      </div>
    </div>)}
    {error&&<p role="alert">{error}</p>}
  </section>;
}
