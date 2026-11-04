import {useEffect,useRef,useState} from 'react';
import type {components} from './generated/api.js';
import type {EngineClient} from './api/client.ts';

type Inventory=components['schemas']['DistributionInventory'];
type Result=components['schemas']['ReadinessResult'];

export function ExecutionSetup({client,onReady}:{client:EngineClient;onReady:(result:Result|undefined)=>void}) {
  const [inventory,setInventory]=useState<Inventory>();
  const [selected,setSelected]=useState('');
  const [result,setResult]=useState<Result>();
  const [busy,setBusy]=useState(false);
  const [error,setError]=useState('');
  const [now,setNow]=useState(Date.now());
  const request=useRef<AbortController|null>(null);
  useEffect(()=>{setInventory(undefined);setSelected('');setResult(undefined);setBusy(false);setError('');return()=>request.current?.abort();},[client]);
  useEffect(()=>{const timer=setInterval(()=>setNow(Date.now()),1000);return()=>clearInterval(timer);},[]);
  const expired=!!result&&now>=Date.parse(result.expires_at);
  useEffect(()=>{onReady(result?.state==='ready'&&!expired?result:undefined);},[result,expired,onReady]);
  async function load() {
    request.current?.abort();const controller=new AbortController();request.current=controller;
    setBusy(true);setError('');setResult(undefined);setSelected('');
    try {const next=await client.executionDistributions(controller.signal);if(!controller.signal.aborted)setInventory(next);}
    catch(failure){if(!controller.signal.aborted)setError(String(failure));}
    finally{if(!controller.signal.aborted)setBusy(false);}
  }
  async function check() {
    if(!selected)return;
    request.current?.abort();const controller=new AbortController();request.current=controller;
    setBusy(true);setError('');setResult(undefined);
    try {const next=await client.executionReadiness(selected,controller.signal);if(!controller.signal.aborted){setResult(next);setNow(Date.now());}}
    catch(failure){if(!controller.signal.aborted)setError(String(failure));}
    finally{if(!controller.signal.aborted)setBusy(false);}
  }
  return <section aria-label="Command sandbox setup"><details><summary>Command sandbox setup</summary>
    <p>Choose an installed WSL distribution and check its isolation controls. This uses a private self-test, not your project files, and does not enable command execution.</p>
    <button onClick={()=>void load()} disabled={busy}>Find installed distributions</button>
    {inventory?.state==='unsupported'&&<p>Command sandboxes currently require Windows with WSL.</p>}
    {inventory?.state==='unavailable'&&<p role="status">Installed distributions could not be listed. Check WSL and try again.</p>}
    {inventory?.state==='available'&&inventory.distributions.length===0&&<p>No installed distributions found.</p>}
    {!!inventory?.distributions.length&&<label>Execution distribution <select value={selected} disabled={busy} onChange={event=>{setSelected(event.target.value);setResult(undefined);}}>
      <option value="">Select a distribution</option>{inventory.distributions.map(item=><option key={item.name} value={item.name} disabled={!item.supported_by_profile}>{item.name}{item.supported_by_profile?'':' (not supported by this profile)'}</option>)}
    </select></label>}
    <button disabled={busy||!selected} onClick={()=>void check()}>{busy?'Working…':'Check sandbox'}</button>
    {result&&<div role="status">
      <p>{expired?'Check expired. Check again to refresh status.':result.state==='ready'?'Sandbox check passed.':result.state==='busy'?'Another sandbox check is running. Try again shortly.':result.state==='unsupported'?'This backend is not supported on this platform.':'Sandbox controls could not be verified. The selected distribution needs Python, Bubblewrap, systemd and libseccomp.'}</p>
      {result.state==='ready'&&result.policy&&<p>Network denied · Project changes stay in a private copy{result.policy.max_seconds!==undefined&&<> · Up to {result.policy.max_seconds} seconds per command</>}{result.policy.memory_bytes!==undefined&&<> · {result.policy.memory_bytes/1048576} MiB per command</>}{result.policy.max_active_executions_per_distribution!==undefined&&<> · {result.policy.max_active_executions_per_distribution} shared slots in this distribution</>}.</p>}
      <p>Checked {new Date(result.checked_at).toLocaleTimeString()} · Valid until {new Date(result.expires_at).toLocaleTimeString()}</p>
    </div>}
    {error&&<p role="alert">{error}</p>}
  </details></section>;
}
