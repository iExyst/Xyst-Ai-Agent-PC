import type {Run} from './api/client.ts';

type Action='pause'|'resume'|'cancel';

export function ActiveRunPanel({run,answer,partial,activity,busy,onControl}:{run:Run;answer:string;partial:string;activity:string[];busy:boolean;onControl:(action:Action)=>void}) {
  function enabled(action:Action) {
    if(action==='resume')return ['paused','blocked'].includes(run.state);
    if(action==='pause')return ['queued','planning','running','verifying','waiting_for_approval'].includes(run.state);
    return !['completed','failed','cancelled'].includes(run.state);
  }
  return <section aria-label="Active run"><h2>Active run <small>{run.state}</small></h2><p>{run.objective}</p>
    <div className="controls">{(['pause','resume','cancel'] as const).map(action=><button key={action} disabled={busy||!enabled(action)} onClick={()=>onControl(action)}>{action}</button>)}</div>
    {answer&&<article><h3>{run.state==='completed'?'Accepted result':'Candidate answer'}</h3><p style={{whiteSpace:'pre-wrap'}}>{answer}</p></article>}
    {partial&&<article><h3>Partial response</h3><p style={{whiteSpace:'pre-wrap'}}>{partial}</p></article>}
    <h3>Activity</h3><ol aria-live="polite">{activity.map((item,index)=><li key={index}>{item}</li>)}</ol>
  </section>;
}
