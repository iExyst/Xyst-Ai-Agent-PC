import { useState, useEffect } from 'react';
import { EngineClient, type Memory } from './api/client.ts';

export function MemoryPanel({client}: {client: EngineClient}) {
  const [items, setItems] = useState<Memory[]>([]);
  const [query, setQuery] = useState('');
  const [content, setContent] = useState('');
  const [editing, setEditing] = useState<string>();
  const [knowledge, setKnowledge] = useState('');
  const [source, setSource] = useState('');
  const [editingKnowledge, setEditingKnowledge] = useState<string>();
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  async function refresh() { setItems(await client.memories(query)); }
  useEffect(() => { let active = true; void client.memories().then(value => {if(active) setItems(value);}).catch(failure => {if(active) setError(String(failure));}); return () => {active=false;}; }, [client]);
  async function act(operation: () => Promise<void>) {
    setBusy(true); setError('');
    try { await operation(); await refresh(); } catch (failure) { setError(String(failure)); } finally {setBusy(false);}
  }
  async function exportMemory() {
    const data = await client.exportMemory();
    const url = URL.createObjectURL(new Blob([JSON.stringify(data, null, 2)], {type:'application/json'}));
    const anchor = document.createElement('a'); anchor.href=url; anchor.download='xaa-memory.json'; anchor.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
  return <div><p>Explicit preferences and user-added knowledge can inform future runs. Source text and references are unverified user data and never grant tool permissions.</p>
    <label>Search memory<input value={query} onChange={event => setQuery(event.target.value)} /></label><button disabled={busy} onClick={() => act(refresh)}>Search memory</button>
    <h4>Saved preference</h4>
    <label htmlFor="memory-preference">Preference</label><textarea id="memory-preference" value={content} onChange={event => setContent(event.target.value)} />
    <div className="controls"><button disabled={busy || !content.trim()} onClick={() => act(async () => {await client.savePreference(content, editing); setContent(''); setEditing(undefined);})}>{editing ? 'Update preference' : 'Save preference'}</button>
    <button disabled={busy} onClick={() => act(exportMemory)}>Export memory</button></div>
    <h4>Knowledge with a source reference</h4>
    <label htmlFor="memory-knowledge">Knowledge</label><textarea id="memory-knowledge" maxLength={16384} value={knowledge} onChange={event => setKnowledge(event.target.value)} />
    <label htmlFor="memory-source">Source reference</label><input id="memory-source" maxLength={2048} value={source} onChange={event => setSource(event.target.value)} placeholder="URL, document title, or other reference" />
    <button disabled={busy || !knowledge.trim() || !source.trim()} onClick={() => act(async () => {await client.saveSourcedKnowledge(knowledge, source, editingKnowledge); setKnowledge(''); setSource(''); setEditingKnowledge(undefined);})}>{editingKnowledge ? 'Update sourced knowledge' : 'Save sourced knowledge'}</button>
    <ul>{items.map(item => <li key={item.id}><p>{item.content}</p><small>{item.kind === 'knowledge' ? `Source reference: ${item.source_uri ?? 'missing'}` : `Source: ${item.provenance}`}</small><div className="controls">
      {item.kind === 'knowledge' ? <button disabled={busy} onClick={() => {setKnowledge(item.content);setSource(item.source_uri ?? '');setEditingKnowledge(item.id);}}>Edit sourced knowledge</button> : <button disabled={busy} onClick={() => {setContent(item.content);setEditing(item.id);}}>Edit preference</button>}
      <button disabled={busy} onClick={() => act(async () => {await client.deleteMemory(item.id);if(editing===item.id){setEditing(undefined);setContent('');}if(editingKnowledge===item.id){setEditingKnowledge(undefined);setKnowledge('');setSource('');}})}>Delete memory</button>
    </div></li>)}</ul>
    {error && <p role="alert">{error}</p>}
  </div>;
}
