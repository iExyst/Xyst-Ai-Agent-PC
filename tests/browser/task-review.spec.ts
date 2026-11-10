import {test,expect} from '@playwright/test';

test('inspect evidence and explicitly accept a specialist candidate',async({page})=>{
  const run={id:'review-run',objective:'Inspect evidence',state:'verifying',acceptance_criteria:[],revision:3};
  const task={id:'review-run:inspect',local_id:'inspect',objective:'Inspect evidence',acceptance_criteria:['Reports actual file evidence'],input_artifacts:[],expected_outputs:["Candidate result"],dependencies:[],assigned_role:'researcher',model_route:'fixture/model',workspace:'C:\\fixture',permission_scope:['workspace.read'],budgets:{max_iterations:2,max_seconds:60},status:'verifying'};
  let reviewed=false;
  let dispatched=false;
  const graph=()=>({tasks:[{...task,status:reviewed?'completed':'verifying'},{...task,id:'review-run:review',local_id:'review',objective:'Inspect prerequisite evidence independently',dependencies:['inspect'],assigned_role:'reviewer',status:dispatched?'verifying':'queued'}],ready:reviewed&&!dispatched?['review']:[],blocked:[]});
  await page.route('http://127.0.0.1:8765/**',async route=>{
    const request=route.request();
    const headers={'access-control-allow-origin':'http://127.0.0.1:5173','access-control-allow-headers':'authorization,content-type,last-event-id','access-control-allow-methods':'GET,POST,OPTIONS'};
    if(request.method()==='OPTIONS')return route.fulfill({status:204,headers});
    const path=new URL(request.url()).pathname;
    if(path==='/runs'&&request.method()==='GET')return route.fulfill({json:{items:[],next_cursor:null},headers});
    if(path.startsWith('/memory'))return route.fulfill({json:[],headers});
    if(path==='/models')return route.fulfill({json:[{id:'fixture/model'}],headers});
    if(path==='/runs'&&request.method()==='POST')return route.fulfill({json:run,headers});
    if(path.endsWith('/candidate'))return route.fulfill({json:{task_id:'inspect',status:'verifying',candidate:'Found <script>window.reviewExecuted=true</script>',acceptance_criteria:task.acceptance_criteria,evidence:[{call_id:'evidence-1',path:'evidence.txt',sha256:'a'.repeat(64),excerpt:'Actual file evidence',excerpt_truncated:false}]},headers});
    if(path.endsWith('/review')){
      expect(request.postDataJSON()).toEqual({decision:'accept',rationale:'Compared the actual file with the criterion.',confirmed_criteria:[0],evidence_call_ids:['evidence-1'],changes_confirmed:false});
      reviewed=true;
      return route.fulfill({json:graph(),headers});
    }
    if(path.endsWith('/dispatch')){expect(request.method()).toBe('POST');dispatched=true;run.state='running';return route.fulfill({status:202,json:run,headers});}
    if(path.endsWith('/plan'))return route.fulfill({json:graph(),headers});
    if(path.endsWith('/events/stream')){
      let body='event: done\ndata: {}\n\n';
      if(dispatched){run.state='verifying';body=`id: 1\nevent: run_event\ndata: ${JSON.stringify({sequence:1,event_type:'run.state_changed',payload:{to:'verifying'},created_at:'2026-11-03T00:00:00Z'})}\n\n${body}`;}
      return route.fulfill({body,contentType:'text/event-stream',headers});
    }
    return route.fulfill({json:run,headers});
  });
  await page.goto('/');
  await page.getByLabel('Engine token').fill('x'.repeat(32));
  await page.getByRole('button',{name:'Connect',exact:true}).click();
  await page.getByLabel('Local model').selectOption('fixture/model');
  await page.getByLabel('Workspace path').fill('C:\\fixture');
  await page.getByLabel('Objective',{exact:true}).fill(run.objective);
  await page.getByRole('button',{name:'Start objective'}).click();
  await page.getByRole('button',{name:'Review candidate',exact:true}).click();
  const accept=page.getByRole('button',{name:'Accept task result',exact:true});
  await expect(accept).toBeDisabled();
  await expect(page.getByText('Actual file evidence',{exact:true})).toBeVisible();
  await page.getByLabel('Reports actual file evidence',{exact:true}).check();
  await page.getByLabel('Reviewed evidence.txt',{exact:true}).check();
  await expect(accept).toBeDisabled();
  await page.getByLabel('Review rationale',{exact:true}).fill('Compared the actual file with the criterion.');
  expect(await page.evaluate(()=>'reviewExecuted' in window)).toBe(false);
  await page.screenshot({path:'test-results/task-review.png',fullPage:true});
  await accept.click();
  await expect(page.getByRole('article',{name:'Task inspect'})).toContainText('completed');
  expect(reviewed).toBe(true);
  await expect(page.getByRole('heading',{name:'Active run verifying'})).toBeVisible();
  const dispatch=page.getByRole('button',{name:'Run ready specialists',exact:true});
  await expect(dispatch).toBeEnabled();
  await dispatch.click();
  await expect(page.getByRole('article',{name:'Task review'})).toContainText('Awaiting review');
  await expect(page.getByRole('heading',{name:'Active run verifying'})).toBeVisible();
  expect(dispatched).toBe(true);
});

