import { expect, test } from '@playwright/test';

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
    expect(request.headers()['authorization']).toBe(`Bearer ${'x'.repeat(32)}`);
    if (path === '/runs') return route.fulfill({json:{items:[],next_cursor:null},headers});
    if (path === '/models') return route.fulfill({json:[{id:'parent/model'},{id:'research/model'}],headers});
    if (path === '/providers') return route.fulfill({json:[{id:'lm-studio',status:'connected'}],headers});
    if (path === '/providers/lm-studio/config') return route.fulfill({json:{endpoint:'http://127.0.0.1:1234',selected_model:'parent/model'},headers});
    if (path === '/providers/lm-studio/credential' || path === '/providers/brave-search/credential') return route.fulfill({json:{configured:false},headers});
    if (path === '/specialists/profiles' && request.method() === 'GET') return route.fulfill({json:profiles,headers});
    const routeMatch = path.match(/^\/specialists\/profiles\/([^/]+)\/(route|instructions)$/);
    if (routeMatch && request.method() === 'PUT') {
      const role = decodeURIComponent(routeMatch[1]!);
      const profile = profiles.find(item => item.role === role)!;
      const body = request.postDataJSON() as {model_route?:string|null;system_instructions?:string};
      if (routeMatch[2] === 'route') {
        profile.model_route = body.model_route ?? null;
        saved.push({role,model_route:profile.model_route});
      } else {
        profile.system_instructions = body.system_instructions ?? '';
        instructionSaves.push({role,system_instructions:profile.system_instructions});
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
  expect(errors).toEqual([]);
});
