import {test,expect} from '@playwright/test';
import {navigateMainSection} from './navigation';

test('sandbox selection checks controls without granting a run, including unavailable models',async({page})=>{
  let checks=0;let runCreates=0;let modelsAvailable=false;
  await page.route('http://127.0.0.1:8765/**',async route=>{
    const req=route.request();const path=new URL(req.url()).pathname;
    const headers={'access-control-allow-origin':'http://127.0.0.1:5173','access-control-allow-headers':'authorization,content-type','access-control-allow-methods':'GET,POST,OPTIONS'};
    if(req.method()==='OPTIONS')return route.fulfill({status:204,headers});
    if(path==='/runs/latest-unfinished')return route.fulfill({json:null,headers});
    if(path==='/providers')return route.fulfill({json:[{id:'lm-studio',status:'connected'}],headers});
    if(path==='/models')return modelsAvailable?route.fulfill({json:[{id:'fixture/model'}],headers}):route.fulfill({status:503,headers});
    if(path==='/runs'&&req.method()==='POST')runCreates++;
    if(path==='/runs')return route.fulfill({json:{items:[],next_cursor:null},headers});
    if(path==='/memory'||path==='/skills')return route.fulfill({json:[],headers});
    if(path==='/execution/distributions')return route.fulfill({json:{state:'available',distributions:[{name:'Ubuntu-24.04',supported_by_profile:true},{name:'Custom Linux',supported_by_profile:false}]},headers});
    if(path==='/execution/readiness'){
      checks++;expect(req.postDataJSON()).toEqual({backend:'wsl-bubblewrap-v1',distribution:'Ubuntu-24.04'});
      return route.fulfill({json:{backend:'wsl-bubblewrap-v1',distribution:'Ubuntu-24.04',profile_sha256:'a'.repeat(64),state:'ready',checked_at:new Date().toISOString(),expires_at:new Date(Date.now()+60000).toISOString(),checks:['cleanup_confirmed'],policy:{network:'denied',workspace_changes:'private_copy_only',max_seconds:30,memory_bytes:134217728,max_active_executions_per_distribution:2},error_code:null},headers});
    }
    return route.fulfill({status:404,headers});
  });
  await page.clock.install();
  await page.goto('/');await page.getByLabel('Engine token').fill('x'.repeat(32));
  await page.getByRole('button',{name:'Connect',exact:true}).click();
  await navigateMainSection(page,'Tools & integrations');
  await page.locator('details').filter({has:page.locator('summary').filter({hasText:'Command sandbox setup'})}).evaluate(element=>{(element as HTMLDetailsElement).open=true;});
  await expect(page.getByRole('button',{name:'Check sandbox',exact:true})).toBeDisabled();
  await page.getByRole('button',{name:'Find installed distributions'}).click();
  await page.getByLabel('Execution distribution').selectOption('Ubuntu-24.04');
  await page.getByRole('button',{name:'Check sandbox',exact:true}).click();
  await expect(page.getByText('Sandbox check passed.',{exact:true})).toBeVisible();
  await expect(page.getByText(/Network denied/)).toBeVisible();
  await expect(page.getByRole('option',{name:/Custom Linux/})).toHaveJSProperty('disabled',true);
  expect(checks).toBe(1);expect(runCreates).toBe(0);
  modelsAvailable=true;await navigateMainSection(page,'Model setup');await page.getByRole('button',{name:'Refresh models',exact:true}).click();
  await expect(page.getByRole('option',{name:'fixture/model',exact:true})).toHaveCount(1);
  await expect(page.getByLabel('Local model or compatible provider model')).toHaveValue('');
  await navigateMainSection(page,'Settings');
  const permission=page.getByLabel('Allow isolated commands for this next run');
  await expect(permission).toBeDisabled();
  await page.getByLabel('Workspace permissions').selectOption('workspace');
  await navigateMainSection(page,'New objective');
  await page.getByLabel('Workspace path').fill('C:\\fixture');
  await page.locator('details.advanced-options').evaluate(element => { (element as HTMLDetailsElement).open=true; });
  await permission.check();await expect(permission).toBeChecked();
  await page.getByLabel('Workspace path').fill('C:\\other');
  await expect(permission).not.toBeChecked();
  await permission.check();
  await navigateMainSection(page,'Settings');await page.getByLabel('Workspace permissions').selectOption('observe');await navigateMainSection(page,'New objective');
  await expect(permission).not.toBeChecked();await expect(permission).toBeDisabled();
  await navigateMainSection(page,'Settings');await page.getByLabel('Workspace permissions').selectOption('workspace');await navigateMainSection(page,'New objective');
  await page.locator('details.advanced-options').evaluate(element => { (element as HTMLDetailsElement).open=true; });await permission.check();
  await page.clock.fastForward(61000);
  await expect(permission).not.toBeChecked();await expect(permission).toBeDisabled();
  await navigateMainSection(page,'Tools & integrations');
  await page.locator('details').filter({has:page.locator('summary').filter({hasText:'Command sandbox setup'})}).evaluate(element=>{(element as HTMLDetailsElement).open=true;});
  await expect(page.getByText('Check expired. Check again to refresh status.',{exact:true})).toBeVisible();
  await page.getByRole('button',{name:'Find installed distributions'}).click();
  await expect(page.getByText('Sandbox check passed.',{exact:true})).toHaveCount(0);
  await expect(page.getByRole('button',{name:'Check sandbox',exact:true})).toBeDisabled();
});

