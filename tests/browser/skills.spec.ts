import {test,expect} from '@playwright/test';
import {navigateMainSection,selectLocalModel} from './navigation';

test('resolve, approve and install imported Python dependencies in WSL',async({page})=>{
  let checkBody:any,resolveBody:any,reviewBody:any,nodeReadinessBody:any;let lockDecision:'pending_review'|'approved'|'rejected'='pending_review';let wheelhousePresent=false,environmentInstalled=false;let npmDecision:'pending_review'|'approved'|'rejected'='pending_review',npmTarballsPresent=false,npmEnvironmentInstalled=false;
  const headers={'access-control-allow-origin':'http://127.0.0.1:5173','access-control-allow-headers':'authorization,content-type,last-event-id','access-control-allow-methods':'GET,POST,PUT,DELETE,OPTIONS'};
  const summary={id:'imported-procedure',name:'Summarize',lifecycle_state:'candidate',pinned:false,active_version:null,lifecycle_revision:0,latest_version:1,created_at:'2026-11-12T00:00:00Z',updated_at:'2026-11-12T00:00:00Z'};
  const version={skill_id:summary.id,version:1,content:'Read a file.',description:'Summarize a file.',category:'writing',required_tools:[],required_permissions:[],created_at:'2026-11-12T00:00:00Z',assets:[{path:'scripts/run.mjs',content_base64:'Y29uc29sZS5sb2coJ2ZpeHR1cmUnKTsK',media_type:'text/javascript',sha256:'b'.repeat(64)}],evaluation_evidence:{state:'candidate',provenance:'hermes_skill',evaluations:[],source:{package_name:'sample',package_version:'1.0',skill_name:'summarize',content_sha256:'a'.repeat(64),description:'Summarize a file.',allowed_tools:'workspace.read_file',unmapped_allowed_tools:'none',declared_requirements:JSON.stringify({python_dependency_specs:['requests>=2.0'],npm_dependency_specs:['left-pad@^1.3.0'],unparsed_dependency_count:0})}}};
  const lock={skill_id:summary.id,skill_version:1,environment_digest:'f'.repeat(64),source_sha256:'a'.repeat(64),decision:lockDecision,wheelhouse_present:wheelhousePresent,created_at:'2026-11-12T00:00:00Z',reviewed_at:null,environment_installed:environmentInstalled,installed_tree_sha256:null,installed_bytes:null,installed_file_count:null,installed_at:null,lock:{schema_version:1,skill_id:summary.id,skill_version:1,source_sha256:'a'.repeat(64),index_url:'https://pypi.org/simple',requirements:['requests>=2.0'],target:{distribution:'Ubuntu-24.04',environment:{python_full_version:'3.12.3'},compatible_wheel_tags:['py3-none-any']},wheels:[{filename:'requests-2.32.3-py3-none-any.whl',project_name:'requests',version:'2.32.3',sha256:'d'.repeat(64),size_bytes:12345,requires_dist:['urllib3>=1.21']},{filename:'urllib3-2.2.3-py3-none-any.whl',project_name:'urllib3',version:'2.2.3',sha256:'e'.repeat(64),size_bytes:54321,requires_dist:[]}]}};
  const secondLock={...lock,environment_digest:'c'.repeat(64),decision:'approved' as const,wheelhouse_present:true,environment_installed:true,installed_tree_sha256:'d'.repeat(64),installed_bytes:1234,installed_file_count:20,lock:{...lock.lock,target:{...lock.lock.target,environment:{...lock.lock.target.environment,python_full_version:'3.12.1'}}}};
  const npmLock={schema_version:1,skill_id:summary.id,skill_version:1,source_sha256:'a'.repeat(64),package_manifest_sha256:'b'.repeat(64),lockfile_sha256:'c'.repeat(64),distribution:'Ubuntu-24.04',node_version:'v22.2.0',npm_version:'10.8.1',requirements:['left-pad@^1.3.0'],root_dependencies:[{name:'left-pad',version_range:'^1.3.0',kind:'dependencies'}],packages:[{package_path:'node_modules/left-pad',name:'left-pad',version:'1.3.0',resolved_url:'https://registry.npmjs.org/left-pad/-/left-pad-1.3.0.tgz',integrity:'sha512-'+'A'.repeat(86)+'=='}]};
  const npmRecord=()=>({skill_id:summary.id,skill_version:1,environment_digest:'9'.repeat(64),source_sha256:'a'.repeat(64),decision:npmDecision,tarballs_present:npmTarballsPresent,verified_tarballs:npmTarballsPresent?[{package_path:'node_modules/left-pad',name:'left-pad',version:'1.3.0',integrity:npmLock.packages[0]!.integrity,archive_sha256:'e'.repeat(64),archive_size_bytes:1234,member_count:2,expanded_size_bytes:345,install_lifecycle_scripts:['install']}]:[],created_at:'2026-11-12T00:00:00Z',reviewed_at:npmDecision==='pending_review'?null:new Date().toISOString(),environment_installed:npmEnvironmentInstalled,installed_tree_sha256:npmEnvironmentInstalled?'d'.repeat(64):null,installed_bytes:npmEnvironmentInstalled?4567:null,installed_file_count:npmEnvironmentInstalled?42:null,installed_at:npmEnvironmentInstalled?new Date().toISOString():null,lock:npmLock});
  await page.route('http://127.0.0.1:8765/**',async route=>{
    const request=route.request(),path=new URL(request.url()).pathname;
    if(request.method()==='OPTIONS')return route.fulfill({status:204,headers});
    expect(request.headers()['authorization']).toBe('Bearer '+'x'.repeat(32));
    if(path==='/runs/latest-unfinished')return route.fulfill({json:null,headers});
    if(path==='/models')return route.fulfill({json:[{id:'fixture/model'}],headers});
    if(path==='/runs')return route.fulfill({json:{items:[],next_cursor:null},headers});
    if(path==='/memory')return route.fulfill({json:[],headers});
    if(path==='/skills')return route.fulfill({json:[summary],headers});
    if(path.endsWith('/versions/1')&&request.method()==='GET')return route.fulfill({json:version,headers});
    if(path.endsWith('/lifecycle'))return route.fulfill({json:[],headers});
    if(path==='/execution/distributions')return route.fulfill({json:{state:'available',distributions:[{name:'Ubuntu-24.04',supported_by_profile:true}]},headers});
    if(path==='/execution/readiness')return route.fulfill({json:{backend:'wsl-bubblewrap-v1',distribution:'Ubuntu-24.04',profile_sha256:'a'.repeat(64),state:'ready',checked_at:new Date().toISOString(),expires_at:new Date(Date.now()+60000).toISOString(),checks:['cleanup_confirmed'],policy:{network:'denied',workspace_changes:'private_copy_only',max_seconds:30,memory_bytes:134217728,max_active_executions_per_distribution:2}},headers});
    if(path.endsWith('/python-readiness')){
      checkBody=request.postDataJSON();
      return route.fulfill({json:{distribution:'Ubuntu-24.04',state:'ready',python_version:'3.12.0',python_environment:{python_full_version:'3.12.0'},compatible_wheel_tags:['cp312-cp312-manylinux_2_17_x86_64','py3-none-any'],dependencies:[{requirement:'requests>=2.0',status:'available',installed_version:'2.32.0'}],unverified_dependency_count:0,checked_at:new Date().toISOString()},headers});
    }
    if(path.endsWith('/node-readiness')){nodeReadinessBody=request.postDataJSON();return route.fulfill({json:{distribution:'Ubuntu-24.04',state:'ready',node_version:'v22.2.0',npm_version:'10.8.1',npm_available:true,checked_at:new Date().toISOString()},headers});}
    if(path.endsWith('/npm-dependency-locks/inspect'))return route.fulfill({json:npmLock,headers});
    if(path.endsWith('/npm-dependency-locks')&&request.method()==='GET')return route.fulfill({json:{locks:npmEnvironmentInstalled?[npmRecord(),{...npmRecord(),environment_digest:'8'.repeat(64),lock:{...npmLock,node_version:'v20.1.0'}}]:[]},headers});
    if(path.endsWith('/npm-dependency-locks')&&request.method()==='PUT')return route.fulfill({json:npmRecord(),headers});
    if(path.endsWith('/npm-dependency-locks/'+'9'.repeat(64)+'/acquire')){npmTarballsPresent=true;return route.fulfill({json:npmRecord(),headers});}
    if(path.endsWith('/npm-dependency-locks/'+'9'.repeat(64)+'/review')){npmDecision=request.postDataJSON().decision;return route.fulfill({json:npmRecord(),headers});}
    if(path.endsWith('/npm-dependency-locks/'+'9'.repeat(64)+'/install')&&request.method()==='POST'){npmEnvironmentInstalled=true;return route.fulfill({json:npmRecord(),headers});}
    if(path.endsWith('/npm-dependency-locks/'+'9'.repeat(64)+'/install')&&request.method()==='DELETE'){npmEnvironmentInstalled=false;return route.fulfill({json:npmRecord(),headers});}
    if(path.endsWith('/python-dependency-locks')&&request.method()==='GET')return route.fulfill({json:{locks:environmentInstalled?[{...lock,decision:lockDecision,wheelhouse_present:wheelhousePresent,environment_installed:true,installed_tree_sha256:'b'.repeat(64),installed_bytes:37331550,installed_file_count:100,installed_at:new Date().toISOString()},secondLock]:[{...lock,decision:lockDecision,wheelhouse_present:wheelhousePresent,environment_installed:false}]},headers});
    if(path.endsWith('/python-dependency-locks/resolve')&&request.method()==='POST'){resolveBody=request.postDataJSON();return route.fulfill({json:{...lock,decision:lockDecision,wheelhouse_present:false},headers});}
    if(path.endsWith('/acquire')&&request.method()==='POST'){wheelhousePresent=true;return route.fulfill({json:{...lock,decision:lockDecision,wheelhouse_present:true},headers});}
    if(path.endsWith('/review')&&request.method()==='POST'){reviewBody=request.postDataJSON();lockDecision=reviewBody.decision;return route.fulfill({json:{...lock,decision:lockDecision,wheelhouse_present:wheelhousePresent,reviewed_at:new Date().toISOString()},headers});}
    if(path.endsWith('/install')&&request.method()==='POST'){environmentInstalled=true;return route.fulfill({json:{...lock,decision:lockDecision,wheelhouse_present:wheelhousePresent,environment_installed:true,installed_tree_sha256:'b'.repeat(64),installed_bytes:37331550,installed_file_count:100,installed_at:new Date().toISOString()},headers});}
    if(path.endsWith('/install')&&request.method()==='DELETE'){environmentInstalled=false;return route.fulfill({json:{...lock,decision:lockDecision,wheelhouse_present:wheelhousePresent,environment_installed:false,installed_tree_sha256:null,installed_bytes:null,installed_file_count:null,installed_at:null},headers});}
    return route.fulfill({status:404,headers});
  });
  await page.goto('/');await page.getByLabel('Engine token').fill('x'.repeat(32));
  await page.getByRole('button',{name:'Connect',exact:true}).click();
  await navigateMainSection(page,'Tools & integrations');
  await page.locator('details').filter({has:page.locator('summary').filter({hasText:'Command sandbox setup'})}).evaluate(element=>{(element as HTMLDetailsElement).open=true;});
  await page.getByRole('button',{name:'Find installed distributions'}).click();
  await page.getByLabel('Execution distribution').selectOption('Ubuntu-24.04');
  await page.getByRole('button',{name:'Check sandbox',exact:true}).click();
  await expect(page.getByText('Sandbox check passed.',{exact:true})).toBeVisible();
  await navigateMainSection(page,'Tools & integrations');
  await page.locator('summary').filter({hasText:'Reusable procedures'}).click();
  const panel=page.getByRole('region',{name:'Procedure candidates',exact:true});
  await panel.getByRole('button',{name:'Inspect Summarize',exact:true}).click();
  await panel.getByRole('button',{name:'Check Python dependencies in WSL',exact:true}).click();
  await expect(panel.getByRole('region',{name:'Restricted WSL Python dependency readiness'})).toContainText('3.12.0');
  await expect(panel.getByRole('region',{name:'Restricted WSL Python dependency readiness'})).toContainText('requests>=2.0: available (2.32.0)');
  await expect(panel.getByRole('region',{name:'Restricted WSL Python dependency readiness'})).toContainText('Other or unverified declared dependencies: 0');
  await expect(panel.getByRole('region',{name:'Restricted WSL Python dependency readiness'})).toContainText('Python target data for wheel resolution');
  await expect(panel.getByRole('region',{name:'Restricted WSL Python dependency readiness'})).toContainText('Showing the first 12 of 2 compatible tags.');
  await panel.getByRole('button',{name:'Check Node.js and npm',exact:true}).click();
  const nodePanel=panel.getByRole('region',{name:'Restricted WSL Node.js runtime readiness'});
  await expect(nodePanel).toContainText('Node.js v22.2.0');
  await expect(nodePanel).toContainText('npm 10.8.1');
  await expect(nodePanel).toContainText('Approved npm locks can be installed offline');
  expect(nodeReadinessBody).toEqual({distribution:'Ubuntu-24.04'});
  const npmReview=panel.getByRole('region',{name:'npm dependency lock review'});
  await npmReview.getByRole('button',{name:'Inspect and prepare npm lock'}).click();
  await expect(npmReview).toContainText('pending_review');
  await expect(npmReview).toContainText('left-pad 1.3.0');
  await npmReview.locator('details summary').click();
  await expect(npmReview.getByRole('button',{name:'Approve this npm lock'})).toBeDisabled();
  await npmReview.getByRole('button',{name:'Fetch and verify exact tarballs'}).click();
  await expect(npmReview).toContainText('1,234 bytes');
  await expect(npmReview).toContainText('Lifecycle scripts: install');
  await npmReview.getByRole('button',{name:'Approve this npm lock'}).click();
  await expect(npmReview).toContainText('approved');
  await npmReview.getByRole('button',{name:'Install approved environment'}).click();
  await expect(npmReview).toContainText('Installed environment · 4,567 bytes · 42 files');
  await npmReview.getByRole('button',{name:'Remove installed environment'}).click();
  await expect(npmReview.getByRole('button',{name:'Install approved environment'})).toBeVisible();
  await panel.getByRole('button',{name:'Resolve requirements and prepare lock',exact:true}).click();
  await expect(panel.getByRole('region',{name:'Python dependency lock review'})).toContainText('pending_review');
  const lockReview=panel.getByRole('region',{name:'Python dependency lock review'});
  await expect(lockReview).toContainText('requests-2.32.3-py3-none-any.whl');
  await expect(lockReview).toContainText('Requires-Dist: urllib3>=1.21');
  await expect(lockReview).toContainText('urllib3 2.2.3');
  await expect(lockReview).toContainText('f'.repeat(64));
  await lockReview.locator('details summary').click();
  await expect(lockReview.getByRole('button',{name:'Approve this lock'})).toBeDisabled();
  await expect(lockReview.getByText('The exact wheel files have not been acquired and stored, so approval is unavailable.')).toBeVisible();
  await lockReview.getByRole('button',{name:'Fetch and verify wheels from PyPI'}).click();
  await expect(lockReview.getByRole('button',{name:'Approve this lock'})).toBeEnabled();
  await lockReview.getByRole('button',{name:'Approve this lock'}).click();
  await expect(lockReview).toContainText('approved');
  await lockReview.getByRole('button',{name:'Install approved wheels offline in WSL'}).click();
  await expect(lockReview).toContainText('Installed as an immutable WSL environment');
  await expect(lockReview).toContainText('37,331,550 bytes');
  npmEnvironmentInstalled=true;
  await panel.getByRole('button',{name:'Inspect Summarize',exact:true}).click();
  await panel.getByRole('button',{name:'Select version for trial run',exact:true}).click();
  await expect(panel.getByRole('button',{name:'Select version for trial run',exact:true})).toBeEnabled();
  await navigateMainSection(page,'Settings');
  await page.getByLabel('Workspace permissions').selectOption('workspace');
  await navigateMainSection(page,'New objective');
  await page.getByLabel('Workspace path').fill('C:\\fixture');
  await page.locator('details.advanced-options').evaluate(element=>{(element as HTMLDetailsElement).open=true;});
  await page.getByRole('checkbox',{name:'Allow isolated commands for this next run'}).check();
  const environmentChoice=page.getByLabel('Python environment for this procedure');
  await expect(environmentChoice.locator('option')).toHaveCount(3);
  await environmentChoice.selectOption('c'.repeat(64));
  await expect(environmentChoice).toHaveValue('c'.repeat(64));
  const npmEnvironmentChoice=page.getByLabel('npm environment for this procedure');
  await expect(npmEnvironmentChoice.locator('option')).toHaveCount(3);
  await npmEnvironmentChoice.selectOption('8'.repeat(64));
  await expect(npmEnvironmentChoice).toHaveValue('8'.repeat(64));
  await navigateMainSection(page,'Tools & integrations');
  await panel.getByRole('button',{name:'Inspect Summarize',exact:true}).click();
  const lockDetails=lockReview.locator('details').first();
  await lockDetails.evaluate(element=>{(element as HTMLDetailsElement).open=true;});
  await lockDetails.getByRole('button',{name:'Remove installed environment'}).click();
  await expect(lockDetails.getByRole('button',{name:'Install approved wheels offline in WSL'})).toBeVisible();
  expect(reviewBody).toEqual({decision:'approved'});
  expect(checkBody).toEqual({distribution:'Ubuntu-24.04'});
  expect(resolveBody).toEqual({distribution:'Ubuntu-24.04'});
});

