import { chromium, expect } from '@playwright/test';
import { createHash, randomUUID } from 'node:crypto';
import { createServer } from 'node:http';
import { mkdir, readFile, writeFile } from 'node:fs/promises';
import { spawn, execFileSync } from 'node:child_process';
import path from 'node:path';

if (process.platform !== 'win32') throw new Error('Native restart acceptance requires Windows.');

const desktopExecutable = path.resolve(process.argv[2] ?? 'build/native-dev/xaa-desktop.exe');
const root = path.resolve('.acceptance', `native-restart-${randomUUID()}`);
const profile = path.join(root, 'profile');
const localAppData = path.join(profile, 'localappdata');
const appData = path.join(profile, 'appdata');
const logDirectory = path.join(localAppData, 'dev.xyst.xaa', 'logs');
const database = path.join(localAppData, 'XAA', 'xaa.sqlite3');
const fixtureWorkspace = path.join(root, 'workspace');
const engineLog = path.join(logDirectory, 'engine.log');
const fakeModel = 'xaa-native-restart-fixture';
const fakeState = { runStarted: false, allowRunResponse: false, accepts: 0, diagnostics: 0, requests: 0, streamedCandidates: 0 };
let diagnosticsNonce;

await Promise.all([root, localAppData, appData, logDirectory, fixtureWorkspace].map(directory => mkdir(directory, { recursive: true })));
await writeFile(path.join(fixtureWorkspace, 'fixture.txt'), 'native restart fixture remains unchanged\n');

function reply(response, status, value) {
  response.writeHead(status, { 'content-type': 'application/json', 'cache-control': 'no-store' });
  response.end(JSON.stringify(value));
}

function chatCompletion(response, message) {
  fakeState.accepts += 1;
  reply(response, 200, {
    id: `fixture-${fakeState.accepts}`,
    object: 'chat.completion',
    created: 1,
    model: fakeModel,
    choices: [{ index: 0, message, finish_reason: message.tool_calls ? 'tool_calls' : 'stop' }],
    usage: { prompt_tokens: 32, completion_tokens: 9, total_tokens: 41 },
  });
}

function streamedCompletion(response, content) {
  const usage = { prompt_tokens: 32, completion_tokens: 9, total_tokens: 41 };
  response.writeHead(200, { 'content-type': 'text/event-stream', 'cache-control': 'no-cache' });
  response.write(`data: ${JSON.stringify({ choices: [{ index: 0, delta: { role: 'assistant', content }, finish_reason: null }] })}\n\n`);
  response.write(`data: ${JSON.stringify({ choices: [{ index: 0, delta: {}, finish_reason: 'stop' }], usage })}\n\n`);
  response.end('data: [DONE]\n\n');
}

const modelServer = createServer(async (request, response) => {
  if (request.method === 'GET' && request.url === '/v1/models') {
    reply(response, 200, { data: [{ id: fakeModel, object: 'model' }] });
    return;
  }
  if (request.method === 'GET' && request.url === '/api/v1/models') {
    reply(response, 200, { models: [{ key: fakeModel, max_context_length: 8192, capabilities: { trained_for_tool_use: true } }] });
    return;
  }
  if (request.method !== 'POST' || request.url !== '/v1/chat/completions') {
    reply(response, 404, { error: 'not found' });
    return;
  }
  fakeState.requests += 1;
  let raw = '';
  for await (const chunk of request) raw += chunk.toString();
  const body = JSON.parse(raw);
  const messages = body.messages ?? [];
  const user = messages.find(message => message.role === 'user');
  const userText = typeof user?.content === 'string' ? user.content : '';
  const diagnosticPrompt = userText.includes('Call xaa_echo with value ');
  if (diagnosticPrompt && messages.some(message => message.role === 'tool')) {
    chatCompletion(response, { role: 'assistant', content: `Diagnostic result ${diagnosticsNonce}` });
    return;
  }
  if (diagnosticPrompt) {
    const match = userText.match(/value ([a-f0-9]+), then report/);
    if (!match) {
      reply(response, 400, { error: 'diagnostic nonce not found' });
      return;
    }
    diagnosticsNonce = match[1];
    fakeState.diagnostics += 1;
    chatCompletion(response, {
      role: 'assistant',
      content: null,
      tool_calls: [{
        id: `diagnostic-${fakeState.diagnostics}`,
        type: 'function',
        function: { name: 'xaa_echo', arguments: JSON.stringify({ value: diagnosticsNonce }) },
      }],
    });
    return;
  }
  if (!fakeState.runStarted) {
    fakeState.runStarted = true;
    request.on('close', () => {});
    return;
  }
  if (!fakeState.allowRunResponse) {
    reply(response, 503, { error: 'unexpected extra run inference before restart' });
    return;
  }
  if (body.stream === true) {
    fakeState.streamedCandidates += 1;
    streamedCompletion(response, 'Recovered after the X.A.A. desktop restart.');
  }
  else chatCompletion(response, { role: 'assistant', content: 'Recovered after the X.A.A. desktop restart.' });
});

