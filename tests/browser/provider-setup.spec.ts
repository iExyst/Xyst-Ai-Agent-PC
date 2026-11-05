import {test,expect} from '@playwright/test';

test('LM Studio address, discovered model and tool diagnostic are configured before use',async({page})=>{
  let endpoint='http://127.0.0.1:1234';
  let selectedModel:string|null=null;
  let diagnostics=0;
  await page.route('http://127.0.0.1:8765/**',async route=>{
    const req=route.request();const path=new URL(req.url()).pathname;
    const headers={'access-control-allow-origin':'http://127.0.0.1:5173','access-control-allow-headers':'authorization,content-type','access-control-allow-methods':'GET,POST,PUT,OPTIONS'};
    if(req.method()==='OPTIONS')return route.fulfill({status:204,headers});
    if(path==='/runs')return route.fulfill({json:{items:[],next_cursor:null},headers});
    if(path==='/models')return route.fulfill({json:[{id:'fixture/model'}],headers});
    if(path==='/providers')return route.fulfill({json:[{id:'lm-studio',status:'connected'}],headers});
    if(path==='/providers/lm-studio/config'&&req.method()==='GET')return route.fulfill({json:{endpoint,selected_model:selectedModel},headers});
    if(path==='/providers/lm-studio/config'&&req.method()==='PUT'){
      endpoint=req.postDataJSON().endpoint==='http://localhost:1234'?'http://127.0.0.1:1234':req.postDataJSON().endpoint;selectedModel=null;
      return route.fulfill({json:{endpoint,selected_model:null},headers});
    }
    if(path==='/providers/lm-studio/selection'&&req.method()==='PUT'){
      selectedModel=req.postDataJSON().model;
      return route.fulfill({json:{model:selectedModel},headers});
    }
    if(path==='/providers/lm-studio/diagnostics'&&req.method()==='POST'){
      expect(req.postDataJSON()).toEqual({model:'fixture/model'});diagnostics++;
      return route.fulfill({json:{model:'fixture/model',tool_round_trip_verified:true},headers});
    }
    if(path==='/memory'||path==='/skills')return route.fulfill({json:[],headers});
    if(path==='/execution/distributions')return route.fulfill({json:{state:'unavailable',distributions:[]},headers});
    return route.fulfill({status:404,headers});
  });
  await page.goto('/');await page.getByLabel('Engine token').fill('x'.repeat(32));
  await page.getByRole('button',{name:'Connect',exact:true}).click();
  await expect(page.getByRole('status').filter({hasText:'LM Studio is connected'})).toBeVisible();
  await expect(page.getByRole('option',{name:'fixture/model',exact:true})).toHaveCount(1);
  await page.getByLabel('LM Studio server address').fill('http://localhost:1234');
  await page.getByRole('button',{name:'Save address and connect'}).click();
  await expect.poll(()=>endpoint).toBe('http://127.0.0.1:1234');
  await page.getByLabel('Local model').selectOption('fixture/model');
  await expect.poll(()=>selectedModel).toBe('fixture/model');
  await page.getByRole('button',{name:'Run harmless tool-use diagnostic'}).click();
  await expect(page.getByText('The selected model completed a validated harmless tool round trip.')).toBeVisible();
  expect(diagnostics).toBe(1);
});
