import {useEffect,useRef,useState} from 'react';
import {EngineClient,type ChatSession,type ChatSessionSummary} from './api/client.ts';

type ChatMode='observe'|'workspace';

export function ConversationSessionsPanel({
  client,model,workspace,mode,refreshKey,onSelect,onContinue,
}: {
  client:EngineClient;
  model:string;
  workspace:string;
  mode:ChatMode;
  refreshKey:string;
  onSelect:(sessionId:string,session?:ChatSession)=>void;
  onContinue?:()=>void;
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
  const titleDirty=useRef(false);
  useEffect(()=>{selection.current=onSelect;},[onSelect]);

  useEffect(()=>{
    if(!loaded)return;
    let cancelled=false;
    void Promise.all([
      client.chatSessions(),
      sessionId?client.chatSession(sessionId):Promise.resolve(undefined),
    ]).then(([rows,record])=>{
      if(!cancelled){setItems(rows);if(record){setSession(record);if(!titleDirty.current)setTitleDraft(record.title);selection.current(sessionId,record);}}
    }).catch(failure=>{if(!cancelled)setError(String(failure));});
    return()=>{cancelled=true;};
  },[client,refreshKey,sessionId,loaded]);

  async function select(id:string) {
    setSessionId(id);setError('');titleDirty.current=false;
    if(!id){setSession(undefined);setTitleDraft('');selection.current('',undefined);return;}
    setBusy(true);
    try {
      const record=await client.chatSession(id);
      setSession(record);if(!titleDirty.current)setTitleDraft(record.title);selection.current(id,record);
    } catch(failure){setSession(undefined);setError(String(failure));}
    finally{setBusy(false);}
  }

  async function renameCurrent() {
    if(!sessionId||!session)return;
    setBusy(true);setError('');
    try {
      const updated=await client.renameChatSession(sessionId,titleDraft);
      setSession(updated);setTitleDraft(updated.title);titleDirty.current=false;setItems(await client.chatSessions());selection.current(sessionId,updated);
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
      setSessionId(id);setSession(record);setTitleDraft(record.title);titleDirty.current=false;setNewName('');
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
      setSessionId('');setSession(undefined);setTitleDraft('');titleDirty.current=false;selection.current('',undefined);
    } catch(failure){setError(String(failure));}
    finally{setBusy(false);}
  }

  return <section aria-label="Shared conversation sessions">
    <h2>Shared conversation</h2>
    <p>Continue a text conversation saved by the desktop or <code>xaa chat</code>. Select a session to show its messages in the workspace. Each new turn still runs through the durable coordinator; saved history is bounded and treated as untrusted context.</p>
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
      <label htmlFor="shared-chat-title">Conversation title<input id="shared-chat-title" value={titleDraft} maxLength={80} onChange={event=>{titleDirty.current=true;setTitleDraft(event.target.value);}} disabled={busy}/></label>
      <button type="button" disabled={busy||!titleDraft.trim()||titleDraft.trim()===session.title} onClick={()=>void renameCurrent()}>Save title</button>
      <p>Session ID <code>{session.session_id}</code> · {session.mode} · {session.model} · {session.workspace??'no workspace'}</p>
      <p>Continue this same history in a terminal with <code>xaa chat --model "{session.model}"{session.workspace?` --workspace "${session.workspace}"`:''} --mode "{session.mode}" --session "{session.session_id}"</code>.</p>
      <button type="button" disabled={busy} onClick={()=>void deleteCurrent()}>Delete shared conversation</button>
      {onContinue&&<button type="button" disabled={busy} onClick={onContinue}>Continue in workspace</button>}
    </>}
    </>}
    {error&&<p role="alert">{error}</p>}
  </section>;
}