await new Promise((resolve, reject) => {
  modelServer.once('error', reject);
  modelServer.listen(0, '127.0.0.1', resolve);
});
const modelPort = modelServer.address().port;
const providerEndpoint = `http://127.0.0.1:${modelPort}`;

const appEnv = Object.fromEntries(Object.entries(process.env).filter(([key]) => !['PATH', 'PYTHONPATH', 'VIRTUAL_ENV'].includes(key.toUpperCase())));
appEnv.PATH = path.join(process.env.SystemRoot, 'System32');
appEnv.APPDATA = appData;
appEnv.LOCALAPPDATA = localAppData;
appEnv.XAA_DATA_DIR = root;
appEnv.XAA_TEST_HIDDEN = '1';
appEnv.XAA_TEST_LOG_DIR = logDirectory;
appEnv.WEBVIEW2_USER_DATA_FOLDER = path.join(root, 'webview-profile');

async function availablePort() {
  const server = createServer();
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const port = server.address().port;
  await new Promise((resolve, reject) => server.close(error => error ? reject(error) : resolve()));
  return port;
}

async function startDesktop(label) {
  const debugPort = await availablePort();
  const proc = spawn(desktopExecutable, [], {
    windowsHide: true,
    stdio: ['ignore', 'pipe', 'pipe'],
    env: { ...appEnv, WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS: `--remote-debugging-port=${debugPort}` },
  });
  let stdout = '';
  let stderr = '';
  proc.stdout.on('data', chunk => { stdout = (stdout + chunk.toString()).slice(-16000); });
  proc.stderr.on('data', chunk => { stderr = (stderr + chunk.toString()).slice(-16000); });
  let browser;
  try {
    const deadline = Date.now() + 60000;
    let lastError = 'Debugging endpoint unavailable';
    while (Date.now() < deadline) {
      try {
        const response = await fetch(`http://127.0.0.1:${debugPort}/json/version`, { signal: AbortSignal.timeout(750) });
        const metadata = await response.json();
        if (!response.ok || typeof metadata.webSocketDebuggerUrl !== 'string') throw new Error('WebView debug metadata is unavailable');
        const endpoint = new URL(metadata.webSocketDebuggerUrl);
        if (endpoint.protocol !== 'ws:' || !['127.0.0.1', 'localhost', '[::1]'].includes(endpoint.hostname) || endpoint.port !== String(debugPort)) throw new Error('Unexpected WebView debug endpoint');
        endpoint.hostname = '127.0.0.1';
        browser = await chromium.connectOverCDP(endpoint.toString(), { timeout: 3000 });
        break;
      } catch (error) {
        lastError = String(error);
        if (proc.exitCode !== null) throw new Error(`${label} desktop exited during startup: ${lastError}`);
        await new Promise(resolve => setTimeout(resolve, 200));
      }
    }
    if (!browser) throw new Error(`${label} desktop WebView did not become available: ${lastError}`);
    const page = browser.contexts().flatMap(context => context.pages())[0];
    if (!page) throw new Error(`${label} desktop did not expose a WebView page`);
    await expect(page.getByRole('heading', { name: 'Xyst AI Agent', exact: true })).toBeVisible({ timeout: 20000 });
    const connection = await page.evaluate(() => window.__TAURI_INTERNALS__.invoke('engine_connection'));
    const headers = { Authorization: `Bearer ${connection.token}` };
    const apiDeadline = Date.now() + 30000;
    while (Date.now() < apiDeadline) {
      try {
        const response = await fetch(`${connection.base_url}/runs`, { headers, signal: AbortSignal.timeout(750) });
        if (response.ok) return { proc, browser, page, connection, headers, stdout, stderr };
      } catch {}
      if (proc.exitCode !== null) throw new Error(`${label} desktop exited before its engine API was ready`);
      await new Promise(resolve => setTimeout(resolve, 200));
    }
    throw new Error(`${label} engine API did not become ready`);
  } catch (error) {
    await browser?.close().catch(() => {});
    if (proc.exitCode === null) execFileSync('taskkill', ['/PID', String(proc.pid), '/T', '/F'], { windowsHide: true, stdio: 'ignore' });
    throw error;
  }
}

