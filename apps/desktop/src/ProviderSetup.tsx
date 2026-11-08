import {useEffect, useState} from 'react';
import type {EngineClient} from './api/client.ts';
import type {components} from './generated/api.js';

type ModelInfo = components['schemas']['ModelInfo'];

type Props = {
  client: EngineClient;
  models: string[];
  model: string;
  onModelsChanged: (models: string[]) => void;
  onModelChanged: (model: string) => void;
  onDiagnosticPassed: (passed: boolean) => void;
};

export function ProviderSetup({client,models,model,onModelsChanged,onModelChanged,onDiagnosticPassed}:Props) {
  const [endpoint,setEndpoint]=useState('http://127.0.0.1:1234');
  const [status,setStatus]=useState('Checking the local model server…');
  const [busy,setBusy]=useState(false);
  const [error,setError]=useState('');
  const [diagnostic,setDiagnostic]=useState<'passed'|'failed'|'unknown'>('unknown');
  const [credential,setCredential]=useState('');
  const [credentialConfigured,setCredentialConfigured]=useState(false);
  const [modelDetails,setModelDetails]=useState<ModelInfo[]>([]);
  const [compatibleEndpoint,setCompatibleEndpoint]=useState('');
  const [compatibleCredential,setCompatibleCredential]=useState('');
  const [compatibleCredentialConfigured,setCompatibleCredentialConfigured]=useState(false);
  const selectedDetails=modelDetails.find(item=>item.id===model);

  useEffect(()=>{
    let live=true;
    void (async()=>{
      try {
        const config=await client.lmStudioConfig();
        if(!live)return;
        if(typeof config.endpoint==='string'&&config.endpoint.trim())setEndpoint(config.endpoint);
        const compatibleConfig=await client.compatibleProviderConfig();
        if(!live)return;
        if(compatibleConfig.endpoint)setCompatibleEndpoint(compatibleConfig.endpoint);
        const compatibleCredentialStatus=await client.compatibleProviderCredentialStatus();
        if(live)setCompatibleCredentialConfigured(compatibleCredentialStatus.configured);
        const credentialStatus=await client.lmStudioCredentialStatus();
        if(live)setCredentialConfigured(credentialStatus.configured);
        const provider=await client.providers();
        if(!live)return;
        const current=provider.find(item=>item.id==='lm-studio');
        if(current?.status==='connected')setStatus('LM Studio is connected.');
        else setStatus(current?.error??'LM Studio is unavailable. Start its Local Server and retry.');
        const available=await client.models();
        if(live) {
          setModelDetails(available);
          const ids=available.map(item=>item.id);
          onModelsChanged(ids);
          const saved=compatibleConfig.selected_model??config.selected_model;
          if(typeof saved==='string'&&ids.includes(saved))onModelChanged(saved);
          else if(config.selected_model)await client.selectLMStudioModel(null);
          else if(compatibleConfig.selected_model)await client.selectCompatibleProviderModel(null);
        }
      } catch {
        if(live)setStatus('Could not reach LM Studio. Start its Local Server in LM Studio, then retry.');
      }
    })();
    return()=>{live=false;};
  },[client,onModelChanged,onModelsChanged]);

  async function refresh() {
    setBusy(true);setError('');
    try {
      const provider=await client.providers();
      const current=provider.find(item=>item.id==='lm-studio');
      if(current?.status!=='connected')throw new Error(current?.error??'LM Studio is unavailable.');
      const available=await client.models();
      setModelDetails(available);
      onModelsChanged(available.map(item=>item.id));
      setStatus(`Connected. Found ${available.length} model${available.length===1?'':'s'}.`);
    } catch (failure) {
      setStatus('LM Studio is unavailable.');
      setError(failure instanceof Error?failure.message:'Could not discover local models.');
    } finally {setBusy(false);}
  }

  async function saveCredential() {
    if(!credential)return;
    setBusy(true);setError('');setDiagnostic('unknown');
    try {
      const result=await client.setLMStudioCredential(credential);
      setCredential('');setCredentialConfigured(result.configured);
      onDiagnosticPassed(false);
      setStatus('API token saved in Windows Credential Manager. Run the tool-use diagnostic again.');
    } catch(failure) {
      setError(failure instanceof Error?failure.message:'Could not save the LM Studio API token.');
    } finally {setBusy(false);}
  }

  async function clearCredential() {
    setBusy(true);setError('');setDiagnostic('unknown');
    try {
      const result=await client.clearLMStudioCredential();
      setCredentialConfigured(result.configured);
      onDiagnosticPassed(false);
      setStatus('Saved API token cleared. Run the tool-use diagnostic again.');
    } catch(failure) {
      setError(failure instanceof Error?failure.message:'Could not clear the LM Studio API token.');
    } finally {setBusy(false);}
  }

  async function saveEndpoint() {
    setBusy(true);setError('');setDiagnostic('unknown');
    try {
      const saved=await client.setLMStudioEndpoint(endpoint);
      setEndpoint(saved.endpoint);
      onDiagnosticPassed(false);
      onModelChanged('');
      onModelsChanged([]);
      setStatus('Address saved. Checking the selected LM Studio server…');
      const provider=await client.providers();
      const current=provider.find(item=>item.id==='lm-studio');
      if(current?.status!=='connected')throw new Error(current?.error??'Server not reachable at this address.');
      const available=await client.models();
      setModelDetails(available);
      onModelsChanged(available.map(item=>item.id));
      setStatus(`Connected. Found ${available.length} model${available.length===1?'':'s'}.`);
    } catch (failure) {
      setError(failure instanceof Error?failure.message:'Could not save the LM Studio address.');
    } finally {setBusy(false);}
  }

  async function select(value:string) {
    onModelChanged(value);
    onDiagnosticPassed(false);
    setDiagnostic('unknown');setError('');
    try {
      if(value.startsWith('openai-compatible::'))await client.selectCompatibleProviderModel(value||null);
      else await client.selectLMStudioModel(value||null);
    }
    catch(failure) {setError(failure instanceof Error?failure.message:'Could not save the selected model.');}
  }

  async function runDiagnostic() {
    if(!model)return;
    setBusy(true);setError('');setDiagnostic('unknown');
    try {
      const result=await client.diagnoseProviderModel(model);
      setDiagnostic(result.tool_round_trip_verified?'passed':'failed');
      onDiagnosticPassed(result.tool_round_trip_verified);
      setStatus(result.tool_round_trip_verified?'Tool-use diagnostic passed. Workspace tools are supported by this model.':'Tool-use diagnostic did not pass.');
    } catch(failure) {
      setDiagnostic('failed');
      onDiagnosticPassed(false);
      setError(failure instanceof Error?failure.message:'Tool-use diagnostic failed.');
      setStatus('Tool-use diagnostic failed. Choose another model or check the LM Studio server.');
    } finally {setBusy(false);}
  }

  return <section aria-labelledby="provider-setup-title">
    <h2 id="provider-setup-title">LM Studio setup</h2>
    <p role="status">{status}</p>
    <label htmlFor="lm-studio-endpoint">LM Studio server address</label>
    <input id="lm-studio-endpoint" type="url" value={endpoint} onChange={event=>setEndpoint(event.target.value)} disabled={busy}/>
    <p>Use an HTTP loopback address. The default is http://127.0.0.1:1234. In LM Studio, open Developer → Local Server and start the server if it is unavailable.</p>
    <button type="button" disabled={busy||!endpoint.trim()} onClick={()=>void saveEndpoint()}>Save address and connect</button>
    <button type="button" disabled={busy} onClick={()=>void refresh()}>Refresh models</button>
    <label htmlFor="lm-studio-token">Optional LM Studio API token</label>
    <input id="lm-studio-token" type="password" autoComplete="new-password" value={credential} onChange={event=>setCredential(event.target.value)} disabled={busy} maxLength={512} />
    <p>{credentialConfigured?'A token is saved in Windows Credential Manager.':'No API token is saved.'} The token is never shown after saving.</p>
    <button type="button" disabled={busy||!credential} onClick={()=>void saveCredential()}>Save API token securely</button>
    {credentialConfigured&&<button type="button" disabled={busy} onClick={()=>void clearCredential()}>Clear saved API token</button>}
    <label htmlFor="local-model">Local model or compatible provider model</label>
    <select id="local-model" value={model} disabled={busy} onChange={event=>void select(event.target.value)}>
      <option value="">Choose a discovered model</option>
      {models.map(id=><option key={id} value={id}>{id}</option>)}
    </select>
    {selectedDetails&&<p role="status">{selectedDetails.size_bytes!==null&&selectedDetails.size_bytes!==undefined?`Installed model files: ${(selectedDetails.size_bytes/1_000_000_000).toFixed(1)} GB. `:''}{selectedDetails.loaded===null||selectedDetails.loaded===undefined?'Load status unavailable from this LM Studio server.':selectedDetails.loaded?'Model is loaded.':'Model is installed and not loaded.'}</p>}
    <button type="button" disabled={busy||!model} onClick={()=>void runDiagnostic()}>Run harmless tool-use diagnostic</button>
    {diagnostic==='passed'&&<p role="status">The selected model completed a validated harmless tool round trip.</p>}
    {diagnostic==='failed'&&<p role="alert">Tool use remains unverified for this model. Try a different model or review the error.</p>}
    {error&&<p role="alert">{error}</p>}
    <hr />
    <h3>Optional OpenAI-compatible provider</h3>
    <p>Use a trusted HTTPS service or an HTTP service on this PC. Selecting one of its models sends that run’s content to the configured service. LM Studio remains the default.</p>
    <label htmlFor="compatible-endpoint">Provider server address</label>
    <input id="compatible-endpoint" type="url" value={compatibleEndpoint} onChange={event=>setCompatibleEndpoint(event.target.value)} disabled={busy}/>
    <button type="button" disabled={busy||!compatibleEndpoint.trim()} onClick={()=>void (async()=>{
      setBusy(true);setError('');
      try {
        const saved=await client.setCompatibleProviderEndpoint(compatibleEndpoint);
        setCompatibleEndpoint(saved.endpoint??'');
        if(model.startsWith('openai-compatible::'))onModelChanged('');
        onDiagnosticPassed(false);
        const providers=await client.providers();
        const configured=providers.find(item=>item.id==='openai-compatible');
        if(configured?.status!=='connected')throw new Error(configured?.error??'The configured provider is unavailable.');
        const found=await client.models();
        setModelDetails(found);onModelsChanged(found.map(item=>item.id));
        setStatus(`Provider connected. Discovered ${found.filter(item=>item.id.startsWith('openai-compatible::')).length} compatible model(s).`);
      } catch(failure) {setError(failure instanceof Error?failure.message:'Could not configure the provider.');}
      finally {setBusy(false);}
    })()}>Save address and discover models</button>
    <label htmlFor="compatible-token">Optional provider API token</label>
    <input id="compatible-token" type="password" autoComplete="new-password" value={compatibleCredential} onChange={event=>setCompatibleCredential(event.target.value)} disabled={busy} maxLength={512}/>
    <p>{compatibleCredentialConfigured?'A token is saved in Windows Credential Manager.':'No token is saved.'} The token is never shown after saving.</p>
    <button type="button" disabled={busy||!compatibleCredential} onClick={()=>void (async()=>{
      setBusy(true);setError('');
      try {const result=await client.setCompatibleProviderCredential(compatibleCredential);setCompatibleCredential('');setCompatibleCredentialConfigured(result.configured);}
      catch(failure) {setError(failure instanceof Error?failure.message:'Could not save the provider token.');}
      finally {setBusy(false);}
    })()}>Save provider token securely</button>
    {compatibleCredentialConfigured&&<button type="button" disabled={busy} onClick={()=>void (async()=>{
      setBusy(true);setError('');
      try {const result=await client.clearCompatibleProviderCredential();setCompatibleCredentialConfigured(result.configured);}
      catch(failure) {setError(failure instanceof Error?failure.message:'Could not clear the provider token.');}
      finally {setBusy(false);}
    })()}>Clear saved provider token</button>}
  </section>;
}
