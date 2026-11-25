import { chromium, expect } from '@playwright/test';
import { createHash, randomUUID } from 'node:crypto';
import { createServer } from 'node:http';
import { spawn, execFileSync } from 'node:child_process';
import { mkdir, readFile, writeFile } from 'node:fs/promises';
import path from 'node:path';

if (process.platform !== 'win32') throw new Error('Native SSH process recovery acceptance requires Windows.');

const desktop = path.resolve(process.argv[2] ?? 'build/native-dev/xaa-desktop.exe');
const root = path.resolve(process.argv[3] ?? path.join('.acceptance', `ssh-active-restart-${randomUUID()}`));
const project = path.join(root, 'project');
const localAppData = path.join(root, 'native-profile', 'localappdata');
const appData = path.join(root, 'native-profile', 'appdata');
const data = path.join(root, 'native-profile', 'data');
const logDirectory = path.join(localAppData, 'dev.xyst.xaa', 'logs');
const database = path.join(data, 'XAA', 'xaa.sqlite3');
const model = 'xaa-ssh-active-recovery-fixture';
const callId = 'active-ssh-call';
const marker = `REMOTE-SSH-ACTIVE-${randomUUID().replaceAll('-', '')}`;
const identity = path.join(process.env.USERPROFILE, '.ssh', 'xaa-acceptance-key');

await Promise.all([project, localAppData, appData, data, logDirectory].map(folder => mkdir(folder, { recursive: true })));
await writeFile(path.join(project, 'fixture.txt'), 'private SSH snapshot fixture\n');

const fixture = { diagnosticCalls: 0, commandProposals: 0, recoveryFollowups: 0, requests: [], http: [] };
function reply(response, status, value) {
  response.writeHead(status, { 'content-type': 'application/json' });
  response.end(JSON.stringify(value));
}
function streamedTool(response, id, name, args) {
  response.writeHead(200, { 'content-type': 'text/event-stream', 'cache-control': 'no-cache' });
  response.write(`data: ${JSON.stringify({ choices: [{ index: 0, delta: { role: 'assistant' }, finish_reason: null }] })}\n\n`);
  response.write(`data: ${JSON.stringify({ choices: [{ index: 0, delta: { tool_calls: [{ index: 0, id, type: 'function', function: { name, arguments: JSON.stringify(args) } }] }, finish_reason: null }] })}\n\n`);
  response.write(`data: ${JSON.stringify({ choices: [{ index: 0, delta: {}, finish_reason: 'tool_calls' }], usage: { prompt_tokens: 32, completion_tokens: 9, total_tokens: 41 } })}\n\n`);
  response.end('data: [DONE]\n\n');
}
function streamedText(response, content) {
  response.writeHead(200, { 'content-type': 'text/event-stream', 'cache-control': 'no-cache' });
  response.write(`data: ${JSON.stringify({ choices: [{ index: 0, delta: { role: 'assistant', content }, finish_reason: null }] })}\n\n`);
  response.write(`data: ${JSON.stringify({ choices: [{ index: 0, delta: {}, finish_reason: 'stop' }], usage: { prompt_tokens: 32, completion_tokens: 9, total_tokens: 41 } })}\n\n`);
  response.end('data: [DONE]\n\n');
}
function jsonCompletion(response, message) {
  reply(response, 200, {
    object: 'chat.completion',
    model,
    choices: [{ index: 0, message, finish_reason: message.tool_calls ? 'tool_calls' : 'stop' }],
    usage: { prompt_tokens: 32, completion_tokens: 9, total_tokens: 41 },
  });
}

