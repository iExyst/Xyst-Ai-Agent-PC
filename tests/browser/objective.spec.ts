import { test, expect } from '@playwright/test';

test('connect, submit an objective and render its saved events', async ({ page }) => {
  const errors: string[] = [];
  let planReads = 0;
  let usageUnavailable = true;
  let braveSearchConfigured = false;
  let braveSearchSecret = '';
  let memories: {id: string; content: string; provenance: string}[] = [];
  page.on('pageerror', error => errors.push(error.message));
  const run = { id: 'browser-run', objective: 'Read input.txt', state: 'verifying', acceptance_criteria:[], revision:3, model:'fixture/model', limits:{max_iterations:3,max_seconds:60,max_total_tokens:5000,max_output_tokens:256} };
  await page.route('http://127.0.0.1:8765/**', async route => {
    const request = route.request();
    const headers = { 'access-control-allow-origin': 'http://127.0.0.1:5173', 'access-control-allow-headers': 'authorization,content-type,last-event-id', 'access-control-allow-methods': 'GET,POST,PUT,DELETE,OPTIONS' };
    if (request.method() === 'OPTIONS') return route.fulfill({ status: 204, headers });
    expect(request.headers()['authorization']).toBe(`Bearer ${'x'.repeat(32)}`);
    const path = new URL(request.url()).pathname;
    if(path==='/providers/brave-search/credential') {
      if(request.method()==='PUT') {braveSearchSecret=request.postDataJSON().token;braveSearchConfigured=true;return route.fulfill({json:{configured:true},headers});}
      if(request.method()==='DELETE') {braveSearchSecret='';braveSearchConfigured=false;return route.fulfill({json:{configured:false},headers});}
      return route.fulfill({json:{configured:braveSearchConfigured},headers});
    }
    if(path==='/runs'&&request.method()==='GET')return route.fulfill({json:{items:[],next_cursor:null},headers});
    if (path.startsWith('/memory')) {
      if (request.method() === 'DELETE') { memories=[]; return route.fulfill({status:204,headers}); }
      if (request.method() === 'POST') memories=[{id:'preference-1',content:request.postDataJSON().content,provenance:'user'}];
      return route.fulfill({json:request.method()==='POST' ? memories[0] : memories,headers});
    }
    if(path.endsWith('/usage')&&usageUnavailable)return route.fulfill({status:503,headers});
    if(path.endsWith('/usage'))return route.fulfill({json:{prompt_tokens:40,completion_tokens:10,total_tokens:50,unmeasured_calls:1,outstanding_reserved_tokens:300,outstanding_reservations:1},headers});
    if (path === '/models') return route.fulfill({ json: [{ id: 'fixture/model' }], headers });
    if (path.endsWith('/plan') && ++planReads === 1) return route.fulfill({json:{tasks:[],ready:[],blocked:[]},headers});
    if (path.endsWith('/plan')) return route.fulfill({json: {tasks: [
      {id:'browser-run:inspect',local_id:'inspect',objective:'Inspect <script>window.planExecuted=true</script>',acceptance_criteria:['Read actual file evidence'],dependencies:[],assigned_role:'researcher',model_route:'fixture/model',workspace:'C:\\fixture',permission_scope:['workspace.read'],budgets:{max_iterations:2,max_seconds:60,max_total_tokens:2000,max_output_tokens:128},status:'queued'},
      {id:'browser-run:review',local_id:'review',objective:'Review inspection evidence',acceptance_criteria:['Check the evidence independently'],dependencies:['inspect'],assigned_role:'reviewer',model_route:'fixture/model',workspace:'C:\\fixture',permission_scope:['workspace.read'],budgets:{max_iterations:2,max_seconds:60},status:'queued'}
    ],ready:['inspect'],blocked:[]},headers});
    if (path.endsWith('/artifacts')) return route.fulfill({ json: [{ id: 'artifact-1', provenance: 'git.diff', size_bytes: 40 }], headers });
    if (path === '/artifacts/artifact-1') return route.fulfill({ body: '<script>window.artifactExecuted=true</script>', contentType: 'text/plain', headers });
    if (path === '/runs' && request.method() === 'POST') {
      expect(request.postDataJSON()).toMatchObject({max_iterations:3,max_seconds:60,max_total_tokens:5000,max_output_tokens:256});
      expect(request.postDataJSON().objective).toBe('Read input.txt');
      expect(request.postDataJSON().approved_web_hosts).toEqual(['docs.example.org', 'standards.example.org']);
      expect(request.postDataJSON().approved_web_search).toBe(true);
      expect(request.postDataJSON().file_checks).toEqual([{path: 'input.txt', expected_content: 'fixture\n'}]);
      return route.fulfill({ json: run, headers });
    }
    if (path.endsWith('/events/stream')) {
      const artifact = { sequence: 1, event_type: 'artifact.created', payload: { artifact_id: 'artifact-1' }, created_at: '2026-11-03T00:00:00Z' };
      const event = { sequence: 2, event_type: 'run.state_changed', payload: { to: 'verifying' }, created_at: '2026-11-03T00:00:00Z' };
      const planSaved = {sequence:3,event_type:'plan.saved',payload:{task_ids:['inspect','review']},created_at:'2026-11-03T00:00:00Z'};
      return route.fulfill({ headers, contentType: 'text/event-stream', body: `id: 1\nevent: run_event\ndata: ${JSON.stringify(artifact)}\n\nid: 2\nevent: run_event\ndata: ${JSON.stringify(event)}\n\nid: 3\nevent: run_event\ndata: ${JSON.stringify(planSaved)}\n\nevent: done\ndata: {}\n\n` });
    }
    return route.fulfill({ json: run, headers });
  });
  await page.goto('/');
  await page.getByLabel('Engine token').fill('x'.repeat(32));
  await page.getByRole('button', { name: 'Connect', exact: true }).click();
  await expect(page.getByLabel('Local model')).toHaveValue('');
  await page.getByText('Web search integration',{exact:true}).click();
  await page.getByLabel('Brave Search API key').fill('brave-key-test-secret');
  await page.getByRole('button',{name:'Save search key'}).click();
  await expect(page.getByText('Brave Search is configured',{exact:true})).toBeVisible();
  expect(braveSearchSecret).toBe('brave-key-test-secret');
  await page.getByRole('button',{name:'Clear saved key'}).click();
  await expect(page.getByText('Brave Search is not configured',{exact:true})).toBeVisible();
  expect(braveSearchSecret).toBe('');
  await page.getByText('Web search integration',{exact:true}).click();
  await page.getByLabel('Local model').selectOption('fixture/model');
  await page.getByText('Memory preferences', {exact:true}).click();
  await page.getByLabel('Preference', {exact:true}).fill('Use concise examples');
  await page.getByRole('button',{name:'Save preference',exact:true}).click();
  await expect(page.getByText('Use concise examples',{exact:true})).toBeVisible();
  await page.getByRole('button',{name:'Edit preference',exact:true}).click();
  await page.getByLabel('Preference',{exact:true}).fill('Use detailed examples');
  await page.getByRole('button',{name:'Update preference',exact:true}).click();
  await expect(page.getByText('Use detailed examples',{exact:true})).toBeVisible();
  const downloaded = page.waitForEvent('download');
  await page.getByRole('button',{name:'Export memory',exact:true}).click();
  expect((await downloaded).suggestedFilename()).toBe('xaa-memory.json');
  await page.getByRole('button',{name:'Delete preference',exact:true}).click();
  await expect(page.getByText('Use detailed examples',{exact:true})).toHaveCount(0);
  await page.getByText('Memory preferences', {exact:true}).click();
  await page.getByLabel('Workspace path').fill('C:\\fixture');
  await page.getByLabel('Public web hosts approved for this run (optional)').fill('docs.example.org, standards.example.org');
  await page.getByLabel('Allow Brave web search for this next run').check();
  await page.getByLabel('File to verify').fill('input.txt');
  await page.getByLabel('Expected file contents').fill('fixture\n');
  await page.getByLabel('Objective', { exact: true }).fill('Read input.txt');
  await page.getByText('Run limits',{exact:true}).click();
  await page.getByLabel('Iteration limit',{exact:true}).fill('0');
  await expect(page.getByRole('button',{name:'Start objective',exact:true})).toBeDisabled();
  await page.getByLabel('Iteration limit',{exact:true}).fill('3');
  await page.getByLabel('Time limit (seconds)',{exact:true}).fill('60');
  await page.getByLabel('Reported token threshold (optional)',{exact:true}).fill('5000');
  await page.getByLabel('Output tokens per request (optional)',{exact:true}).fill('1.5');
  await expect(page.getByRole('button',{name:'Start objective',exact:true})).toBeDisabled();
  await page.getByLabel('Output tokens per request (optional)',{exact:true}).fill('256');
  await page.getByRole('button', { name: 'Start objective' }).click();
  const resources=page.getByRole('region',{name:'Run resources'});
  await expect(resources).toContainText('3 iterations · 60 seconds');
  await expect(resources).toContainText('Reported token threshold: 5000 · Output tokens per request: 256');
  await expect(resources).toContainText('Token usage unavailable.');
  usageUnavailable=false;
  await resources.getByRole('button',{name:'Retry token usage',exact:true}).click();
  await expect(resources).toContainText('Measured tokens: 50 (40 input, 10 output). Calls without measured usage: 1.');
  await expect(resources).toContainText('Reserved tokens awaiting measured results: 300 across 1 request.');
  await expect(resources).toContainText('Interrupted requests keep their hold until a measured result is saved.');
  await expect(page.getByRole('heading', { name: 'Active run verifying' })).toBeVisible();
  await expect(page.getByText('Run is verifying')).toBeVisible();
  const plan = page.getByRole('region', {name:'Task plan'});
  await expect(plan.getByRole('heading', {name:'Plan and specialists'})).toBeVisible();
  await expect(plan.getByRole('article', {name:'Task inspect'})).toContainText('Ready to start');
  await expect(plan.getByRole('article',{name:'Task inspect'})).toContainText('Reported token allowance: 2000 · Output tokens per request: 128');
  const review = plan.getByRole('article', {name:'Task review'});
  await expect(review).toContainText('Waiting for dependencies');
  await expect(review).toContainText('2 iterations · 60 seconds');
  await expect(review).toContainText('Check the evidence independently');
  expect(await page.evaluate(() => 'planExecuted' in window)).toBe(false);
  expect(planReads).toBe(2);
  expect(errors).toEqual([]);
  await page.getByRole('button', { name: 'View git.diff' }).click();
  await expect(page.locator('pre')).toContainText('<script>');
  expect(await page.evaluate(() => 'artifactExecuted' in window)).toBe(false);
  await page.getByLabel('Objective', {exact:true}).fill('Read another file', {timeout:2000});
  await expect(page.getByLabel('Objective', {exact:true})).toHaveValue('Read another file');
  await page.getByLabel('Expected file contents', {exact:true}).fill('second fixture\n', {timeout:2000});
  await expect(page.getByLabel('Expected file contents', {exact:true})).toHaveValue('second fixture\n');
  await page.getByLabel('Local model').selectOption('');
  await expect(page.getByRole('button',{name:'Start objective',exact:true})).toBeDisabled();
  await page.getByLabel('Local model').selectOption('fixture/model');
  await expect(page.getByRole('button',{name:'Start objective',exact:true})).toBeEnabled();
  await page.screenshot({ path: 'test-results/objective.png', fullPage: true });
});
