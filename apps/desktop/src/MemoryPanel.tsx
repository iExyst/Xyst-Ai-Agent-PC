import { useState, useEffect } from 'react';
import { EngineClient, type Memory } from './api/client.ts';

export function MemoryPanel({client}: {client: EngineClient}) {
  const [items, setItems] = useState<Memory[]>([]);
  const [query, setQuery] = useState('');
  const [content, setContent] = useState('');
  const [editing, setEditing] = useState<string>();
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  async function refresh() { setItems(await client.memories(query)); }
  useEffect(() => { let active = true; void client.memories().then(value => {if(active) setItems(value);}).catch(failure => {if(active) setError(String(failure));}); return () => {active=false;}; }, [client]);
  async function act(operation: () => Promise<void>) {
    setBusy(true); setError('');
    try { await operation(); await refresh(); } catch (failure) { setError(String(failure)); } finally {setBusy(false);}
  }
  async function exportPreferences() {
    const data = await client.exportMemory();
    const url = URL.createObjectURL(new Blob([JSON.stringify(data, null, 2)], {type:'application/json'}));
    const anchor = document.createElement('a'); anchor.href=url; anchor.download='xaa-memory.json'; anchor.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
  return <div><p>Only preferences you explicitly save are recalled. They do not grant tool permissions.</p>
    <label>Search memory<input value={query} onChange={event => setQuery(event.target.value)} /></label><button disabled={busy} onClick={() => act(refresh)}>Search preferences</button>
    <label htmlFor="memory-preference">Preference</label><textarea id="memory-preference" value={content} onChange={event => setContent(event.target.value)} />
    <div className="controls"><button disabled={busy || !content.trim()} onClick={() => act(async () => {await client.savePreference(content, editing); setContent(''); setEditing(undefined);})}>{editing ? 'Update preference' : 'Save preference'}</button>
    <button disabled={busy} onClick={() => act(exportPreferences)}>Export memory</button></div>
    <ul>{items.map(item => <li key={item.id}><p>{item.content}</p><small>Source: {item.provenance}</small><div className="controls"><button disabled={busy} onClick={() => {setContent(item.content);setEditing(item.id);}}>Edit preference</button><button disabled={busy} onClick={() => act(async () => {await client.deleteMemory(item.id);if(editing===item.id){setEditing(undefined);setContent('');}})}>Delete preference</button></div></li>)}</ul>
    {error && <p role="alert">{error}</p>}
  </div>;
}
