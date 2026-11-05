import {expect,test} from '@playwright/test';

test('save Telegram token securely, pair an account and clear the credential',async({page})=>{
  const errors:string[]=[];const requests:any[]=[];
  let status:any={configured:false,bot_username:null,linked_users:0,processed_updates:0,deliveries:{}};
  await page.on('pageerror',error=>errors.push(error.message));
  await page.route('http://127.0.0.1:8765/**',async route=>{
    const request=route.request(),url=new URL(request.url()),path=url.pathname;
    const headers={'access-control-allow-origin':'http://127.0.0.1:5173','access-control-allow-headers':'authorization,content-type,last-event-id','access-control-allow-methods':'GET,POST,PUT,DELETE,OPTIONS'};
    if(request.method()==='OPTIONS')return route.fulfill({status:204,headers});
    expect(request.headers()['authorization']).toBe(`Bearer ${'x'.repeat(32)}`);
    if(path==='/models')return route.fulfill({json:[{id:'fixture/model'}],headers});
    if(path==='/providers')return route.fulfill({json:[{id:'lm-studio',status:'connected'}],headers});
    if(path==='/providers/lm-studio/config')return route.fulfill({json:{endpoint:'http://127.0.0.1:1234',selected_model:null},headers});
    if(path==='/providers/lm-studio/credential')return route.fulfill({json:{configured:false},headers});
    if(path==='/runs')return route.fulfill({json:{items:[],next_cursor:null},headers});
    if(path==='/memory'||path==='/memory/export')return route.fulfill({json:[],headers});
    if(path.startsWith('/execution/'))return route.fulfill({json:[],headers});
    if(path==='/channels/telegram'&&request.method()==='GET')return route.fulfill({json:status,headers});
    if(path==='/channels/telegram/credential'&&request.method()==='PUT'){
      const body=request.postDataJSON();requests.push(body);expect(body.token).toBe('123456:telegram-test-secret');
      status={configured:true,bot_username:'xaa_test_bot',linked_users:0,processed_updates:0,deliveries:{}};
      return route.fulfill({json:status,headers});
    }
    if(path==='/channels/telegram/credential'&&request.method()==='DELETE'){
      status={configured:false,bot_username:null,linked_users:0,processed_updates:0,deliveries:{}};
      return route.fulfill({json:status,headers});
    }
    if(path==='/channels/telegram/pairing'&&request.method()==='POST'){
      status={...status,linked_users:1};
      return route.fulfill({status:201,json:{code:'one-time-pair-code',expires_at:'2030-01-01T12:05:00Z'},headers});
    }
    if(path==='/channels/telegram/links'&&request.method()==='DELETE'){
      status={...status,linked_users:0};
      return route.fulfill({json:{revoked_users:1,linked_users:0},headers});
    }
    return route.fulfill({json:{status:'ok'},headers});
  });
  await page.goto('/');
  await page.getByLabel('Engine token').fill('x'.repeat(32));
  await page.getByRole('button',{name:'Connect',exact:true}).click();
  await page.locator('summary').filter({hasText:'Messaging channels'}).click();
  const panel=page.getByRole('region',{name:'Messaging channels',exact:true});
  await panel.getByLabel('Telegram bot token',{exact:true}).fill('123456:telegram-test-secret');
  await panel.getByRole('button',{name:'Verify and save bot token',exact:true}).click();
  await expect(panel.getByLabel('Telegram bot token',{exact:true})).toHaveValue('');
  await expect(panel.getByText('Connected bot: @xaa_test_bot')).toBeVisible();
  expect(requests).toHaveLength(1);
  await panel.getByRole('button',{name:'Generate one-time pairing code',exact:true}).click();
  await expect(panel.getByLabel('Telegram pairing instructions')).toContainText('/pair one-time-pair-code');
  await panel.getByRole('button',{name:'Refresh channel status',exact:true}).click();
  await panel.getByRole('button',{name:'Revoke all linked users',exact:true}).click();
  await panel.getByRole('button',{name:'Confirm revoke links',exact:true}).click();
  await expect(panel.getByText(/0 linked user/)).toBeVisible();
  await panel.getByRole('button',{name:'Clear saved token',exact:true}).click();
  await expect(panel.getByText('Telegram bot is not configured')).toBeVisible();
  expect(errors).toEqual([]);
});
