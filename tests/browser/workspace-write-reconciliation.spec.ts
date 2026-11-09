import {test,expect} from '@playwright/test';

test('owner confirms an interrupted workspace write only after reviewing the destination',async({page})=>{
  const requests:{method:string;path:string;body?:unknown}[]=[];
  let pending=true;
  const run={id:'workspace-recovery-run',objective:'Create a fixture file',state:'blocked',revision:1,acceptance_criteria:[],model:'fixture/model',limits:{max_iterations:3,max_seconds:60}};
  const item={call_id:'create-call',operation:'create',path:'new.txt',expected_sha256:null,desired_sha256:'d'.repeat(64)};
  await page.route('http://127.0.0.1:8765/**',async route=>{
    const request=route.request();const path=new URL(request.url()).pathname;
    const headers={'access-control-allow-origin':'http://127.0.0.1:5173','access-control-allow-headers':'authorization,content-type,last-event-id','access-control-allow-methods':'GET,POST,OPTIONS'};
    if(request.method()==='OPTIONS')return route.fulfill({status:204,headers});
    const body=request.method()==='POST'?request.postDataJSON():undefined;
    requests.push({method:request.method(),path,...(body?{body}:{})});
    if(path==='/models')return route.fulfill({json:[{id:'fixture/model'}],headers});
    if(path==='/memory'||path==='/schedules')return route.fulfill({json:[],headers});
    if(path==='/providers')return route.fulfill({json:[{id:'lm-studio',status:'connected'}],headers});
    if(path==='/runs'&&request.method()==='GET')return route.fulfill({json:{items:[],next_cursor:null},headers});
    if(path==='/runs'&&request.method()==='POST')return route.fulfill({json:run,headers});
    if(path.endsWith('/plan'))return route.fulfill({json:{tasks:[],ready:[],blocked:[]},headers});
    if(path.endsWith('/mcp-reconciliations'))return route.fulfill({json:[],headers});
    if(path.endsWith('/workspace-write-reconciliations')&&request.method()==='GET')return route.fulfill({json:pending?[item]:[],headers});
    if(path.endsWith('/workspace-write-reconcile')&&request.method()==='POST'){
      pending=false;
      return route.fulfill({json:[],headers});
    }
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
  const panel=page.getByRole('region',{name:'Workspace write recovery'});
  await expect(panel).toBeVisible();
  await panel.getByRole('button',{name:'Inspect uncertain workspace writes'}).click();
  await expect(panel).toContainText('new.txt');
  await expect(panel).toContainText('Desired SHA-256');
  await panel.getByRole('button',{name:'Confirm write did not apply'}).click();
  await expect(panel).toContainText('The interrupted write was recorded as not applied.');
  expect(requests.filter(request=>request.path.endsWith('/workspace-write-reconcile'))).toHaveLength(1);
  expect(requests.find(request=>request.path.endsWith('/workspace-write-reconcile'))?.body).toEqual({confirmed_workspace_inspected:true});
  expect(requests.filter(request=>request.path.endsWith('/resume'))).toHaveLength(0);
});
