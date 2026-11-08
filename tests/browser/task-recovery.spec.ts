import {test,expect} from '@playwright/test';

test('explain a fenced specialist after its worker is lost',async({page})=>{
  const run={id:'recovery-run',objective:'Inspect the fixture',state:'blocked',acceptance_criteria:[],revision:1};
  const task={id:'recovery-run:inspect',local_id:'inspect',objective:'Inspect the fixture',acceptance_criteria:['Report actual evidence'],dependencies:[],assigned_role:'researcher',model_route:'fixture/model',workspace:'C:\\fixture',permission_scope:['workspace.read'],budgets:{max_iterations:2,max_seconds:60},status:'blocked',blocked_reason:'worker_lost_reconciliation_required'};
  const plan={tasks:[task],ready:[],blocked:[]};
  await page.route('http://127.0.0.1:8765/**',async route=>{
    const request=route.request();
    const headers={'access-control-allow-origin':'http://127.0.0.1:5173','access-control-allow-headers':'authorization,content-type,last-event-id','access-control-allow-methods':'GET,POST,OPTIONS'};
    if(request.method()==='OPTIONS')return route.fulfill({status:204,headers});
    const path=new URL(request.url()).pathname;
    if(path==='/runs'&&request.method()==='GET')return route.fulfill({json:{items:[],next_cursor:null},headers});
    if(path.startsWith('/memory'))return route.fulfill({json:[],headers});
    if(path==='/models')return route.fulfill({json:[{id:'fixture/model'}],headers});
    if(path==='/runs'&&request.method()==='POST')return route.fulfill({json:run,headers});
    if(path.endsWith('/recovery')&&request.method()==='GET')return route.fulfill({json:{task_id:'inspect',status:'blocked',candidate:null,candidate_reviewable:false,saved_tool_results:0,pending_model_tool_calls:[{call_id:'call-1',name:'workspace_read_file'}],unresolved_tool_calls:0,recoverable_commands:[],recoverable_task_actions:[],safe_to_resume:false,consumed_iterations:1,run_measured_tokens:15,reserved_tokens:0},headers});
    if(path.endsWith('/recovery')&&request.method()==='POST')return route.fulfill({status:202,json:{...plan,tasks:[{...task,status:'failed',blocked_reason:null}]},headers});
    if(path.endsWith('/plan'))return route.fulfill({json:plan,headers});
    if(path.endsWith('/events/stream'))return route.fulfill({body:'event: done\ndata: {}\n\n',contentType:'text/event-stream',headers});
    return route.fulfill({json:run,headers});
  });
  await page.goto('/');
  await page.getByLabel('Engine token').fill('x'.repeat(32));
  await page.getByRole('button',{name:'Connect',exact:true}).click();
  await page.getByLabel('Local model').selectOption('fixture/model');
  await page.getByLabel('Workspace path').fill('C:\\fixture');
  await page.getByLabel('Objective',{exact:true}).fill(run.objective);
  await page.getByRole('button',{name:'Start objective'}).click();
  const card=page.getByRole('article',{name:'Task inspect'});
  await expect(card).toContainText('blocked');
  await expect(card).toContainText('The specialist worker stopped while this task may have been in flight. Its lease was fenced; inspect the task activity and reconcile its journal before continuing.');
  await expect(card.getByRole('button',{name:'Run ready specialists'})).toHaveCount(0);
  await card.getByRole('button',{name:'Inspect saved task journal'}).click();
  await expect(card).toContainText('workspace_read_file');
  await expect(card.getByRole('button',{name:'Resume from saved results'})).toBeDisabled();
  await card.getByRole('button',{name:'Mark task failed'}).click();
  await expect(card).toContainText('failed');
});

test('offer a clean restart when the specialist died before saving a reply',async({page})=>{
  const run={id:'fresh-recovery-run',objective:'Inspect the fixture',state:'blocked',acceptance_criteria:[],revision:1};
  const task={id:'fresh-recovery-run:inspect',local_id:'inspect',objective:'Inspect the fixture',acceptance_criteria:['Report actual evidence'],dependencies:[],assigned_role:'researcher',model_route:'fixture/model',workspace:'C:\\fixture',permission_scope:['workspace.read'],budgets:{max_iterations:2,max_seconds:60},status:'blocked',blocked_reason:'worker_lost_reconciliation_required'};
  const plan={tasks:[task],ready:[],blocked:[]};
  await page.route('http://127.0.0.1:8765/**',async route=>{
    const request=route.request();
    const headers={'access-control-allow-origin':'http://127.0.0.1:5173','access-control-allow-headers':'authorization,content-type,last-event-id','access-control-allow-methods':'GET,POST,OPTIONS'};
    if(request.method()==='OPTIONS')return route.fulfill({status:204,headers});
    const path=new URL(request.url()).pathname;
    if(path==='/runs'&&request.method()==='GET')return route.fulfill({json:{items:[],next_cursor:null},headers});
    if(path.startsWith('/memory'))return route.fulfill({json:[],headers});
    if(path==='/models')return route.fulfill({json:[{id:'fixture/model'}],headers});
    if(path==='/runs'&&request.method()==='POST')return route.fulfill({json:run,headers});
    if(path.endsWith('/recovery')&&request.method()==='GET')return route.fulfill({json:{task_id:'inspect',status:'blocked',candidate:null,candidate_reviewable:false,saved_tool_results:0,pending_model_tool_calls:[],unresolved_tool_calls:0,recoverable_commands:[],recoverable_task_actions:[],safe_to_resume:true,resume_from_start:true,consumed_iterations:1,run_measured_tokens:0,reserved_tokens:0},headers});
    if(path.endsWith('/recovery')&&request.method()==='POST')return route.fulfill({status:202,json:{...plan,tasks:[{...task,status:'queued',blocked_reason:null}]},headers});
    if(path.endsWith('/plan'))return route.fulfill({json:plan,headers});
    if(path.endsWith('/events/stream'))return route.fulfill({body:'event: done\ndata: {}\n\n',contentType:'text/event-stream',headers});
    return route.fulfill({json:run,headers});
  });
  await page.goto('/');
  await page.getByLabel('Engine token').fill('x'.repeat(32));
  await page.getByRole('button',{name:'Connect',exact:true}).click();
  await page.getByLabel('Local model').selectOption('fixture/model');
  await page.getByLabel('Workspace path').fill('C:\\fixture');
  await page.getByLabel('Objective',{exact:true}).fill(run.objective);
  await page.getByRole('button',{name:'Start objective'}).click();
  const card=page.getByRole('article',{name:'Task inspect'});
  await card.getByRole('button',{name:'Inspect saved task journal'}).click();
  await expect(card).toContainText('No model reply or tool result was saved for this task. Resuming starts again from the task objective; the prior inference attempt still counts against its budgets.');
  await card.getByRole('button',{name:'Resume from saved results'}).click();
  await expect(card).toContainText('queued');
});

