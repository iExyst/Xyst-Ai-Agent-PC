import {test,expect} from '@playwright/test';
import {spawn} from 'node:child_process';
import {randomBytes,randomUUID} from 'node:crypto';
import {mkdir,open,writeFile,readFile} from 'node:fs/promises';
import {createServer} from 'node:net';
import path from 'node:path';

test('real sandbox setup selects installed distro and runs fixed controls check',async({page,request})=>{
  test.skip(process.env.XAA_EXECUTION_LIVE!=='1','Explicit Windows/WSL readiness acceptance only');
  test.setTimeout(240000);
  const distribution=process.env.XAA_EXECUTION_DISTRO;
  if(!distribution)throw new Error('Select an existing distribution for live acceptance');
  const root=path.resolve('.acceptance',randomUUID());await mkdir(root,{recursive:true});
  const model=process.env.XAA_EXECUTION_MODEL;let instance:string|undefined;
  const project=path.join(root,'project');await mkdir(project);const code='CHECK-PASSED-'+randomUUID().replaceAll('-','');
  const script=`from pathlib import Path\nPath('private-generated.txt').write_text('private only')\nprint('${code}')\n`;
  await writeFile(path.join(project,'check.py'),script);
  const listener=createServer();await new Promise<void>(resolve=>listener.listen(0,'127.0.0.1',resolve));
  const address=listener.address();if(!address||typeof address==='string')throw new Error('No test port');
  const port=address.port;await new Promise<void>((resolve,reject)=>listener.close(error=>error?reject(error):resolve()));
  const token=randomBytes(32).toString('hex');const base=`http://127.0.0.1:${port}`;
  const headers={Authorization:`Bearer ${token}`};const log=await open(path.join(root,'engine.log'),'w');
  const engine=spawn(path.resolve('.venv/Scripts/python.exe'),['-I','-m','xaa.api'],{windowsHide:true,stdio:['ignore',log.fd,log.fd],env:{...process.env,XAA_API_TOKEN:token,XAA_API_PORT:String(port),XAA_DATA_DIR:path.join(root,'data'),XAA_EXACT_CONTEXT:'1',XAA_UI_ORIGIN:'http://127.0.0.1:5173'}});
  try {
    await expect.poll(async()=>{try{return(await request.get(base+'/runs',{headers})).status();}catch{return 0;}},{timeout:20000}).toBe(200);
    if(model){const loaded=await request.post(base+'/models/load',{headers,data:{model,context_length:32768},timeout:60000});expect(loaded.ok()).toBeTruthy();instance=(await loaded.json()).instance_id;}
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
    if(model){
      await page.getByLabel('Workspace permissions').selectOption('workspace');
      await page.getByLabel('Workspace path').fill(project);
      await page.getByLabel('Local model').selectOption(model);
      await page.getByLabel('Objective',{exact:true}).fill('Please execute the existing project check.py using workspace_run_command with argv ["/usr/bin/python3", "-I", "check.py"] and max_seconds 10. Then tell me exactly what it printed in stdout. Your final answer must contain the entire stdout line including the CHECK-PASSED- prefix and every character following it. Use only this one command tool call. Do not read or edit files or save a plan.');
      await page.getByText('Run limits',{exact:true}).click();
      await page.getByLabel('Iteration limit').fill('4');
      await page.getByLabel('Output tokens per request (optional)').fill('2048');
      await page.getByLabel('Allow isolated commands for this next run').check();
      const creation=page.waitForResponse(resp=>resp.url()===base+'/runs'&&resp.request().method()==='POST');
      await page.getByRole('button',{name:'Start objective',exact:true}).click();
      const created=await(await creation).json();expect(created.command_grant.distribution).toBe(distribution);
      await expect(page.getByRole('article')).toContainText(code,{timeout:150000});
      await expect(page.getByRole('heading',{name:'Active run verifying',exact:true})).toBeVisible();
      await expect(page.getByText(/Isolated commands authorized for this run/)).toBeVisible();
      await expect(page.getByLabel('Allow isolated commands for this next run')).not.toBeChecked();
      const events=await(await request.get(base+'/runs/'+created.id+'/events',{headers})).json();
      expect(events.filter((event:{event_type:string})=>event.event_type==='execution.completed')).toHaveLength(1);
      expect(events.filter((event:{event_type:string})=>event.event_type==='tool.executing')).toHaveLength(1);
      expect(await readFile(path.join(project,'check.py'),'utf8')).toBe(script);
      await expect(readFile(path.join(project,'private-generated.txt'))).rejects.toThrow();
    }
    await page.screenshot({path:path.join(root,'setup.png'),fullPage:true});
    await writeFile(path.join(root,'report.json'),JSON.stringify({passed:true,distribution,profile_sha256:actual.profile_sha256,checks:actual.checks,run_creates:runCreates,readiness_requests:checkRequests,model:model??null,command_run_verified:!!model,scope:'Edge UI with real source API/WSL and optional real model; test connection shim, not native IPC'},null,2));
  } finally {
    if(instance)await request.post(base+'/models/unload',{headers,data:{instance_id:instance},timeout:20000}).catch(()=>undefined);
    await request.post(base+'/shutdown',{headers,timeout:10000}).catch(()=>undefined);
    await Promise.race([new Promise<void>(resolve=>{if(engine.exitCode!==null)resolve();else engine.once('exit',()=>resolve());}),new Promise(resolve=>setTimeout(resolve,10000))]);
    if(engine.exitCode===null){engine.kill();await new Promise<void>(resolve=>engine.once('exit',()=>resolve()));}
    await log.close();
  }
});
