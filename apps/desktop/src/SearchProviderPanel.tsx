import {useEffect,useState} from 'react';
import type {EngineClient,CredentialStatus} from './api/client.ts';

export function SearchProviderPanel({client}:{client:EngineClient}) {
  const [status,setStatus]=useState<CredentialStatus>();
  const [key,setKey]=useState('');
  const [busy,setBusy]=useState(false);
  const [error,setError]=useState('');
  const [diagnostic,setDiagnostic]=useState('');
  async function refresh() {
    try {setStatus(await client.braveSearchStatus());setError('');}
    catch(failure) {setError(failure instanceof Error?failure.message:'Could not read search provider status.');}
  }
  useEffect(()=>{void refresh();},[client]);
  async function save() {
    setBusy(true);setError('');
    try {setStatus(await client.setBraveSearchKey(key));setKey('');setDiagnostic('');}
    catch(failure) {setError(failure instanceof Error?failure.message:'Could not save the search API key.');}
    finally {setBusy(false);}
  }
  async function clear() {
    setBusy(true);setError('');
    try {setStatus(await client.clearBraveSearchKey());setKey('');setDiagnostic('');}
    catch(failure) {setError(failure instanceof Error?failure.message:'Could not clear the search API key.');}
    finally {setBusy(false);}
  }
  async function testConnection() {
    setBusy(true);setError('');setDiagnostic('');
    try {
      const result=await client.diagnoseBraveSearch();
      setDiagnostic(`Connection verified. Brave Search returned ${result.result_count} result${result.result_count===1?'':'s'} for the check.`);
    } catch(failure) {setError(failure instanceof Error?failure.message:'Could not verify the Brave Search connection.');}
    finally {setBusy(false);}
  }
  return <section aria-labelledby="web-search-title">
    <h3 id="web-search-title">Brave Search</h3>
    <p>The API key is stored in Windows Credential Manager. When search is enabled for a run, its search queries are sent to Brave. Testing sends the fixed query “X.A.A. Brave Search connection check” and displays only whether Brave responded and how many results it returned. Results are untrusted; fetching result pages requires separately approving their exact hostnames for that run.</p>
    <label htmlFor="brave-search-key">Brave Search API key</label>
    <input id="brave-search-key" type="password" autoComplete="new-password" maxLength={512} value={key} onChange={event=>setKey(event.target.value)} disabled={busy}/>
    <div className="controls">
      <button type="button" onClick={()=>void save()} disabled={busy||!key.trim()}>Save search key</button>
      {status?.configured&&<button type="button" onClick={()=>void clear()} disabled={busy}>Clear saved key</button>}
      <button type="button" onClick={()=>void testConnection()} disabled={busy||!status?.configured}>Test connection</button>
      <button type="button" onClick={()=>void refresh()} disabled={busy}>Refresh status</button>
    </div>
    {status&&<p role="status">{status.configured?'Brave Search is configured':'Brave Search is not configured'}</p>}
    {diagnostic&&<p role="status">{diagnostic}</p>}
    {error&&<p role="alert">{error}</p>}
  </section>;
}