test('create, revise, inspect, export and delete an unverified procedure',async({page})=>{
  const errors:string[]=[];page.on('pageerror',error=>errors.push(error.message));
  let summary:any;const versions:any[]=[];const runSubmissions:any[]=[];let runAttempts=0;
  await page.route('http://127.0.0.1:8765/**',async route=>{
    const request=route.request(),url=new URL(request.url()),path=url.pathname;
    const headers={'access-control-allow-origin':'http://127.0.0.1:5173','access-control-allow-headers':'authorization,content-type,last-event-id','access-control-allow-methods':'GET,POST,PUT,DELETE,OPTIONS'};
    if(request.method()==='OPTIONS')return route.fulfill({status:204,headers});
    expect(request.headers()['authorization']).toBe('Bearer '+'x'.repeat(32));
    if(path==='/runs/latest-unfinished')return route.fulfill({json:null,headers});
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
      if(path.endsWith('/python-dependency-locks'))return route.fulfill({json:{locks:[]},headers});
      if(path.endsWith('/npm-dependency-locks'))return route.fulfill({json:{locks:[]},headers});
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
  await navigateMainSection(page,'Tools & integrations');
  await page.locator('summary').filter({hasText:'Reusable procedures'}).click();
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
  await expect(panel.getByRole('button',{name:'Select version for trial run',exact:true})).toBeEnabled();
  await navigateMainSection(page,'New objective');
  await expect(page.getByRole('region',{name:'Selected procedure',exact:true})).toContainText('Inspect safely \u00b7 version 1');
  await expect(page.getByLabel('Allow this run to draft a new version of this procedure')).toBeDisabled();
  await page.getByRole('button',{name:'Remove selected procedure',exact:true}).click();
  await expect(page.getByRole('region',{name:'Selected procedure',exact:true})).toHaveCount(0);
  await navigateMainSection(page,'Tools & integrations');
  await panel.getByLabel('Procedure version',{exact:true}).selectOption('2');
  await panel.getByRole('button',{name:'Select version for trial run',exact:true}).click();
  await navigateMainSection(page,'New objective');
  const revisionGrant=page.getByLabel('Allow this run to draft a new version of this procedure');
  const resourceRevisionGrant=page.getByLabel('Allow this run to create or replace linked UTF-8 resources (up to 8 operations)');
  const resourceRemovalGrant=page.getByLabel('Allow this run to remove one linked resource');
  await expect(revisionGrant).toBeEnabled();
  await expect(resourceRevisionGrant).toBeEnabled();
  await expect(resourceRemovalGrant).toBeEnabled();
  await revisionGrant.check();
  await expect(resourceRevisionGrant).toBeDisabled();
  await expect(resourceRemovalGrant).toBeDisabled();
  await selectLocalModel(page,'fixture/model');
  await page.getByLabel('Workspace path').fill('C:\\fixture');
  await page.getByLabel('Objective',{exact:true}).fill('Improve the selected procedure');
  await page.getByRole('button',{name:'Start objective',exact:true}).click();
  await navigateMainSection(page,'New objective');
  await expect(revisionGrant).not.toBeChecked();
  await revisionGrant.check();
  await page.getByRole('button',{name:'Start objective',exact:true}).click();
  await navigateMainSection(page,'Tools & integrations');
  expect(runSubmissions[0].allow_procedure_revision).toBe(true);
  expect(runSubmissions[1].allow_procedure_revision).toBe(true);
  expect(runSubmissions[0].allow_procedure_resource_revision).toBe(false);
  expect(runSubmissions[1].allow_procedure_resource_revision).toBe(false);
  expect(runSubmissions[1].allow_procedure_resource_removal).toBe(false);
  expect(runSubmissions[1].procedure_trial).toEqual({skill_id:'procedure-1',version:2,purpose:'evaluation_trial'});
  await panel.getByLabel('Procedure version',{exact:true}).selectOption('2');
  await panel.getByRole('button',{name:'Select version for trial run',exact:true}).click();
  await navigateMainSection(page,'New objective');
  await resourceRevisionGrant.check();
  await expect(revisionGrant).toBeDisabled();
  await page.getByLabel('Objective',{exact:true}).fill('Update one linked procedure resource');
  await page.getByRole('button',{name:'Start objective',exact:true}).click();
  await navigateMainSection(page,'Tools & integrations');
  expect(runSubmissions[2].allow_procedure_revision).toBe(false);
  expect(runSubmissions[2].allow_procedure_resource_revision).toBe(true);
  expect(runSubmissions[2].allow_procedure_resource_removal).toBe(false);
  await panel.getByRole('button',{name:'Select version for trial run',exact:true}).click();
  await navigateMainSection(page,'New objective');
  await resourceRemovalGrant.check();
  await page.getByLabel('Objective',{exact:true}).fill('Remove one obsolete linked procedure resource');
  await page.getByRole('button',{name:'Start objective',exact:true}).click();
  await navigateMainSection(page,'Tools & integrations');
  expect(runSubmissions[3].allow_procedure_resource_removal).toBe(true);
  await panel.getByRole('button',{name:'Select version for trial run',exact:true}).click();
  await navigateMainSection(page,'New objective');
  await expect(resourceRevisionGrant).not.toBeChecked();
  await resourceRevisionGrant.check();
  await expect(resourceRemovalGrant).toBeEnabled();
  await resourceRemovalGrant.check();
  await page.getByLabel('Objective',{exact:true}).fill('Replace one resource and remove one obsolete resource atomically');
  await page.getByRole('button',{name:'Start objective',exact:true}).click();
  await navigateMainSection(page,'Tools & integrations');
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
