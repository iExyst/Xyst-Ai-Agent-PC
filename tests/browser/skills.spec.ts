import {test,expect} from '@playwright/test';

test('create, revise, inspect, export and delete an unverified procedure',async({page})=>{
  const errors:string[]=[];page.on('pageerror',error=>errors.push(error.message));
  let summary:any;const versions:any[]=[];
  await page.route('http://127.0.0.1:8765/**',async route=>{
    const request=route.request(),url=new URL(request.url()),path=url.pathname;
    const headers={'access-control-allow-origin':'http://127.0.0.1:5173','access-control-allow-headers':'authorization,content-type,last-event-id','access-control-allow-methods':'GET,POST,DELETE,OPTIONS'};
    if(request.method()==='OPTIONS')return route.fulfill({status:204,headers});
    expect(request.headers()['authorization']).toBe('Bearer '+'x'.repeat(32));
    if(path==='/models')return route.fulfill({json:[{id:'fixture/model'}],headers});
    if(path==='/runs')return route.fulfill({json:{items:[],next_cursor:null},headers});
    if(path.endsWith('/export'))return route.fulfill({status:200,body:Buffer.from('mock zip'),contentType:'application/zip',headers});
    if(path.startsWith('/memory'))return route.fulfill({json:[],headers});
    if(path.startsWith('/skills')){
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
        summary={id:'procedure-1',name:body.name,lifecycle_state:'candidate',active_version:null,lifecycle_revision:0,latest_version:versions.length,created_at:'2026-11-04T00:00:00Z',updated_at:'2026-11-04T00:00:00Z'};
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
  await expect(panel.getByRole('button',{name:'Save new procedure version',exact:true})).toBeEnabled();
  await panel.getByRole('textbox',{name:'Procedure text',exact:true}).fill('Revised procedure.');
  await panel.getByRole('button',{name:'Save new procedure version',exact:true}).click();
  await expect(panel.getByLabel('Procedure version',{exact:true})).toHaveValue('2');
  await panel.getByLabel('Procedure version',{exact:true}).selectOption('1');
  await expect(panel.getByLabel('Procedure text',{exact:true})).toHaveValue('Read files and preserve evidence.');
  await expect(panel.getByRole('button',{name:'Save new procedure version',exact:true})).toBeDisabled();
  await panel.getByRole('button',{name:'Select version for trial run',exact:true}).click();
  await expect(page.getByRole('region',{name:'Selected procedure',exact:true})).toContainText('Inspect safely \u00b7 version 1');
  await page.getByRole('button',{name:'Remove selected procedure',exact:true}).click();
  await expect(page.getByRole('region',{name:'Selected procedure',exact:true})).toHaveCount(0);
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
