import {test,expect} from '@playwright/test';
import {selectLocalModel} from './navigation';

test('explicit final review completes a run after objective and criterion confirmation',async({page})=>{
  const run={id:'final-run',objective:'Produce the checked result',state:'verifying',revision:3,acceptance_criteria:['The result matches the evidence']};
  let accepted=false;
  await page.route('http://127.0.0.1:8765/**',async route=>{
    const request=route.request();
    const headers={'access-control-allow-origin':'http://127.0.0.1:5173','access-control-allow-headers':'authorization,content-type,last-event-id','access-control-allow-methods':'GET,POST,OPTIONS'};
    if(request.method()==='OPTIONS')return route.fulfill({status:204,headers});
    const path=new URL(request.url()).pathname;
    if(path==='/runs'&&request.method()==='GET')return route.fulfill({json:{items:[],next_cursor:null},headers});
    if(path.startsWith('/memory'))return route.fulfill({json:[],headers});
    if(path==='/runs/latest-unfinished')return route.fulfill({json:null,headers});
    if(path==='/models')return route.fulfill({json:[{id:'fixture/model'}],headers});
    if(path.endsWith('/plan'))return route.fulfill({json:{tasks:[],ready:[],blocked:[]},headers});
    if(path.endsWith('/review')){
      expect(request.postDataJSON()).toEqual({decision:'accept',expected_revision:3,rationale:'Compared the candidate with the objective and evidence.',objective_confirmed:true,confirmed_criteria:[0]});
      accepted=true;run.state='completed';run.revision=4;
      return route.fulfill({json:run,headers});
    }
    if(path.endsWith('/events/stream'))return route.fulfill({headers,contentType:'text/event-stream',body:`id: 1\nevent: run_event\ndata: ${JSON.stringify({sequence:1,event_type:'model.message',payload:{message:{role:'assistant',content:'Candidate result with evidence.'}},created_at:'2026-11-03T00:00:00Z'})}\n\nevent: done\ndata: {}\n\n`});
    return route.fulfill({json:run,headers});
  });
  await page.goto('/');
  await page.getByLabel('Engine token').fill('x'.repeat(32));
  await page.getByRole('button',{name:'Connect',exact:true}).click();
  await selectLocalModel(page,'fixture/model');
  await page.getByLabel('Workspace path').fill('C:\\fixture');
  await page.getByLabel('Objective',{exact:true}).fill(run.objective);
  await page.getByRole('button',{name:'Start objective'}).click();
  const panel=page.getByRole('region',{name:'Final run review'});
  const accept=panel.getByRole('button',{name:'Accept and complete run',exact:true});
  await expect(accept).toBeDisabled();
  await page.getByText('Candidate result with evidence.',{exact:true}).waitFor();
  await panel.getByLabel('I reviewed the objective, result, and available evidence',{exact:true}).check();
  await panel.getByLabel('Confirm criterion: The result matches the evidence',{exact:true}).check();
  await expect(accept).toBeDisabled();
  await panel.getByLabel('Final review rationale').fill('Compared the candidate with the objective and evidence.');
  await expect(accept).toBeEnabled();
  await page.screenshot({path:'test-results/final-review.png',fullPage:true});
  await accept.click();
  await expect(page.getByRole('heading',{name:'Active run completed'})).toBeVisible();
  await expect(panel).toHaveCount(0);
  expect(accepted).toBe(true);
});

