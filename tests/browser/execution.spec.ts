import {test,expect} from '@playwright/test';

test('sandbox selection checks controls without granting a run, including unavailable models',async({page})=>{
  let checks=0;let runCreates=0;let modelsAvailable=false;
  await page.route('http://127.0.0.1:8765/**',async route=>{
    const req=route.request();const path=new URL(req.url()).pathname;
    const headers={'access-control-allow-origin':'http://127.0.0.1:5173','access-control-allow-headers':'authorization,content-type','access-control-allow-methods':'GET,POST,OPTIONS'};
    if(req.method()==='OPTIONS')return route.fulfill({status:204,headers});
    if(path==='/models')return modelsAvailable?route.fulfill({json:[{id:'fixture/model'}],headers}):route.fulfill({status:503,headers});
    if(path==='/runs'&&req.method()==='POST')runCreates++;
    if(path==='/runs')return route.fulfill({json:{items:[],next_cursor:null},headers});
    if(path==='/memory'||path==='/skills')return route.fulfill({json:[],headers});
    if(path==='/execution/distributions')return route.fulfill({json:{state:'available',distributions:[{name:'Ubuntu-24.04',supported_by_profile:true},{name:'Custom Linux',supported_by_profile:false}]},headers});
    if(path==='/execution/readiness'){
      checks++;expect(req.postDataJSON()).toEqual({distribution:'Ubuntu-24.04'});
      return route.fulfill({json:{backend:'wsl-bubblewrap-v1',distribution:'Ubuntu-24.04',profile_sha256:'a'.repeat(64),state:'ready',checked_at:new Date().toISOString(),expires_at:new Date(Date.now()+60000).toISOString(),checks:['cleanup_confirmed'],policy:{network:'denied',workspace_changes:'private_copy_only',max_seconds:30,memory_bytes:134217728,max_active_executions_per_distribution:2},error_code:null},headers});
    }
    return route.fulfill({status:404,headers});
  });
  await page.goto('/');await page.getByLabel('Engine token').fill('x'.repeat(32));
  await page.getByRole('button',{name:'Connect',exact:true}).click();
  await page.getByText('Command sandbox setup',{exact:true}).click();
  await expect(page.getByRole('button',{name:'Check sandbox',exact:true})).toBeDisabled();
  await page.getByRole('button',{name:'Find installed distributions'}).click();
  await page.getByLabel('Execution distribution').selectOption('Ubuntu-24.04');
  await page.getByRole('button',{name:'Check sandbox',exact:true}).click();
  await expect(page.getByText('Sandbox check passed.',{exact:true})).toBeVisible();
  await expect(page.getByText(/Network denied/)).toBeVisible();
  await expect(page.getByRole('option',{name:/Custom Linux/})).toHaveJSProperty('disabled',true);
  expect(checks).toBe(1);expect(runCreates).toBe(0);
  modelsAvailable=true;await page.getByRole('button',{name:'Refresh models',exact:true}).click();
  await expect(page.getByRole('option',{name:'fixture/model',exact:true})).toHaveCount(1);
  await expect(page.getByLabel('Local model')).toHaveValue('');
  await page.getByRole('button',{name:'Find installed distributions'}).click();
  await expect(page.getByText('Sandbox check passed.',{exact:true})).toHaveCount(0);
  await expect(page.getByRole('button',{name:'Check sandbox',exact:true})).toBeDisabled();
});
