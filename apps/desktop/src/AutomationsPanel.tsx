import {useEffect,useState} from 'react';
import type {EngineClient,Schedule} from './api/client.ts';

function localDateTime(value:Date) {
  const pad=(part:number)=>String(part).padStart(2,'0');
  return `${value.getFullYear()}-${pad(value.getMonth()+1)}-${pad(value.getDate())}T${pad(value.getHours())}:${pad(value.getMinutes())}`;
}

function recipientKey(destination:string|null|undefined) {
  if(!destination)return '';
  const [,accountId,userId]=destination.split(':');
  return accountId&&userId?`${accountId}:${userId}`:'';
}

export function AutomationsPanel({client,model,workspace}:{client:EngineClient;model:string;workspace:string}) {
  const [items,setItems]=useState<Schedule[]>([]);
  const [objective,setObjective]=useState('');
  const [startAt,setStartAt]=useState(()=>localDateTime(new Date(Date.now()+15*60_000)));
  const [frequency,setFrequency]=useState<'once'|'daily'|'weekly'>('once');
  const [timezone,setTimezone]=useState(()=>Intl.DateTimeFormat().resolvedOptions().timeZone||'UTC');
  const [missedRunPolicy,setMissedRunPolicy]=useState<'skip'|'run_once'>('skip');
  const [quietStart,setQuietStart]=useState('');
  const [quietEnd,setQuietEnd]=useState('');
  const [telegramUsers,setTelegramUsers]=useState<string[]>([]);
  const [telegramBotAccountId,setTelegramBotAccountId]=useState<string|null>(null);
  const [telegramDeliveryUser,setTelegramDeliveryUser]=useState('');
  const [telegramFailureUser,setTelegramFailureUser]=useState('');
  const [scheduleReconcileChoice,setScheduleReconcileChoice]=useState<{scheduleId:string;executionId:string;resolution:'delivered'|'retry'}>();
  const [editingRecipients,setEditingRecipients]=useState<{scheduleId:string;resultUser:string;failureUser:string}>();
  const [busy,setBusy]=useState(false);
  const [error,setError]=useState('');

  async function refresh() {
    setError('');
    try {
      const [schedules,users]=await Promise.all([client.schedules(),client.telegramLinkedUsers()]);
      setItems(schedules);setTelegramBotAccountId(users.bot_account_id);setTelegramUsers(users.users.map(user=>user.user_id));
      if(!users.users.some(user=>user.user_id===telegramDeliveryUser))setTelegramDeliveryUser('');
      if(!users.users.some(user=>user.user_id===telegramFailureUser))setTelegramFailureUser('');
    } catch(failure) {setError(failure instanceof Error?failure.message:'Could not refresh schedules or Telegram destinations.');}
  }
  useEffect(()=>{let live=true;void Promise.all([client.schedules(),client.telegramLinkedUsers()]).then(([schedules,users])=>{if(live){setItems(schedules);setTelegramBotAccountId(users.bot_account_id);setTelegramUsers(users.users.map(user=>user.user_id));}}).catch(failure=>{if(live)setError(String(failure));});return()=>{live=false;};},[client]);

  function recipientIsLinkedHere(value:string) {
    if(!value)return true;
    const [accountId,userId]=value.split(':');
    return Boolean(telegramBotAccountId&&accountId===telegramBotAccountId&&userId&&telegramUsers.includes(userId));
  }

  async function create() {
    setBusy(true);setError('');
    try {
      if(Boolean(quietStart)!==Boolean(quietEnd))throw new Error('Set both quiet-hours times or leave both empty.');
      if(quietStart&&quietStart===quietEnd)throw new Error('Quiet hours must have different start and end times.');
      const quietHours=quietStart&&quietEnd?`${quietStart}-${quietEnd}`:undefined;
      const schedule=await client.createSchedule({objective,acceptance_criteria:[],workspace:workspace||null,model,start_at:startAt,frequency,timezone,missed_run_policy:missedRunPolicy,max_iterations:8,max_seconds:900,...(quietHours?{quiet_hours:quietHours}:{}),...(telegramDeliveryUser?{telegram_user_id:telegramDeliveryUser}:{}),...(telegramFailureUser?{telegram_failure_user_id:telegramFailureUser}:{})});
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

  async function saveRecipients() {
    if(!editingRecipients)return;
    setBusy(true);setError('');
    try {
      if(!recipientIsLinkedHere(editingRecipients.resultUser)||!recipientIsLinkedHere(editingRecipients.failureUser))throw new Error('Choose a linked Telegram destination for the current bot or clear that recipient.');
      const updated=await client.setScheduleDeliveryDestinations(editingRecipients.scheduleId,{telegram_user_id:editingRecipients.resultUser.split(':')[1]||null,telegram_failure_user_id:editingRecipients.failureUser.split(':')[1]||null});
      setItems(current=>current.map(item=>item.id===updated.id?updated:item));setEditingRecipients(undefined);
    } catch(failure) {setError(failure instanceof Error?failure.message:'Could not update Telegram recipients.');}
    finally {setBusy(false);}
  }

  async function reconcileScheduleDelivery() {
    if(!scheduleReconcileChoice)return;
    setBusy(true);setError('');
    try {
      const updated=await client.reconcileScheduleDelivery(scheduleReconcileChoice.scheduleId,scheduleReconcileChoice.executionId,scheduleReconcileChoice.resolution);
      setItems(current=>current.map(item=>item.id===updated.id?updated:item));
      setScheduleReconcileChoice(undefined);
    } catch(failure) {setError(failure instanceof Error?failure.message:'Could not reconcile the scheduled Telegram delivery.');}
    finally {setBusy(false);}
  }

  return <section aria-labelledby="automations-title">
    <h2 id="automations-title">Automations</h2>
    <p>Scheduled objectives run in Observe mode through the same coordinator and appear in run history. They do not make workspace changes. Telegram delivery is optional and authorizes a result message and, separately, a failure notice to the linked private accounts you select.</p>
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
    <label htmlFor="quiet-hours-start">Telegram quiet hours start (optional, local schedule time)</label>
    <input id="quiet-hours-start" type="time" value={quietStart} onChange={event=>setQuietStart(event.target.value)} disabled={busy}/>
    <label htmlFor="quiet-hours-end">Telegram quiet hours end</label>
    <input id="quiet-hours-end" type="time" value={quietEnd} onChange={event=>setQuietEnd(event.target.value)} disabled={busy}/>
    <label htmlFor="schedule-telegram-destination">Telegram result destination (optional)</label>
    <select id="schedule-telegram-destination" value={telegramDeliveryUser} onChange={event=>setTelegramDeliveryUser(event.target.value)} disabled={busy}>
      <option value="">Do not send Telegram messages</option>
      {telegramUsers.map(userId=><option key={userId} value={userId}>Linked Telegram account {userId}</option>)}
    </select>
    <label htmlFor="schedule-telegram-failure-destination">Telegram failure notification destination (optional)</label>
    <select id="schedule-telegram-failure-destination" value={telegramFailureUser} onChange={event=>setTelegramFailureUser(event.target.value)} disabled={busy}>
      <option value="">Use result destination for failures, if selected</option>
      {telegramUsers.map(userId=><option key={userId} value={userId}>Linked Telegram account {userId}</option>)}
    </select>
    <p>Quiet hours defer authorized Telegram result messages; they do not delay the scheduled run. Set a start and end to cover a local daily window, including overnight windows. Daylight-saving gaps are skipped; repeated wall-clock times run once at the earlier offset. Overlapping runs are skipped. Choose a local model above before scheduling.</p>
    <button type="button" onClick={()=>void create()} disabled={busy||!objective.trim()||!model||!startAt||!timezone.trim()}>Create schedule</button>
    {items.length>0&&<ul>{items.map(item=><li key={item.id}>
      <p>{item.objective}</p>
      <p>{String(item.recurrence.frequency??'schedule')} · Next local time: {String(item.recurrence.next_at??'not scheduled')} ({item.timezone}){item.quiet_hours?` · Telegram quiet hours ${item.quiet_hours}`:''}{item.delivery_destination?` · Telegram result user ${item.delivery_destination.split(':')[2]}`:''}{item.failure_notification_destination?` · Telegram failure user ${item.failure_notification_destination.split(':')[2]}`:''} · {item.enabled?'Enabled':'Disabled'}</p>
      <button type="button" disabled={busy} onClick={()=>void toggle(item)}>{item.enabled?'Pause schedule':'Resume schedule'}</button>
      {editingRecipients?.scheduleId===item.id?<div className="controls">
        <p>Recipient changes apply to future occurrences. Existing executions keep their saved destination.</p>
        {([editingRecipients.resultUser,editingRecipients.failureUser].some(value=>!recipientIsLinkedHere(value)))&&<p role="status">A saved recipient belongs to a different bot or is no longer linked. Choose a linked account or clear that recipient before saving.</p>}
        <label htmlFor={`schedule-edit-result-${item.id}`}>Telegram result destination</label>
        <select id={`schedule-edit-result-${item.id}`} value={editingRecipients.resultUser} onChange={event=>setEditingRecipients({...editingRecipients,resultUser:event.target.value})} disabled={busy}>
          {editingRecipients.resultUser&&!recipientIsLinkedHere(editingRecipients.resultUser)&&<option value={editingRecipients.resultUser}>Previously saved destination {editingRecipients.resultUser} (unavailable)</option>}
          <option value="">Do not send Telegram result messages</option>{telegramUsers.map(userId=><option key={userId} value={`${telegramBotAccountId}:${userId}`} disabled={!telegramBotAccountId}>Linked Telegram account {userId}</option>)}
        </select>
        <label htmlFor={`schedule-edit-failure-${item.id}`}>Telegram failure notification destination</label>
        <select id={`schedule-edit-failure-${item.id}`} value={editingRecipients.failureUser} onChange={event=>setEditingRecipients({...editingRecipients,failureUser:event.target.value})} disabled={busy}>
          {editingRecipients.failureUser&&!recipientIsLinkedHere(editingRecipients.failureUser)&&<option value={editingRecipients.failureUser} disabled>Previously saved destination {editingRecipients.failureUser} (unavailable)</option>}
          <option value="">Use result destination for failures, if selected</option>{telegramUsers.map(userId=><option key={userId} value={`${telegramBotAccountId}:${userId}`} disabled={!telegramBotAccountId}>Linked Telegram account {userId}</option>)}
        </select>
        <button type="button" disabled={busy||[editingRecipients.resultUser,editingRecipients.failureUser].some(value=>!recipientIsLinkedHere(value))} onClick={()=>void saveRecipients()}>Save recipients</button>
        <button type="button" disabled={busy} onClick={()=>setEditingRecipients(undefined)}>Cancel</button>
      </div>:<button type="button" disabled={busy} onClick={()=>setEditingRecipients({scheduleId:item.id,resultUser:recipientKey(item.delivery_destination),failureUser:recipientKey(item.failure_notification_destination)})}>Change Telegram recipients</button>}
      {item.executions.length>0&&<details><summary>Execution history</summary><ul>{item.executions.map(execution=><li key={execution.id}>
        {execution.state}{execution.run_id?` · run ${execution.run_id}`:''}{execution.delivery_evidence?` · Telegram delivery ${execution.delivery_evidence}`:''}{execution.delivery_destination?` · Telegram destination user ${execution.delivery_destination.split(':')[2]}`:''}{execution.delivery_attempt_count!==null?` · send attempts ${execution.delivery_attempt_count}/3`:''}{execution.delivery_next_attempt_at?` · retry after ${execution.delivery_next_attempt_at}`:''} · {execution.created_at}
        {execution.delivery_evidence==='unknown'&&<>
          <p>Check the selected Telegram chat before resolving this uncertain scheduled send.</p>
          <div className="controls">
            <button type="button" disabled={busy} onClick={()=>setScheduleReconcileChoice({scheduleId:item.id,executionId:execution.id,resolution:'delivered'})}>I checked; mark delivered</button>
            <button type="button" disabled={busy||execution.delivery_attempt_count===null||execution.delivery_attempt_count>=3} onClick={()=>setScheduleReconcileChoice({scheduleId:item.id,executionId:execution.id,resolution:'retry'})}>I checked; retry scheduled message</button>
          </div>
          {execution.delivery_attempt_count!==null&&execution.delivery_attempt_count>=3&&<p>The three-attempt limit has been reached; retry is unavailable.</p>}
          {scheduleReconcileChoice?.scheduleId===item.id&&scheduleReconcileChoice.executionId===execution.id&&<div role="alertdialog" aria-label="Confirm scheduled Telegram delivery reconciliation">
            <p>{scheduleReconcileChoice.resolution==='retry'?'Retrying may send a duplicate if Telegram accepted the first message.':'This records that you found the scheduled message in Telegram.'} Confirm that you checked the destination chat.</p>
            <button type="button" disabled={busy} onClick={()=>void reconcileScheduleDelivery()}>Confirm {scheduleReconcileChoice.resolution==='retry'?'retry':'delivered'}</button>
            <button type="button" disabled={busy} onClick={()=>setScheduleReconcileChoice(undefined)}>Cancel</button>
          </div>}
        </>}
      </li>)}</ul></details>}
    </li>)}</ul>}
    <button type="button" disabled={busy} onClick={()=>void refresh()}>Refresh schedules</button>
    {error&&<p role="alert">{error}</p>}
  </section>;
}