test('final review requires selected validation for the current integrated project',async({page})=>{
  const run={id:'integrated-validation-run',objective:'Validate the integrated project',state:'verifying',revision:7,acceptance_criteria:['The project check passes']};
  const evidence={call_id:'check-1',argv:['/usr/bin/python3','-m','pytest','-q'],snapshot_sha256:'a'.repeat(64),matches_current_project:true,succeeded:true,exit_code:0,termination_reason:'finished',stdout_excerpt:'3 passed',stderr_excerpt:'',stdout_artifact_id:'out',stderr_artifact_id:'err'};
  let accepted=false;
  await page.route('http://127.0.0.1:8765/**',async route=>{
    const request=route.request();const headers={'access-control-allow-origin':'http://127.0.0.1:5173','access-control-allow-headers':'authorization,content-type,last-event-id','access-control-allow-methods':'GET,POST,OPTIONS'};
    if(request.method()==='OPTIONS')return route.fulfill({status:204,headers});
    const path=new URL(request.url()).pathname;
    if(path==='/runs/latest-unfinished')return route.fulfill({json:null,headers});
    if(path==='/runs'&&request.method()==='GET')return route.fulfill({json:{items:[],next_cursor:null},headers});
    if(path.startsWith('/memory'))return route.fulfill({json:[],headers});
    if(path==='/models')return route.fulfill({json:[{id:'fixture/model'}],headers});
    if(path.endsWith('/plan'))return route.fulfill({json:{tasks:[],ready:[],blocked:[]},headers});
    if(path.endsWith('/validation'))return route.fulfill({json:{available:true,required:true,current_snapshot_sha256:'a'.repeat(64),suggestions:[{label:'Python tests (pytest)',rationale:'pyproject.toml was found.',argv:['/usr/bin/python3','-I','-m','pytest','-q']}],evidence:[evidence]},headers});
    if(path.endsWith('/review')){expect(request.postDataJSON()).toEqual({decision:'accept',expected_revision:7,rationale:'The integrated check passed on the current project.',objective_confirmed:true,confirmed_criteria:[0],validation_call_ids:['check-1']});accepted=true;run.state='completed';run.revision=8;return route.fulfill({json:run,headers});}
    if(path.endsWith('/events/stream'))return route.fulfill({headers,contentType:'text/event-stream',body:`id: 1\nevent: run_event\ndata: ${JSON.stringify({sequence:1,event_type:'model.message',payload:{message:{role:'assistant',content:'The integrated project passed.'}},created_at:'2026-11-03T00:00:00Z'})}\n\nevent: done\ndata: {}\n\n`});
    return route.fulfill({json:run,headers});
  });
  await page.goto('/');await page.getByLabel('Engine token').fill('x'.repeat(32));await page.getByRole('button',{name:'Connect',exact:true}).click();await selectLocalModel(page,'fixture/model');await page.getByLabel('Workspace path').fill('C:\\fixture');await page.getByLabel('Objective',{exact:true}).fill(run.objective);await page.getByRole('button',{name:'Start objective'}).click();
  const panel=page.getByRole('region',{name:'Final run review'});await page.getByText('The integrated project passed.',{exact:true}).waitFor();await panel.getByRole('button',{name:/Python tests \(pytest\):/}).click();await expect(panel.getByLabel('Command arguments as a JSON string array')).toHaveValue('["/usr/bin/python3","-I","-m","pytest","-q"]');await panel.getByLabel('I reviewed the objective, result, and available evidence',{exact:true}).check();await panel.getByLabel('Confirm criterion: The project check passes',{exact:true}).check();await panel.getByLabel('Final review rationale').fill('The integrated check passed on the current project.');
  const accept=panel.getByRole('button',{name:'Accept and complete run',exact:true});await expect(accept).toBeDisabled();await panel.getByLabel('Reviewed successful current validation check-1').check();await expect(accept).toBeEnabled();await accept.click();await expect(page.getByRole('heading',{name:'Active run completed'})).toBeVisible();expect(accepted).toBe(true);
});

test('automatically runs one unambiguous Swift package check under the existing command grant',async({page})=>{
  const run={id:'automatic-validation-run',objective:'Validate the edited project',state:'verifying',revision:5,acceptance_criteria:['The check passes']};
  const suggestion={label:'Swift tests (Swift Package Manager) — apps/ios',rationale:'apps/ios/Package.swift was found.',argv:['/usr/bin/env','swift','test','--package-path','apps/ios']};
  const evidence={call_id:'automatic-check',argv:suggestion.argv,snapshot_sha256:'b'.repeat(64),matches_current_project:true,succeeded:true,exit_code:0,termination_reason:'finished',stdout_excerpt:'3 passed',stderr_excerpt:'',stdout_artifact_id:'out',stderr_artifact_id:'err'};
  let dispatches=0;
  await page.route('http://127.0.0.1:8765/**',async route=>{
    const request=route.request();const headers={'access-control-allow-origin':'http://127.0.0.1:5173','access-control-allow-headers':'authorization,content-type,last-event-id','access-control-allow-methods':'GET,POST,OPTIONS'};
    if(request.method()==='OPTIONS')return route.fulfill({status:204,headers});
    const path=new URL(request.url()).pathname;
    if(path==='/runs/latest-unfinished')return route.fulfill({json:null,headers});
    if(path==='/runs'&&request.method()==='GET')return route.fulfill({json:{items:[],next_cursor:null},headers});
    if(path.startsWith('/memory'))return route.fulfill({json:[],headers});
    if(path==='/models')return route.fulfill({json:[{id:'fixture/model'}],headers});
    if(path.endsWith('/plan'))return route.fulfill({json:{tasks:[],ready:[],blocked:[]},headers});
    if(path.endsWith('/validation')&&request.method()==='GET')return route.fulfill({json:{available:true,required:true,current_snapshot_sha256:'b'.repeat(64),suggestions:[suggestion],evidence:[]},headers});
    if(path.endsWith('/validation')&&request.method()==='POST'){
      const body=request.postDataJSON();expect(body).toMatchObject({expected_revision:5,argv:suggestion.argv,max_seconds:15});expect(body.request_id).toMatch(/^[0-9a-f-]{36}$/);dispatches++;
      return route.fulfill({json:{available:true,required:true,current_snapshot_sha256:'b'.repeat(64),suggestions:[suggestion],evidence:[evidence]},headers});
    }
    if(path.endsWith('/events/stream'))return route.fulfill({headers,contentType:'text/event-stream',body:`id: 1\nevent: run_event\ndata: ${JSON.stringify({sequence:1,event_type:'model.message',payload:{message:{role:'assistant',content:'The integrated project is ready for review.'}},created_at:'2026-11-08T00:00:00Z'})}\n\nevent: done\ndata: {}\n\n`});
    return route.fulfill({json:run,headers});
  });
  await page.goto('/');await page.getByLabel('Engine token').fill('x'.repeat(32));await page.getByRole('button',{name:'Connect',exact:true}).click();await selectLocalModel(page,'fixture/model');await page.getByLabel('Workspace path').fill('C:\\fixture');await page.getByLabel('Objective',{exact:true}).fill(run.objective);await page.getByRole('button',{name:'Start objective'}).click();
  const panel=page.getByRole('region',{name:'Final run review'});await page.getByText('The integrated project is ready for review.',{exact:true}).waitFor();await expect(panel.getByRole('status')).toContainText('only suggested check');await expect(panel.getByLabel('Reviewed successful current validation automatic-check')).toBeVisible();await expect(panel.getByLabel('Reviewed successful current validation automatic-check')).not.toBeChecked();expect(dispatches).toBe(1);
});