for(const choice of ['current','proposed'] as const){
  test(`conflicted specialist edits require an explicit ${choice} integration choice`,async({page})=>{
    const run={id:`conflict-${choice}-run`,objective:'Review a conflicting private edit',state:'verifying',acceptance_criteria:[],revision:1};
    const task={id:`${run.id}:edit`,local_id:'edit',objective:'Propose a private file edit',acceptance_criteria:['The file evidence supports the proposed change'],input_artifacts:[],expected_outputs:["Candidate result"],dependencies:[],assigned_role:'developer',model_route:'fixture/model',workspace:'C:\\fixture',permission_scope:['workspace.read','workspace.write'],budgets:{max_iterations:4,max_seconds:90},status:'verifying'};
    const baseHash='c'.repeat(64);
    const currentHash='a'.repeat(64);
    const proposedHash='b'.repeat(64);
    let previewRequested=false;
    let reviewed=false;
    const graph=()=>({tasks:[task],ready:[],blocked:[]});
    await page.route('http://127.0.0.1:8765/**',async route=>{
      const request=route.request();
      const headers={'access-control-allow-origin':'http://127.0.0.1:5173','access-control-allow-headers':'authorization,content-type,last-event-id','access-control-allow-methods':'GET,POST,OPTIONS'};
      if(request.method()==='OPTIONS')return route.fulfill({status:204,headers});
      const path=new URL(request.url()).pathname;
      if(path==='/runs'&&request.method()==='GET')return route.fulfill({json:{items:[],next_cursor:null},headers});
      if(path.startsWith('/memory'))return route.fulfill({json:[],headers});
      if(path==='/providers')return route.fulfill({json:[{id:'lm-studio',status:'connected'}],headers});
      if(path==='/providers/lm-studio/config')return route.fulfill({json:{endpoint:'http://127.0.0.1:1234',selected_model:null},headers});
      if(path==='/providers/lm-studio/credential')return route.fulfill({json:{configured:false},headers});
      if(path==='/models')return route.fulfill({json:[{id:'fixture/model'}],headers});
      if(path==='/runs'&&request.method()==='POST')return route.fulfill({json:run,headers});
      if(path.endsWith('/candidate'))return route.fulfill({json:{task_id:'edit',status:'verifying',candidate:'The private candidate proposes a changed file.',acceptance_criteria:task.acceptance_criteria,evidence:[{call_id:'read-1',path:'shared.py',sha256:baseHash,excerpt:'Observed original source',excerpt_truncated:false}],workspace_changes:[{path:'shared.py',original_sha256:baseHash,proposed_sha256:proposedHash,diff:'-return "original"\n+return "proposed"',diff_truncated:false}]},headers});
      if(path.endsWith('/integration-preview')){
        previewRequested=true;
        return route.fulfill({json:{task_id:'edit',changes:[{path:'shared.py',base_sha256:baseHash,current_sha256:currentHash,proposed_sha256:proposedHash,conflict:true,diff:'-return "original"\n+return "proposed"',diff_truncated:false,current_excerpt:'return "current project"',current_excerpt_truncated:false}]},headers});
      }
      if(path.endsWith('/review')){
        expect(request.postDataJSON()).toEqual({decision:'accept',rationale:'I inspected the current and proposed versions.',confirmed_criteria:[0],evidence_call_ids:['read-1'],changes_confirmed:true,resolutions:[{path:'shared.py',expected_current_sha256:currentHash,choice}]});
        reviewed=true;
        task.status='completed';
        return route.fulfill({json:graph(),headers});
      }
      if(path.endsWith('/plan'))return route.fulfill({json:graph(),headers});
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
    await page.getByRole('button',{name:'Review candidate',exact:true}).click();
    const integrate=page.getByRole('button',{name:'Integrate changes and accept task',exact:true});
    await expect(integrate).toBeDisabled();
    await page.getByRole('button',{name:'Check current project for integration'}).click();
    await expect(page.getByText(/Current project checked\./)).toBeVisible();
    expect(previewRequested).toBe(true);
    await expect(page.getByText('Conflict: shared.py',{exact:true})).toBeVisible();
    await expect(integrate).toBeDisabled();
    await page.getByLabel('Resolve shared.py for edit').selectOption(choice);
    await expect(integrate).toBeDisabled();
    await page.getByLabel('I reviewed the file changes and authorize these integration choices').check();
    await page.getByLabel('The file evidence supports the proposed change',{exact:true}).check();
    await page.getByLabel('Reviewed shared.py',{exact:true}).check();
    await page.getByLabel('Review rationale',{exact:true}).fill('I inspected the current and proposed versions.');
    await expect(integrate).toBeEnabled();
    await integrate.click();
    expect(reviewed).toBe(true);
    await expect(page.getByRole('article',{name:'Task edit'})).toContainText('completed');
  });
}
