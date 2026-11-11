import {expect,test} from '@playwright/test';

test('create, inspect, pause and resume a durable local schedule',async({page})=>{
  const errors:string[]=[];
  page.on('pageerror',error=>errors.push(error.message));
  const requests:any[]=[];
  const schedules:any[]=[];
  await page.route('http://127.0.0.1:8765/**',async route=>{
    const request=route.request(),url=new URL(request.url()),path=url.pathname;
    const headers={'access-control-allow-origin':'http://127.0.0.1:5173','access-control-allow-headers':'authorization,content-type,last-event-id','access-control-allow-methods':'GET,POST,PUT,DELETE,OPTIONS'};
    if(request.method()==='OPTIONS')return route.fulfill({status:204,headers});
    expect(request.headers()['authorization']).toBe(`Bearer ${'x'.repeat(32)}`);
    if(path==='/models')return route.fulfill({json:[{id:'fixture/model'}],headers});
    if(path==='/providers')return route.fulfill({json:[{id:'lm-studio',status:'connected'}],headers});
    if(path==='/providers/lm-studio/config')return route.fulfill({json:{endpoint:'http://127.0.0.1:1234',selected_model:'fixture/model'},headers});
    if(path==='/providers/lm-studio/credential')return route.fulfill({json:{configured:false},headers});
    if(path==='/runs/latest-unfinished')return route.fulfill({json:null,headers});
    if(path==='/runs')return route.fulfill({json:{items:[],next_cursor:null},headers});
    if(path==='/memory'||path==='/memory/export')return route.fulfill({json:[],headers});
    if(path.startsWith('/execution/'))return route.fulfill({json:[],headers});
    if(path==='/schedules'&&request.method()==='GET')return route.fulfill({json:schedules,headers});
    if(path==='/channels/telegram/linked-users')return route.fulfill({json:[{user_id:'123456'}],headers});
    if(path==='/schedules'&&request.method()==='POST'){
      const body=request.postDataJSON();requests.push(body);
      const saved={id:'schedule-1',objective:body.objective,recurrence:{frequency:body.frequency,next_at:body.start_at,workspace:body.workspace,model:body.model},timezone:body.timezone,missed_run_policy:body.missed_run_policy,overlap_policy:'skip',quiet_hours:body.quiet_hours??null,delivery_destination:body.telegram_user_id?'telegram:bot-42:'+body.telegram_user_id:null,failure_notification_destination:body.telegram_failure_user_id?'telegram:bot-42:'+body.telegram_failure_user_id:null,enabled:true,created_at:'2026-11-05T00:00:00Z',updated_at:'2026-11-05T00:00:00Z',executions:[{id:'execution-1',occurrence_id:'schedule-1:2026-11-05T12:00',run_id:'run-1',state:'completed',delivery_evidence:'retrying',delivery_attempt_count:2,delivery_next_attempt_at:'2026-11-05T12:00:10+00:00',created_at:'2026-11-05T12:00:00Z',completed_at:'2026-11-05T12:00:01Z'},{id:'execution-2',occurrence_id:'schedule-1:2026-11-06T12:00',run_id:'run-2',state:'completed',delivery_evidence:'unknown',delivery_attempt_count:2,delivery_next_attempt_at:null,created_at:'2026-11-06T12:00:00Z',completed_at:'2026-11-06T12:00:01Z'}]};
      schedules.unshift(saved);return route.fulfill({status:201,json:saved,headers});
    }
    if(path==='/schedules/schedule-1/executions/execution-2/delivery/reconcile'&&request.method()==='POST'){
      expect(request.postDataJSON()).toEqual({resolution:'retry',confirmed_destination_checked:true});
      schedules[0].executions.find((execution:any)=>execution.id==='execution-2').delivery_evidence='pending';
      return route.fulfill({json:schedules[0],headers});
    }
    if(path==='/schedules/schedule-1/enabled'&&request.method()==='PUT'){
      schedules[0].enabled=request.postDataJSON().enabled;schedules[0].updated_at='2026-11-05T00:01:00Z';
      return route.fulfill({json:schedules[0],headers});
    }
    return route.fulfill({json:{status:'ok'},headers});
  });
  await page.goto('/');
  await page.getByLabel('Engine token').fill('x'.repeat(32));
  await page.getByRole('button',{name:'Connect',exact:true}).click();
  const model=page.getByLabel('Local model or compatible provider model',{exact:true});
  await expect(model.locator('option[value="fixture/model"]')).toHaveCount(1);
  await model.selectOption('fixture/model');
  await page.locator('summary').filter({hasText:'Automations'}).click();
  const panel=page.getByRole('region',{name:'Automations',exact:true});
  await panel.getByLabel('Scheduled objective',{exact:true}).fill('Review repository health');
  await panel.getByLabel('Repeat',{exact:true}).selectOption('weekly');
  await panel.getByLabel('If the engine was closed at the scheduled time',{exact:true}).selectOption('run_once');
  await panel.getByLabel('Telegram quiet hours start (optional, local schedule time)',{exact:true}).fill('22:00');
  await panel.getByLabel('Telegram quiet hours end',{exact:true}).fill('07:00');
  await panel.getByLabel('Telegram result destination (optional)',{exact:true}).selectOption('123456');
  await panel.getByLabel('Telegram failure notification destination (optional)',{exact:true}).selectOption('123456');
  await panel.getByRole('button',{name:'Create schedule',exact:true}).click();
  await expect(panel.getByText('Review repository health',{exact:true})).toBeVisible();
  await panel.getByText('Execution history',{exact:true}).click();
  await expect(panel.getByText(/send attempts 2\/3 · retry after 2026-11-05T12:00:10\+00:00/)).toBeVisible();
  await panel.getByRole('button',{name:'I checked; retry scheduled message',exact:true}).click();
  await panel.getByRole('button',{name:'Confirm retry',exact:true}).click();
  await expect(panel.getByText(/Telegram delivery pending/)).toBeVisible();
  expect(requests).toHaveLength(1);
  const browserTimezone=await page.evaluate(()=>Intl.DateTimeFormat().resolvedOptions().timeZone);
  expect(requests[0]).toMatchObject({objective:'Review repository health',frequency:'weekly',missed_run_policy:'run_once',model:'fixture/model',timezone:browserTimezone,telegram_user_id:'123456',telegram_failure_user_id:'123456',quiet_hours:'22:00-07:00'});
  await panel.getByRole('button',{name:'Pause schedule',exact:true}).click();
  await expect(panel.getByRole('button',{name:'Resume schedule',exact:true})).toBeVisible();
  await panel.getByRole('button',{name:'Resume schedule',exact:true}).click();
  await expect(panel.getByRole('button',{name:'Pause schedule',exact:true})).toBeVisible();
  expect(errors).toEqual([]);
});

