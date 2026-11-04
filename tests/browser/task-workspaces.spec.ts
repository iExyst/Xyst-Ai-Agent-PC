import {test,expect} from '@playwright/test';

test('private edits require a fresh integration preview, conflict choice and explicit authorization',async({page})=>{
  const run={id:'branch-run',objective:'Review private edits',state:'verifying',acceptance_criteria:[],revision:3};
  const task={id:'branch-run:edit',local_id:'edit',objective:'Propose an edit',acceptance_criteria:['Review actual changes'],dependencies:[],assigned_role:'developer',model_route:'fixture/model',workspace:'C:\\fixture',permission_scope:['workspace.read','workspace.write'],budgets:{max_iterations:4,max_seconds:60},status:'verifying'};
  let accepted=false;let previewed=false;
  const change={path:'shared.txt',base_sha256:'a'.repeat(64),proposed_sha256:'b'.repeat(64),diff:'--- a/shared.txt\n+++ b/shared.txt\n-original\n+<script>window.branchExecuted=true</script>',diff_truncated:false};
  await page.route('http://127.0.0.1:8765/**',async route=>{
    const request=route.request();const pathname=new URL(request.url()).pathname;
    const headers={'access-control-allow-origin':'http://127.0.0.1:5173','access-control-allow-headers':'authorization,content-type,last-event-id','access-control-allow-methods':'GET,POST,OPTIONS'};
    if(request.method()==='OPTIONS')return route.fulfill({status:204,headers});
    if(pathname==='/models')return route.fulfill({json:[{id:'fixture/model'}],headers});
    if(pathname==='/memory'||pathname==='/skills')return route.fulfill({json:[],headers});
    if(pathname==='/runs')return route.fulfill({json:request.method()==='POST'?run:{items:[],next_cursor:null},headers});
    if(pathname.endsWith('/plan'))return route.fulfill({json:{tasks:[{...task,status:accepted?'completed':'verifying'}],ready:[],blocked:[]},headers});
    if(pathname.endsWith('/candidate'))return route.fulfill({json:{task_id:'edit',status:'verifying',candidate:'Private edit proposal',acceptance_criteria:task.acceptance_criteria,evidence:[{call_id:'read-1',path:'shared.txt',sha256:'a'.repeat(64),excerpt:'original',excerpt_truncated:false}],workspace_changes:[change]},headers});
    if(pathname.endsWith('/integration-preview')){previewed=true;return route.fulfill({json:{task_id:'edit',changes:[{...change,current_sha256:'c'.repeat(64),current_excerpt:'External current version',current_excerpt_truncated:false,conflict:true}]},headers});}
    if(pathname.endsWith('/review')){
      expect(previewed).toBe(true);
      expect(request.postDataJSON()).toEqual({decision:'accept',rationale:'Compared the complete diff and deliberately chose the proposed version.',confirmed_criteria:[0],evidence_call_ids:['read-1'],changes_confirmed:true,resolutions:[{path:'shared.txt',expected_current_sha256:'c'.repeat(64),choice:'proposed'}]});
      accepted=true;return route.fulfill({json:{tasks:[{...task,status:'completed'}],ready:[],blocked:[]},headers});
    }
    if(pathname.endsWith('/events/stream'))return route.fulfill({body:'event: done\ndata: {}\n\n',contentType:'text/event-stream',headers});
    return route.fulfill({json:run,headers});
  });
  await page.goto('/');await page.getByLabel('Engine token').fill('x'.repeat(32));await page.getByRole('button',{name:'Connect',exact:true}).click();
  await page.getByLabel('Local model').selectOption('fixture/model');await page.getByLabel('Workspace path').fill('C:\\fixture');await page.getByLabel('Objective',{exact:true}).fill(run.objective);await page.getByRole('button',{name:'Start objective'}).click();
  await page.getByRole('button',{name:'Review candidate',exact:true}).click();
  const accept=page.getByRole('button',{name:'Integrate changes and accept task',exact:true});
  await page.getByLabel('Review actual changes',{exact:true}).check();await page.getByLabel('Reviewed shared.txt',{exact:true}).check();await page.getByLabel('Review rationale',{exact:true}).fill('Compared the complete diff and deliberately chose the proposed version.');
  await expect(accept).toBeDisabled();
  await expect(page.getByText(/window.branchExecuted=true/)).toBeVisible();expect(await page.evaluate(()=>('branchExecuted' in window))).toBe(false);
  await page.getByRole('button',{name:'Check current project for integration',exact:true}).click();
  await expect(page.getByText('External current version',{exact:true})).toBeVisible();
  const authorize=page.getByLabel('I reviewed the file changes and authorize these integration choices');await expect(authorize).toBeDisabled();
  await page.getByRole('combobox',{name:'Resolve shared.txt for edit',exact:true}).selectOption('proposed');
  await expect(accept).toBeDisabled();await authorize.check();await expect(accept).toBeEnabled();await accept.click();
  await expect(page.getByRole('article',{name:'Task edit'})).toContainText('completed');
});
