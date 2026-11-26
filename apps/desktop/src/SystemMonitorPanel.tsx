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

function trendY(value:number):number {
  const bounded=Math.max(0,Math.min(100,value));
  return 27-bounded/100*24;
}

function trendPoints(samples:number[]):string {
  const values=samples.filter(value=>Number.isFinite(value)).slice(-60);
  return values.map((value,index)=>{
    const x=values.length===1?100:index/(values.length-1)*100;
    return `${x.toFixed(1)},${trendY(value).toFixed(1)}`;
  }).join(' ');
}

function TrendTrace({title,values}:{title:string;values:number[]}) {
  const samples=values.filter(value=>Number.isFinite(value)).slice(-60);
  if(samples.length===0)return null;
  const points=trendPoints(samples);
  return <div className="system-resource-trend">
    <span className="system-resource-trend-label">RECENT</span>
    <svg role="img" aria-label={`${title} trend across ${samples.length} recent local samples`} viewBox="0 0 100 30" preserveAspectRatio="none">
      <path className="system-resource-trend-grid" d="M0 9H100M0 20H100"/>
      {samples.length===1
        ?<circle className="system-resource-trend-dot" cx="100" cy={trendY(samples[0]!)} r="2.2"/>
        :<polyline className="system-resource-trend-line" points={points}/>}
    </svg>
    <span className="system-resource-trend-count">{samples.length}</span>
  </div>;
}

function ResourceCard({title,value,detail,percent,trend}:{title:string;value:string;detail:string;percent:number|null;trend?:number[]}) {
  return <section className="system-resource-card" aria-label={title}>
    <div className="system-resource-heading"><h2>{title}</h2><strong>{value}</strong></div>
    {percent!==null&&<meter min={0} max={100} value={percent} aria-label={`${title} usage`}>{percent}%</meter>}
    <p>{detail}</p>
    {trend&&<TrendTrace title={title} values={trend}/>}
  </section>;
}

export function SystemMonitorPanel({client}:{client:EngineClient}) {
  const [snapshot,setSnapshot]=useState<SystemResourceSnapshot>();
  const [error,setError]=useState('');
  const [refreshing,setRefreshing]=useState(false);
  const [refreshNotice,setRefreshNotice]=useState('');
  const [history,setHistory]=useState<SystemResourceSnapshot[]>([]);
  const refresh=useCallback(async(announce=false)=>{
    setRefreshing(true);
    try{
      const next=await client.systemResources();
      setSnapshot(next);setError('');
      setHistory(current=>[...current,next].sort((left,right)=>Date.parse(left.sampled_at)-Date.parse(right.sampled_at)).slice(-60));
      setRefreshNotice(announce?`System monitor updated ${new Date(next.sampled_at).toLocaleTimeString()}.`:'');
    }
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
  const diskTrend=history.map(sample=>sample.disk_total_bytes!==null&&sample.disk_free_bytes!==null&&sample.disk_total_bytes>0
    ?(sample.disk_total_bytes-sample.disk_free_bytes)*100/sample.disk_total_bytes:Number.NaN);
  const gpuCards=(snapshot?.gpu_devices??[]).map((gpu,index,devices)=>{
    const sameNameBefore=devices.slice(0,index).filter(item=>item.name===gpu.name).length;
    const sameNameCount=devices.filter(item=>item.name===gpu.name).length;
    const adapterOrdinal=sameNameBefore+1;
    const title=sameNameCount>1?`${gpu.name} · Adapter ${adapterOrdinal}`:gpu.name;
    const trend=history.map(sample=>sample.gpu_devices.filter(item=>item.name===gpu.name)[adapterOrdinal-1]?.utilization_percent??Number.NaN);
    return {gpu,adapterOrdinal,title,trend};
  });
  return <div className="system-monitor">
    <section className="system-monitor-intro"><div><p className="eyebrow">LOCAL HARDWARE</p><h2>System monitor</h2><p>Live host capacity and local inference queue estimates. Measurements stay on this computer.</p></div><button type="button" className="secondary-button" onClick={()=>void refresh(true)} disabled={refreshing}>{refreshing?'Refreshing…':'Refresh now'}</button></section>
    {snapshot&&<>
      <div className="system-resource-grid">
        <ResourceCard title="Processor" value={snapshot.cpu_percent===null?'Measuring…':`${snapshot.cpu_percent.toFixed(1)}%`} detail={`${snapshot.logical_processors} logical processors`} percent={snapshot.cpu_percent} trend={history.map(sample=>sample.cpu_percent??Number.NaN)}/>
        <ResourceCard title="Memory" value={memoryPercent===null?'Unavailable':`${memoryPercent.toFixed(1)}% used`} detail={`${formatBytes(snapshot.memory_available_bytes)} available of ${formatBytes(snapshot.memory_total_bytes)}`} percent={memoryPercent} trend={history.map(sample=>sample.memory_used_percent??Number.NaN)}/>
        <ResourceCard title="Profile drive" value={diskPercent===null?'Unavailable':`${diskPercent.toFixed(1)}% used`} detail={`${formatBytes(snapshot.disk_free_bytes)} free of ${formatBytes(snapshot.disk_total_bytes)}`} percent={diskPercent} trend={diskTrend}/>
        {gpuCards.map(({gpu,adapterOrdinal,title,trend})=><ResourceCard key={`${gpu.name}-${adapterOrdinal}`} title={title} value={gpu.utilization_percent===null?'Unavailable':`${gpu.utilization_percent}% active`} detail={`${formatBytes(gpu.memory_free_bytes)} GPU memory free of ${formatBytes(gpu.memory_total_bytes)}`} percent={gpu.utilization_percent} trend={trend}/>)}
        <section className="system-resource-card" aria-label="Inference queue"><div className="system-resource-heading"><h2>Inference queue</h2><strong>{snapshot.queue_active?'Working':'Idle'}</strong></div><p>{snapshot.queue_waiting} waiting · {snapshot.queue_reserved_weight.toLocaleString()} estimated queue weight</p><p>Capacity: {snapshot.queue_max_pending} waiting requests · {snapshot.queue_max_reserved_weight.toLocaleString()} weight units</p></section>
      </div>
      <p className="system-monitor-note">GPU readings are reported when Windows adapter or NVIDIA driver telemetry is available. A missing GPU card means that telemetry isn’t available; it does not mean this computer has no accelerator. Queue weight limits estimated queued payload size; they do not predict model tokenization or GPU memory requirements.</p>
      {refreshNotice&&<p className="system-monitor-updated" role="status">{refreshNotice}</p>}
    </>}
    {!snapshot&&!error&&<p role="status">Waiting for the first resource sample…</p>}
    {error&&<p role="alert">Could not read local resource information: {error}</p>}
  </div>;
}
