import {useCallback, useEffect, useState} from 'react';
import {EngineClient, type SystemResourceSnapshot} from './api/client.ts';

const POLL_INTERVAL_MS=2500;

function formatBytes(bytes:number|null):string {
  if(bytes===null)return 'Unavailable';
  const units=['B','KiB','MiB','GiB','TiB'];
  let value=bytes;
  let unit=0;
  while(value>=1024&&unit<units.length-1){value/=1024;unit+=1;}
  return `${value.toFixed(unit===0?0:1)} ${units[unit]}`;
}

function ResourceCard({title,value,detail,percent}:{title:string;value:string;detail:string;percent:number|null}) {
  return <section className="system-resource-card" aria-label={title}>
    <div className="system-resource-heading"><h2>{title}</h2><strong>{value}</strong></div>
    {percent!==null&&<meter min={0} max={100} value={percent} aria-label={`${title} usage`}>{percent}%</meter>}
    <p>{detail}</p>
  </section>;
}

export function SystemMonitorPanel({client}:{client:EngineClient}) {
  const [snapshot,setSnapshot]=useState<SystemResourceSnapshot>();
  const [error,setError]=useState('');
  const [refreshing,setRefreshing]=useState(false);
  const refresh=useCallback(async()=>{
    setRefreshing(true);
    try{setSnapshot(await client.systemResources());setError('');}
    catch(failure){setError(String(failure));}
    finally{setRefreshing(false);}
  },[client]);
  useEffect(()=>{
    void refresh();
    const timer=window.setInterval(()=>void refresh(),POLL_INTERVAL_MS);
    return ()=>window.clearInterval(timer);
  },[refresh]);

  const memoryPercent=snapshot?.memory_used_percent??null;
  const diskPercent=snapshot&&snapshot.disk_total_bytes!==null&&snapshot.disk_free_bytes!==null&&snapshot.disk_total_bytes>0
    ?Math.round((snapshot.disk_total_bytes-snapshot.disk_free_bytes)*100/snapshot.disk_total_bytes*10)/10:null;
  return <div className="system-monitor">
    <section className="system-monitor-intro"><div><p className="eyebrow">LOCAL HARDWARE</p><h2>System monitor</h2><p>Live host capacity and local inference queue estimates. Measurements stay on this computer.</p></div><button type="button" className="secondary-button" onClick={()=>void refresh()} disabled={refreshing}>{refreshing?'Refreshing…':'Refresh now'}</button></section>
    {snapshot&&<>
      <div className="system-resource-grid">
        <ResourceCard title="Processor" value={snapshot.cpu_percent===null?'Measuring…':`${snapshot.cpu_percent.toFixed(1)}%`} detail={`${snapshot.logical_processors} logical processors`} percent={snapshot.cpu_percent}/>
        <ResourceCard title="Memory" value={memoryPercent===null?'Unavailable':`${memoryPercent.toFixed(1)}% used`} detail={`${formatBytes(snapshot.memory_available_bytes)} available of ${formatBytes(snapshot.memory_total_bytes)}`} percent={memoryPercent}/>
        <ResourceCard title="Profile drive" value={diskPercent===null?'Unavailable':`${diskPercent.toFixed(1)}% used`} detail={`${formatBytes(snapshot.disk_free_bytes)} free of ${formatBytes(snapshot.disk_total_bytes)}`} percent={diskPercent}/>
        {snapshot.gpu_devices.map((gpu,index)=><ResourceCard key={`${gpu.name}-${index}`} title={gpu.name} value={gpu.utilization_percent===null?'Unavailable':`${gpu.utilization_percent}% active`} detail={`${formatBytes(gpu.memory_free_bytes)} VRAM free of ${formatBytes(gpu.memory_total_bytes)}`} percent={gpu.utilization_percent}/>) }
        <section className="system-resource-card" aria-label="Inference queue"><div className="system-resource-heading"><h2>Inference queue</h2><strong>{snapshot.queue_active?'Working':'Idle'}</strong></div><p>{snapshot.queue_waiting} waiting · {snapshot.queue_reserved_tokens.toLocaleString()} estimated tokens reserved</p><p>Capacity: {snapshot.queue_max_pending} waiting requests · {snapshot.queue_max_reserved_tokens.toLocaleString()} estimated tokens</p></section>
      </div>
      <p className="system-monitor-note">GPU readings are reported when NVIDIA driver telemetry is available. A missing GPU card means that telemetry isn’t available; it does not mean this computer has no accelerator. Queue tokens are payload estimates and do not predict model VRAM requirements.</p>
      <p className="system-monitor-updated" role="status">Updated {new Date(snapshot.sampled_at).toLocaleTimeString()}</p>
    </>}
    {!snapshot&&!error&&<p role="status">Waiting for the first resource sample…</p>}
    {error&&<p role="alert">Could not read local resource information: {error}</p>}
  </div>;
}