const modelServer = createServer(async (request, response) => {
  fixture.http.push({ method: request.method, url: request.url });
  if (request.method === 'GET' && request.url === '/v1/models') {
    reply(response, 200, { data: [{ id: model, object: 'model' }] });
    return;
  }
  if (request.method === 'GET' && request.url === '/api/v1/models') {
    reply(response, 200, { models: [{ key: model, max_context_length: 8192, capabilities: { trained_for_tool_use: true } }] });
    return;
  }
  if (request.method !== 'POST' || request.url !== '/v1/chat/completions') {
    reply(response, 404, { error: 'not found' });
    return;
  }
  let raw = '';
  for await (const chunk of request) raw += chunk.toString();
  const body = JSON.parse(raw);
  const messages = body.messages ?? [];
  const userText = messages.filter(message => message.role === 'user').map(message => String(message.content)).join('\n');
  const toolNames = (body.tools ?? []).map(tool => tool.function?.name);
  fixture.requests.push({
    stream: body.stream === true,
    tools: toolNames,
    message_roles: messages.map(message => message.role),
    latest_user_text: userText.slice(-1800),
  });

  if (userText.includes('Call xaa_echo with value ')) {
    if (!messages.some(message => message.role === 'tool')) {
      fixture.diagnosticCalls += 1;
      const match = userText.match(/value ([a-f0-9]+), then report/);
      if (!match) return reply(response, 400, { error: 'diagnostic nonce missing' });
      if (body.stream === true) streamedTool(response, `diagnostic-${fixture.diagnosticCalls}`, 'xaa_echo', { value: match[1] });
      else jsonCompletion(response, { role: 'assistant', content: null, tool_calls: [{
        id: `diagnostic-${fixture.diagnosticCalls}`, type: 'function',
        function: { name: 'xaa_echo', arguments: JSON.stringify({ value: match[1] }) },
      }] });
    } else {
      const returnedValue = messages.find(message => message.role === 'tool')?.content ?? '';
      if (body.stream === true) streamedText(response, returnedValue);
      else jsonCompletion(response, { role: 'assistant', content: returnedValue });
    }
    return;
  }

  if (toolNames.includes('workspace_run_command') && fixture.commandProposals === 0) {
    fixture.commandProposals += 1;
    const args = {
      argv: ['/usr/bin/python3', '-I', '-c', `import time; print('${marker}', flush=True); time.sleep(25)`],
      max_seconds: 30,
    };
    if (body.stream === true) streamedTool(response, callId, 'workspace_run_command', args);
    else jsonCompletion(response, { role: 'assistant', content: null, tool_calls: [{
      id: callId, type: 'function', function: { name: 'workspace_run_command', arguments: JSON.stringify(args) },
    }] });
    return;
  }

  const savedCommandResult = messages.find(message => message.role === 'tool' && message.tool_call_id === callId);
  if (savedCommandResult) {
    fixture.recoveryFollowups += 1;
    if (!String(savedCommandResult.content).includes('command_result_unavailable_locally')
      || !String(savedCommandResult.content).includes('not_verified')) {
      return reply(response, 409, { error: 'recovery prompt did not contain the unverified command result' });
    }
    const answer = `The remote command was interrupted; its result is unverified and it was not replayed. ${marker}`;
    if (body.stream === true) streamedText(response, answer);
    else jsonCompletion(response, { role: 'assistant', content: answer });
    return;
  }

  reply(response, 503, { error: 'unexpected inference request from active SSH recovery fixture' });
});

await new Promise((resolve, reject) => {
  modelServer.once('error', reject);
  modelServer.listen(0, '127.0.0.1', resolve);
});
const endpoint = `http://127.0.0.1:${modelServer.address().port}`;
const appEnv = Object.fromEntries(Object.entries(process.env).filter(([key]) => !['PATH', 'PYTHONPATH', 'VIRTUAL_ENV'].includes(key.toUpperCase())));
appEnv.PATH = [path.join(process.env.SystemRoot, 'System32'), path.join(process.env.SystemRoot, 'System32', 'OpenSSH')].join(path.delimiter);
appEnv.APPDATA = appData;
appEnv.LOCALAPPDATA = localAppData;
appEnv.XAA_DATA_DIR = data;
appEnv.XAA_TEST_HIDDEN = '1';
appEnv.XAA_TEST_LOG_DIR = logDirectory;
appEnv.WEBVIEW2_USER_DATA_FOLDER = path.join(root, 'native-profile', 'webview');

async function debugPort() {
  const server = createServer();
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const port = server.address().port;
  await new Promise((resolve, reject) => server.close(error => error ? reject(error) : resolve()));
  return port;
}

