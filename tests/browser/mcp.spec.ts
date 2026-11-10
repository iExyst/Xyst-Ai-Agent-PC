import { expect, test } from '@playwright/test';

test('configure an MCP server, discover tools and explicitly grant write access', async ({ page }) => {
  const errors: string[] = [];
  const requests: {method:string;path:string;body?:any}[] = [];
  const serverId = '1234567890abcdef1234567890abcdef';
  let importedSkill: any;
  let importedPackageServer: any;
  let importedPackageHTTPServer: any;
  let installedPackagePresent = false;
  let retainedDataPresent = false;
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
    if (path === '/extensions/packages/install') {
      installedPackagePresent = true;
      return route.fulfill({status:201,json:{root:'C:\\plugins',package:{name:'sample-package',version:'1.2.3',description:'Fixture',path:'C:\\plugins\\sample-package',skills:[],servers:[]}},headers});
    }
    if (path === '/extensions/packages/installed') return route.fulfill({json:{root:'C:\\plugins',packages:installedPackagePresent?[{name:'sample-package',version:'1.2.3',description:'Fixture',path:'C:\\plugins\\sample-package',skills:[],servers:[]}]:[],diagnostics:[]},headers});
    if (path === '/extensions/packages/remove') {
      installedPackagePresent=false;
      retainedDataPresent=true;
      return route.fulfill({json:{removed:true,package_name:body.package_name},headers});
    }
    if (path === '/extensions/packages/data') return route.fulfill({json:{root:'C:\\plugin-data',package_names:retainedDataPresent?['sample-package']:[]},headers});
    if (path === '/extensions/packages/data/remove') {
      retainedDataPresent=false;
      return route.fulfill({json:{removed:true,package_name:body.package_name},headers});
    }
    if (path === '/runs/latest-unfinished') return route.fulfill({json:null,headers});
    if (path === '/runs') return route.fulfill({json:{items:[],next_cursor:null},headers});
    if (path === '/models') return route.fulfill({json:[{id:'fixture/model'}],headers});
    if (path === '/providers') return route.fulfill({json:[{id:'lm-studio',status:'connected'}],headers});
    if (path === '/providers/lm-studio/config') return route.fulfill({json:{endpoint:'http://127.0.0.1:1234',selected_model:null},headers});
    if (path === '/providers/lm-studio/credential' || path === '/providers/brave-search/credential') return route.fulfill({json:{configured:false},headers});
    if (path === '/memory' || path === '/schedules') return route.fulfill({json:[],headers});
    if (path === '/skills') return route.fulfill({json:importedSkill?[importedSkill]:[],headers});
    if (path === '/extensions/packages/discover') return route.fulfill({json:{root:'C:\\plugins',packages:[{name:'sample-package',version:'1.2.3',description:'Fixture',path:'C:\\plugins\\sample',skills:[{name:'summarize',path:'skills/summarize/SKILL.md',description:'Summarize safely.',allowed_tools:['workspace.read_file'],declared_requirements:{platforms:['windows'],required_command_names:['git'],required_environment_names:['FIXTURE_TOKEN'],dependencies_declared:true,dependency_count:1,dependency_names:['requests'],dependency_names_truncated:false,dependency_managers:['python'],unparsed_dependency_count:0,hermes_toolsets:['terminal'],hermes_required_tools:['shell.exec'],hermes_fallback_toolsets:['browser'],hermes_fallback_tools:['web.search']},requirement_readiness:{current_platform:'windows',platform_status:'compatible',unknown_platform_names:[],available_command_names:['git'],missing_command_names:[],unchecked_command_names:[],present_environment_names:[],missing_environment_names:['FIXTURE_TOKEN'],dependencies_unresolved:true,dependency_count_unresolved:1,dependency_names:['requests'],dependency_names_truncated:false,dependency_managers:['python'],unparsed_dependency_count:0,hermes_toolsets:['terminal'],hermes_tools_unresolved:['shell.exec'],hermes_fallback_toolsets_unresolved:['browser'],hermes_fallback_tools_unresolved:['web.search'],not_assessed:['dependency_installation','hermes_toolsets','hermes_tools','hermes_fallback_conditions','command_semantics']}}],servers:[{name:'local-helper',transport:'stdio',command_name:'python.exe',argument_count:1,environment_names:['API_TOKEN']},{name:'remote-helper',transport:'streamable_http',endpoint:'https://example.test/…',environment_names:['MCP_TOKEN']}] }],diagnostics:[]},headers});
    if (path === '/extensions/packages/import-skill') {
      importedSkill={id:'imported-procedure',name:'summarize',lifecycle_state:'candidate',active_version:null,lifecycle_revision:0,latest_version:1,created_at:'2026-11-05T00:00:00Z',updated_at:'2026-11-05T00:00:00Z'};
      return route.fulfill({status:201,json:importedSkill,headers});
    }
    if (path === '/channels/telegram') return route.fulfill({json:{configured:false,bot_username:null,linked_users:0,processed_updates:0,deliveries:{}},headers});
    if (path === '/mcp/servers' && request.method() === 'GET') return route.fulfill({json:[...(server?[server]:[]),...(importedPackageServer?[importedPackageServer]:[]),...(importedPackageHTTPServer?[importedPackageHTTPServer]:[])],headers});
    if (path === '/mcp/servers' && request.method() === 'POST') {
      server = {...body,id:serverId,created_at:'2026-11-05T00:00:00Z',updated_at:'2026-11-05T00:00:00Z',state:'disconnected',error:null,secret_env_configured:{},tools:[]};
      return route.fulfill({status:201,json:server,headers});
    }
    if (path === '/extensions/packages/import-mcp-server') {
      if (body.server_name==='remote-helper') {
        importedPackageHTTPServer={id:'fedcba9876543210fedcba9876543210',name:'sample-package:remote-helper',transport:'streamable_http',endpoint:'https://example.test/mcp',command:'',args:[],cwd:null,secret_env:['MCP_TOKEN'],plugin_backed:true,created_at:'2026-11-10T00:00:00Z',updated_at:'2026-11-10T00:00:00Z',state:'disconnected',error:null,secret_env_configured:{MCP_TOKEN:true},oauth_configured:false,tools:[]};
        return route.fulfill({status:201,json:{id:importedPackageHTTPServer.id,name:importedPackageHTTPServer.name,transport:'streamable_http',state:'disconnected',secret_env:['MCP_TOKEN']},headers});
      }
      importedPackageServer={id:'abcdef0123456789abcdef0123456789',name:'sample-package:local-helper',transport:'stdio',endpoint:null,command:'python.exe',args:['server.py'],cwd:null,secret_env:['API_TOKEN'],plugin_backed:true,created_at:'2026-11-10T00:00:00Z',updated_at:'2026-11-10T00:00:00Z',state:'disconnected',error:null,secret_env_configured:{API_TOKEN:true},oauth_configured:false,tools:[]};
      return route.fulfill({status:201,json:{id:importedPackageServer.id,name:importedPackageServer.name,transport:'stdio',state:'disconnected',secret_env:['API_TOKEN']},headers});
    }
    if (path.startsWith('/mcp/servers/') && request.method() === 'DELETE') {
      const removedId=path.split('/').at(-1);
      if(removedId===serverId)server=null;
      if(removedId===importedPackageServer?.id)importedPackageServer=null;
      if(removedId===importedPackageHTTPServer?.id)importedPackageHTTPServer=null;
      return route.fulfill({status:204,headers});
    }
    if (path === `/mcp/servers/${serverId}/connect`) {
      server = {...server,state:'connected',tools:[tool]};
      return route.fulfill({json:{state:'connected',tools:[tool]},headers});
    }
    if (path === `/mcp/servers/${serverId}/resources`) return route.fulfill({json:{resources:[{uri:'xaa://fixture/readme',name:'readme',title:'Fixture readme',description:'Owner inspection fixture.',mime_type:'text/plain',size:20}]},headers});
    if (path === `/mcp/servers/${serverId}/resources/subscription`) return route.fulfill({json:{uri:body.uri,subscribed:body.subscribe,trust:'untrusted_mcp_output'},headers});
    if (path === `/mcp/servers/${serverId}/resources/notifications`) return route.fulfill({json:{trust:'untrusted_mcp_output',notifications:[{kind:'resource_updated',uri:'xaa://fixture/readme',trust:'untrusted_mcp_output'}]},headers});
    if (path === `/mcp/servers/${serverId}/resource-templates`) return route.fulfill({json:{resource_templates:[{name:'item',title:'Fixture item',uri_template:'xaa://fixture/items/{item_id}',variables:['item_id'],description:'Owner template catalog fixture.',mime_type:'text/plain'}]},headers});
    if (path === `/mcp/servers/${serverId}/resource-templates/read`) return route.fulfill({json:{trust:'untrusted_mcp_output',contents:[{uri:'xaa://fixture/items/sample-42',mime_type:'text/plain',kind:'text',text:'Fixture item: sample-42'}]},headers});
    if (path === `/mcp/servers/${serverId}/resources/read`) return route.fulfill({json:{trust:'untrusted_mcp_output',contents:[{uri:body.uri,mime_type:'text/plain',kind:'text',text:'Fixture resource data.'}]},headers});
    if (path === `/mcp/servers/${serverId}/prompts`) return route.fulfill({json:{prompts:[{name:'summarize_topic',title:'Summarize topic',description:'Text-only preview fixture.',arguments:[{name:'topic',description:null,required:true}]}]},headers});
    if (path === `/mcp/servers/${serverId}/prompts/preview`) return route.fulfill({json:{trust:'untrusted_mcp_output',description:null,messages:[{role:'user',content:{kind:'text',text:`Summarize: ${body.arguments.topic}`}}]},headers});
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
  await panel.getByRole('button',{name:'List resources'}).click();
  await expect(panel.getByText('Fixture readme')).toBeVisible();
  await panel.getByRole('button',{name:'Subscribe to updates'}).click();
  await expect(panel.getByRole('button',{name:'Unsubscribe from updates'})).toBeVisible();
  await panel.getByRole('button',{name:'Check for server updates'}).click();
  await expect(panel.getByRole('complementary',{name:'Untrusted MCP server update notices'})).toContainText('Read it explicitly');
  expect(requests.some(item=>item.method==='POST'&&item.path.endsWith('/resources/subscription')&&item.body.subscribe===true)).toBe(true);
  expect(requests.some(item=>item.method==='POST'&&item.path.endsWith('/resources/notifications'))).toBe(true);
  await panel.getByRole('button',{name:'Read resource'}).click();
  await expect(panel.getByRole('complementary',{name:'Untrusted MCP resource data'})).toContainText('Fixture resource data.');
  expect(requests.some(item=>item.method==='POST'&&item.path.endsWith('/resources/read')&&item.body.uri==='xaa://fixture/readme')).toBe(true);
  await panel.getByRole('button',{name:'List resource templates'}).click();
  await expect(panel.locator('.mcp-resource-template')).toContainText('xaa://fixture/items/{item_id}');
  expect(requests.some(item=>item.method==='POST'&&item.path.endsWith('/resource-templates'))).toBe(true);
  await panel.getByLabel('Arguments JSON for resource template item').fill('{"item_id":"sample-42"}');
  await panel.getByRole('button',{name:'Read template resource'}).click();
  await expect(panel.getByRole('complementary',{name:'Untrusted MCP resource data'})).toContainText('Fixture item: sample-42');
  expect(requests.some(item=>item.method==='POST'&&item.path.endsWith('/resource-templates/read')&&item.body.arguments.item_id==='sample-42')).toBe(true);
  await panel.getByRole('button',{name:'List prompts'}).click();
  const promptArgs=panel.getByLabel('Arguments JSON for summarize_topic');
  await expect(promptArgs).toBeVisible();
  await promptArgs.fill('{"topic":"browser acceptance"}');
  await panel.getByRole('button',{name:'Preview prompt'}).click();
  await expect(panel.getByRole('complementary',{name:'Untrusted MCP prompt preview'})).toContainText('Summarize: browser acceptance');
  expect(requests.some(item=>item.method==='POST'&&item.path.endsWith('/prompts/preview')&&item.body.arguments.topic==='browser acceptance')).toBe(true);
  expect(requests.some(item=>item.method==='POST'&&item.path==='/runs')).toBe(false);
  await panel.getByRole('button',{name:'Add text to objective for review'}).click();
  await expect(page.locator('#run-objective')).toContainText('Untrusted MCP prompt preview');
  await expect(page.locator('#run-objective')).toContainText('Summarize: browser acceptance');
  const oversizedObjective='x'.repeat(9990);
  await page.locator('#run-objective').fill(oversizedObjective);
  await panel.getByRole('button',{name:'Add text to objective for review'}).click();
  await expect(page.getByRole('alert')).toContainText('10,000-character objective limit');
  await expect(page.locator('#run-objective')).toHaveValue(oversizedObjective);
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
  await page.evaluate(()=>{
    (window as any).__TAURI_INTERNALS__={invoke:async(command:string)=>{
      if(command==='plugin:dialog|open')return 'C:\\downloads\\sample.zip';
      throw new Error(`Unexpected native command: ${command}`);
    }};
  });
  await plugins.getByRole('button',{name:'Install plugin ZIP'}).click();
  await expect(plugins.getByRole('status')).toContainText('installed for discovery');
  expect(requests.some(item=>item.method==='POST'&&item.path==='/extensions/packages/install'&&item.body.archive_path==='C:\\downloads\\sample.zip')).toBe(true);
  await plugins.getByLabel('Package folder').fill('C:\\plugins');
  await plugins.getByRole('button',{name:'Discover packages'}).click();
  await expect(plugins.getByText('sample-package · 1.2.3')).toBeVisible();
  await expect(plugins.getByText('Summarize safely.')).toBeVisible();
  await expect(plugins.getByText(/Current-machine declaration check only/)).toContainText('platform compatible');
  await expect(plugins.getByText(/Current-machine declaration check only/)).toContainText('FIXTURE_TOKEN');
  await expect(plugins.getByText(/Package names/)).toContainText('requests');
  await expect(plugins.locator('p').filter({hasText:'Hermes tools: shell.exec'})).toHaveCount(2);
  await expect(plugins.getByText(/Fallback toolsets: browser/)).toBeVisible();
  await expect(plugins.getByText(/Unresolved fallback conditions: browser, web.search/)).toBeVisible();
  await plugins.getByRole('listitem').filter({hasText:'local-helper'}).getByRole('button',{name:'Add disconnected server'}).click();
  await expect(plugins.getByRole('status')).toContainText('disconnected MCP server');
  const mcpPanel=page.getByRole('region',{name:'MCP integrations'});
  await expect(mcpPanel.getByRole('heading',{name:/sample-package:local-helper/})).toBeVisible();
  await expect(mcpPanel.getByRole('heading',{name:/sample-package:local-helper/})).toContainText('(disconnected)');
  const remoteItem=plugins.getByRole('listitem').filter({hasText:'remote-helper'});
  await expect(remoteItem).toContainText('MCP_TOKEN');
  await remoteItem.getByRole('button',{name:'Add disconnected server'}).click();
  await expect(plugins.getByRole('status')).toContainText('sample-package:remote-helper');
  await expect(mcpPanel.getByRole('heading',{name:/sample-package:remote-helper/})).toContainText('(disconnected)');
  expect(requests.some(item=>item.method==='POST'&&item.path==='/extensions/packages/import-mcp-server'&&item.body.package_name==='sample-package'&&item.body.server_name==='local-helper')).toBe(true);
  await plugins.getByRole('button',{name:'Import as candidate'}).click();
  await expect(plugins.getByRole('status')).toContainText('inactive candidate');
  await page.locator('summary').filter({hasText:'Reusable procedures'}).click();
  await expect(page.getByRole('button',{name:'Inspect summarize',exact:true})).toBeVisible();
  expect(requests.some(item=>item.method==='POST'&&item.path==='/extensions/packages/import-skill'&&item.body.package_name==='sample-package'&&item.body.skill_name==='summarize')).toBe(true);
  for(const packageServer of ['sample-package:remote-helper','sample-package:local-helper','HTTP MCP']) {
    await mcpPanel.locator('article').filter({hasText:packageServer}).getByRole('button',{name:'Remove server'}).click();
  }
  page.on('dialog',dialog=>void dialog.accept());
  await plugins.getByRole('button',{name:'Show installed packages'}).click();
  await expect(plugins.getByRole('button',{name:'Remove sample-package'})).toBeVisible();
  await plugins.getByRole('button',{name:'Remove sample-package'}).click();
  await expect(plugins.getByRole('status')).toContainText('Imported skill versions and package data were kept');
  expect(requests.some(item=>item.method==='POST'&&item.path==='/extensions/packages/remove'&&item.body.package_name==='sample-package')).toBe(true);
  await plugins.getByRole('button',{name:'Show retained package data'}).click();
  await expect(plugins.getByRole('button',{name:'Delete retained data for sample-package'})).toBeVisible();
  await plugins.getByRole('button',{name:'Delete retained data for sample-package'}).click();
  await expect(plugins.getByText('No retained package data found.')).toBeVisible();
  expect(requests.some(item=>item.method==='POST'&&item.path==='/extensions/packages/data/remove'&&item.body.package_name==='sample-package')).toBe(true);
  expect(errors).toEqual([]);
});
