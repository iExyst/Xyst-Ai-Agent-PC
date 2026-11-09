import {useState} from 'react';
import type {EngineClient,WorkspaceWriteReconciliationItem} from './api/client.ts';

export function WorkspaceWriteReconciliationPanel({client,runId,enabled=true}:{client:EngineClient;runId:string;enabled?:boolean}) {
  const [items,setItems]=useState<WorkspaceWriteReconciliationItem[]>([]);
  const [checked,setChecked]=useState(false);
  const [busy,setBusy]=useState(false);
  const [error,setError]=useState('');
  const [message,setMessage]=useState('');

  async function load() {
    setBusy(true);setError('');setMessage('');
    try {setItems(await client.workspaceWriteReconciliations(runId));setChecked(true);}
    catch(failure) {setError(failure instanceof Error?failure.message:'Could not inspect uncertain workspace writes.');}
    finally {setBusy(false);}
  }

  async function confirmNotApplied(item:WorkspaceWriteReconciliationItem) {
    const before=item.operation==='create'
      ? 'Delete the incomplete file first, then verify the destination is absent.'
      : item.operation==='delete'
        ? `Restore the deleted file and verify its SHA-256 is ${item.expected_sha256}.`
        : `Restore the original file and verify its SHA-256 is ${item.expected_sha256}.`;
    if(!window.confirm(`${before} Confirm only after inspecting the selected workspace. This closes the old write as not applied; it does not repeat it.`))return;
    setBusy(true);setError('');setMessage('');
    try {
      setItems(await client.reconcileWorkspaceWriteNotApplied(runId,item.call_id));
      setMessage('The interrupted write was recorded as not applied. Review the run, then use Resume to continue.');
    } catch(failure) {setError(failure instanceof Error?failure.message:'Could not reconcile the workspace write.');}
    finally {setBusy(false);}
  }

  if(checked&&items.length===0&&!error&&!message)return null;
  return <section aria-label="Workspace write recovery">
    <h3>Uncertain workspace writes</h3>
    <p>Inspect each destination. The saved write is never repeated automatically. To confirm it did not apply, restore the original file hash or remove the incomplete new file; the engine checks that state again before recording your decision.</p>
    {!checked&&<button type="button" disabled={busy||!enabled} onClick={()=>void load()}>Inspect uncertain workspace writes</button>}
    {items.length>0&&<ul>{items.map(item=><li key={item.call_id}>
      <strong>{item.operation==='create'?'Create':item.operation==='delete'?'Delete':'Edit'} <code>{item.path}</code></strong>
      <p>{item.operation==='delete'?<>Required restored SHA-256: <code>{item.expected_sha256}</code></>:<>Desired SHA-256: <code>{item.desired_sha256}</code>{item.expected_sha256&&<> · original SHA-256: <code>{item.expected_sha256}</code></>}</>}</p>
      <button type="button" disabled={busy||!enabled} onClick={()=>void confirmNotApplied(item)}>Confirm write did not apply</button>
    </li>)}</ul>}
    {checked&&items.length===0&&<p>No unresolved workspace writes.</p>}
    {message&&<p role="status">{message}</p>}
    {error&&<p role="alert">{error}</p>}
  </section>;
}
