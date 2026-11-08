import {test,expect} from '@playwright/test';

test('owner reviews an ambiguous MCP write without replaying it',async({page})=>{
  const requests:{method:string;path:string;body?:unknown}[]=[];
  let pending=true;
  const run={id:'mcp-recovery-run',objective:'Update fixture',state:'completed',revision:1,acceptance_criteria:[],model:'fixture/model',limits:{max_iterations:3,max_seconds:60}};
  const item={call_id:'call-1',tool:'mcp_fixture_update',status:'unknown_outcome',updated_at:'2026-11-08T12:00:00Z'};
  await page.route('http://127.0.0.1:8765/**',async route=>{
    const request=route.request();const path=new URL(request.url()).pathname;
    const headers={'access-control-allow-origin':'http://127.0.0.1:5173','access-control-allow-headers':'authorization,content-type,last-event-id','access-control-allow-methods':'GET,POST,OPTIONS'};
    if(request.method()==='OPTIONS')return route.fulfill({status:204,headers});
    const body=request.method()==='POST'?request.postDataJSON():undefined;requests.push({method:request.method(),path,...(body?{body}:{})});
    if(path==='/models')return route.fulfill({json:[{id:'fixture/model'}],headers});
    if(path==='/memory'||path==='/schedules')return route.fulfill({json:[],headers});
    if(path==='/providers')return route.fulfill({json:[{id:'lm-studio',status:'connected'}],headers});
    if(path==='/runs'&&request.method()==='GET')return route.fulfill({json:{items:[],next_cursor:null},headers});
    if(path==='/runs'&&request.method()==='POST')return route.fulfill({json:run,headers});
    if(path.endsWith('/plan'))return route.fulfill({json:{tasks:[],ready:[],blocked:[]},headers});
    if(path.endsWith('/mcp-reconciliations'))return route.fulfill({json:pending?[item]:[],headers});
    if(path.endsWith('/mcp-reconcile')&&request.method()==='POST'){pending=false;return route.fulfill({json:[],headers});}
    if(path.endsWith('/events/stream'))return route.fulfill({headers,contentType:'text/event-stream',body:'event: done\ndata: {}\n\n'});
    return route.fulfill({json:run,headers});
  });
  page.on('dialog',dialog=>void dialog.accept());
  await page.goto('/');
  await page.getByLabel('Engine token').fill('x'.repeat(32));
  await page.getByRole('button',{name:'Connect',exact:true}).click();
  await page.getByLabel('Local model').selectOption('fixture/model');
  await page.getByLabel('Workspace path').fill('C:\\fixture');
  await page.getByLabel('Objective',{exact:true}).fill(run.objective);
  await page.getByRole('button',{name:'Start objective'}).click();
  await expect(page.getByRole('region',{name:'MCP effect reconciliation'})).toBeVisible();
  await expect(page.getByText('hidden server output',{exact:true})).toHaveCount(0);
  await page.getByRole('button',{name:'Confirm effect did not happen'}).click();
  await expect(page.getByRole('region',{name:'MCP effect reconciliation'})).toHaveCount(0);
  expect(requests.find(request=>request.path.endsWith('/mcp-reconcile'))?.body).toEqual({resolution:'not_applied',confirmed_external_effect_reviewed:true});
  expect(requests.filter(request=>request.path.endsWith('/mcp-reconcile'))).toHaveLength(1);
});
