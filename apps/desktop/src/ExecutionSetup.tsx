import {useEffect,useRef,useState} from 'react';
import type {components} from './generated/api.js';
import type {EngineClient} from './api/client.ts';

type Inventory=components['schemas']['DistributionInventory'];
type Result=components['schemas']['ReadinessResult'];
type Backend='wsl-bubblewrap-v1'|'windows-appcontainer-v1'|'docker-local-v1'|'ssh-wsl-bubblewrap-v1';

export function ExecutionSetup({client,onReady}:{client:EngineClient;onReady:(result:Result|undefined)=>void}) {
  const [inventory,setInventory]=useState<Inventory>();
  const [selected,setSelected]=useState('');
  const [imageReference,setImageReference]=useState('');
  const [sshIdentityFile,setSshIdentityFile]=useState('');
  const [backend,setBackend]=useState<Backend>('wsl-bubblewrap-v1');
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
    if(!selected||(backend==='docker-local-v1'&&!imageReference.trim())||(backend==='ssh-wsl-bubblewrap-v1'&&!sshIdentityFile.trim()))return;
    request.current?.abort();const controller=new AbortController();request.current=controller;
    setBusy(true);setError('');setResult(undefined);
    try {const next=await client.executionReadiness(selected,controller.signal,backend,backend==='docker-local-v1'?imageReference.trim():undefined,backend==='ssh-wsl-bubblewrap-v1'?sshIdentityFile.trim():undefined);if(!controller.signal.aborted){setResult(next);setNow(Date.now());}}
    catch(failure){if(!controller.signal.aborted)setError(String(failure));}
    finally{if(!controller.signal.aborted)setBusy(false);}
  }
  return <section aria-label="Command sandbox setup">
    <p>Choose where commands will run and check its isolation controls. The check uses a private fixture, not your project files, and does not enable command execution.</p>
    <label>Execution backend <select value={backend} disabled={busy} onChange={event=>{const value=event.target.value as Backend;setBackend(value);setSelected(value==='windows-appcontainer-v1'?'Windows-Local':value==='docker-local-v1'?'Docker-local':'');setInventory(undefined);setResult(undefined);}}>
      <option value="wsl-bubblewrap-v1">WSL isolated Linux environment</option>
      <option value="windows-appcontainer-v1">Windows AppContainer (no network)</option>
      <option value="docker-local-v1">Local Docker Engine (Linux containers)</option>
      <option value="ssh-wsl-bubblewrap-v1">Remote SSH sandbox (Bubblewrap)</option>
    </select></label>
    {backend==='wsl-bubblewrap-v1'&&<button onClick={()=>void load()} disabled={busy}>Find installed distributions</button>}
    {backend==='wsl-bubblewrap-v1'&&inventory?.state==='unsupported'&&<p>WSL command sandboxes require Windows with WSL.</p>}
    {backend==='wsl-bubblewrap-v1'&&inventory?.state==='unavailable'&&<p role="status">Installed distributions could not be listed. Check WSL and try again.</p>}
    {backend==='wsl-bubblewrap-v1'&&inventory?.state==='available'&&inventory.distributions.length===0&&<p>No installed distributions found.</p>}
    {backend==='wsl-bubblewrap-v1'&&!!inventory?.distributions.length&&<label>Execution distribution <select value={selected} disabled={busy} onChange={event=>{setSelected(event.target.value);setResult(undefined);}}>
      <option value="">Select a distribution</option>{inventory.distributions.map(item=><option key={item.name} value={item.name} disabled={!item.supported_by_profile}>{item.name}{item.supported_by_profile?'':' (not supported by this profile)'}</option>)}
    </select></label>}
    {backend==='windows-appcontainer-v1'&&<p>Commands run in a no-network AppContainer against a private copy of the selected workspace. The original project is never mounted writable.</p>}
    {backend==='docker-local-v1'&&<><p>Docker must already be installed and running with Linux containers enabled. X.A.A. never pulls an image. Use a locally available image pinned by its repository SHA-256 digest; commands run in a disposable private container.</p><label>Local image digest <input aria-label="Local Docker image digest" value={imageReference} disabled={busy} onChange={event=>{setImageReference(event.target.value);setResult(undefined);}} placeholder="registry.example/image@sha256:…" /></label></>}
    {backend==='ssh-wsl-bubblewrap-v1'&&<><p>Use an alias from your Windows OpenSSH <code>.ssh/config</code> and a private-key path under your <code>.ssh</code> folder. The host key must already be pinned in <code>.ssh/known_hosts</code>. The remote account must force the X.A.A. sandbox manager with SSH forwarding disabled. X.A.A. sends only its bounded private workspace snapshot.</p><label>SSH host alias <input aria-label="SSH host alias" value={selected} disabled={busy} onChange={event=>{setSelected(event.target.value);setResult(undefined);}} placeholder="xaa-builder" /></label><label>SSH private-key path <input aria-label="SSH private-key path" value={sshIdentityFile} disabled={busy} onChange={event=>{setSshIdentityFile(event.target.value);setResult(undefined);}} placeholder="C:\\Users\\you\\.ssh\\id_ed25519" /></label></>}
    <button disabled={busy||!selected||(backend==='docker-local-v1'&&!imageReference.trim())||(backend==='ssh-wsl-bubblewrap-v1'&&!sshIdentityFile.trim())} onClick={()=>void check()}>{busy?'Working…':'Check sandbox'}</button>
    {result&&<div role="status">
      <p>{expired?'Check expired. Check again to refresh status.':result.state==='ready'?'Sandbox check passed.':result.state==='busy'?'Another sandbox check is running. Try again shortly.':result.state==='unsupported'?'This backend is not supported on this platform.':result.backend==='docker-local-v1'?'Docker is unavailable or the selected image failed an isolation check. Start the local Docker Engine with Linux containers, verify the digest exists locally, then retry.':result.backend==='windows-appcontainer-v1'?'Windows AppContainer controls could not be verified. Check Windows security settings and try again.':result.backend==='ssh-wsl-bubblewrap-v1'?'SSH sandbox check failed. Verify the host alias, pinned host key, selected private key, remote forced-command manager, and remote Bubblewrap/systemd profile.':'Sandbox controls could not be verified. The selected distribution needs Python, Bubblewrap, systemd and libseccomp.'}</p>
      {result.state==='ready'&&result.policy&&<p>Network denied · Project changes stay in a private copy{result.policy.max_seconds!==undefined&&<> · Up to {result.policy.max_seconds} seconds per command</>}{result.policy.memory_bytes!==undefined&&<> · {result.policy.memory_bytes/1048576} MiB per command</>}{result.policy.max_active_executions_per_distribution!==undefined&&<> · {result.policy.max_active_executions_per_distribution} shared slots in this distribution</>}.</p>}
      <p>Checked {new Date(result.checked_at).toLocaleTimeString()} · Valid until {new Date(result.expires_at).toLocaleTimeString()}</p>
    </div>}
    {error&&<p role="alert">{error}</p>}
  </section>;
}
