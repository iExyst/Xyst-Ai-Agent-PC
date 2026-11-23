import { expect, test } from '@playwright/test';
import {navigateMainSection} from './navigation';

test('configure persistent specialist model routes from discovered local models', async ({ page }) => {
  const roles = ['planner','researcher','developer','reviewer','tester','executor'];
  const profiles = roles.map(role => ({
    role,
    display_name: role[0]!.toUpperCase() + role.slice(1),
    model_route: null as string | null,
    system_instructions: `Default ${role} instructions.`,
    procedure_skill_id: null as string | null,
    procedure_name: null as string | null,
    procedure_version: null as number | null,
    procedure_python_environment_digest: null as string | null,
    procedure_npm_environment_digest: null as string | null,
    updated_at: '2026-11-05T00:00:00Z',
  }));
  const saved: {role:string;model_route:string|null}[] = [];
  const instructionSaves: {role:string;system_instructions:string}[] = [];
  const identitySaves: {role:string;display_name:string}[] = [];
  const procedureSaves: {role:string;skill_id:string|null}[] = [];
  const environmentSaves: {role:string;python_environment_digest:string|null;npm_environment_digest:string|null}[] = [];
  const roleMemories: Record<string,{id:string;content:string}[]> = Object.fromEntries(roles.map(role => [role, []]));
  const errors: string[] = [];
  const integrationModuleRequests: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  page.on('request', request => {
    if (/SpecialistProfilesPanel|ChannelsPanel|SearchProviderPanel|MCPPanel|AgentPluginsPanel|ExecutionSetup/.test(request.url())) integrationModuleRequests.push(request.url());
  });
  await page.route('http://127.0.0.1:8765/**', async route => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    const headers = {
      'access-control-allow-origin':'http://127.0.0.1:5173',
      'access-control-allow-headers':'authorization,content-type,last-event-id',
      'access-control-allow-methods':'GET,POST,PUT,DELETE,OPTIONS',
    };
    if (request.method() === 'OPTIONS') return route.fulfill({status:204,headers});
    if(path==='/runs/latest-unfinished')return route.fulfill({json:null,headers});
    expect(request.headers()['authorization']).toBe(`Bearer ${'x'.repeat(32)}`);
    if (path === '/runs') return route.fulfill({json:{items:[],next_cursor:null},headers});
    if (path === '/models') return route.fulfill({json:[{id:'parent/model'},{id:'research/model'}],headers});
    if (path === '/providers') return route.fulfill({json:[{id:'lm-studio',status:'connected'}],headers});
    if (path === '/providers/lm-studio/config') return route.fulfill({json:{endpoint:'http://127.0.0.1:1234',selected_model:'parent/model'},headers});
    if (path === '/providers/lm-studio/credential' || path === '/providers/brave-search/credential') return route.fulfill({json:{configured:false},headers});
    if (path === '/specialists/profiles' && request.method() === 'GET') return route.fulfill({json:profiles,headers});
    if (path === '/specialists/procedures') return route.fulfill({json:[{id:'procedure-1',name:'Evidence procedure',version:3,linked_resource_count:2,required_tools:[{name:'workspace.search',version:'1'}],required_permissions:['workspace.read'],python_environments:[{environment_digest:'a'.repeat(64),distribution:'Ubuntu-24.04',installed_bytes:1234}],npm_environments:[]}],headers});
    const memoryMatch = path.match(/^\/specialists\/profiles\/([^/]+)\/memory(?:\/([^/]+))?$/);
    if (memoryMatch) {
      const role = decodeURIComponent(memoryMatch[1]!);
      if (request.method() === 'GET') return route.fulfill({json:roleMemories[role],headers});
      if (request.method() === 'POST') {
        const item = {id:`memory-${roleMemories[role]!.length+1}`,content:(request.postDataJSON() as {content:string}).content};
        roleMemories[role]!.push(item);
        return route.fulfill({status:201,json:{...item,kind:'preference',scope:`specialist:${role}`,provenance:'user',confidence:1,created_at:'2026-11-05T00:00:00Z',updated_at:'2026-11-05T00:00:00Z',schema_version:1},headers});
      }
      if (request.method() === 'DELETE') {
        roleMemories[role] = roleMemories[role]!.filter(item => item.id !== memoryMatch[2]);
        return route.fulfill({status:204,headers});
      }
    }
    const routeMatch = path.match(/^\/specialists\/profiles\/([^/]+)\/(route|instructions|identity|procedure)$/);
    if (routeMatch && request.method() === 'PUT') {
      const role = decodeURIComponent(routeMatch[1]!);
      const profile = profiles.find(item => item.role === role)!;
      const body = request.postDataJSON() as {model_route?:string|null;system_instructions?:string;display_name?:string;skill_id?:string|null;python_environment_digest?:string|null;npm_environment_digest?:string|null};
      if (routeMatch[2] === 'route') {
        profile.model_route = body.model_route ?? null;
        saved.push({role,model_route:profile.model_route});
      } else if (routeMatch[2] === 'instructions') {
        profile.system_instructions = body.system_instructions ?? '';
        instructionSaves.push({role,system_instructions:profile.system_instructions});
      } else if (routeMatch[2] === 'identity') {
        profile.display_name = body.display_name ?? profile.display_name;
        identitySaves.push({role,display_name:profile.display_name});
      } else {
        profile.procedure_skill_id = body.skill_id ?? null;
        profile.procedure_name = profile.procedure_skill_id ? 'Evidence procedure' : null;
        profile.procedure_version = profile.procedure_skill_id ? 3 : null;
        procedureSaves.push({role,skill_id:profile.procedure_skill_id});
        profile.procedure_python_environment_digest = body.python_environment_digest ?? null;
        profile.procedure_npm_environment_digest = body.npm_environment_digest ?? null;
        if (body.python_environment_digest || body.npm_environment_digest) environmentSaves.push({role,python_environment_digest:profile.procedure_python_environment_digest,npm_environment_digest:profile.procedure_npm_environment_digest});
      }
      return route.fulfill({json:profile,headers});
    }
    if (path === '/memory' || path === '/skills' || path === '/schedules') return route.fulfill({json:[],headers});
    if (path === '/channels/telegram') return route.fulfill({json:{configured:false,bot_username:null,linked_users:0,processed_updates:0,deliveries:{}},headers});
    if (path === '/mcp/servers') return route.fulfill({json:[],headers});
    if (path === '/execution/distributions') return route.fulfill({json:{state:'unsupported',distributions:[]},headers});
    return route.fulfill({json:[],headers});
  });

  await page.goto('/');
  await page.getByLabel('Engine token').fill('x'.repeat(32));
  await page.getByRole('button',{name:'Connect',exact:true}).click();
  await navigateMainSection(page,'Tools & integrations');
  expect(integrationModuleRequests).toEqual([]);
  await page.locator('summary').filter({hasText:'Web search integration'}).click();
  await expect.poll(() => integrationModuleRequests.some(url => url.includes('SearchProviderPanel'))).toBe(true);
  await expect(page.getByRole('heading',{name:'Brave Search'})).toBeVisible();
  expect(integrationModuleRequests.some(url => url.includes('MCPPanel'))).toBe(false);
  await page.locator('summary').filter({hasText:'Specialist identities and model routes'}).click();
  await expect.poll(() => integrationModuleRequests.some(url => url.includes('SpecialistProfilesPanel'))).toBe(true);
  expect(integrationModuleRequests.some(url => url.includes('ChannelsPanel'))).toBe(false);
  const panel = page.getByRole('region',{name:'Specialist profiles'});
  const researcher = panel.getByLabel('Researcher model');
  await expect(researcher).toHaveValue('');
  await researcher.selectOption('research/model');
  await panel.getByRole('button',{name:'Save route'}).nth(1).click();
  await expect(researcher).toHaveValue('research/model');
  expect(saved).toEqual([{role:'researcher',model_route:'research/model'}]);
  const procedure = panel.getByLabel('Researcher active procedure');
  await expect(procedure.locator('option[value="procedure-1"]')).toContainText('2 linked text resources');
  await procedure.selectOption('procedure-1');
  await expect(panel.getByLabel('Researcher procedure requirements')).toHaveText('Declared requirements: workspace.search@1, workspace.read. Selecting a procedure does not grant any tools or permissions.');
  expect(procedureSaves).toEqual([{role:'researcher',skill_id:'procedure-1'}]);
  const pythonEnvironment = panel.getByLabel('Researcher Python environment');
  await expect(pythonEnvironment.locator('option[value="' + 'a'.repeat(64) + '"]')).toContainText('Ubuntu-24.04');
  await pythonEnvironment.selectOption('a'.repeat(64));
  await expect(pythonEnvironment).toHaveValue('a'.repeat(64));
  expect(environmentSaves).toEqual([{role:'researcher',python_environment_digest:'a'.repeat(64),npm_environment_digest:null}]);
  const instructions = panel.getByLabel('Researcher role instructions');
  await instructions.fill('Cite evidence and label uncertainty.');
  await panel.getByRole('button',{name:'Save role instructions'}).nth(1).click();
  await expect(instructions).toHaveValue('Cite evidence and label uncertainty.');
  expect(instructionSaves).toEqual([{role:'researcher',system_instructions:'Cite evidence and label uncertainty.'}]);
  const memory = panel.getByRole('textbox',{name:'Researcher saved memory'});
  await memory.fill('Prefer primary documentation.');
  await panel.getByRole('button',{name:'Save role memory'}).nth(1).click();
  await expect(panel.getByText('Prefer primary documentation.')).toBeVisible();
  await panel.getByRole('button',{name:'Delete memory'}).nth(0).click();
  await expect(panel.getByText('Prefer primary documentation.')).toHaveCount(0);
  const identity = panel.getByLabel('researcher specialist name');
  await identity.fill('Evidence Scout');
  await panel.getByRole('button',{name:'Save identity'}).nth(1).click();
  await expect(panel.getByLabel('Evidence Scout model')).toBeVisible();
  expect(identitySaves).toEqual([{role:'researcher',display_name:'Evidence Scout'}]);
  await page.locator('summary').filter({hasText:'Command sandbox setup'}).click();
  await expect.poll(() => integrationModuleRequests.some(url => url.includes('ExecutionSetup'))).toBe(true);
  await expect(page.getByRole('button',{name:'Find installed distributions'})).toBeVisible();
  expect(errors).toEqual([]);
  expect(procedureSaves).toEqual([{role:'researcher',skill_id:'procedure-1'},{role:'researcher',skill_id:'procedure-1'}]);
});
