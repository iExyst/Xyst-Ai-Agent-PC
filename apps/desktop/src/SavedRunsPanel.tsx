import {useEffect,useRef,useState} from 'react';
import {EngineClient,type RunHistory} from './api/client.ts';

export function SavedRunsPanel({client,refreshKey,onOpen}:{client:EngineClient;refreshKey:string;onOpen:(id:string)=>void}) {
  const [query,setQuery]=useState('');
  const [scope,setScope]=useState<'objective'|'conversation'>('objective');
  const [activeScope,setActiveScope]=useState<'objective'|'conversation'>('objective');
  const [activeQuery,setActiveQuery]=useState('');
  const [items,setItems]=useState<RunHistory['items']>([]);
  const [next,setNext]=useState<string|null>(null);
  const [busy,setBusy]=useState(false);
  const [error,setError]=useState('');
  const request=useRef<AbortController|null>(null);
  async function load(search:string,before?:string,searchScope:typeof scope=activeScope) {
    request.current?.abort();
    const controller=new AbortController();request.current=controller;
    setBusy(true);setError('');
    try {
      const result=await client.history(search,before,controller.signal,searchScope);
      if(!controller.signal.aborted){setItems(previous=>before?[...previous,...result.items]:result.items);setNext(result.next_cursor);}
    } catch(failure){if(!controller.signal.aborted)setError(String(failure));}
    finally{if(!controller.signal.aborted)setBusy(false);}
  }
  useEffect(()=>{void load(activeQuery);return()=>request.current?.abort();},[client,refreshKey,activeQuery,activeScope]);
  return <section aria-label="Saved runs"><h2>Saved runs</h2>
    <form onSubmit={event=>{event.preventDefault();const value=query.trim();if(value===activeQuery&&scope===activeScope)void load(value);else {setActiveQuery(value);setActiveScope(scope);}}}><label>Search in<select aria-label="Search in" value={scope} onChange={event=>setScope(event.target.value as typeof scope)}><option value="objective">Objectives (exact case substring)</option><option value="conversation">Conversations (full text phrase)</option></select></label><label>{scope==='objective'?'Search objective text (exact case)':'Search conversation phrase'}<input value={query} maxLength={256} onChange={event=>setQuery(event.target.value)}/></label><div className="controls"><button disabled={busy}>Search saved runs</button><button type="button" disabled={busy} onClick={()=>void load(activeQuery)}>Refresh saved runs</button></div></form>
    {scope==='conversation'&&<p>Searches objectives and public assistant answers. Matches whole words without case or accent distinctions. Tool results and private model metadata are excluded.</p>}
    {!busy&&items.length===0&&<p>No saved runs match this search.</p>}
    <ul>{items.map(item=><li key={item.id}><button onClick={()=>onOpen(item.id)}>Open {item.objective}</button><p>{item.state} · {new Date(item.created_at).toLocaleString()}</p></li>)}</ul>
    {next&&<button disabled={busy} onClick={()=>void load(activeQuery,next)}>Load older runs</button>}
    {error&&<p role="alert">{error}</p>}
  </section>;
}
