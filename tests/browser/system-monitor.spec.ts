import {test,expect} from '@playwright/test';
import {navigateMainSection} from './navigation';

test('system monitor shows local capacity and inference queue without exposing request data',async({page})=>{
  let samples=0;
  const headers={'access-control-allow-origin':'http://127.0.0.1:5173','access-control-allow-headers':'authorization,content-type,last-event-id','access-control-allow-methods':'GET,POST,OPTIONS'};
  await page.route('http://127.0.0.1:8765/**',async route=>{
    const request=route.request();const path=new URL(request.url()).pathname;
    if(request.method()==='OPTIONS')return route.fulfill({status:204,headers});
    if(path==='/system/resources'){
      samples++;
      return route.fulfill({headers,json:{sampled_at:'2026-11-14T17:00:00Z',platform:'win32',logical_processors:16,cpu_percent:42.5,memory_total_bytes:32*1024**3,memory_available_bytes:8*1024**3,memory_used_percent:75,disk_total_bytes:2*1024**4,disk_free_bytes:512*1024**3,queue_active:true,queue_waiting:2,queue_reserved_tokens:640,queue_max_reserved_tokens:2_000_000,queue_max_pending:16,accelerator_telemetry_available:false}});
    }
    if(path==='/runs/latest-unfinished')return route.fulfill({headers,json:null});
    if(path==='/runs')return route.fulfill({headers,json:{items:[],next_cursor:null}});
    if(path==='/models')return route.fulfill({headers,json:[{id:'fixture/model'}]});
    if(path==='/providers')return route.fulfill({headers,json:[{id:'lm-studio',status:'connected'}]});
    if(path==='/execution/distributions')return route.fulfill({headers,json:{state:'unsupported',distributions:[]}});
    return route.fulfill({status:404,headers});
  });
  await page.goto('/');
  await page.getByLabel('Engine token').fill('x'.repeat(32));
  await page.getByRole('button',{name:'Connect',exact:true}).click();
  await navigateMainSection(page,'System monitor');
  await expect(page.getByRole('region',{name:'Processor'})).toContainText('42.5%');
  await expect(page.getByRole('region',{name:'Memory'})).toContainText('75.0% used');
  await expect(page.getByRole('region',{name:'Profile drive'})).toContainText('75.0% used');
  await expect(page.getByRole('region',{name:'Inference queue'})).toContainText('2 waiting');
  await expect(page.getByText(/GPU telemetry is unavailable/)).toBeVisible();
  expect(samples).toBe(1);
});