let first;
let second;
let report;
try {
  first = await startDesktop('First');
  const configResponse = await fetch(`${first.connection.base_url}/providers/lm-studio/config`, {
    method: 'PUT', headers: { ...first.headers, 'Content-Type': 'application/json' },
    body: JSON.stringify({ endpoint: providerEndpoint }),
  });
  if (!configResponse.ok) throw new Error(`Could not configure the local recovery fixture: ${await configResponse.text()}`);
  const diagnosticResponse = await fetch(`${first.connection.base_url}/providers/lm-studio/diagnostics`, {
    method: 'POST', headers: { ...first.headers, 'Content-Type': 'application/json' },
    body: JSON.stringify({ model: fakeModel }),
  });
  if (!diagnosticResponse.ok) throw new Error(`The native provider diagnostic failed: ${await diagnosticResponse.text()}`);
  const diagnosticResult = await diagnosticResponse.json();
  if (diagnosticResult.tool_round_trip_verified !== true) throw new Error('The native provider diagnostic did not complete its harmless tool round trip.');
  const fixtureReadme = path.join(fixtureWorkspace, 'fixture.txt');
  const createResponse = await fetch(`${first.connection.base_url}/runs`, {
    method: 'POST', headers: { ...first.headers, 'Content-Type': 'application/json' },
    body: JSON.stringify({
      objective: 'Produce a harmless result so desktop crash recovery can be inspected.',
      acceptance_criteria: ['Return a short recovery result.'],
      workspace: fixtureWorkspace,
      mode: 'observe', model: fakeModel, max_iterations: 4, max_seconds: 90,
    }),
  });
  if (createResponse.status !== 201) throw new Error(`Could not create the recovery run: ${await createResponse.text()}`);
  const run = await createResponse.json();
  if (!run.id) throw new Error('The native engine did not return a durable run ID.');
  const blockedDeadline = Date.now() + 30000;
  while (!fakeState.runStarted && Date.now() < blockedDeadline) await new Promise(resolve => setTimeout(resolve, 50));
  if (!fakeState.runStarted) throw new Error('The recovered run never reached its first inference request.');
  const inFlight = await (await fetch(`${first.connection.base_url}/runs/${encodeURIComponent(run.id)}`, { headers: first.headers })).json();
  if (!['planning', 'running'].includes(inFlight.state)) throw new Error(`Expected an active run at the crash boundary, found ${inFlight.state}.`);
  await first.page.screenshot({ path: path.join(root, 'before-restart.png'), fullPage: true });
  await first.browser.close();
  first.browser = undefined;
  execFileSync('taskkill', ['/PID', String(first.proc.pid), '/T', '/F'], { windowsHide: true, stdio: 'ignore' });
  await new Promise((resolve, reject) => {
    const deadline = Date.now() + 15000;
    const poll = () => first.proc.exitCode !== null ? resolve() : Date.now() >= deadline ? reject(new Error('The killed desktop process did not exit.')) : setTimeout(poll, 100);
    poll();
  });
  let oldApiStopped = false;
  try { await fetch(`${first.connection.base_url}/runs/${encodeURIComponent(run.id)}`, { headers: first.headers, signal: AbortSignal.timeout(500) }); }
  catch { oldApiStopped = true; }
  if (!oldApiStopped) throw new Error('The old bundled engine API remained reachable after desktop process death.');

  execFileSync('uv', ['run', 'python', '-c',
    "import sqlite3,sys; c=sqlite3.connect(sys.argv[1]); c.execute(\"UPDATE runs SET lease_expires_at='2000-01-01T00:00:00+00:00' WHERE id=?\",(sys.argv[2],)); c.commit(); c.close()",
    database, run.id], { windowsHide: true, stdio: 'pipe', timeout: 30000, env: process.env });
  fakeState.allowRunResponse = true;
  second = await startDesktop('Restarted');
  const recoveryDeadline = Date.now() + 45000;
  let recovered;
  while (Date.now() < recoveryDeadline) {
    const response = await fetch(`${second.connection.base_url}/runs/${encodeURIComponent(run.id)}`, { headers: second.headers });
    if (response.ok) {
      recovered = await response.json();
      if (recovered.state === 'verifying') break;
    }
    await new Promise(resolve => setTimeout(resolve, 200));
  }
  if (!recovered || recovered.state !== 'verifying') throw new Error(`Recovered native run did not reach review: ${recovered?.state ?? 'missing'}`);
  const events = await (await fetch(`${second.connection.base_url}/runs/${encodeURIComponent(run.id)}/events`, { headers: second.headers })).json();
  const recoveredEvent = events.find(event => event.event_type === 'run.recovered');
  const savedAnswers = events.filter(event => event.event_type === 'model.message');
  if (!recoveredEvent || savedAnswers.length !== 1 || !JSON.stringify(savedAnswers[0]).includes('Recovered after the X.A.A. desktop restart.')) {
    throw new Error('The restarted Tauri app did not persist exactly one recovery candidate and recovery event.');
  }
  if (events.some(event => ['tool.intended', 'tool.executing', 'tool.completed'].includes(event.event_type))) {
    throw new Error('The read-only native recovery fixture unexpectedly executed a tool.');
  }
  const fixtureAfter = await readFile(fixtureReadme, 'utf8');
  if (fixtureAfter !== 'native restart fixture remains unchanged\n') throw new Error('The recovery fixture changed.');
  await second.page.screenshot({ path: path.join(root, 'after-restart.png'), fullPage: true });
  report = {
    passed: true,
    scope: 'Windows Tauri desktop plus bundled engine process death and same-profile restart; deterministic loopback OpenAI-compatible fixture; no LM Studio or external provider',
    run_id: run.id,
    model_requests: fakeState.requests,
    tool_diagnostic_round_trips: fakeState.diagnostics,
    streamed_candidates: fakeState.streamedCandidates,
    harmless_tool_diagnostic_verified: diagnosticResult.tool_round_trip_verified,
    recovery_event: recoveredEvent.payload,
    candidate_count: savedAnswers.length,
    final_state: recovered.state,
    tool_effects: 0,
    workspace_unchanged: true,
    desktop_sha256: createHash('sha256').update(await readFile(desktopExecutable)).digest('hex'),
    engine_sha256: createHash('sha256').update(await readFile(path.join(path.dirname(desktopExecutable), 'xaa-engine.exe'))).digest('hex'),
  };
  await writeFile(path.join(root, 'report.json'), JSON.stringify(report, null, 2));
  await mkdir('build/installer', { recursive: true });
  await writeFile('build/installer/native-restart-acceptance.json', JSON.stringify(report, null, 2));
  console.log(JSON.stringify({ ...report, evidence: root }, null, 2));
} catch (error) {
  const detail = { passed: false, error: String(error), root, provider_requests: fakeState.accepts, diagnostic_requests: fakeState.diagnostics };
  await writeFile(path.join(root, 'report.json'), JSON.stringify(detail, null, 2)).catch(() => {});
  throw error;
} finally {
  await first?.browser?.close().catch(() => {});
  await second?.browser?.close().catch(() => {});
  for (const item of [first, second]) {
    if (item?.proc.exitCode === null) execFileSync('taskkill', ['/PID', String(item.proc.pid), '/T', '/F'], { windowsHide: true, stdio: 'ignore' });
  }
  await new Promise(resolve => modelServer.close(resolve));
}