test('leaves check selection to the user when multiple suggestions are available',async({page})=>{
  const run={id:'ambiguous-validation-run',objective:'Validate the edited project',state:'verifying',revision:6,acceptance_criteria:['The check passes']};
  const suggestions=[
    {label:'Python tests (pytest)',rationale:'pyproject.toml was found.',argv:['/usr/bin/python3','-I','-m','pytest','-q']},
    {label:'JavaScript tests (npm)',rationale:'package.json was found.',argv:['/usr/bin/env','npm','test']},
  ];
  let dispatches=0;
  await page.route('http://127.0.0.1:8765/**',async route=>{
    const request=route.request();const headers={'access-control-allow-origin':'http://127.0.0.1:5173','access-control-allow-headers':'authorization,content-type,last-event-id','access-control-allow-methods':'GET,POST,OPTIONS'};
    if(request.method()==='OPTIONS')return route.fulfill({status:204,headers});
    const path=new URL(request.url()).pathname;
    if(path==='/runs/latest-unfinished')return route.fulfill({json:null,headers});
    if(path==='/runs'&&request.method()==='GET')return route.fulfill({json:{items:[],next_cursor:null},headers});
    if(path.startsWith('/memory'))return route.fulfill({json:[],headers});
    if(path==='/models')return route.fulfill({json:[{id:'fixture/model'}],headers});
    if(path.endsWith('/plan'))return route.fulfill({json:{tasks:[],ready:[],blocked:[]},headers});
    if(path.endsWith('/validation')&&request.method()==='GET')return route.fulfill({json:{available:true,required:true,current_snapshot_sha256:'c'.repeat(64),suggestions,evidence:[]},headers});
    if(path.endsWith('/validation')&&request.method()==='POST'){dispatches++;return route.fulfill({status:500,headers});}
    if(path.endsWith('/events/stream'))return route.fulfill({headers,contentType:'text/event-stream',body:`id: 1\nevent: run_event\ndata: ${JSON.stringify({sequence:1,event_type:'model.message',payload:{message:{role:'assistant',content:'Choose a relevant project check.'}},created_at:'2026-11-08T00:00:00Z'})}\n\nevent: done\ndata: {}\n\n`});
    return route.fulfill({json:run,headers});
  });
  await page.goto('/');await page.getByLabel('Engine token').fill('x'.repeat(32));await page.getByRole('button',{name:'Connect',exact:true}).click();await selectLocalModel(page,'fixture/model');await page.getByLabel('Workspace path').fill('C:\\fixture');await page.getByLabel('Objective',{exact:true}).fill(run.objective);await page.getByRole('button',{name:'Start objective'}).click();
  const panel=page.getByRole('region',{name:'Final run review'});await page.getByText('Choose a relevant project check.',{exact:true}).waitFor();await expect(panel.getByRole('button',{name:/Python tests \(pytest\):/})).toBeEnabled();await expect(panel.getByRole('button',{name:/JavaScript tests \(npm\):/})).toBeEnabled();await expect(panel.getByRole('button',{name:/Run check \(15 second limit\)/})).toBeEnabled();await expect(panel.getByRole('status')).toHaveCount(0);expect(dispatches).toBe(0);
});
