import {useEffect,useState} from 'react';
import type {EngineClient,MCPReconciliationItem} from './api/client.ts';

export function MCPReconciliationPanel({client,runId}:{client:EngineClient;runId:string}) {
  const [items,setItems]=useState<MCPReconciliationItem[]>([]);
  const [busy,setBusy]=useState(false);
  const [error,setError]=useState('');
  useEffect(()=>{let live=true;void client.mcpReconciliations(runId).then(value=>{if(live)setItems(value);}).catch(()=>{if(live)setError('Could not load uncertain MCP effects.');});return()=>{live=false;};},[client,runId]);
  async function resolve(item:MCPReconciliationItem,resolution:'applied'|'not_applied') {
    const description=resolution==='applied'?'The external effect happened.':'The external effect did not happen.';
    if(!window.confirm(`Inspect the MCP server’s external system first. Confirm that ${description.toLowerCase()} This records your review and will not retry the call.`))return;
    setBusy(true);setError('');
    try{setItems(await client.reconcileMcpEffect(runId,item.call_id,resolution));}
    catch(failure){setError(failure instanceof Error?failure.message:'Could not reconcile the MCP effect.');}
    finally{setBusy(false);}
  }
  if(items.length===0&&!error)return null;
  return <section aria-label="MCP effect reconciliation"><h3>Uncertain MCP effects</h3><p>Inspect each MCP server’s external system. Recording a result does not retry the call.</p>{error&&<p role="alert">{error}</p>}<ul>{items.map(item=><li key={item.call_id}><strong>{item.tool}</strong> — {item.status.replaceAll('_',' ')}<div className="controls"><button type="button" disabled={busy} onClick={()=>void resolve(item,'applied')}>Confirm effect happened</button><button type="button" disabled={busy} onClick={()=>void resolve(item,'not_applied')}>Confirm effect did not happen</button></div></li>)}</ul></section>;
}
