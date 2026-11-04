import {test,expect} from '@playwright/test';

for(const validationRequired of [false,true])test(`private edits require preview, conflict choice, authorization and validation=${validationRequired}`,async({page})=>{
  const run={id:'branch-run',objective:'Review private edits',state:'verifying',acceptance_criteria:[],revision:3};
  const task={id:'branch-run:edit',local_id:'edit',objective:'Propose an edit',acceptance_criteria:['Review actual changes'],dependencies:[],assigned_role:'developer',model_route:'fixture/model',workspace:'C:\\fixture',permission_scope:['workspace.read','workspace.write'],budgets:{max_iterations:4,max_seconds:60},status:'verifying'};
  let accepted=false;let previewed=false;
  const change={path:'shared.txt',base_sha256:'a'.repeat(64),proposed_sha256:'b'.repeat(64),diff:'--- a/shared.txt\n+++ b/shared.txt\n-original\n+<script>window.branchExecuted=true</script>',diff_truncated:false};
  const validation={argv:['/usr/bin/python3','validate.py'],snapshot_sha256:'d'.repeat(64),matches_current_private_tree:true,succeeded:true,exit_code:0,termination_reason:'finished',stdout_excerpt:'Actual private validation passed',stderr_excerpt:'',stdout_artifact_id:'stdout',stderr_artifact_id:'stderr'};
  await page.route('http://127.0.0.1:8765/**',async route=>{
    const request=route.request();const pathname=new URL(request.url()).pathname;
    const headers={'access-control-allow-origin':'http://127.0.0.1:5173','access-control-allow-headers':'authorization,content-type,last-event-id','access-control-allow-methods':'GET,POST,OPTIONS'};
    if(request.method()==='OPTIONS')return route.fulfill({status:204,headers});
    if(pathname==='/models')return route.fulfill({json:[{id:'fixture/model'}],headers});
    if(pathname==='/memory'||pathname==='/skills')return route.fulfill({json:[],headers});
    if(pathname==='/runs')return route.fulfill({json:request.method()==='POST'?run:{items:[],next_cursor:null},headers});
    if(pathname.endsWith('/plan'))return route.fulfill({json:{tasks:[{...task,status:accepted?'completed':'verifying'}],ready:[],blocked:[]},headers});
    if(pathname.endsWith('/candidate'))return route.fulfill({json:{task_id:'edit',status:'verifying',candidate:'Private edit proposal',acceptance_criteria:task.acceptance_criteria,evidence:[{call_id:'read-1',path:'shared.txt',sha256:'a'.repeat(64),excerpt:'original',excerpt_truncated:false}],workspace_changes:[change],validation_required:validationRequired,validation_evidence:validationRequired?[{...validation,call_id:'stale',matches_current_private_tree:false},{...validation,call_id:'failed',succeeded:false,exit_code:1},{...validation,call_id:'good'}]:[]},headers});
    if(pathname.startsWith('/artifacts/'))return route.fulfill({body:'Retained exact private output',headers});
    if(pathname.endsWith('/integration-preview')){previewed=true;return route.fulfill({json:{task_id:'edit',changes:[{...change,current_sha256:'c'.repeat(64),current_excerpt:'External current version',current_excerpt_truncated:false,conflict:true}]},headers});}
    if(pathname.endsWith('/review')){
      expect(previewed).toBe(true);
      expect(request.postDataJSON()).toEqual({decision:'accept',rationale:'Compared the complete diff and deliberately chose the proposed version.',confirmed_criteria:[0],evidence_call_ids:['read-1'],changes_confirmed:true,...(validationRequired?{validation_call_ids:['good']}:{}),resolutions:[{path:'shared.txt',expected_current_sha256:'c'.repeat(64),choice:'proposed'}]});
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
  await expect(accept).toBeDisabled();await authorize.check();
  if(validationRequired){await expect(accept).toBeDisabled();await expect(page.getByLabel('Reviewed validation stale',{exact:true})).toBeDisabled();await expect(page.getByLabel('Reviewed validation failed',{exact:true})).toBeDisabled();await page.getByRole('button',{name:'Inspect saved stdout',exact:true}).last().click();await expect(page.getByText('Retained exact private output',{exact:true})).toHaveCount(3);await page.getByLabel('Reviewed validation good',{exact:true}).check();}
  await expect(accept).toBeEnabled();await accept.click();
  await expect(page.getByRole('article',{name:'Task edit'})).toContainText('completed');
});
