import { expect, test } from '@playwright/test';

test('configure an MCP server, discover tools and explicitly grant write access', async ({ page }) => {
  const errors: string[] = [];
  const requests: {method:string;path:string;body?:any}[] = [];
  const serverId = '1234567890abcdef1234567890abcdef';
  let importedSkill: any;
  const tool = {
    server_id: serverId,
    name: 'change_value',
    title: 'Change value',
    description: 'Changes one value in the fixture system.',
    input_schema: {type:'object',properties:{value:{type:'string'}},required:['value']},
    output_schema: null,
    schema_sha256: 'a'.repeat(64),
    permission: 'none',
    qualified_name: `mcp_${serverId}_change_value`,
    updated_at: '2026-11-05T00:00:00Z',
  };
  let server: any;
  page.on('pageerror', error => errors.push(error.message));
  await page.route('http://127.0.0.1:8765/**', async route => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    const headers = {
      'access-control-allow-origin':'http://127.0.0.1:5173',
      'access-control-allow-headers':'authorization,content-type,last-event-id',
      'access-control-allow-methods':'GET,POST,PUT,DELETE,OPTIONS',
    };
    if (request.method() === 'OPTIONS') return route.fulfill({status:204,headers});
    expect(request.headers()['authorization']).toBe(`Bearer ${'x'.repeat(32)}`);
    const body = ['POST','PUT'].includes(request.method()) ? request.postDataJSON() : undefined;
    requests.push({method:request.method(),path,...(body?{body}:{})});
    if (path === '/runs') return route.fulfill({json:{items:[],next_cursor:null},headers});
    if (path === '/models') return route.fulfill({json:[{id:'fixture/model'}],headers});
    if (path === '/providers') return route.fulfill({json:[{id:'lm-studio',status:'connected'}],headers});
    if (path === '/providers/lm-studio/config') return route.fulfill({json:{endpoint:'http://127.0.0.1:1234',selected_model:null},headers});
    if (path === '/providers/lm-studio/credential' || path === '/providers/brave-search/credential') return route.fulfill({json:{configured:false},headers});
    if (path === '/memory' || path === '/schedules') return route.fulfill({json:[],headers});
    if (path === '/skills') return route.fulfill({json:importedSkill?[importedSkill]:[],headers});
    if (path === '/extensions/packages/discover') return route.fulfill({json:{root:'C:\\plugins',packages:[{name:'sample-package',version:'1.2.3',description:'Fixture',path:'C:\\plugins\\sample',skills:[{name:'summarize',path:'skills/summarize/SKILL.md',description:'Summarize safely.',allowed_tools:['workspace.read_file']}],servers:[]}],diagnostics:[]},headers});
    if (path === '/extensions/packages/import-skill') {
      importedSkill={id:'imported-procedure',name:'summarize',lifecycle_state:'candidate',active_version:null,lifecycle_revision:0,latest_version:1,created_at:'2026-11-05T00:00:00Z',updated_at:'2026-11-05T00:00:00Z'};
      return route.fulfill({status:201,json:importedSkill,headers});
    }
    if (path === '/channels/telegram') return route.fulfill({json:{configured:false,bot_username:null,linked_users:0,processed_updates:0,deliveries:{}},headers});
    if (path === '/mcp/servers' && request.method() === 'GET') return route.fulfill({json:server?[server]:[],headers});
    if (path === '/mcp/servers' && request.method() === 'POST') {
      server = {...body,id:serverId,created_at:'2026-11-05T00:00:00Z',updated_at:'2026-11-05T00:00:00Z',state:'disconnected',error:null,secret_env_configured:{},tools:[]};
      return route.fulfill({status:201,json:server,headers});
    }
    if (path === `/mcp/servers/${serverId}` && request.method() === 'DELETE') { server = null; return route.fulfill({status:204,headers}); }
    if (path === `/mcp/servers/${serverId}/connect`) {
      server = {...server,state:'connected',tools:[tool]};
      return route.fulfill({json:{state:'connected',tools:[tool]},headers});
    }
    if (path === `/mcp/servers/${serverId}/tools/change_value/permission`) {
      Object.assign(tool,body);
      server = {...server,tools:[tool]};
      return route.fulfill({json:tool,headers});
    }
    if (path === '/execution/distributions') return route.fulfill({json:{state:'unsupported',distributions:[]},headers});
    return route.fulfill({json:[],headers});
  });

  await page.goto('/');
  await page.getByLabel('Engine token').fill('x'.repeat(32));
  await page.getByRole('button',{name:'Connect',exact:true}).click();
  await page.locator('summary').filter({hasText:'MCP integrations'}).click();
  const panel = page.getByRole('region',{name:'MCP integrations'});
  await panel.getByLabel('Server name').fill('Fixture MCP');
  await panel.getByLabel('Command').fill('python');
  await panel.getByLabel('Arguments (JSON string array)').fill('["fixture_server.py"]');
  await panel.getByRole('button',{name:'Add MCP server'}).click();
  await expect(panel.getByText('Fixture MCP')).toBeVisible();
  await panel.getByRole('button',{name:'Connect and discover'}).click();
  await expect(panel.getByText('Change value')).toBeVisible();
  await panel.getByLabel('Permission').selectOption('write');
  const grant = panel.getByLabel('Allow this write tool for the next run');
  await expect(grant).toBeVisible();
  await expect(grant).not.toBeChecked();
  await grant.check();
  await expect(grant).toBeChecked();
  expect(requests.some(item=>item.method==='PUT'&&item.path.endsWith('/permission')&&item.body.permission==='write')).toBe(true);
  await panel.getByRole('button',{name:'Remove server'}).click();
  await panel.getByLabel('Server name').fill('HTTP MCP');
  await panel.getByLabel('Transport').selectOption('streamable_http');
  await panel.getByLabel('Streamable HTTP endpoint').fill('https://mcp.example.com/mcp');
  await panel.getByLabel('Bearer token variable name (optional)').fill('MCP_TOKEN');
  await panel.getByRole('button',{name:'Add MCP server'}).click();
  await expect(panel.getByText('https://mcp.example.com/mcp')).toBeVisible();
  expect(requests.some(item=>item.method==='POST'&&item.path==='/mcp/servers'&&item.body.transport==='streamable_http'&&item.body.endpoint==='https://mcp.example.com/mcp'&&item.body.secret_env[0]==='MCP_TOKEN')).toBe(true);
  await page.locator('summary').filter({hasText:'Agent plugin discovery'}).click();
  const plugins=page.getByRole('region',{name:'Agent plugin package discovery'});
  await plugins.getByLabel('Package folder').fill('C:\\plugins');
  await plugins.getByRole('button',{name:'Discover packages'}).click();
  await expect(plugins.getByText('sample-package · 1.2.3')).toBeVisible();
  await expect(plugins.getByText('Summarize safely.')).toBeVisible();
  await plugins.getByRole('button',{name:'Import as candidate'}).click();
  await expect(plugins.getByRole('status')).toContainText('inactive candidate');
  await page.locator('summary').filter({hasText:'Reusable procedures'}).click();
  await expect(page.getByRole('button',{name:'Inspect summarize',exact:true})).toBeVisible();
  expect(requests.some(item=>item.method==='POST'&&item.path==='/extensions/packages/import-skill'&&item.body.package_name==='sample-package'&&item.body.skill_name==='summarize')).toBe(true);
  expect(errors).toEqual([]);
});