test('attach a command result that was saved before the specialist stopped',async({page})=>{
  const run={id:'saved-command-recovery',objective:'Run the saved check',state:'blocked',acceptance_criteria:[],revision:1};
  const task={id:'saved-command-recovery:check',local_id:'check',objective:'Run the saved check',acceptance_criteria:['Report the check result'],dependencies:[],assigned_role:'researcher',model_route:'fixture/model',workspace:'C:\\fixture',permission_scope:['workspace.read','workspace.command'],budgets:{max_iterations:2,max_seconds:60},status:'blocked',blocked_reason:'worker_lost_reconciliation_required'};
  const plan={tasks:[task],ready:[],blocked:[]};
  const saved={task_id:'check',status:'blocked',candidate:null,candidate_reviewable:false,saved_tool_results:0,pending_model_tool_calls:[{call_id:'model-call-1',name:'workspace_run_command'},{call_id:'model-call-2',name:'workspace_read_file'}],unresolved_tool_calls:0,recoverable_commands:[{model_call_id:'model-call-1',journal_call_id:'journal-call-1',argv:['printf','saved'],state:'completed'}],recoverable_task_actions:[{model_call_id:'model-call-2',journal_call_id:'journal-file-2',tool_name:'workspace.read_file'}],safe_to_resume:false,consumed_iterations:1,run_measured_tokens:15,reserved_tokens:0};
  let attachedCommand=false;
  let attachedFile=false;
  await page.route('http://127.0.0.1:8765/**',async route=>{
    const request=route.request();
    const headers={'access-control-allow-origin':'http://127.0.0.1:5173','access-control-allow-headers':'authorization,content-type,last-event-id','access-control-allow-methods':'GET,POST,OPTIONS'};
    if(request.method()==='OPTIONS')return route.fulfill({status:204,headers});
    const path=new URL(request.url()).pathname;
    if(path==='/runs'&&request.method()==='GET')return route.fulfill({json:{items:[],next_cursor:null},headers});
    if(path.startsWith('/memory'))return route.fulfill({json:[],headers});
    if(path==='/models')return route.fulfill({json:[{id:'fixture/model'}],headers});
    if(path==='/runs'&&request.method()==='POST')return route.fulfill({json:run,headers});
    if(path.endsWith('/recovery/commands/model-call-1')&&request.method()==='POST'){
      attachedCommand=true;
      return route.fulfill({json:{...saved,saved_tool_results:1,pending_model_tool_calls:[saved.pending_model_tool_calls[1]],recoverable_commands:[],safe_to_resume:false},headers});
    }
    if(path.endsWith('/recovery/actions/model-call-2')&&request.method()==='POST'){
      attachedFile=true;
      return route.fulfill({json:{...saved,saved_tool_results:2,pending_model_tool_calls:[],recoverable_commands:[],recoverable_task_actions:[],safe_to_resume:true},headers});
    }
    if(path.endsWith('/recovery')&&request.method()==='GET')return route.fulfill({json:saved,headers});
    if(path.endsWith('/plan'))return route.fulfill({json:plan,headers});
    if(path.endsWith('/events/stream'))return route.fulfill({body:'event: done\\ndata: {}\\n\\n',contentType:'text/event-stream',headers});
    return route.fulfill({json:run,headers});
  });
  await page.goto('/');
  await page.getByLabel('Engine token').fill('x'.repeat(32));
  await page.getByRole('button',{name:'Connect',exact:true}).click();
  await page.getByLabel('Local model').selectOption('fixture/model');
  await page.getByLabel('Workspace path').fill('C:\\fixture');
  await page.getByLabel('Objective',{exact:true}).fill(run.objective);
  await page.getByRole('button',{name:'Start objective'}).click();
  const card=page.getByRole('article',{name:'Task check'});
  await card.getByRole('button',{name:'Inspect saved task journal'}).click();
  await expect(card).toContainText('The command result is already saved; recovery attaches it to the pending specialist call.');
  await card.getByRole('button',{name:'Recover saved command'}).click();
  await expect.poll(()=>attachedCommand).toBe(true);
  await expect(card).toContainText('1 saved tool results');
  await expect(card.getByRole('button',{name:'Resume from saved results'})).toBeDisabled();
  await card.getByRole('button',{name:'Attach saved file result'}).click();
  await expect.poll(()=>attachedFile).toBe(true);
  await expect(card).toContainText('2 saved tool results');
  await expect(card.getByRole('button',{name:'Resume from saved results'})).toBeEnabled();
});
