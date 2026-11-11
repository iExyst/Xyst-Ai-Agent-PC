import {useEffect,useRef,useState} from 'react';
import {EngineClient,type ChatSession,type ChatSessionSummary} from './api/client.ts';

type ChatMode='observe'|'workspace';

export function ConversationSessionsPanel({
  client,model,workspace,mode,refreshKey,onSelect,
}: {
  client:EngineClient;
  model:string;
  workspace:string;
  mode:ChatMode;
  refreshKey:string;
  onSelect:(sessionId:string,session?:ChatSession)=>void;
}) {
  const [items,setItems]=useState<ChatSessionSummary[]>([]);
  const [sessionId,setSessionId]=useState('');
  const [session,setSession]=useState<ChatSession>();
  const [titleDraft,setTitleDraft]=useState('');
  const [newName,setNewName]=useState('');
  const [busy,setBusy]=useState(false);
  const [loaded,setLoaded]=useState(false);
  const [error,setError]=useState('');
  const selection=useRef(onSelect);
  useEffect(()=>{selection.current=onSelect;},[onSelect]);

  useEffect(()=>{
    if(!loaded)return;
    let cancelled=false;
    void Promise.all([
      client.chatSessions(),
      sessionId?client.chatSession(sessionId):Promise.resolve(undefined),
    ]).then(([rows,record])=>{
      if(!cancelled){setItems(rows);if(record){setSession(record);setTitleDraft(record.title);selection.current(sessionId,record);}}
    }).catch(failure=>{if(!cancelled)setError(String(failure));});
    return()=>{cancelled=true;};
  },[client,refreshKey,sessionId,loaded]);

  async function select(id:string) {
    setSessionId(id);setError('');
    if(!id){setSession(undefined);selection.current('',undefined);return;}
    setBusy(true);
    try {
      const record=await client.chatSession(id);
      setSession(record);setTitleDraft(record.title);selection.current(id,record);
    } catch(failure){setSession(undefined);setError(String(failure));}
    finally{setBusy(false);}
  }

  async function renameCurrent() {
    if(!sessionId||!session)return;
    setBusy(true);setError('');
    try {
      const updated=await client.renameChatSession(sessionId,titleDraft);
      setSession(updated);setTitleDraft(updated.title);setItems(await client.chatSessions());
    } catch(failure){setError(String(failure));}
    finally{setBusy(false);}
  }

  async function create() {
    if(!model||!workspace.trim())return;
    const id=newName.trim()||`desktop-${crypto.randomUUID()}`;
    if(!/^[A-Za-z0-9._-]{1,64}$/.test(id)){setError('Use 1–64 letters, numbers, dots, underscores or hyphens for a session name.');return;}
    setBusy(true);setError('');
    try {
      const record=await client.openChatSession(id,{model,workspace,mode});
      setSessionId(id);setSession(record);setNewName('');
      setLoaded(true);setItems(await client.chatSessions());selection.current(id,record);
    } catch(failure){setError(String(failure));}
    finally{setBusy(false);}
  }

  async function deleteCurrent() {
    if(!sessionId||!session)return;
    if(!window.confirm(`Delete shared conversation “${sessionId}” and its saved turns? This cannot be undone.`))return;
    setBusy(true);setError('');
    try {
      await client.deleteChatSession(sessionId);
      setItems(current=>current.filter(item=>item.session_id!==sessionId));
      setSessionId('');setSession(undefined);selection.current('',undefined);
    } catch(failure){setError(String(failure));}
    finally{setBusy(false);}
  }

  return <section aria-label="Shared conversation sessions">
    <h2>Shared conversation</h2>
    <p>Continue a text conversation saved by the desktop or <code>xaa chat</code>. Each turn still runs through the durable coordinator; session context is bounded and treated as untrusted history. Deleting a session removes it for both interfaces after confirmation.</p>
    {!loaded&&<button type="button" disabled={busy} onClick={()=>setLoaded(true)}>Load shared conversations</button>}
    {loaded&&<>
    <label htmlFor="shared-chat-session">Saved terminal and desktop sessions</label>
    <select id="shared-chat-session" value={sessionId} disabled={busy} onChange={event=>void select(event.target.value)}>
      <option value="">No shared conversation</option>
      {items.map(item=><option key={item.session_id} value={item.session_id}>{item.title} · {item.session_id}</option>)}
    </select>
    <label htmlFor="new-shared-chat-name">New session name (optional)<input id="new-shared-chat-name" value={newName} maxLength={64} onChange={event=>setNewName(event.target.value)} placeholder="desktop-project-chat" disabled={busy}/></label>
    <button type="button" disabled={busy||!model||!workspace.trim()} onClick={()=>void create()}>Start or open shared conversation</button>
    {session&&<>
      <label htmlFor="shared-chat-title">Conversation title<input id="shared-chat-title" value={titleDraft} maxLength={80} onChange={event=>setTitleDraft(event.target.value)} disabled={busy}/></label>
      <button type="button" disabled={busy||!titleDraft.trim()||titleDraft.trim()===session.title} onClick={()=>void renameCurrent()}>Save title</button>
      <p><strong>{session.title}</strong> · Session ID <code>{session.session_id}</code> · {session.mode} · {session.model} · {session.workspace??'no workspace'}</p>
      <p>Continue this same history in a terminal with <code>xaa chat --model "{session.model}"{session.workspace?` --workspace "${session.workspace}"`:''} --mode "{session.mode}" --session "{session.session_id}"</code>.</p>
      <button type="button" disabled={busy} onClick={()=>void deleteCurrent()}>Delete shared conversation</button>
      <ol aria-label="Saved conversation turns">{session.messages.map((message,index)=><li key={`${message.run_id}:${index}`}><strong>{message.role==='user'?'You':'X.A.A.'}</strong><pre style={{whiteSpace:'pre-wrap',overflowWrap:'anywhere'}}>{message.content}</pre></li>)}</ol>
    </>}
    </>}
    {error&&<p role="alert">{error}</p>}
  </section>;
}
