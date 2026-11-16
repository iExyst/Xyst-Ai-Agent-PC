import { EngineClient, type Plan } from './api/client.ts';
import { TaskReviewPanel } from './TaskReviewPanel.tsx';
import { TaskRecoveryPanel } from './TaskRecoveryPanel.tsx';
import './plan.css';

function dependencyStages(tasks:Plan['tasks']):{stages:Array<[number,Plan['tasks']]>;valid:boolean}{
  const all=new Map(tasks.map(task=>[task.local_id,task]));
  const remaining=new Map(all);
  const stages:Array<[number,Plan['tasks']]> = [];
  let depth=0;
  while(remaining.size){
    const ready=[...remaining.values()].filter(task=>task.dependencies.every(dependency=>all.has(dependency)&&!remaining.has(dependency)));
    if(!ready.length)return {stages:[[0,tasks]],valid:false};
    stages.push([depth,ready]);
    for(const task of ready)remaining.delete(task.local_id);
    depth+=1;
  }
  return {stages,valid:true};
}

export function PlanPanel({ plan, client, runId, partials, onReviewed, onDispatch, canDispatch }: { plan: Plan; client: EngineClient; runId: string; partials:Record<string,string>; onReviewed: (plan:Plan)=>void; onDispatch:()=>void; canDispatch:boolean }) {
  const completedTasks=plan.tasks.filter(task=>task.status==='completed').length;
  const workingTasks=plan.tasks.filter(task=>task.status==='running').length;
  const reviewTasks=plan.tasks.filter(task=>task.status==='verifying').length;
  const waitingTasks=plan.tasks.filter(task=>task.status==='queued').length;
  const blockedTasks=plan.tasks.filter(task=>task.status==='blocked'||task.status==='failed').length;
  const cancelledTasks=plan.tasks.filter(task=>task.status==='cancelled').length;
  const stageLayout=dependencyStages(plan.tasks);
  return <section aria-label="Task plan"><h2>Plan and specialists</h2>
    {plan.tasks.length === 0 ? <p>No task plan has been saved for this run.</p> : <>
      <section className="plan-progress" aria-label="Specialist progress">
        <div className="plan-progress-heading"><strong>{completedTasks} of {plan.tasks.length} tasks complete</strong></div>
        <p className="sr-only" role="status" aria-live="polite" aria-atomic="true">Specialist progress: {completedTasks} of {plan.tasks.length} tasks complete; {workingTasks} working; {reviewTasks} awaiting review; {waitingTasks} waiting; {blockedTasks} need attention{cancelledTasks>0?`; ${cancelledTasks} cancelled`:''}.</p>
        <progress aria-label="Completed specialist tasks" max={plan.tasks.length} value={completedTasks}/>
        <ul className="plan-progress-counts" aria-label="Task status counts">
          <li><strong>{workingTasks}</strong> working</li>
          <li><strong>{reviewTasks}</strong> awaiting review</li>
          <li><strong>{waitingTasks}</strong> waiting</li>
          <li><strong>{blockedTasks}</strong> need attention</li>
          {cancelledTasks>0&&<li><strong>{cancelledTasks}</strong> cancelled</li>}
        </ul>
        <p>{plan.tasks.length} {plan.tasks.length===1?'task':'tasks'} in this plan.</p>
      </section>
      {plan.ready.length>0&&<button disabled={!canDispatch} onClick={onDispatch}>Run ready specialists</button>}
      <div className="plan-stages">{stageLayout.stages.map(([depth,tasks])=><div className="plan-stage" key={depth} role="group" aria-label={`Dependency stage ${depth+1}`}>
        <div className="plan-stage-heading"><h3>Stage {depth+1}</h3><span>{stageLayout.valid?`${tasks.length} independent ${tasks.length===1?'task':'tasks'}`:'Dependency order unavailable; showing saved task order'}</span></div>
        <div className="plan-tasks">{tasks.map(task => {
        const readiness = task.status === 'queued' ? plan.blocked.includes(task.local_id) ? 'Blocked by a dependency' : plan.ready.includes(task.local_id) ? 'Ready to start' : 'Waiting for dependencies' : task.status;
        return <article key={task.id} aria-label={`Task ${task.local_id}`}>
          <h3>{task.local_id} <small>{task.assigned_role}</small></h3>
          <p>{task.objective}</p>
          <dl><dt>Status</dt><dd>{task.status==='queued'?`queued · ${readiness}`:task.status==='verifying'?'Awaiting review':task.status}</dd>
            <dt>Depends on</dt><dd>{task.dependencies.length ? task.dependencies.join(', ') : 'None'}</dd>
            <dt>Model</dt><dd>{task.model_route ?? 'Unassigned'}</dd>
            <dt>Workspace</dt><dd>{task.workspace ?? 'None'}</dd>
            <dt>Permissions</dt><dd>{task.permission_scope.join(', ') || 'None'}</dd>
            <dt>Budget</dt><dd>{task.budgets.max_iterations} iterations · {task.budgets.max_seconds} seconds<br/>Reported token allowance: {task.budgets.max_total_tokens ?? 'Shared run threshold'} · Output tokens per request: {task.budgets.max_output_tokens ?? 'Run setting'}</dd>
          </dl>
          {task.status==='blocked' && <p>{task.blocked_reason==='token_budget_exhausted_or_unmeasured'
            ? 'The reported token allowance was reached, or usage could not be measured. Start a new objective to change its allowance.'
            : task.blocked_reason==='task_time_budget_exhausted' ? 'This specialist reached its assigned time allowance. Review the task scope or time limits before retrying.'
            : task.blocked_reason==='run_time_budget_exhausted' ? 'The run time allowance expired while this specialist was working. Review the run and task time limits before retrying.'
            : task.blocked_reason==='prompt_count_mismatch' ? 'LM Studio reported a different prompt count than the exact preflight count. The response was discarded before tools ran; measured usage was saved.'
            : task.blocked_reason==='dependency_failed' ? 'A prerequisite task is blocked or failed. This task cannot proceed.'
            : task.blocked_reason==='worker_lost_reconciliation_required' ? 'The specialist worker stopped while this task may have been in flight. Its lease was fenced; inspect the task activity and reconcile its journal before continuing.'
            : task.blocked_reason==='review_evidence_missing' ? 'The reviewer reached its request limit without inspecting every required source, changed artifact, and saved test output. This result cannot be accepted as independent review.'
            : 'Task execution is blocked. Inspect the activity before starting a new objective.'}</p>}
          <h4>Acceptance criteria</h4>
          {task.acceptance_criteria.length ? <ul>{task.acceptance_criteria.map((criterion, index) => <li key={index}>{criterion}</li>)}</ul> : <p>No criteria recorded.</p>}
          <h4>Assigned input artifacts</h4>
          {task.input_artifacts.length ? <ul>{task.input_artifacts.map(id => <li key={id}>{id}</li>)}</ul> : <p>None</p>}
          <h4>Expected outputs</h4>
          {task.expected_outputs.length ? <ul>{task.expected_outputs.map((output, index) => <li key={index}>{output}</li>)}</ul> : <p>None recorded.</p>}
          {partials[task.local_id]&&<><p role="status" className="sr-only">An unverified {task.assigned_role} specialist draft is available and may still change.</p><article aria-live="off"><h4>Partial specialist draft · unverified</h4><p style={{whiteSpace:'pre-wrap'}}>{partials[task.local_id]}</p></article></>}
          {task.status==='verifying'&&<TaskReviewPanel client={client} runId={runId} taskId={task.local_id} onReviewed={onReviewed}/>}
          {task.status==='blocked'&&task.blocked_reason==='worker_lost_reconciliation_required'&&<TaskRecoveryPanel client={client} runId={runId} taskId={task.local_id} onUpdated={onReviewed}/>}
        </article>;
        })}</div>
      </div>)}</div>
    </>}
  </section>;
}
