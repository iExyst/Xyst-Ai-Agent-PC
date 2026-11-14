import {test,expect} from '@playwright/test';
import {navigateMainSection} from './navigation';

test('saves a portable verified backup through the desktop save dialog',async({page})=>{
  const exportedPath='C:\\Users\\owner\\Documents\\xaa-test.xaa-backup';
  const commands:string[]=[];
  const restoreSelections=['C:\\Users\\owner\\AppData\\Local\\XAA\\backups\\verified.xaa-backup','C:\\Users\\owner\\Documents'];
  await page.addInitScript(({exportedPath,commands,restoreSelections})=>{
    (window as unknown as {__TAURI_INTERNALS__:{invoke:(command:string)=>Promise<unknown>}}).__TAURI_INTERNALS__={
      invoke:async(command:string)=>{
        commands.push(command);
        if(command==='engine_connection')return{base_url:'http://127.0.0.1:8765',token:'x'.repeat(32)};
        if(command==='get_close_behavior')return'keep_running';
        if(command==='get_start_on_login')return false;
        if(command==='get_active_data_root')return null;
        if(command==='take_acceptance_restore_dialog_selection')return{configured:false,path:null};
        if(command==='plugin:dialog|save')return exportedPath;
        if(command==='plugin:dialog|open')return restoreSelections.shift()??null;
        if(command==='export_backup_bundle')return exportedPath;
        if(command==='activate_restored_profile')return null;
        throw new Error(`Unexpected Tauri command: ${command}`);
      },
    };
    (window as unknown as {__backupCommands:string[]}).__backupCommands=commands;
  },{exportedPath,commands,restoreSelections});
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
    if(url.pathname==='/backups/restore'&&request.method()==='POST')return route.fulfill({json:{data_root:'C:\\Users\\owner\\Documents\\XAA-Restored-abc123',database:'C:\\Users\\owner\\Documents\\XAA-Restored-abc123\\XAA\\xaa.sqlite3',status:'verified'},headers});
    return route.fulfill({json:[],headers});
  });
  await page.goto('/');
  await expect(page.getByText('Engine ready',{exact:true})).toBeVisible();
  await navigateMainSection(page,'Settings');
  const backup=page.locator('section').filter({has:page.getByRole('heading',{name:'Portable backup',exact:true})});
  await page.getByRole('button',{name:'Save verified backup'}).click();
  await expect(backup.getByRole('status').filter({hasText:'Verified backup saved to'})).toHaveText(`Verified backup saved to ${exportedPath}`);
  expect(await page.evaluate(()=>(window as unknown as {__backupCommands:string[]}).__backupCommands)).toContain('export_backup_bundle');
  await page.getByRole('button',{name:'Restore backup into a new profile'}).click();
  await expect(backup.getByRole('status').filter({hasText:'Backup verified and restored into a separate profile'})).toBeVisible();
  await page.getByLabel('Restart X.A.A. and use this restored profile').check();
  await page.getByRole('button',{name:'Switch to restored profile and restart'}).click();
  expect(await page.evaluate(()=>(window as unknown as {__backupCommands:string[]}).__backupCommands)).toContain('activate_restored_profile');
});
