import {test,expect} from '@playwright/test';

async function selectFixtureModel(page: import('@playwright/test').Page){
  await page.getByRole('link',{name:'Model setup'}).click();
  await page.getByLabel('Local model').selectOption('fixture/model');
  await page.getByRole('link',{name:'New objective'}).click();
}

for(const reconnect of [false,true])test(reconnect?'interrupted stream resumes into a saved candidate without repeating partial text':'partial streamed text is displayed without becoming a final candidate',async({page})=>{
  const run={id:'stream-run',objective:'Stream a response',state:'running',revision:2,acceptance_criteria:[]};
  let runState='running';
  const cursors:string[]=[];
  await page.route('http://127.0.0.1:8765/**',async route=>{
    const request=route.request();
    const path=new URL(request.url()).pathname;
    const headers={'access-control-allow-origin':'http://127.0.0.1:5173','access-control-allow-headers':'authorization,content-type,last-event-id','access-control-allow-methods':'GET,POST,OPTIONS'};
    if(request.method()==='OPTIONS')return route.fulfill({status:204,headers});
    if(path==='/models')return route.fulfill({json:[{id:'fixture/model'}],headers});
    if(path.startsWith('/memory'))return route.fulfill({json:[],headers});
    if(path==='/runs/latest-unfinished')return route.fulfill({json:null,headers});
    if(path==='/runs/latest-unfinished')return route.fulfill({json:null,headers});
    if(path==='/runs'&&request.method()==='GET')return route.fulfill({json:{items:[],next_cursor:null},headers});
    if(path.endsWith('/plan'))return route.fulfill({json:{tasks:[],ready:[],blocked:[]},headers});
    if(path==='/runs/stream-run')return route.fulfill({json:{...run,state:runState},headers});
    if(path.endsWith('/events/stream')){
      cursors.push(request.headers()['last-event-id']??'');
      const stateChanged=`id: 1\nevent: run_event\ndata: ${JSON.stringify({sequence:1,event_type:'run.state_changed',payload:{to:'paused'},created_at:'2026-11-03T00:00:00Z'})}\n\n`;
      const partial=`id: ${reconnect?'1':'2'}\nevent: run_event\ndata: ${JSON.stringify({sequence:reconnect?1:2,event_type:'model.delta',payload:{inference_id:'attempt',text:'Unfinished streamed text'},created_at:'2026-11-03T00:00:00Z'})}\n\n`;
      const final=`id: 2\nevent: run_event\ndata: ${JSON.stringify({sequence:2,event_type:'model.message',payload:{message:{content:'Recovered saved answer'}},created_at:'2026-11-03T00:00:01Z'})}\n\n`;
      if(!reconnect)runState='paused';
      return route.fulfill({headers,contentType:'text/event-stream',body:(reconnect?'':stateChanged)+partial+(reconnect?(cursors.length===1?'id: 2\nevent: run_event\ndata: {':final+'event: done\ndata: {}\n\n'):'event: done\ndata: {}\n\n')});
    }
    return route.fulfill({json:run,headers});
  });
  await page.goto('/');
  await page.getByLabel('Engine token').fill('x'.repeat(32));
  await page.getByRole('button',{name:'Connect',exact:true}).click();
  await selectFixtureModel(page);
  await page.getByLabel('Workspace path').fill('C:\\fixture');
  await page.getByLabel('Objective',{exact:true}).fill(run.objective);
  await page.getByRole('button',{name:'Start objective'}).click();
  const runStatus=page.getByRole('status',{name:'Run status'});
  const activity=page.getByRole('list',{name:'Run activity history'});
  const currentActivity=page.getByRole('region',{name:'Current activity'});
  await expect(activity).toHaveAttribute('aria-live','off');
  await expect(currentActivity.getByRole('status')).toHaveAttribute('aria-live','polite');
  await expect(currentActivity.getByRole('status')).toHaveAttribute('aria-atomic','true');
  if(reconnect){
    await expect(runStatus).toHaveText('Run status: Running');
    await expect(page.getByRole('heading',{name:'Candidate answer',exact:true})).toBeVisible();
    await expect(page.getByText('Recovered saved answer',{exact:true})).toBeVisible();
    await expect(page.getByRole('heading',{name:'Partial response',exact:true})).toHaveCount(0);
    expect(cursors).toEqual(['0','1']);
    return;
  }
  await expect(runStatus).toHaveText('Run status: Paused');
  await expect(activity).toContainText('Run is paused');
  const pauseEvent=activity.getByRole('listitem').filter({hasText:'Run is paused'});
  await expect(pauseEvent.locator('time')).toHaveAttribute('datetime','2026-11-03T00:00:00Z');
  await expect(pauseEvent.locator('.run-activity-type')).toHaveText('run.state_changed');
  await expect(currentActivity).toContainText('Run is paused');
  await expect(page.getByRole('heading',{name:'Partial response',exact:true})).toBeVisible();
  await expect(page.getByText('Unfinished streamed text',{exact:true})).toBeVisible();
  await expect(page.getByRole('heading',{name:'Candidate answer',exact:true})).toHaveCount(0);
  await expect(page.getByRole('region',{name:'Final run review'})).toHaveCount(0);
});