const launchCredentials = [];
async function startDesktop(label) {
  const port = await debugPort();
  const processHandle = spawn(desktop, [], {
    windowsHide: true,
    stdio: ['ignore', 'pipe', 'pipe'],
    env: { ...appEnv, WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS: `--remote-debugging-port=${port}` },
  });
  let browser;
  let lastError = 'WebView debug endpoint unavailable';
  const deadline = Date.now() + 90000;
  while (Date.now() < deadline) {
    try {
      const response = await fetch(`http://127.0.0.1:${port}/json/version`, { signal: AbortSignal.timeout(750) });
      const metadata = await response.json();
      browser = await chromium.connectOverCDP(metadata.webSocketDebuggerUrl, { timeout: 3000 });
      break;
    } catch (error) {
      lastError = String(error);
      if (processHandle.exitCode !== null) throw new Error(`${label} desktop exited during startup: ${lastError}`);
      await new Promise(resolve => setTimeout(resolve, 200));
    }
  }
  if (!browser) throw new Error(`${label} WebView did not start: ${lastError}`);
  const page = browser.contexts().flatMap(context => context.pages())[0];
  if (!page) throw new Error(`${label} desktop exposed no WebView page`);
  await expect(page.getByRole('navigation', { name: 'Main sections' })).toBeVisible({ timeout: 20000 });
  const connection = await page.evaluate(() => window.__TAURI_INTERNALS__.invoke('engine_connection'));
  launchCredentials.push(connection);
  const headers = { Authorization: `Bearer ${connection.token}`, 'Content-Type': 'application/json' };
  const readyDeadline = Date.now() + 75000;
  while (Date.now() < readyDeadline) {
    try {
      const response = await fetch(`${connection.base_url}/runs`, { headers, signal: AbortSignal.timeout(750) });
      if (response.ok) return { processHandle, browser, page, connection, headers };
    } catch {}
    if (processHandle.exitCode !== null) throw new Error(`${label} desktop exited before engine readiness`);
    await new Promise(resolve => setTimeout(resolve, 200));
  }
  throw new Error(`${label} engine API did not become ready`);
}

function cleanLaunchCredentials() {
  const latest = launchCredentials.at(-1);
  if (!latest) return false;
  const source = [
    'import sys',
    'from xaa.credentials import API_LAUNCH_TOKEN_PROFILE, API_LAUNCH_URL_PROFILE, CredentialStore',
    'store=CredentialStore(); clean=True',
    'for profile,expected in ((API_LAUNCH_TOKEN_PROFILE,sys.argv[1]),(API_LAUNCH_URL_PROFILE,sys.argv[2])):',
    ' current=store.get(profile)',
    ' if current==expected: store.delete(profile)',
    ' elif current is not None: clean=False',
    'print(clean)',
  ].join('\n');
  try {
    return execFileSync('uv', ['run', 'python', '-c', source, latest.token, latest.base_url], {
      cwd: path.resolve('.'), windowsHide: true, encoding: 'utf8', timeout: 30000, env: process.env,
    }).trim() === 'True';
  } catch {
    return false;
  }
}

