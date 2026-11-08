import {expect,test} from '@playwright/test';

test('save Telegram token securely, pair an account and clear the credential',async({page})=>{
  const errors:string[]=[];const requests:any[]=[];
  let status:any={configured:false,bot_username:null,linked_users:0,processed_updates:0,deliveries:{},unknown_deliveries:[]};
  let discordStatus:any={configured:false,bot_id:null,bot_username:null,linked_users:0,processed_messages:0,deliveries:{},unknown_deliveries:[]};
  let emailStatus:any={configured:false,address:null,processed_messages:0,deliveries:{},unknown_deliveries:[]};
  let webhookStatus:any={configured:false};
  let identityBindings:any[]=[
    {id:'telegram-binding',channel:'telegram',account_id:'bot-1',external_identity:'456',session_id:'telegram-session',session_channel:'telegram',identity_link_id:null,authorized:true,title:'Telegram history',link_created_at:null},
    {id:'email-binding',channel:'email',account_id:'mailbox-1',external_identity:'owner@example.test',session_id:'email-session',session_channel:'email',identity_link_id:null,authorized:true,title:'Email history',link_created_at:null},
  ];
  let identityLinkId='';
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
    if(path==='/channels/identity-bindings'&&request.method()==='GET')return route.fulfill({json:identityBindings,headers});
    if(path==='/channels/identity-links'&&request.method()==='POST'){
      const body=request.postDataJSON();expect(body.binding_ids).toEqual(['telegram-binding','email-binding']);expect(body.shared_history_binding_id).toBe('telegram-binding');
      identityLinkId='shared-identity-link';const selected=identityBindings.find(binding=>binding.id==='telegram-binding');
      identityBindings=identityBindings.map(binding=>({...binding,identity_link_id:identityLinkId,session_id:selected.session_id,session_channel:selected.session_channel}));
      return route.fulfill({status:201,json:{id:identityLinkId,shared_history_binding_id:'telegram-binding'},headers});
    }
    if(path==='/channels/identity-links/shared-identity-link'&&request.method()==='DELETE'){
      expect(request.postDataJSON()).toEqual({confirmed:true});
      identityBindings=identityBindings.map((binding,index)=>({...binding,identity_link_id:null,session_id:`unlinked-session-${index}`,session_channel:binding.channel}));
      return route.fulfill({json:{unlinked_bindings:2},headers});
    }
    if(path==='/memory'||path==='/memory/export')return route.fulfill({json:[],headers});
    if(path.startsWith('/execution/'))return route.fulfill({json:[],headers});
    if(path==='/channels/telegram'&&request.method()==='GET')return route.fulfill({json:status,headers});
    if(path==='/channels/discord'&&request.method()==='GET')return route.fulfill({json:discordStatus,headers});
    if(path==='/channels/discord/credential'&&request.method()==='PUT'){
      const body=request.postDataJSON();requests.push(body);expect(body.token).toBe('discord-test-secret');
      discordStatus={configured:true,bot_id:'100000000000000001',bot_username:'xaa-test',linked_users:0,processed_messages:0,deliveries:{unknown:1},unknown_deliveries:[{message_id:'51',user_id:'300000000000000003',run_id:'discord-run',updated_at:'2026-11-08T12:00:00Z'}]};
      return route.fulfill({json:discordStatus,headers});
    }
    if(path==='/channels/discord/deliveries/51/reconcile'&&request.method()==='POST'){
      expect(request.postDataJSON()).toEqual({resolution:'retry',confirmed_destination_checked:true});
      discordStatus={...discordStatus,deliveries:{pending:1},unknown_deliveries:[]};
      return route.fulfill({json:discordStatus,headers});
    }
    if(path==='/channels/discord/credential'&&request.method()==='DELETE'){
      discordStatus={...discordStatus,configured:false};return route.fulfill({json:discordStatus,headers});
    }
    if(path==='/channels/discord/pairing'&&request.method()==='POST'){
      discordStatus={...discordStatus,linked_users:1};
      return route.fulfill({status:201,json:{code:'discord-one-time-code',expires_at:'2030-01-01T12:05:00Z'},headers});
    }
    if(path==='/channels/discord/links'&&request.method()==='DELETE'){
      discordStatus={...discordStatus,linked_users:0};
      return route.fulfill({json:{revoked_users:1,linked_users:0},headers});
    }
    if(path==='/channels/telegram/credential'&&request.method()==='PUT'){
      const body=request.postDataJSON();requests.push(body);expect(body.token).toBe('123456:telegram-test-secret');
      status={configured:true,bot_username:'xaa_test_bot',linked_users:0,processed_updates:2,deliveries:{unknown:1},unknown_deliveries:[{update_id:17,user_id:'456',run_id:'fixture-run',updated_at:'2026-11-08T12:00:00Z'}]};
      return route.fulfill({json:status,headers});
    }
    if(path==='/channels/telegram/deliveries/17/reconcile'&&request.method()==='POST'){
      expect(request.postDataJSON()).toEqual({resolution:'retry',confirmed_destination_checked:true});
      status={...status,deliveries:{pending:1},unknown_deliveries:[]};
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
    if(path==='/channels/email'&&request.method()==='GET')return route.fulfill({json:emailStatus,headers});
    if(path==='/channels/email/credential'&&request.method()==='PUT'){
      const body=request.postDataJSON();requests.push(body);expect(body.password).toBe('email-app-password');expect(body.authserv_id).toBe('mx.example.test');
      emailStatus={configured:true,address:'agent@example.test',processed_messages:1,deliveries:{unknown:1},unknown_deliveries:[{uid_validity:'77',uid:'3',sender:'owner@example.test',run_id:'email-fixture-run',updated_at:'2026-11-08T12:00:00Z'}]};
      return route.fulfill({json:emailStatus,headers});
    }
    if(path==='/channels/email/deliveries/77/3/reconcile'&&request.method()==='POST'){
      expect(request.postDataJSON()).toEqual({resolution:'retry',confirmed_destination_checked:true});
      emailStatus={...emailStatus,deliveries:{pending:1},unknown_deliveries:[]};
      return route.fulfill({json:emailStatus,headers});
    }
    if(path==='/channels/email/credential'&&request.method()==='DELETE'){
      emailStatus={configured:false,address:null,processed_messages:0,deliveries:{}};
      return route.fulfill({json:emailStatus,headers});
    }
    if(path==='/channels/webhook'&&request.method()==='GET')return route.fulfill({json:webhookStatus,headers});
    if(path==='/channels/webhook/credential'&&request.method()==='POST'){
      webhookStatus={configured:true};
      return route.fulfill({json:{configured:true,secret:'one-time-webhook-secret-which-is-long-enough'},headers});
    }
    if(path==='/channels/webhook/credential'&&request.method()==='DELETE'){
      webhookStatus={configured:false};
      return route.fulfill({json:webhookStatus,headers});
    }
    return route.fulfill({json:{status:'ok'},headers});
  });
  await page.goto('/');
  await page.getByLabel('Engine token').fill('x'.repeat(32));
  await page.getByRole('button',{name:'Connect',exact:true}).click();
  await page.locator('summary').filter({hasText:'Messaging channels'}).click();
  const panel=page.getByRole('region',{name:'Messaging channels',exact:true});
  await panel.getByLabel('First authorized account').selectOption('telegram-binding');
  await panel.getByLabel('Second account from another channel').selectOption('email-binding');
  await panel.getByRole('radio',{name:/telegram · 456/}).check();
  await panel.getByRole('button',{name:'Link accounts and share selected history'}).click();
  await expect(panel.getByRole('list',{name:'Active cross-channel identity links'})).toContainText('telegram: 456 ↔ email: owner@example.test');
  await panel.getByRole('button',{name:'Unlink these accounts'}).click();
  await panel.getByRole('button',{name:'Confirm unlink'}).click();
  await expect(panel.getByRole('list',{name:'Active cross-channel identity links'})).toHaveCount(0);
  await panel.getByLabel('Discord bot token',{exact:true}).fill('discord-test-secret');
  await panel.getByRole('button',{name:'Verify and save Discord bot token',exact:true}).click();
  await expect(panel.getByLabel('Discord bot token',{exact:true})).toHaveValue('');
  await expect(panel.getByText('Connected bot: xaa-test')).toBeVisible();
  await expect(panel.getByRole('region',{name:'Discord replies needing review'})).toBeVisible();
  await panel.getByRole('button',{name:'I checked; retry Discord reply'}).click();
  await panel.getByRole('button',{name:'Confirm Discord retry'}).click();
  await expect(panel.getByRole('region',{name:'Discord replies needing review'})).toHaveCount(0);
  await panel.getByRole('button',{name:'Generate one-time Discord pairing code',exact:true}).click();
  await expect(panel.getByLabel('Discord pairing instructions')).toContainText('/pair discord-one-time-code');
  await panel.getByRole('button',{name:'Refresh Discord status',exact:true}).click();
  await panel.getByRole('button',{name:'Revoke all Discord users',exact:true}).click();
  await panel.getByRole('button',{name:'Confirm revoke Discord links',exact:true}).click();
  await panel.getByRole('button',{name:'Clear saved Discord token',exact:true}).click();
  await expect(panel.getByText('Discord bot is not configured')).toBeVisible();
  await panel.getByLabel('Telegram bot token',{exact:true}).fill('123456:telegram-test-secret');
  await panel.getByRole('button',{name:'Verify and save bot token',exact:true}).click();
  await expect(panel.getByLabel('Telegram bot token',{exact:true})).toHaveValue('');
  await expect(panel.getByText('Connected bot: @xaa_test_bot')).toBeVisible();
  expect(requests).toHaveLength(2);
  await panel.getByRole('button',{name:'Generate one-time pairing code',exact:true}).click();
  await expect(panel.getByLabel('Telegram pairing instructions')).toContainText('/pair one-time-pair-code');
  await expect(panel.getByText(/Update 17 · Telegram user 456/)).toBeVisible();
  await panel.getByRole('button',{name:'I checked; retry reply',exact:true}).click();
  await panel.getByRole('button',{name:'Confirm retry',exact:true}).click();
  await expect(panel.getByText('Unconfirmed replies')).toHaveCount(0);
  await panel.getByRole('button',{name:'Refresh channel status',exact:true}).click();
  await panel.getByRole('button',{name:'Revoke all linked users',exact:true}).click();
  await panel.getByRole('button',{name:'Confirm revoke links',exact:true}).click();
  await expect(panel.getByText(/0 linked user/)).toBeVisible();
  await panel.getByRole('button',{name:'Clear saved token',exact:true}).click();
  await expect(panel.getByText('Telegram bot is not configured')).toBeVisible();
  await panel.getByRole('button',{name:'Create webhook secret',exact:true}).click();
  await expect(panel.getByLabel('New webhook secret')).toContainText('one-time-webhook-secret-which-is-long-enough');
  await panel.getByRole('button',{name:'Disable webhook ingress',exact:true}).click();
  await expect(panel.getByText('Signed webhook ingress is disabled.')).toBeVisible();
  await panel.getByLabel('Mailbox address').fill('agent@example.test');
  await panel.getByLabel('Mailbox password or app password').fill('email-app-password');
  await panel.getByLabel('IMAP server').fill('imap.example.test');
  await panel.getByLabel('SMTP server').fill('smtp.example.test');
  await panel.getByLabel('Authorized sender addresses (comma separated)').fill('owner@example.test');
  await panel.getByLabel('Receiving mail server authserv-id').fill('mx.example.test');
  await panel.getByRole('button',{name:'Save email settings',exact:true}).click();
  await expect(panel.getByLabel('Mailbox password or app password')).toHaveValue('');
  await expect(panel.getByText('Configured mailbox: agent@example.test')).toBeVisible();
  await expect(panel.getByText(/Message UID 3 · sender owner@example.test/)).toBeVisible();
  await panel.getByRole('button',{name:'I checked; retry email',exact:true}).click();
  await panel.getByRole('button',{name:'Confirm retry',exact:true}).click();
  await expect(panel.getByText('Unconfirmed email replies')).toHaveCount(0);
  await panel.getByRole('button',{name:'Clear email credentials',exact:true}).click();
  await expect(panel.getByText('Email is not configured')).toBeVisible();
  expect(requests).toHaveLength(3);
  expect(errors).toEqual([]);
});