test('specialist streamed drafts stay attached to their task and visibly unverified',async({page})=>{
  const run={id:'specialist-stream-run',objective:'Inspect one fixture',state:'running',revision:1,acceptance_criteria:[],model:'fixture/model',limits:{max_iterations:3,max_seconds:60}};
  const task={id:'specialist-stream-run:inspect',local_id:'inspect',objective:'Read the fixture evidence',acceptance_criteria:['Report the file evidence'],input_artifacts:[],expected_outputs:["Candidate result"],dependencies:[],assigned_role:'researcher',model_route:'fixture/model',workspace:'C:\\fixture',permission_scope:['workspace.read'],budgets:{max_iterations:2,max_seconds:30},status:'running'};
  await page.route('http://127.0.0.1:8765/**',async route=>{
    const request=route.request();
    const path=new URL(request.url()).pathname;
    const headers={'access-control-allow-origin':'http://127.0.0.1:5173','access-control-allow-headers':'authorization,content-type,last-event-id','access-control-allow-methods':'GET,POST,OPTIONS'};
    if(request.method()==='OPTIONS')return route.fulfill({status:204,headers});
    if(path==='/models')return route.fulfill({json:[{id:'fixture/model'}],headers});
    if(path.startsWith('/memory'))return route.fulfill({json:[],headers});
    if(path==='/runs'&&request.method()==='GET')return route.fulfill({json:{items:[],next_cursor:null},headers});
    if(path.endsWith('/plan'))return route.fulfill({json:{tasks:[task],ready:[],blocked:[]},headers});
    if(path.endsWith('/events/stream')){
      const events=[
        {sequence:1,event_type:'task.procedure.readiness_checked',payload:{task_id:'inspect',procedure_id:'source-review',procedure_version:3,required_tool_count:2,unavailable_required_tools:[{name:'workspace.write_file',version:'1'}],unavailable_required_permissions:['workspace.write'],ready:false},created_at:'2026-11-06T00:00:00Z'},
        {sequence:2,event_type:'task.model.delta',payload:{task_id:'inspect',inference_id:'inference-1',text:'Unverified streamed specialist draft'},created_at:'2026-11-06T00:00:01Z'},
        {sequence:3,event_type:'task.model.message',payload:{task_id:'inspect',inference_id:'inference-1',message:{role:'assistant',content:'Saved candidate'}},created_at:'2026-11-06T00:00:02Z'},
      ];
      return route.fulfill({headers,contentType:'text/event-stream',body:events.map(event=>`id: ${event.sequence}\nevent: run_event\ndata: ${JSON.stringify(event)}\n\n`).join('')+'event: done\ndata: {}\n\n'});
    }
    return route.fulfill({json:run,headers});
  });
  await page.goto('/');
  await page.getByLabel('Engine token').fill('x'.repeat(32));
  await page.getByRole('button',{name:'Connect',exact:true}).click();
  await selectFixtureModel(page);
  await page.getByLabel('Workspace path').fill('C:\\fixture');
  await page.getByLabel('Objective',{exact:true}).fill(run.objective);
  await page.getByRole('button',{name:'Start objective'}).click();
  const inspect=page.getByRole('article',{name:'Task inspect'});
  const activity=page.getByRole('list',{name:'Run activity history'});
  const readiness=activity.getByRole('listitem').filter({hasText:'source-review v3'});
  await expect(readiness.locator('.run-activity-type')).toHaveText('task.procedure.readiness_checked');
  await expect(readiness).toContainText('workspace.write_file@1, workspace.write');
  await expect(inspect.getByRole('heading',{name:'Partial specialist draft · unverified'})).toBeVisible();
  await expect(inspect.locator('article[aria-live="off"]')).toContainText('Unverified streamed specialist draft');
  await expect(inspect.locator('[role="status"]')).toHaveText('An unverified researcher specialist draft is available and may still change.');
  await expect(inspect).toContainText('Unverified streamed specialist draft');
  await expect(inspect.getByRole('heading',{name:'Partial specialist draft · unverified'})).toBeVisible();
  await expect(inspect.getByRole('heading',{name:'Partial specialist draft · unverified'})).toHaveCount(0,{timeout:2500});
});
test('replays durable command output deltas with UTF-8 boundaries and separate streams',async({page})=>{
  const run={id:'command-output-run',objective:'Run a bounded fixture command',state:'running',revision:1,acceptance_criteria:[]};
  const events=[
    {sequence:1,event_type:'execution.output.delta',payload:{call_id:'command-1',execution_id:'a'.repeat(32),stream:'stdout',chunk_b64:'aGVsbG8g4g==',trust:'untrusted_command_output'},created_at:'2026-11-09T00:00:00Z'},
    {sequence:2,event_type:'execution.output.delta',payload:{call_id:'command-1',execution_id:'a'.repeat(32),stream:'stderr',chunk_b64:'V0FSTg==',trust:'untrusted_command_output'},created_at:'2026-11-09T00:00:01Z'},
    {sequence:3,event_type:'execution.output.delta',payload:{call_id:'command-1',execution_id:'a'.repeat(32),stream:'stdout',chunk_b64:'gqwK',trust:'untrusted_command_output'},created_at:'2026-11-09T00:00:02Z'},
    {sequence:4,event_type:'execution.completed',payload:{call_id:'command-1',execution_id:'a'.repeat(32)},created_at:'2026-11-09T00:00:03Z'},
  ];
  await page.route('http://127.0.0.1:8765/**',async route=>{
    const request=route.request();
    const url=new URL(request.url());
    const path=url.pathname;
    const headers={'access-control-allow-origin':'http://127.0.0.1:5173','access-control-allow-headers':'authorization,content-type,last-event-id','access-control-allow-methods':'GET,POST,OPTIONS'};
    if(request.method()==='OPTIONS')return route.fulfill({status:204,headers});
    if(path==='/models')return route.fulfill({json:[{id:'fixture/model'}],headers});
    if(path.startsWith('/memory'))return route.fulfill({json:[],headers});
    if(path==='/runs'&&request.method()==='GET')return route.fulfill({json:{items:[],next_cursor:null},headers});
    if(path.endsWith('/plan'))return route.fulfill({json:{tasks:[],ready:[],blocked:[]},headers});
    if(path.endsWith('/events/stream'))return route.fulfill({headers,contentType:'text/event-stream',body:events.map(event=>`id: ${event.sequence}\nevent: run_event\ndata: ${JSON.stringify(event)}\n\n`).join('')+'event: done\ndata: {}\n\n'});
    return route.fulfill({json:run,headers});
  });
  await page.goto('/');
  await page.getByLabel('Engine token').fill('x'.repeat(32));
  await page.getByRole('button',{name:'Connect',exact:true}).click();
  await selectFixtureModel(page);
  await page.getByLabel('Workspace path').fill('C:\\fixture');
  await page.getByLabel('Objective',{exact:true}).fill(run.objective);
  await page.getByRole('button',{name:'Start objective'}).click();
  const output=page.getByRole('region',{name:'Streaming command output'});
  await expect(output).toBeVisible();
  await expect(output.getByRole('heading',{name:'stdout'})).toBeVisible();
  await expect(output).toContainText('hello €');
  await expect(output.getByRole('heading',{name:'stderr'})).toBeVisible();
  await expect(output.getByText('WARN',{exact:true})).toBeVisible();
});

