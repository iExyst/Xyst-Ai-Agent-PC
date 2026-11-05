import {useEffect,useState} from 'react';
import type {EngineClient,Schedule} from './api/client.ts';

function localDateTime(value:Date) {
  const pad=(part:number)=>String(part).padStart(2,'0');
  return `${value.getFullYear()}-${pad(value.getMonth()+1)}-${pad(value.getDate())}T${pad(value.getHours())}:${pad(value.getMinutes())}`;
}

export function AutomationsPanel({client,model,workspace}:{client:EngineClient;model:string;workspace:string}) {
  const [items,setItems]=useState<Schedule[]>([]);
  const [objective,setObjective]=useState('');
  const [startAt,setStartAt]=useState(()=>localDateTime(new Date(Date.now()+15*60_000)));
  const [frequency,setFrequency]=useState<'once'|'daily'|'weekly'>('once');
  const [timezone,setTimezone]=useState(()=>Intl.DateTimeFormat().resolvedOptions().timeZone||'UTC');
  const [missedRunPolicy,setMissedRunPolicy]=useState<'skip'|'run_once'>('skip');
  const [busy,setBusy]=useState(false);
  const [error,setError]=useState('');

  async function refresh() { setItems(await client.schedules()); }
  useEffect(()=>{let live=true;void client.schedules().then(value=>{if(live)setItems(value);}).catch(failure=>{if(live)setError(String(failure));});return()=>{live=false;};},[client]);

  async function create() {
    setBusy(true);setError('');
    try {
      const schedule=await client.createSchedule({objective,acceptance_criteria:[],workspace:workspace||null,model,start_at:startAt,frequency,timezone,missed_run_policy:missedRunPolicy,max_iterations:8,max_seconds:900});
      setItems(current=>[schedule,...current]);setObjective('');
    } catch(failure) {setError(failure instanceof Error?failure.message:'Could not create the schedule.');}
    finally {setBusy(false);}
  }

  async function toggle(item:Schedule) {
    setBusy(true);setError('');
    try {const updated=await client.setScheduleEnabled(item.id,!item.enabled);setItems(current=>current.map(value=>value.id===item.id?updated:value));}
    catch(failure) {setError(failure instanceof Error?failure.message:'Could not update the schedule.');}
    finally {setBusy(false);}
  }

  return <section aria-labelledby="automations-title">
    <h2 id="automations-title">Automations</h2>
    <p>Scheduled objectives run in Observe mode through the same coordinator and appear in run history. They do not send messages or make workspace changes.</p>
    <label htmlFor="schedule-objective">Scheduled objective</label>
    <textarea id="schedule-objective" value={objective} onChange={event=>setObjective(event.target.value)} maxLength={10000} disabled={busy}/>
    <label htmlFor="schedule-start">First occurrence (local time)</label>
    <input id="schedule-start" type="datetime-local" value={startAt} onChange={event=>setStartAt(event.target.value)} disabled={busy}/>
    <label htmlFor="schedule-timezone">Timezone (IANA)</label>
    <input id="schedule-timezone" value={timezone} onChange={event=>setTimezone(event.target.value)} disabled={busy}/>
    <label htmlFor="schedule-frequency">Repeat</label>
    <select id="schedule-frequency" value={frequency} onChange={event=>setFrequency(event.target.value as 'once'|'daily'|'weekly')} disabled={busy}>
      <option value="once">Once</option><option value="daily">Daily</option><option value="weekly">Weekly</option>
    </select>
    <label htmlFor="schedule-missed">If the engine was closed at the scheduled time</label>
    <select id="schedule-missed" value={missedRunPolicy} onChange={event=>setMissedRunPolicy(event.target.value as 'skip'|'run_once')} disabled={busy}>
      <option value="skip">Skip missed work</option><option value="run_once">Run the latest missed occurrence once</option>
    </select>
    <p>Daylight-saving gaps are skipped; repeated wall-clock times run once at the earlier offset. Overlapping runs are skipped. Choose a local model above before scheduling.</p>
    <button type="button" onClick={()=>void create()} disabled={busy||!objective.trim()||!model||!startAt||!timezone.trim()}>Create schedule</button>
    {items.length>0&&<ul>{items.map(item=><li key={item.id}>
      <p>{item.objective}</p>
      <p>{String(item.recurrence.frequency??'schedule')} · Next local time: {String(item.recurrence.next_at??'not scheduled')} ({item.timezone}) · {item.enabled?'Enabled':'Disabled'}</p>
      <button type="button" disabled={busy} onClick={()=>void toggle(item)}>{item.enabled?'Pause schedule':'Resume schedule'}</button>
      {item.executions.length>0&&<details><summary>Execution history</summary><ul>{item.executions.map(execution=><li key={execution.id}>{execution.state}{execution.run_id?` · run ${execution.run_id}`:''} · {execution.created_at}</li>)}</ul></details>}
    </li>)}</ul>}
    <button type="button" disabled={busy} onClick={()=>void refresh()}>Refresh schedules</button>
    {error&&<p role="alert">{error}</p>}
  </section>;
}
