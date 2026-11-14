import {useEffect,useState} from 'react';
import type {EngineClient} from './api/client.ts';
import type {components} from './generated/api.js';

type LockRecord=components['schemas']['NpmDependencyLockRecord'];

export function NpmDependencyPanel({client,skillId,version,distribution,hasNodeScripts,hasNpmDependencies}:{client:EngineClient;skillId:string;version:number;distribution?:string;hasNodeScripts:boolean;hasNpmDependencies:boolean}){
  const [locks,setLocks]=useState<LockRecord[]>([]);
  const [busy,setBusy]=useState(false);
  const [error,setError]=useState('');
  const [notice,setNotice]=useState('');
  useEffect(()=>{
    let active=true;
    void client.skillNpmDependencyLocks(skillId,version).then(result=>{
      if(!Array.isArray(result.locks))throw new Error('npm dependency lock list is invalid.');
      if(active)setLocks(result.locks);
    }).catch(failure=>{if(active)setError(String(failure));});
    return()=>{active=false;};
  },[client,skillId,version]);
  async function prepare(){
    if(!distribution)return;
    setBusy(true);setError('');setNotice('');
    try{
      const proposal=await client.inspectSkillNpmDependencyLock(skillId,version,distribution);
      const record=await client.saveSkillNpmDependencyLock(skillId,version,proposal);
      setLocks(current=>[record,...current.filter(item=>item.environment_digest!==record.environment_digest)]);
      setNotice('The exact package set is saved as a pending proposal. It has not been approved or installed.');
    }catch(failure){setError(String(failure));}finally{setBusy(false);}
  }
  async function acquire(lock:LockRecord){
    setBusy(true);setError('');setNotice('');
    try{
      const updated=await client.acquireSkillNpmDependencyTarballs(skillId,version,lock.environment_digest);
      setLocks(current=>current.map(item=>item.environment_digest===updated.environment_digest?updated:item));
      setNotice('All locked tarballs were fetched, verified, and stored for this exact proposal.');
    }catch(failure){setError(String(failure));}finally{setBusy(false);}
  }
  async function review(lock:LockRecord,decision:'approved'|'rejected'){
    setBusy(true);setError('');setNotice('');
    try{
      const updated=await client.reviewSkillNpmDependencyLock(skillId,version,lock.environment_digest,{decision});
      setLocks(current=>current.map(item=>item.environment_digest===updated.environment_digest?updated:item));
    }catch(failure){setError(String(failure));}finally{setBusy(false);}
  }
  if(!hasNodeScripts||!hasNpmDependencies)return null;
  return <section aria-label="npm dependency lock review">
    <h4>Node.js package dependencies</h4>
    <p>Inspection checks linked package.json and package-lock.json against this procedure and the selected WSL Node/npm runtime. Acquisition fetches only exact lockfile tarballs through the network-denied worker. Package scripts are recorded for review and never run during acquisition.</p>
    <button disabled={busy||!distribution} onClick={()=>void prepare()}>{busy?'Inspecting…':'Inspect and prepare npm lock'}</button>
    {!distribution&&<p>Run the sandbox check first to select a ready WSL distribution.</p>}
    {error&&<p role="alert">{error}</p>}
    {notice&&<p role="status">{notice}</p>}
    <h5>Saved npm proposals</h5>
    {locks.length===0?<p>No npm dependency lock has been prepared for this procedure version.</p>:locks.map(lock=>{
      const reports=new Map(lock.verified_tarballs.map(report=>[report.package_path,report]));
      return <details key={lock.environment_digest}>
        <summary>{lock.lock.distribution} · Node {lock.lock.node_version} · {lock.decision}</summary>
        <p>Source SHA-256: <code>{lock.source_sha256}</code></p>
        <p>Proposal digest: <code>{lock.environment_digest}</code></p>
        <h6>Root requirements</h6>
        <ul>{lock.lock.root_dependencies.map(item=><li key={`${item.kind}:${item.name}`}><code>{item.name}@{item.version_range}</code> · {item.kind}</li>)}</ul>
        <h6>Complete locked package set ({lock.lock.packages.length})</h6>
        <ul>{lock.lock.packages.map(item=>{
          const report=reports.get(item.package_path);
          return <li key={item.package_path}>
            <strong>{item.name} {item.version}</strong> · <code>{item.package_path}</code><br/>
            Tarball: <code>{item.resolved_url}</code><br/>
            Integrity: <code>{item.integrity}</code>
            {report&&<><br/>Verified archive: {report.archive_size_bytes.toLocaleString()} bytes · SHA-256 <code>{report.archive_sha256}</code><br/>Lifecycle scripts: {report.install_lifecycle_scripts.join(', ')||'none detected'}</>}
          </li>;
        })}</ul>
        {lock.decision==='pending_review'&&!lock.tarballs_present&&<>
          <p role="status">The package set is saved, but exact archive bytes are not yet available for approval.</p>
          <button disabled={busy} onClick={()=>void acquire(lock)}>{busy?'Fetching and verifying…':'Fetch and verify exact tarballs'}</button>
        </>}
        {lock.tarballs_present&&<p role="status">Stored tarballs are reverified against the lock when loaded.</p>}
        {lock.decision==='pending_review'&&<div className="controls">
          <button disabled={busy||!lock.tarballs_present} onClick={()=>void review(lock,'approved')}>Approve this npm lock</button>
          <button disabled={busy} onClick={()=>void review(lock,'rejected')}>Reject this npm lock</button>
        </div>}
        {lock.reviewed_at&&<p>Reviewed {new Date(lock.reviewed_at).toLocaleString()}: {lock.decision}.</p>}
      </details>;
    })}
  </section>;
}