test('Docker setup checks a pinned image and network access is a separate run grant',async({page})=>{
  const imageReference=`registry.example/xaa/python@sha256:${'b'.repeat(64)}`;let checks=0;const grants:unknown[]=[];let modelsAvailable=false;
  await page.route('http://127.0.0.1:8765/**',async route=>{
    const req=route.request();const path=new URL(req.url()).pathname;
    const headers={'access-control-allow-origin':'http://127.0.0.1:5173','access-control-allow-headers':'authorization,content-type','access-control-allow-methods':'GET,POST,OPTIONS'};
    if(req.method()==='OPTIONS')return route.fulfill({status:204,headers});
    if(path==='/runs/latest-unfinished')return route.fulfill({json:null,headers});
    if(path==='/providers')return route.fulfill({json:[{id:'lm-studio',status:'connected'}],headers});
    if(path==='/models')return modelsAvailable?route.fulfill({json:[{id:'fixture/model'}],headers}):route.fulfill({status:503,headers});
    if(path==='/runs'&&req.method()==='POST'){grants.push(req.postDataJSON().command_grant);return route.fulfill({status:409,json:{detail:'fixture rejection'},headers});}
    if(path==='/runs')return route.fulfill({json:{items:[],next_cursor:null},headers});
    if(path==='/memory'||path==='/skills')return route.fulfill({json:[],headers});
    if(path==='/execution/readiness'){
      checks++;expect(req.postDataJSON()).toEqual({backend:'docker-local-v1',distribution:'Docker-local',image_reference:imageReference});
      return route.fulfill({json:{backend:'docker-local-v1',distribution:'Docker-local',image_reference:imageReference,profile_sha256:'c'.repeat(64),state:'ready',checked_at:new Date().toISOString(),expires_at:new Date(Date.now()+60000).toISOString(),checks:['local_daemon','cleanup_confirmed'],policy:{network:'denied',workspace_changes:'private_copy_only',max_seconds:30,memory_bytes:134217728,max_active_executions_per_distribution:2},error_code:null},headers});
    }
    return route.fulfill({status:404,headers});
  });
  await page.goto('/');await page.getByLabel('Engine token').fill('x'.repeat(32));await page.getByRole('button',{name:'Connect',exact:true}).click();
  await navigateMainSection(page,'Tools & integrations');
  await page.locator('details').filter({has:page.locator('summary').filter({hasText:'Command sandbox setup'})}).evaluate(element=>{(element as HTMLDetailsElement).open=true;});
  await page.getByLabel('Execution backend').selectOption('docker-local-v1');
  await expect(page.getByRole('button',{name:'Check sandbox',exact:true})).toBeDisabled();
  await page.getByLabel('Local Docker image digest').fill(imageReference);
  await page.getByRole('button',{name:'Check sandbox',exact:true}).click();
  await expect(page.getByText('Sandbox check passed.',{exact:true})).toBeVisible();
  expect(checks).toBe(1);
  modelsAvailable=true;await navigateMainSection(page,'Model setup');
  await page.getByRole('button',{name:'Refresh models',exact:true}).click();
  await page.getByLabel('Local model or compatible provider model').selectOption('fixture/model');
  await navigateMainSection(page,'New objective');
  await page.getByLabel('Workspace path').fill('C:\\fixture');
  await navigateMainSection(page,'Settings');await page.getByLabel('Workspace permissions').selectOption('workspace');
  await navigateMainSection(page,'New objective');
  await page.locator('details.advanced-options').evaluate(element=>{(element as HTMLDetailsElement).open=true;});
  const commandPermission=page.getByLabel('Allow isolated commands for this next run');
  const networkPermission=page.getByLabel("Allow this run's Docker commands to use the bridge network");
  const sessionPermission=page.getByLabel('Reuse a private Docker session for this run');
  await expect(networkPermission).toBeDisabled();
  await expect(sessionPermission).not.toBeChecked();
  await commandPermission.check();
  await expect(networkPermission).toBeEnabled();
  await expect(sessionPermission).toBeEnabled();
  await page.getByLabel('Objective',{exact:true}).fill('Check the Docker network grant');
  await page.getByRole('button',{name:'Start objective',exact:true}).click();
  await expect.poll(()=>grants.length).toBe(1);
  expect(grants[0]).toMatchObject({backend:'docker-local-v1',docker_network_access:false,docker_session_reuse:false});
  await networkPermission.check();
  await page.getByRole('button',{name:'Start objective',exact:true}).click();
  await expect.poll(()=>grants.length).toBe(2);
  expect(grants[1]).toMatchObject({backend:'docker-local-v1',docker_network_access:true,docker_session_reuse:false});
  await sessionPermission.check();
  await page.getByRole('button',{name:'Start objective',exact:true}).click();
  await expect.poll(()=>grants.length).toBe(3);
  expect(grants[2]).toMatchObject({backend:'docker-local-v1',docker_network_access:true,docker_session_reuse:true});
});

