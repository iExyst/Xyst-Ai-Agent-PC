import {test,expect} from '@playwright/test';
import {spawn} from 'node:child_process';
import {randomBytes,randomUUID} from 'node:crypto';
import {mkdir,open,writeFile} from 'node:fs/promises';
import {createServer} from 'node:net';
import path from 'node:path';
import {navigateMainSection} from './navigation';

test('real desktop npm review flow reaches the live WSL installer',async({page,request})=>{
  test.skip(process.env.XAA_NPM_BROWSER_LIVE!=='1','Explicit Windows/WSL/npm registry acceptance only');
  test.setTimeout(600000);
  const distribution=process.env.XAA_NPM_BROWSER_DISTRO??'Ubuntu-24.04';
  const root=path.resolve('.acceptance',`npm-browser-live-${randomUUID()}`);
  await mkdir(root,{recursive:true});
  const packageRoot=path.join(root,'fixture-plugin');
  const skillRoot=path.join(packageRoot,'skills','live-npm-check');
  await mkdir(path.join(skillRoot,'scripts'),{recursive:true});
  const manifest={name:'xaa-live-browser-npm',version:'1.0.0',dependencies:{'is-odd':'^3.0.1'}};
  const lock={name:manifest.name,version:manifest.version,lockfileVersion:3,requires:true,packages:{
    '':{name:manifest.name,version:manifest.version,dependencies:manifest.dependencies},
    'node_modules/is-odd':{version:'3.0.1',resolved:'https://registry.npmjs.org/is-odd/-/is-odd-3.0.1.tgz',integrity:'sha512-CQpnWPrDwmP1+SMHXZhtLtJv90yiyVfluGsX5iNCVkrhQtU3TQHsUWPG9wkdk9Lgd5yNpAg9jQEo90CBaXgWMA==',dependencies:{'is-number':'^6.0.0'}},
    'node_modules/is-number':{version:'6.0.0',resolved:'https://registry.npmjs.org/is-number/-/is-number-6.0.0.tgz',integrity:'sha512-Wu1VHeILBK8KAWJUAiSZQX94GmOE45Rg6/538fKwiloUu21KncEkYGPqob2oSZ5mUT73vLGrHQjKw3KMPwfDzg=='},
  }};
  await writeFile(path.join(packageRoot,'plugin.json'),JSON.stringify({$schema:'https://agent-plugins.org/schemas/1.0.0/plugin.schema.json',name:'xaa-live-browser-npm',version:'1.0.0',description:'Live browser acceptance fixture.'}));
  await writeFile(path.join(skillRoot,'SKILL.md'),[
    '---','name: live-npm-check','description: Verify the live approved npm dependency flow.','dependencies:','  npm: [is-odd@^3.0.1]','allowed-tools: shell.exec','---','Inspect the imported package in X.A.A.','',
    '[Manifest](package.json) [Lock](package-lock.json) [Script](scripts/check.cjs)','',
  ].join('\n'));
  await writeFile(path.join(skillRoot,'package.json'),JSON.stringify(manifest));
  await writeFile(path.join(skillRoot,'package-lock.json'),JSON.stringify(lock));
  await writeFile(path.join(skillRoot,'scripts','check.cjs'),"const isOdd=require('is-odd');if(!isOdd(7)||isOdd(2))process.exit(1);console.log('live-browser-module-ok');\n");

  const listener=createServer();await new Promise<void>(resolve=>listener.listen(0,'127.0.0.1',resolve));
  const address=listener.address();if(!address||typeof address==='string')throw new Error('No test port');
  const port=address.port;await new Promise<void>((resolve,reject)=>listener.close(error=>error?reject(error):resolve()));
  const token=randomBytes(32).toString('hex');const base=`http://127.0.0.1:${port}`;
  const headers={Authorization:`Bearer ${token}`};const log=await open(path.join(root,'engine.log'),'w');
  const engine=spawn(path.resolve('.venv/Scripts/python.exe'),['-I','-m','xaa.api'],{windowsHide:true,stdio:['ignore',log.fd,log.fd],env:{...process.env,XAA_API_TOKEN:token,XAA_API_PORT:String(port),XAA_DATA_DIR:path.join(root,'data'),XAA_UI_ORIGIN:'http://127.0.0.1:5173'}});
  let skillId:string|undefined;let environmentDigest:string|undefined;let installed=false;
  const report:Record<string,unknown>={distribution,model_inference:'not used',registry_packages:['is-odd@3.0.1','is-number@6.0.0'],steps:[]};
  const steps:string[]=[];
  try{
    await expect.poll(async()=>{try{return(await request.get(`${base}/runs`,{headers})).status();}catch{return 0;}},{timeout:30000}).toBe(200);
    const imported=await request.post(`${base}/extensions/packages/import-skill`,{headers,data:{root:packageRoot,package_name:'xaa-live-browser-npm',skill_name:'live-npm-check'}});
    expect(imported.status(),await imported.text()).toBe(201);skillId=(await imported.json()).id;steps.push('authenticated live package import');
    await page.addInitScript(({apiBase,apiToken})=>{
      (window as unknown as {__TAURI_INTERNALS__:unknown}).__TAURI_INTERNALS__={invoke:async(command:string)=>{if(command!=='engine_connection')throw new Error(`Unexpected IPC command: ${command}`);return{base_url:apiBase,token:apiToken};}};
    },{apiBase:base,apiToken:token});
    await page.goto('/');await navigateMainSection(page,'Tools & integrations');
    await page.locator('details').filter({has:page.locator('summary').filter({hasText:'Command sandbox setup'})}).evaluate(element=>{(element as HTMLDetailsElement).open=true;});
    await page.getByRole('button',{name:'Find installed distributions'}).click();
    await page.getByLabel('Execution distribution').selectOption(distribution);
    await page.getByRole('button',{name:'Check sandbox',exact:true}).click();
    await expect(page.getByText('Sandbox check passed.',{exact:true})).toBeVisible({timeout:60000});
    steps.push('real WSL sandbox selected in desktop');
    await page.locator('summary').filter({hasText:'Reusable procedures'}).click();
    const panel=page.getByRole('region',{name:'Procedure candidates',exact:true});
    await panel.getByRole('button',{name:'Inspect live-npm-check',exact:true}).click();
    const npmReview=panel.getByRole('region',{name:'npm dependency lock review'});
    await expect(npmReview).toBeVisible({timeout:15000});
    await npmReview.getByRole('button',{name:'Inspect and prepare npm lock'}).click();
    await expect(npmReview).toContainText('is-odd 3.0.1',{timeout:30000});
    await npmReview.locator('details summary').click();
    await expect(npmReview.getByRole('button',{name:'Approve this npm lock'})).toBeDisabled();
    await npmReview.getByRole('button',{name:'Fetch and verify exact tarballs'}).click();
    await expect(npmReview).toContainText('Lifecycle scripts: none detected',{timeout:90000});
    steps.push('live linked lock inspection and registry acquisition through rendered UI');
    await expect(npmReview.getByRole('button',{name:'Approve this npm lock'})).toBeEnabled();
    await npmReview.getByRole('button',{name:'Approve this npm lock'}).click();
    await expect(npmReview).toContainText('approved',{timeout:15000});
    await npmReview.getByRole('button',{name:'Install approved environment'}).click();
    await expect(npmReview).toContainText('Installed environment ·',{timeout:300000});installed=true;
    steps.push('owner approval and offline WSL install through rendered UI');
    const apiLocks=await request.get(`${base}/skills/${skillId}/versions/1/npm-dependency-locks`,{headers});
    expect(apiLocks.ok()).toBeTruthy();const records=(await apiLocks.json()).locks as Array<{environment_digest:string;environment_installed:boolean}>;
    const record=records.find(item=>item.environment_installed);expect(record).toBeDefined();environmentDigest=record!.environment_digest;
    await npmReview.getByRole('button',{name:'Remove installed environment'}).click();
    await expect(npmReview.getByRole('button',{name:'Install approved environment'})).toBeVisible({timeout:60000});installed=false;
    steps.push('exact environment removal through rendered UI and API receipt confirmation');
    report.steps=steps;report.skill_id=skillId;report.environment_digest=environmentDigest;report.exact_environment_removed=true;report.network_during_install='none';
  }finally{
    if(installed&&skillId&&environmentDigest){
      const cleanup=await request.delete(`${base}/skills/${skillId}/versions/1/npm-dependency-locks/${environmentDigest}/install`,{headers,timeout:60000}).catch(()=>undefined);
      report.cleanup_api_status=cleanup?.status()??null;
    }
    report.engine_exit_code=await new Promise<number|null>(resolve=>{if(engine.exitCode!==null)return resolve(engine.exitCode);engine.once('exit',code=>resolve(code));engine.kill();setTimeout(()=>resolve(null),5000).unref();});
    await log.close();report.steps=steps;await writeFile(path.join(root,'report.json'),JSON.stringify(report,null,2)+'\n').catch(()=>undefined);
  }
});