test('reveals only bounded public results for completed tools in the activity timeline',async({page})=>{
  const run={id:'tool-result-run',objective:'Inspect a saved tool result',state:'running',revision:1,acceptance_criteria:[]};
  const events=[
    {sequence:1,event_type:'tool.completed',payload:{call_id:'public-call',internal:false},created_at:'2026-11-16T00:00:00Z'},
    {sequence:2,event_type:'tool.completed',payload:{call_id:'private-call',internal:true},created_at:'2026-11-16T00:00:01Z'},
    {sequence:3,event_type:'tool.completed',payload:{call_id:'unspecified-visibility'},created_at:'2026-11-16T00:00:02Z'},
  ];
  const resultRequests:string[]=[];
  await page.route('http://127.0.0.1:8765/**',async route=>{
    const request=route.request();
    const path=new URL(request.url()).pathname;
    const headers={'access-control-allow-origin':'http://127.0.0.1:5173','access-control-allow-headers':'authorization,content-type,last-event-id','access-control-allow-methods':'GET,POST,OPTIONS'};
    if(request.method()==='OPTIONS')return route.fulfill({status:204,headers});
    if(path==='/models')return route.fulfill({json:[{id:'fixture/model'}],headers});
    if(path.startsWith('/memory'))return route.fulfill({json:[],headers});
    if(path==='/runs'&&request.method()==='GET')return route.fulfill({json:{items:[],next_cursor:null},headers});
    if(path.endsWith('/plan'))return route.fulfill({json:{tasks:[],ready:[],blocked:[]},headers});
    if(path==='/runs/tool-result-run')return route.fulfill({json:run,headers});
    if(path.endsWith('/events/stream'))return route.fulfill({headers,contentType:'text/event-stream',body:events.map(event=>`id: ${event.sequence}\nevent: run_event\ndata: ${JSON.stringify(event)}\n\n`).join('')+'event: done\ndata: {}\n\n'});
    if(path==='/runs/tool-result-run/tools/public-call/result'){
      resultRequests.push(path);
      return route.fulfill({json:{result:'Saved fixture result'},headers});
    }
    return route.fulfill({json:run,headers});
  });
  await page.goto('/');
  await page.getByLabel('Engine token').fill('x'.repeat(32));
  await page.getByRole('button',{name:'Connect',exact:true}).click();
  await selectFixtureModel(page);
  await page.getByLabel('Workspace path').fill('C:\\fixture');
  await page.getByLabel('Objective',{exact:true}).fill(run.objective);
  await page.getByRole('button',{name:'Start objective'}).click();
  const activity=page.getByRole('list',{name:'Run activity history'});
  await expect(activity.getByRole('button',{name:'View saved result'})).toHaveCount(1);
  await expect(activity).not.toContainText('Saved fixture result');
  await activity.getByRole('button',{name:'View saved result'}).click();
  await expect(page.getByText('Untrusted saved tool result')).toBeVisible();
  await expect(page.getByText('Saved fixture result',{exact:true})).toBeVisible();
  expect(resultRequests).toEqual(['/runs/tool-result-run/tools/public-call/result']);
});
