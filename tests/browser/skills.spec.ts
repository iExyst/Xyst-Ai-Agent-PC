import {test,expect} from '@playwright/test';

test('create, revise, inspect, export and delete an unverified procedure',async({page})=>{
  const errors:string[]=[];page.on('pageerror',error=>errors.push(error.message));
  let summary:any;const versions:any[]=[];const runSubmissions:any[]=[];let runAttempts=0;
  await page.route('http://127.0.0.1:8765/**',async route=>{
    const request=route.request(),url=new URL(request.url()),path=url.pathname;
    const headers={'access-control-allow-origin':'http://127.0.0.1:5173','access-control-allow-headers':'authorization,content-type,last-event-id','access-control-allow-methods':'GET,POST,PUT,DELETE,OPTIONS'};
    if(request.method()==='OPTIONS')return route.fulfill({status:204,headers});
    expect(request.headers()['authorization']).toBe('Bearer '+'x'.repeat(32));
    if(path==='/models')return route.fulfill({json:[{id:'fixture/model'}],headers});
    if(path==='/runs'&&request.method()==='GET')return route.fulfill({json:{items:[],next_cursor:null},headers});
    if(path==='/runs'&&request.method()==='POST'){
      runSubmissions.push(request.postDataJSON());
      runAttempts+=1;
      if(runAttempts===1)return route.fulfill({status:503,body:'temporary unavailable',headers});
      return route.fulfill({json:{id:'revision-run',objective:'Improve procedure',state:'running',acceptance_criteria:[],revision:1,model:'fixture/model',limits:{max_iterations:8,max_seconds:900}},headers});
    }
    if(path.endsWith('/events/stream'))return route.fulfill({headers,contentType:'text/event-stream',body:'event: done\ndata: {}\n\n'});
    if(path.endsWith('/plan'))return route.fulfill({json:{tasks:[],ready:[],blocked:[]},headers});
    if(path.endsWith('/export'))return route.fulfill({status:200,body:Buffer.from('mock zip'),contentType:'application/zip',headers});
    if(path.startsWith('/memory'))return route.fulfill({json:[],headers});
    if(path.startsWith('/skills')){
      if(path.endsWith('/pin')&&request.method()==='PUT'){
        summary.pinned=request.postDataJSON().pinned;
        return route.fulfill({json:summary,headers});
      }
      if(path.endsWith('/lifecycle'))return route.fulfill({json:[],headers});
      if(path.endsWith('/trials'))return route.fulfill({json:[{id:'trial-a',objective:'ASCII case',created_at:'2026-11-04T00:00:00Z'},{id:'trial-b',objective:'Unicode case',created_at:'2026-11-04T00:00:00Z'}],headers});
      if(path.endsWith('/evaluations')){
        const body=request.postDataJSON();expect(body.expected_evaluation_count).toBe(0);expect(body.confirmed_criteria).toEqual([0]);
        const entry={id:'review-1',actor:'local-user',evaluated_at:'2026-11-04T00:00:00Z',snapshot_sha256:'fixture',trials:body.trials.map((trial:any)=>({...trial,objective:'Reviewed case',run_revision:5,final_review_sequence:20})),criteria:body.criteria,confirmed_criteria:body.confirmed_criteria,rationale:body.rationale};
        versions[0].evaluation_evidence={state:'evaluated',provenance:'user',evaluations:[entry]};
        return route.fulfill({status:201,json:versions[0],headers});
      }

      if(request.method()==='DELETE'){summary=undefined;return route.fulfill({status:204,headers});}
      if(request.method()==='POST'){
        const body=request.postDataJSON();
        if(summary)expect(body.expected_version).toBe(versions.length);
        versions.push({skill_id:'procedure-1',version:versions.length+1,content:body.content,description:body.description,category:body.category,required_tools:body.required_tools,required_permissions:body.required_permissions,evaluation_evidence:{state:'candidate',provenance:'user',evaluations:[]},created_at:'2026-11-04T00:00:00Z'});
        summary={id:'procedure-1',name:body.name,lifecycle_state:'candidate',pinned:false,active_version:null,lifecycle_revision:0,latest_version:versions.length,created_at:'2026-11-04T00:00:00Z',updated_at:'2026-11-04T00:00:00Z'};
        return route.fulfill({status:201,json:summary,headers});
      }
      if(path.includes('/versions/'))return route.fulfill({json:versions[Number(path.split('/').at(-1))-1],headers});
      return route.fulfill({json:path==='/skills'?(summary?[summary]:[]):summary,headers});
    }
    return route.fulfill({json:{status:'ok'},headers});
  });
  await page.goto('/');await page.getByLabel('Engine token').fill('x'.repeat(32));
  await page.getByRole('button',{name:'Connect',exact:true}).click();
  await page.getByText('Reusable procedures',{exact:true}).click();
  const panel=page.getByRole('region',{name:'Procedure candidates',exact:true});
  await panel.getByLabel('Procedure name',{exact:true}).fill('Inspect safely');
  await panel.getByLabel('Procedure description',{exact:true}).fill('Read-only project inspection');
  await panel.getByLabel('Procedure category',{exact:true}).fill('engineering');
  await panel.getByLabel('Procedure text',{exact:true}).fill('Read files and preserve evidence.');
  await panel.getByLabel('Tool dependencies (one name@version per line)',{exact:true}).fill('workspace.read_file@1');
  await panel.getByLabel('Requires reading workspace files',{exact:true}).check();
  await panel.getByRole('button',{name:'Save procedure candidate',exact:true}).click();
  expect(versions[0].description).toBe('Read-only project inspection');
  expect(versions[0].category).toBe('engineering');
  await expect(panel.getByRole('button',{name:'Inspect Inspect safely',exact:true})).toBeVisible();
  await expect(panel.getByText('Draft reusable procedures here.',{exact:false})).toContainText('evaluation trial');
  await panel.getByRole('button',{name:'Pin procedure',exact:true}).click();
  await expect(panel.getByRole('button',{name:'Unpin procedure',exact:true})).toBeVisible();
  await expect(panel.getByRole('button',{name:'Delete procedure and all versions',exact:true})).toBeDisabled();
  await panel.getByRole('button',{name:'Unpin procedure',exact:true}).click();
  await expect(panel.getByRole('button',{name:'Delete procedure and all versions',exact:true})).toBeEnabled();
  await expect(panel.getByRole('button',{name:'Save new procedure version',exact:true})).toBeEnabled();
  await panel.getByRole('textbox',{name:'Procedure text',exact:true}).fill('Revised procedure.');
  await panel.getByRole('button',{name:'Save new procedure version',exact:true}).click();
  await expect(panel.getByLabel('Procedure version',{exact:true})).toHaveValue('2');
  await panel.getByLabel('Procedure version',{exact:true}).selectOption('1');
  await expect(panel.getByLabel('Procedure text',{exact:true})).toHaveValue('Read files and preserve evidence.');
  await expect(panel.getByRole('button',{name:'Save new procedure version',exact:true})).toBeDisabled();
  await panel.getByRole('button',{name:'Select version for trial run',exact:true}).click();
  await expect(page.getByRole('region',{name:'Selected procedure',exact:true})).toContainText('Inspect safely \u00b7 version 1');
  await expect(page.getByLabel('Allow this run to draft a new version of this procedure')).toBeDisabled();
  await page.getByRole('button',{name:'Remove selected procedure',exact:true}).click();
  await expect(page.getByRole('region',{name:'Selected procedure',exact:true})).toHaveCount(0);
  await panel.getByLabel('Procedure version',{exact:true}).selectOption('2');
  await panel.getByRole('button',{name:'Select version for trial run',exact:true}).click();
  const revisionGrant=page.getByLabel('Allow this run to draft a new version of this procedure');
  const resourceRevisionGrant=page.getByLabel('Allow this run to create or replace linked UTF-8 resources (up to 8 operations)');
  const resourceRemovalGrant=page.getByLabel('Allow this run to remove one linked resource');
  await expect(revisionGrant).toBeEnabled();
  await expect(resourceRevisionGrant).toBeEnabled();
  await expect(resourceRemovalGrant).toBeEnabled();
  await revisionGrant.check();
  await expect(resourceRevisionGrant).toBeDisabled();
  await expect(resourceRemovalGrant).toBeDisabled();
  await page.getByLabel('Local model').selectOption('fixture/model');
  await page.getByLabel('Workspace path').fill('C:\\fixture');
  await page.getByLabel('Objective',{exact:true}).fill('Improve the selected procedure');
  await page.getByRole('button',{name:'Start objective',exact:true}).click();
  await expect(revisionGrant).not.toBeChecked();
  await revisionGrant.check();
  await page.getByRole('button',{name:'Start objective',exact:true}).click();
  expect(runSubmissions[0].allow_procedure_revision).toBe(true);
  expect(runSubmissions[1].allow_procedure_revision).toBe(true);
  expect(runSubmissions[0].allow_procedure_resource_revision).toBe(false);
  expect(runSubmissions[1].allow_procedure_resource_revision).toBe(false);
  expect(runSubmissions[1].allow_procedure_resource_removal).toBe(false);
  expect(runSubmissions[1].procedure_trial).toEqual({skill_id:'procedure-1',version:2,purpose:'evaluation_trial'});
  await panel.getByLabel('Procedure version',{exact:true}).selectOption('2');
  await panel.getByRole('button',{name:'Select version for trial run',exact:true}).click();
  await resourceRevisionGrant.check();
  await expect(revisionGrant).toBeDisabled();
  await page.getByLabel('Objective',{exact:true}).fill('Update one linked procedure resource');
  await page.getByRole('button',{name:'Start objective',exact:true}).click();
  expect(runSubmissions[2].allow_procedure_revision).toBe(false);
  expect(runSubmissions[2].allow_procedure_resource_revision).toBe(true);
  expect(runSubmissions[2].allow_procedure_resource_removal).toBe(false);
  await panel.getByRole('button',{name:'Select version for trial run',exact:true}).click();
  await resourceRemovalGrant.check();
  await page.getByLabel('Objective',{exact:true}).fill('Remove one obsolete linked procedure resource');
  await page.getByRole('button',{name:'Start objective',exact:true}).click();
  expect(runSubmissions[3].allow_procedure_resource_removal).toBe(true);
  await panel.getByRole('button',{name:'Select version for trial run',exact:true}).click();
  await expect(resourceRevisionGrant).not.toBeChecked();
  await resourceRevisionGrant.check();
  await expect(resourceRemovalGrant).toBeEnabled();
  await resourceRemovalGrant.check();
  await page.getByLabel('Objective',{exact:true}).fill('Replace one resource and remove one obsolete resource atomically');
  await page.getByRole('button',{name:'Start objective',exact:true}).click();
  expect(runSubmissions[4].allow_procedure_resource_revision).toBe(true);
  expect(runSubmissions[4].allow_procedure_resource_removal).toBe(true);
  await panel.getByRole('button',{name:'Select version for trial run',exact:true}).click();
  await expect(resourceRevisionGrant).not.toBeChecked();
  await panel.getByLabel('Procedure version',{exact:true}).selectOption('1');
  const download=page.waitForEvent('download');await panel.getByRole('button',{name:'Export procedure version',exact:true}).click();
  expect((await download).suggestedFilename()).toBe('procedure-procedure-1-v1.zip');
  await panel.getByRole('button',{name:'Find completed procedure trials',exact:true}).click();
  await panel.getByLabel('First completed trial',{exact:true}).selectOption('trial-a');
  await panel.getByLabel('Second completed trial',{exact:true}).selectOption('trial-b');
  await panel.getByLabel('First representative case',{exact:true}).fill('ASCII report');
  await panel.getByLabel('Second representative case',{exact:true}).fill('Unicode report');
  await panel.getByLabel('Evaluation criteria (one per line)',{exact:true}).fill('Returns the correct evidence.');
  await panel.getByLabel('Evaluation rationale',{exact:true}).fill('Reviewed both cases against their expected output.');
  await panel.getByLabel('I reviewed both trials and confirmed every evaluation criterion',{exact:true}).check();
  await panel.getByRole('button',{name:'Record procedure evaluation',exact:true}).click();
  await expect(panel.getByRole('region',{name:'Procedure evaluation',exact:true})).toContainText('Version 1: evaluated. Saved evaluations: 1.');
  await panel.getByRole('button',{name:'Delete procedure and all versions',exact:true}).click();
  await expect(panel.getByRole('button',{name:'Inspect Inspect safely',exact:true})).toHaveCount(0);
  expect(errors).toEqual([]);
});
