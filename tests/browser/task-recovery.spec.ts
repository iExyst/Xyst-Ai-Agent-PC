import {test,expect} from '@playwright/test';

test('explain a fenced specialist after its worker is lost',async({page})=>{
  const run={id:'recovery-run',objective:'Inspect the fixture',state:'verifying',acceptance_criteria:[],revision:1};
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
});
