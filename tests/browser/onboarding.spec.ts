import {test,expect} from '@playwright/test';

test('onboarding requires a real tool diagnostic and runs its demonstration in Observe mode',async({page})=>{
  let created:Record<string,unknown>|undefined;
  const run={id:'onboarding-run',objective:'Read project documentation',state:'verifying',acceptance_criteria:[],revision:1,model:'fixture/model',limits:{max_iterations:8,max_seconds:900}};
  await page.route('http://127.0.0.1:8765/**',async route=>{
    const request=route.request();
    const headers={'access-control-allow-origin':'http://127.0.0.1:5173','access-control-allow-headers':'authorization,content-type,last-event-id','access-control-allow-methods':'GET,POST,PUT,DELETE,OPTIONS'};
    if(request.method()==='OPTIONS')return route.fulfill({status:204,headers});
    const path=new URL(request.url()).pathname;
    if(path==='/runs'&&request.method()==='GET')return route.fulfill({json:{items:[],next_cursor:null},headers});
    if(path==='/models')return route.fulfill({json:[{id:'fixture/model'}],headers});
    if(path==='/providers/lm-studio/config'&&request.method()==='GET')return route.fulfill({json:{endpoint:'http://127.0.0.1:1234',selected_model:null},headers});
    if(path==='/providers')return route.fulfill({json:[{id:'lm-studio',status:'connected'}],headers});
    if(path==='/providers/lm-studio/selection')return route.fulfill({json:{model:request.postDataJSON().model},headers});
    if(path==='/providers/diagnostics')return route.fulfill({json:{model:'fixture/model',tool_round_trip_verified:true},headers});
    if(path==='/execution/distributions')return route.fulfill({json:{state:'unsupported',distributions:[]},headers});
    if(path==='/runs'&&request.method()==='POST'){created=request.postDataJSON();return route.fulfill({json:run,headers});}
    if(path.endsWith('/plan'))return route.fulfill({json:{tasks:[],ready:[],blocked:[]},headers});
    if(path.endsWith('/events/stream'))return route.fulfill({headers,contentType:'text/event-stream',body:`id: 1\nevent: run_event\ndata: ${JSON.stringify({sequence:1,event_type:'run.state_changed',payload:{to:'verifying'},created_at:'2026-11-05T00:00:00Z'})}\n\nevent: done\ndata: {}\n\n`});
    if(path==='/runs/onboarding-run')return route.fulfill({json:run,headers});
    return route.fulfill({json:[],headers});
  });
  await page.goto('/');
  await expect(page.getByRole('note')).toContainText('keeps it running in the system tray');
  await page.getByLabel('Engine token').fill('x'.repeat(32));
  await page.getByRole('button',{name:'Connect',exact:true}).click();
  const navigation=page.getByRole('navigation',{name:'Main sections'});
  await expect(navigation.getByRole('link',{name:'New objective'})).toBeVisible();
  await navigation.getByRole('link',{name:'New objective'}).click();
  await expect(page.locator('#new-objective')).toBeInViewport();
  await navigation.getByRole('link',{name:'Tools & integrations'}).click();
  await expect(page.locator('#integrations')).toBeInViewport();
  const demo=page.getByRole('button',{name:'Start read-only onboarding demonstration'});
  await expect(demo).toBeDisabled();
  await page.getByLabel('Local model').selectOption('fixture/model');
  await page.getByLabel('Workspace path').fill('C:\\fixture-project');
  await expect(demo).toBeDisabled();
  await page.getByRole('button',{name:'Run harmless tool-use diagnostic'}).click();
  await expect(page.getByText('The selected model completed a validated harmless tool round trip.')).toBeVisible();
  await expect(demo).toBeEnabled();
  await demo.click();
  await expect(page.getByRole('heading',{name:'Active run verifying'})).toBeVisible();
  await navigation.getByRole('link',{name:'Active run'}).click();
  await expect(page.locator('#active-run')).toBeInViewport();
  expect(created).toMatchObject({workspace:'C:\\fixture-project',mode:'observe',command_grant:null});
  expect(created?.objective).toContain('Do not modify files or run commands');
});