let first;
let second;
let report = { passed: false, scenario: 'Windows Tauri/engine death during live SSH execution, followed by unknown-outcome recovery', marker, backend: 'ssh-wsl-bubblewrap-v1', model_inference_provider: 'deterministic loopback fixture; no LM Studio or external provider' };
try {
  first = await startDesktop('Initial');
  const base = first.connection.base_url;
  async function api(context, route, method = 'GET', payload) {
    const response = await fetch(`${context.connection.base_url}${route}`, {
      method,
      headers: context.headers,
      ...(payload === undefined ? {} : { body: JSON.stringify(payload) }),
      signal: AbortSignal.timeout(60000),
    });
    if (!response.ok) throw new Error(`${route} returned ${response.status}: ${await response.text()}`);
    return response.status === 204 ? null : response.json();
  }

  await api(first, '/providers/lm-studio/config', 'PUT', { endpoint });
  const inventory = await api(first, '/models');
  if (!inventory.some(item => item.id === model)) throw new Error('The fixture model was not discovered.');
  await api(first, '/providers/lm-studio/selection', 'PUT', { model });

  await first.page.getByRole('link', { name: 'Tools & integrations' }).click();
  const setup = first.page.locator('details').filter({ has: first.page.locator('summary').filter({ hasText: 'Command sandbox setup' }) });
  await setup.evaluate(element => { element.open = true; });
  await first.page.getByLabel('Execution backend').selectOption('ssh-wsl-bubblewrap-v1');
  await first.page.getByLabel('SSH host alias').fill('xaa-e2e');
  await first.page.getByLabel('SSH private-key path').fill(identity);
  const readinessWait = first.page.waitForResponse(response => new URL(response.url()).pathname === '/execution/readiness');
  await first.page.getByRole('button', { name: 'Check sandbox', exact: true }).click();
  const readiness = await (await readinessWait).json();
  if (readiness.state !== 'ready' || readiness.profile_sha256?.length !== 64) throw new Error(`SSH readiness failed: ${JSON.stringify(readiness)}`);
  report.readiness_checks = readiness.checks;

  const created = await api(first, '/runs', 'POST', {
    objective: 'Run one harmless command in the pinned SSH sandbox. Report its output.',
    acceptance_criteria: ['Report the command result without inventing missing evidence.'],
    workspace: project,
    mode: 'workspace',
    model,
    max_iterations: 4,
    max_seconds: 120,
    max_output_tokens: 512,
    command_grant: {
      backend: readiness.backend,
      distribution: readiness.distribution,
      profile_sha256: readiness.profile_sha256,
      max_seconds: 30,
      ssh_identity_file: readiness.ssh_identity_file,
    },
  });
  report.run_id = created.id;
  const runBase = `/runs/${encodeURIComponent(created.id)}`;
  const launchedDeadline = Date.now() + 90000;
  let activeEvents = [];
  while (Date.now() < launchedDeadline) {
    activeEvents = await api(first, `${runBase}/events`);
    const sawOutput = activeEvents.some(event => event.event_type === 'execution.output.delta'
      && event.payload?.stream === 'stdout'
      && Buffer.from(event.payload.chunk_b64 ?? '', 'base64').toString('utf8').includes(marker));
    if (sawOutput) break;
    const state = await api(first, runBase);
    if (['failed', 'blocked', 'cancelled'].includes(state.state)) throw new Error(`Run stopped before the SSH command became active: ${state.state}`);
    await new Promise(resolve => setTimeout(resolve, 100));
  }
  if (!activeEvents.some(event => event.event_type === 'tool.executing' && event.payload?.call_id === callId)) {
    throw new Error('The SSH command did not reach its durable launch boundary.');
  }
  if (!activeEvents.some(event => event.event_type === 'execution.output.delta'
    && Buffer.from(event.payload?.chunk_b64 ?? '', 'base64').toString('utf8').includes(marker))) {
    throw new Error('The remote command did not stream its active marker before process death.');
  }
  report.active_output_observed = true;
  await first.page.screenshot({ path: path.join(root, 'before-process-death.png'), fullPage: true });
  await first.browser.close();
  first.browser = undefined;
  execFileSync('taskkill', ['/PID', String(first.processHandle.pid), '/T', '/F'], { windowsHide: true, stdio: 'ignore' });
  const deathDeadline = Date.now() + 15000;
  while (first.processHandle.exitCode === null && Date.now() < deathDeadline) await new Promise(resolve => setTimeout(resolve, 100));
  if (first.processHandle.exitCode === null) throw new Error('The original Tauri process tree did not exit.');
  let oldApiStopped = false;
  try { await fetch(`${base}/runs`, { headers: first.headers, signal: AbortSignal.timeout(500) }); }
  catch { oldApiStopped = true; }
  if (!oldApiStopped) throw new Error('The old bundled engine API remained reachable after Tauri process death.');

  const expireLease = [
    'import sys',
    'from pathlib import Path',
    'from xaa.storage.runs import RunStore',
    'store=RunStore(Path(sys.argv[1]))',
    'with store.connection() as connection:',
    ' connection.execute("UPDATE runs SET lease_expires_at=? WHERE id=?",("2000-01-01T00:00:00+00:00",sys.argv[2]))',
  ].join('\n');
  execFileSync('uv', ['run', 'python', '-c', expireLease, database, created.id], {
    cwd: path.resolve('.'), windowsHide: true, stdio: 'pipe', timeout: 30000, env: process.env,
  });

  second = await startDesktop('Restarted');
  const recoveryDeadline = Date.now() + 90000;
  let recoveredRun;
  let recoveredEvents = [];
  while (Date.now() < recoveryDeadline) {
    recoveredEvents = await api(second, `${runBase}/events`);
    recoveredRun = await api(second, runBase);
    const reconciled = recoveredEvents.some(event => event.event_type === 'execution.reconciled'
      && event.payload?.verified === false && event.payload?.cleanup_confirmed === true);
    if (reconciled && ['verifying', 'paused', 'blocked', 'failed'].includes(recoveredRun.state)) break;
    await new Promise(resolve => setTimeout(resolve, 150));
  }
  const failures = recoveredEvents.filter(event => event.event_type === 'tool.failed' && event.payload?.call_id === callId);
  const launches = recoveredEvents.filter(event => event.event_type === 'tool.executing' && event.payload?.call_id === callId);
  const completions = recoveredEvents.filter(event => event.event_type === 'execution.completed' && event.payload?.call_id === callId);
  if (launches.length !== 1 || failures.length !== 1 || completions.length !== 0
    || failures[0].payload?.code !== 'command_result_unavailable_locally') {
    throw new Error(`Interrupted SSH action was replayed or promoted: ${JSON.stringify({ launches: launches.length, failures, completions: completions.length, state: recoveredRun?.state })}`);
  }
  if (fixture.commandProposals !== 1 || fixture.recoveryFollowups !== 1) {
    throw new Error(`Deterministic provider observed an unexpected retry: ${JSON.stringify(fixture)}`);
  }
  report.recovered_run_state = recoveredRun.state;
  report.execution_launch_count = launches.length;
  report.unverified_failure_count = failures.length;
  report.execution_completion_count = completions.length;
  report.result_not_replayed = true;
  report.fixture_provider = fixture;

  await second.page.getByRole('link', { name: 'Saved runs' }).click();
  await expect(second.page.getByRole('button', { name: `Open ${created.objective}` })).toBeVisible({ timeout: 15000 });
  await second.page.getByRole('button', { name: `Open ${created.objective}` }).click();
  const output = second.page.getByRole('region', { name: 'Streaming command output' });
  await expect(output).toContainText(marker, { timeout: 15000 });
  await expect(second.page.getByText('Interrupted command reconciled; inspect verification status', { exact: true })).toBeVisible({ timeout: 15000 });
  await second.page.screenshot({ path: path.join(root, 'after-recovery.png'), fullPage: true });
  report.ui_restored_partial_output_and_unverified_state = true;
  report.desktop_sha256 = createHash('sha256').update(await readFile(desktop)).digest('hex');
  report.engine_sha256 = createHash('sha256').update(await readFile(path.join(path.dirname(desktop), 'xaa-engine.exe'))).digest('hex');
  report.passed = true;
} catch (error) {
  report.error = String(error);
  report.fixture_provider = fixture;
  throw error;
} finally {
  await first?.browser?.close().catch(() => {});
  await second?.browser?.close().catch(() => {});
  for (const context of [second, first]) {
    if (context?.processHandle.exitCode === null) {
      execFileSync('taskkill', ['/PID', String(context.processHandle.pid), '/T', '/F'], { windowsHide: true, stdio: 'ignore' });
    }
  }
  await new Promise(resolve => modelServer.close(resolve));
  report.launch_credentials_cleaned = cleanLaunchCredentials();
  await writeFile(path.join(root, 'report.json'), JSON.stringify(report, null, 2));
  console.log(JSON.stringify({ ...report, evidence: path.join(root, 'report.json') }, null, 2));
  if (!report.launch_credentials_cleaned) throw new Error('The SSH restart acceptance could not remove its own launch credentials.');
}