test('SSH setup sends the selected Windows alias and identity for pinned-host checks',async({page})=>{
  const identityFile='C:\\Users\\fixture\\.ssh\\id_ed25519';let checks=0;let runCreates=0;
  await page.route('http://127.0.0.1:8765/**',async route=>{
    const req=route.request();const path=new URL(req.url()).pathname;
    const headers={'access-control-allow-origin':'http://127.0.0.1:5173','access-control-allow-headers':'authorization,content-type','access-control-allow-methods':'GET,POST,OPTIONS'};
    if(req.method()==='OPTIONS')return route.fulfill({status:204,headers});
    if(path==='/runs/latest-unfinished')return route.fulfill({json:null,headers});
    if(path==='/providers')return route.fulfill({json:[{id:'lm-studio',status:'connected'}],headers});
    if(path==='/models')return route.fulfill({status:503,headers});
    if(path==='/runs'&&req.method()==='POST')runCreates++;
    if(path==='/runs')return route.fulfill({json:{items:[],next_cursor:null},headers});
    if(path==='/memory'||path==='/skills')return route.fulfill({json:[],headers});
    if(path==='/execution/readiness'){
      checks++;expect(req.postDataJSON()).toEqual({backend:'ssh-wsl-bubblewrap-v1',distribution:'xaa-builder',ssh_identity_file:identityFile});
      return route.fulfill({json:{backend:'ssh-wsl-bubblewrap-v1',distribution:'xaa-builder',ssh_identity_file:identityFile,profile_sha256:'d'.repeat(64),state:'ready',checked_at:new Date().toISOString(),expires_at:new Date(Date.now()+60000).toISOString(),checks:['pinned_host_key','restricted_remote_manager'],policy:{network:'denied',workspace_changes:'private_copy_only',max_seconds:30,memory_bytes:134217728,max_active_executions_per_distribution:2},error_code:null},headers});
    }
    return route.fulfill({status:404,headers});
  });
  await page.goto('/');await page.getByLabel('Engine token').fill('x'.repeat(32));await page.getByRole('button',{name:'Connect',exact:true}).click();
  await navigateMainSection(page,'Tools & integrations');
  await page.locator('details').filter({has:page.locator('summary').filter({hasText:'Command sandbox setup'})}).evaluate(element=>{(element as HTMLDetailsElement).open=true;});
  await page.getByLabel('Execution backend').selectOption('ssh-wsl-bubblewrap-v1');
  await page.getByLabel('SSH host alias').fill('xaa-builder');
  await page.getByLabel('SSH private-key path').fill(identityFile);
  await page.getByRole('button',{name:'Check sandbox',exact:true}).click();
  await expect(page.getByText('Sandbox check passed.',{exact:true})).toBeVisible();
  expect(checks).toBe(1);expect(runCreates).toBe(0);
});
