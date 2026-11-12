import { expect, test } from '@playwright/test';
import {navigateMainSection} from './navigation';

test('configure persistent specialist model routes from discovered local models', async ({ page }) => {
  const roles = ['planner','researcher','developer','reviewer','tester','executor'];
  const profiles = roles.map(role => ({
    role,
    display_name: role[0]!.toUpperCase() + role.slice(1),
    model_route: null as string | null,
    system_instructions: `Default ${role} instructions.`,
    updated_at: '2026-11-05T00:00:00Z',
  }));
  const saved: {role:string;model_route:string|null}[] = [];
  const instructionSaves: {role:string;system_instructions:string}[] = [];
  const identitySaves: {role:string;display_name:string}[] = [];
  const roleMemories: Record<string,{id:string;content:string}[]> = Object.fromEntries(roles.map(role => [role, []]));
  const errors: string[] = [];
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
    if(path==='/runs/latest-unfinished')return route.fulfill({json:null,headers});
    expect(request.headers()['authorization']).toBe(`Bearer ${'x'.repeat(32)}`);
    if (path === '/runs') return route.fulfill({json:{items:[],next_cursor:null},headers});
    if (path === '/models') return route.fulfill({json:[{id:'parent/model'},{id:'research/model'}],headers});
    if (path === '/providers') return route.fulfill({json:[{id:'lm-studio',status:'connected'}],headers});
    if (path === '/providers/lm-studio/config') return route.fulfill({json:{endpoint:'http://127.0.0.1:1234',selected_model:'parent/model'},headers});
    if (path === '/providers/lm-studio/credential' || path === '/providers/brave-search/credential') return route.fulfill({json:{configured:false},headers});
    if (path === '/specialists/profiles' && request.method() === 'GET') return route.fulfill({json:profiles,headers});
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
    const routeMatch = path.match(/^\/specialists\/profiles\/([^/]+)\/(route|instructions|identity)$/);
    if (routeMatch && request.method() === 'PUT') {
      const role = decodeURIComponent(routeMatch[1]!);
      const profile = profiles.find(item => item.role === role)!;
      const body = request.postDataJSON() as {model_route?:string|null;system_instructions?:string;display_name?:string};
      if (routeMatch[2] === 'route') {
        profile.model_route = body.model_route ?? null;
        saved.push({role,model_route:profile.model_route});
      } else if (routeMatch[2] === 'instructions') {
        profile.system_instructions = body.system_instructions ?? '';
        instructionSaves.push({role,system_instructions:profile.system_instructions});
      } else {
        profile.display_name = body.display_name ?? profile.display_name;
        identitySaves.push({role,display_name:profile.display_name});
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
  await page.locator('summary').filter({hasText:'Specialist identities and model routes'}).click();
  const panel = page.getByRole('region',{name:'Specialist profiles'});
  const researcher = panel.getByLabel('Researcher model');
  await expect(researcher).toHaveValue('');
  await researcher.selectOption('research/model');
  await panel.getByRole('button',{name:'Save route'}).nth(1).click();
  await expect(researcher).toHaveValue('research/model');
  expect(saved).toEqual([{role:'researcher',model_route:'research/model'}]);
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
  expect(errors).toEqual([]);
});
