import {test,expect} from '@playwright/test';
import {spawn} from 'node:child_process';
import {randomBytes,randomUUID} from 'node:crypto';
import {mkdir,open,writeFile} from 'node:fs/promises';
import {createServer} from 'node:net';
import path from 'node:path';

test('real sandbox setup selects installed distro and runs fixed controls check',async({page,request})=>{
  test.skip(process.env.XAA_EXECUTION_LIVE!=='1','Explicit Windows/WSL readiness acceptance only');
  test.setTimeout(90000);
  const distribution=process.env.XAA_EXECUTION_DISTRO;
  if(!distribution)throw new Error('Select an existing distribution for live acceptance');
  const root=path.resolve('.acceptance',randomUUID());await mkdir(root,{recursive:true});
  const listener=createServer();await new Promise<void>(resolve=>listener.listen(0,'127.0.0.1',resolve));
  const address=listener.address();if(!address||typeof address==='string')throw new Error('No test port');
  const port=address.port;await new Promise<void>((resolve,reject)=>listener.close(error=>error?reject(error):resolve()));
  const token=randomBytes(32).toString('hex');const base=`http://127.0.0.1:${port}`;
  const headers={Authorization:`Bearer ${token}`};const log=await open(path.join(root,'engine.log'),'w');
  const engine=spawn(path.resolve('.venv/Scripts/python.exe'),['-I','-m','xaa.api'],{windowsHide:true,stdio:['ignore',log.fd,log.fd],env:{...process.env,XAA_API_TOKEN:token,XAA_API_PORT:String(port),XAA_DATA_DIR:root,XAA_UI_ORIGIN:'http://127.0.0.1:5173'}});
  try {
    await expect.poll(async()=>{try{return(await request.get(base+'/runs',{headers})).status();}catch{return 0;}},{timeout:20000}).toBe(200);
    let runCreates=0;let checkRequests=0;
    page.on('request',req=>{if(req.url()===base+'/runs'&&req.method()==='POST')runCreates++;if(req.url()===base+'/execution/readiness'&&req.method()==='POST')checkRequests++;});
    await page.addInitScript(({base,token})=>{
      (window as unknown as {__TAURI_INTERNALS__:unknown}).__TAURI_INTERNALS__={invoke:async(cmd:string)=>{if(cmd!=='engine_connection')throw new Error('Unexpected test IPC command');return{base_url:base,token};}};
    },{base,token});
    await page.goto('/');await page.getByText('Command sandbox setup',{exact:true}).click();
    await page.getByRole('button',{name:'Find installed distributions'}).click();
    await page.getByLabel('Execution distribution').selectOption(distribution);
    const response=page.waitForResponse(base+'/execution/readiness');
    await page.getByRole('button',{name:'Check sandbox',exact:true}).click();
    const actual=await(await response).json();expect(actual.state).toBe('ready');
    await expect(page.getByText('Sandbox check passed.',{exact:true})).toBeVisible({timeout:30000});
    await expect(page.getByText(/Network denied/)).toBeVisible();
    expect(runCreates).toBe(0);expect(checkRequests).toBe(1);
    expect((await(await request.get(base+'/runs',{headers})).json()).items).toEqual([]);
    await page.screenshot({path:path.join(root,'setup.png'),fullPage:true});
    await writeFile(path.join(root,'report.json'),JSON.stringify({passed:true,distribution,profile_sha256:actual.profile_sha256,checks:actual.checks,run_creates:0,readiness_requests:1,scope:'Edge UI with real source API/WSL; test connection shim, not native IPC; no permission grant'},null,2));
  } finally {
    await request.post(base+'/shutdown',{headers,timeout:10000}).catch(()=>undefined);
    await Promise.race([new Promise<void>(resolve=>{if(engine.exitCode!==null)resolve();else engine.once('exit',()=>resolve());}),new Promise(resolve=>setTimeout(resolve,10000))]);
    if(engine.exitCode===null){engine.kill();await new Promise<void>(resolve=>engine.once('exit',()=>resolve()));}
    await log.close();
  }
});
