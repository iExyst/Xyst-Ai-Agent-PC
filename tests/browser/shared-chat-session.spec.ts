import {test,expect} from '@playwright/test';

test('desktop resumes a terminal chat session through coordinator runs and saves the next turn',async({page})=>{
  const stamp='2026-11-08T12:00:00Z';
  const originalMessages=[
    {role:'user',content:'Earlier question',run_id:'terminal-run-1'},
    {role:'assistant',content:'Earlier reply',run_id:'terminal-run-1'},
  ];
  let messages=[...originalMessages];
  const run={id:'desktop-chat-run',objective:'',state:'verifying',revision:1,acceptance_criteria:[],model:'fixture/model',workspace:'C:\\fixture',mode:'observe',created_at:stamp,updated_at:stamp,limits:{max_iterations:8,max_seconds:60,max_total_tokens:null,max_output_tokens:null,pre_inference_token_reservations:false},file_checks:[]};
  let submittedObjective='';
  let savedUserMessage='';
  let savedAssistantMessage='';
  const headers={'access-control-allow-origin':'http://127.0.0.1:5173','access-control-allow-headers':'authorization,content-type,last-event-id','access-control-allow-methods':'GET,POST,PUT,DELETE,OPTIONS'};
  page.on('pageerror',error=>{throw error;});
  await page.route('http://127.0.0.1:8765/**',async route=>{
    const request=route.request();
    const url=new URL(request.url());
    const path=url.pathname;
    if(request.method()==='OPTIONS')return route.fulfill({status:204,headers});
    if(path==='/models')return route.fulfill({json:[{id:'fixture/model'}],headers});
    if(path==='/providers')return route.fulfill({json:[{id:'lm-studio',status:'connected'}],headers});
    if(path==='/providers/lm-studio/config')return route.fulfill({json:{endpoint:'http://127.0.0.1:1234',selected_model:'fixture/model'},headers});
    if(path==='/providers/lm-studio/credential')return route.fulfill({json:{configured:false},headers});
    if(path==='/memory'||path==='/skills'||path==='/specialists/profiles'||path==='/schedules'||path==='/mcp/servers'||path==='/extensions/packages/discover')return route.fulfill({json:[],headers});
    if(path==='/chat/sessions'&&request.method()==='GET')return route.fulfill({json:[{session_id:'terminal-chat',model:'fixture/model',workspace:'C:\\fixture',mode:'observe',updated_at:stamp}],headers});
    if(path==='/chat/sessions/terminal-chat'&&request.method()==='GET')return route.fulfill({json:{session_id:'terminal-chat',model:'fixture/model',workspace:'C:\\fixture',mode:'observe',messages},headers});
    if(path==='/chat/sessions/terminal-chat/turn'&&request.method()==='POST'){
      const turn=request.postDataJSON();savedUserMessage=turn.user_message;savedAssistantMessage=turn.assistant_message;
      messages=[...messages,{role:'user',content:turn.user_message,run_id:turn.run_id},{role:'assistant',content:turn.assistant_message,run_id:turn.run_id}];
      return route.fulfill({json:{session_id:'terminal-chat',model:'fixture/model',workspace:'C:\\fixture',mode:'observe',messages},headers});
    }
    if(path==='/runs'&&request.method()==='GET')return route.fulfill({json:{items:[],next_cursor:null},headers});
    if(path==='/runs'&&request.method()==='POST'){
      submittedObjective=request.postDataJSON().objective;
      return route.fulfill({json:{...run,objective:submittedObjective},headers});
    }
    if(path==='/runs/desktop-chat-run/plan')return route.fulfill({json:{tasks:[],ready:[],blocked:[]},headers});
    if(path==='/runs/desktop-chat-run/events/stream'){
      const answer={sequence:1,event_type:'model.message',payload:{message:{role:'assistant',content:'Desktop continued the saved conversation.'}},created_at:stamp};
      const verifying={sequence:2,event_type:'run.state_changed',payload:{from:'running',to:'verifying'},created_at:stamp};
      return route.fulfill({headers,contentType:'text/event-stream',body:`id: 1\nevent: run_event\ndata: ${JSON.stringify(answer)}\n\nid: 2\nevent: run_event\ndata: ${JSON.stringify(verifying)}\n\nevent: done\ndata: {}\n\n`});
    }
    if(path==='/runs/desktop-chat-run')return route.fulfill({json:{...run,objective:submittedObjective},headers});
    if(path.endsWith('/usage'))return route.fulfill({json:{prompt_tokens:0,completion_tokens:0,total_tokens:0,unmeasured_calls:0,outstanding_reserved_tokens:0,outstanding_reservations:0},headers});
    return route.fulfill({json:[],headers});
  });

  await page.goto('/');
  await page.getByLabel('Engine token').fill('x'.repeat(32));
  await page.getByRole('button',{name:'Connect',exact:true}).click();
  await page.getByLabel('Workspace path').fill('C:\\fixture');
  await page.getByRole('button',{name:'Load shared conversations'}).click();
  await page.getByLabel('Saved terminal and desktop sessions').selectOption('terminal-chat');
  await expect(page.getByText('Earlier reply',{exact:true})).toBeVisible();
  await page.getByLabel('Objective',{exact:true}).fill('Continue it');
  await page.getByRole('button',{name:'Start objective',exact:true}).click();

  await expect.poll(()=>submittedObjective).toContain('Prior turns:\n[["user","Earlier question"],["assistant","Earlier reply"]]');
  await expect.poll(()=>savedUserMessage).toBe('Continue it');
  await expect.poll(()=>savedAssistantMessage).toBe('Desktop continued the saved conversation.');
  await expect(page.getByRole('region',{name:'Active run'}).getByText('Desktop continued the saved conversation.',{exact:true})).toBeVisible();
  await expect(page.getByText('Earlier reply',{exact:true})).toBeVisible();
  await expect(page.getByRole('list',{name:'Saved conversation turns'}).getByText('Continue it',{exact:true})).toBeVisible();
});
