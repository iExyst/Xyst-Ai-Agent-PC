import {test,expect} from '@playwright/test';

test('reopens the latest unfinished run on connection for owner recovery without resuming it',async({page})=>{
  const stamp='2026-11-08T12:00:00Z';
  const blocked={id:'blocked-after-restart',objective:'Recover the interrupted workspace operation',state:'blocked',revision:7,acceptance_criteria:[],created_at:stamp};
  const calls:{method:string;path:string}[]=[];
  page.on('request',request=>{
    const url=new URL(request.url());
    if(url.hostname==='127.0.0.1')calls.push({method:request.method(),path:url.pathname});
  });
  await page.route('http://127.0.0.1:8765/**',async route=>{
    const request=route.request();
    const url=new URL(request.url());
    const path=url.pathname;
    const headers={'access-control-allow-origin':'http://127.0.0.1:5173','access-control-allow-headers':'authorization,content-type,last-event-id','access-control-allow-methods':'GET,POST,OPTIONS'};
    if(request.method()==='OPTIONS')return route.fulfill({status:204,headers});
    if(path==='/models')return route.fulfill({json:[{id:'fixture/model'}],headers});
    if(path==='/providers/lm-studio/config')return route.fulfill({json:{endpoint:'http://127.0.0.1:1234',selected_model:'fixture/model'},headers});
    if(path.startsWith('/memory'))return route.fulfill({json:[],headers});
    if(path==='/runs/latest-unfinished')return route.fulfill({json:blocked,headers});
    if(path==='/runs'){
      expect(['20','50']).toContain(url.searchParams.get('limit'));
      return route.fulfill({json:{items:[blocked],next_cursor:null},headers});
    }
    if(path.endsWith('/plan'))return route.fulfill({json:{tasks:[],ready:[],blocked:[]},headers});
    if(path.endsWith('/workspace-write-reconciliations'))return route.fulfill({json:[{call_id:'write-call-1',operation:'edit',path:'src/config.txt',expected_sha256:'a'.repeat(64),desired_sha256:'b'.repeat(64)}],headers});
    if(path.endsWith('/events/stream'))return route.fulfill({headers,contentType:'text/event-stream',body:'event: done\ndata: {}\n\n'});
    if(path.endsWith('/workspace-write-reconcile'))return route.fulfill({json:[],headers});
    if(path.endsWith('/blocked-after-restart'))return route.fulfill({json:blocked,headers});
    if(request.method()==='GET')return route.fulfill({json:[],headers});
    throw new Error(`Unexpected API request: ${request.method()} ${path}`);
  });
  page.on('dialog',dialog=>void dialog.accept());
  await page.goto('/');
  await page.getByLabel('Engine token').fill('x'.repeat(32));
  await page.getByRole('button',{name:'Connect',exact:true}).click();
  await expect.poll(()=>calls.some(call=>call.method==='GET'&&call.path==='/runs/latest-unfinished')).toBe(true);
  await expect(page.getByRole('heading',{name:'Active run blocked'})).toBeVisible();
  await expect(page.getByRole('heading',{name:'Uncertain workspace writes'})).toBeVisible();
  await page.getByRole('button',{name:'Inspect uncertain workspace writes'}).click();
  await expect(page.getByText('Edit src/config.txt',{exact:false})).toBeVisible();
  await page.getByRole('button',{name:'Confirm write did not apply'}).click();
  await expect(page.getByText('The interrupted write was recorded as not applied. Review the run, then use Resume to continue.',{exact:true})).toBeVisible();
  expect(calls.some(call=>call.method==='POST'&&call.path.endsWith('/resume'))).toBe(false);
  expect(calls.some(call=>call.path==='/v1/chat/completions')).toBe(false);
});
