import {test,expect} from '@playwright/test';
import {navigateMainSection} from './navigation';

test('saves a portable verified backup through the desktop save dialog',async({page})=>{
  const exportedPath='C:\\Users\\owner\\Documents\\xaa-test.xaa-backup';
  const commands:string[]=[];
  await page.addInitScript(({exportedPath,commands})=>{
    (window as unknown as {__TAURI_INTERNALS__:{invoke:(command:string)=>Promise<unknown>}}).__TAURI_INTERNALS__={
      invoke:async(command:string)=>{
        commands.push(command);
        if(command==='engine_connection')return{base_url:'http://127.0.0.1:8765',token:'x'.repeat(32)};
        if(command==='get_close_behavior')return'keep_running';
        if(command==='get_start_on_login')return false;
        if(command==='plugin:dialog|save')return exportedPath;
        if(command==='export_backup_bundle')return exportedPath;
        throw new Error(`Unexpected Tauri command: ${command}`);
      },
    };
    (window as unknown as {__backupCommands:string[]}).__backupCommands=commands;
  },{exportedPath,commands});
  await page.route('http://127.0.0.1:8765/**',async route=>{
    const request=route.request();
    const url=new URL(request.url());
    const headers={'access-control-allow-origin':'http://127.0.0.1:5173','access-control-allow-headers':'authorization,content-type','access-control-allow-methods':'GET,POST,OPTIONS'};
    if(request.method()==='OPTIONS')return route.fulfill({status:204,headers});
    if(url.pathname==='/runs')return route.fulfill({json:{items:[],next_cursor:null},headers});
    if(url.pathname==='/runs/latest-unfinished')return route.fulfill({json:null,headers});
    if(url.pathname==='/models')return route.fulfill({json:[],headers});
    if(url.pathname==='/providers/lm-studio/config')return route.fulfill({json:{endpoint:'http://127.0.0.1:1234',selected_model:null},headers});
    if(url.pathname==='/backups'&&request.method()==='POST')return route.fulfill({json:{path:'C:\\Users\\owner\\AppData\\Local\\XAA\\backups\\verified.xaa-backup',status:'verified'},headers});
    return route.fulfill({json:[],headers});
  });
  await page.goto('/');
  await expect(page.getByText('Engine ready',{exact:true})).toBeVisible();
  await navigateMainSection(page,'Settings');
  await page.getByRole('button',{name:'Save verified backup'}).click();
  await expect(page.getByRole('status')).toHaveText(`Verified backup saved to ${exportedPath}`);
  expect(await page.evaluate(()=>(window as unknown as {__backupCommands:string[]}).__backupCommands)).toContain('export_backup_bundle');
});
