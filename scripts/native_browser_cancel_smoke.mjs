import { chromium, expect } from '@playwright/test';
import { randomUUID } from 'node:crypto';
import { createServer } from 'node:http';
import { mkdir, readFile, writeFile } from 'node:fs/promises';
import { spawn, execFileSync } from 'node:child_process';
import path from 'node:path';

if (process.platform !== 'win32') throw new Error('Installed desktop cancellation acceptance requires Windows.');

const desktopExecutable = path.resolve(process.argv[2] ?? 'build/native-release/xaa-desktop.exe');
const root = path.resolve('.acceptance', `native-browser-ui-cancel-${randomUUID()}`);
const profile = path.join(root, 'profile');
const localAppData = path.join(profile, 'localappdata');
const roamingAppData = path.join(profile, 'appdata');
const configDirectory = path.join(profile, 'config');
const dataDirectory = path.join(root, 'data');
const workspace = path.join(root, 'workspace');
const logs = path.join(localAppData, 'dev.xyst.xaa', 'logs');
const engineLog = path.join(logs, 'engine.log');
const fakeModel = 'xaa-native-browser-cancel-fixture';
const fakeState = { requests: 0, diagnostics: 0, browserProposals: 0, browserResultFollowups: 0 };
const launchCredentials = [];
let server;
let desktop;
let browser;
let page;
let connection;
let runId;
let browserCallId;
let stdout = '';
let stderr = '';
let report;

await mkdir(root, { recursive: true });
await Promise.all([localAppData, roamingAppData, configDirectory, dataDirectory, workspace, logs]
  .map(directory => mkdir(directory, { recursive: true })));

function sendJson(response, status, value) {
  response.writeHead(status, { 'content-type': 'application/json', 'cache-control': 'no-store' });
  response.end(JSON.stringify(value));
}

function sendCompletion(response, message) {
  fakeState.requests += 1;
  sendJson(response, 200, {
    id: `ui-cancel-fixture-${fakeState.requests}`,
    object: 'chat.completion',
    created: 1,
    model: fakeModel,
    choices: [{ index: 0, message, finish_reason: message.tool_calls ? 'tool_calls' : 'stop' }],
    usage: { prompt_tokens: 32, completion_tokens: 8, total_tokens: 40 },
  });
}

function sendStream(response, message) {
  fakeState.requests += 1;
  response.writeHead(200, { 'content-type': 'text/event-stream', 'cache-control': 'no-cache' });
  response.write(`data: ${JSON.stringify({ choices: [{ index: 0, delta: { role: 'assistant' }, finish_reason: null }] })}\n\n`);
  if (message.tool_calls) {
    const call = message.tool_calls[0];
    response.write(`data: ${JSON.stringify({ choices: [{ index: 0, delta: { tool_calls: [{ index: 0, ...call }] }, finish_reason: null }] })}\n\n`);
  } else if (message.content) {
    response.write(`data: ${JSON.stringify({ choices: [{ index: 0, delta: { content: message.content }, finish_reason: null }] })}\n\n`);
  }
  response.write(`data: ${JSON.stringify({ choices: [{ index: 0, delta: {}, finish_reason: message.tool_calls ? 'tool_calls' : 'stop' }], usage: { prompt_tokens: 32, completion_tokens: 8, total_tokens: 40 } })}\n\n`);
  response.end('data: [DONE]\n\n');
}

server = createServer(async (request, response) => {
  if (request.method === 'GET' && request.url === '/v1/models') {
    sendJson(response, 200, { data: [{ id: fakeModel, object: 'model' }] });
    return;
  }
  if (request.method === 'GET' && request.url === '/api/v1/models') {
    sendJson(response, 200, { models: [{ key: fakeModel, max_context_length: 8192, capabilities: { trained_for_tool_use: true } }] });
    return;
  }
  if (request.method !== 'POST' || request.url !== '/v1/chat/completions') {
    sendJson(response, 404, { error: 'not found' });
    return;
  }
  let raw = '';
  for await (const chunk of request) raw += chunk.toString();
  let body;
  try { body = JSON.parse(raw); }
  catch { sendJson(response, 400, { error: 'invalid JSON' }); return; }
  const messages = body.messages ?? [];
  const user = messages.find(message => message.role === 'user');
  const userText = typeof user?.content === 'string' ? user.content : '';
  const diagnostic = userText.includes('Call xaa_echo with value ');
  let message;
  if (diagnostic && messages.some(item => item.role === 'tool')) {
    const result = messages.find(item => item.role === 'tool')?.content;
    message = { role: 'assistant', content: `Diagnostic result ${String(result ?? '')}` };
  } else if (diagnostic) {
    const match = userText.match(/value ([a-f0-9]+), then report/);
    if (!match) { sendJson(response, 400, { error: 'diagnostic nonce is missing' }); return; }
    fakeState.diagnostics += 1;
    message = {
      role: 'assistant',
      content: null,
      tool_calls: [{
        id: `ui-cancel-diagnostic-${fakeState.diagnostics}`,
        type: 'function',
        function: { name: 'xaa_echo', arguments: JSON.stringify({ value: match[1] }) },
      }],
    };
  } else {
    const tool = (body.tools ?? []).find(item => item.function?.name === 'browser_run');
    if (!tool) { sendJson(response, 409, { error: 'browser_run was not authorized for this run' }); return; }
    const toolResult = messages.some(item => item.role === 'tool');
    if (toolResult) {
      fakeState.browserResultFollowups += 1;
      message = { role: 'assistant', content: 'The browser operation returned.' };
    } else {
      fakeState.browserProposals += 1;
      message = {
        role: 'assistant',
        content: null,
        tool_calls: [{
          id: 'ui-cancel-browser-read',
          type: 'function',
          function: { name: 'browser_run', arguments: JSON.stringify({ url: 'https://httpbin.org/delay/10' }) },
        }],
      };
    }
  }
  if (body.stream === true) sendStream(response, message);
  else sendCompletion(response, message);
});

await new Promise((resolve, reject) => {
  server.once('error', reject);
  server.listen(0, '127.0.0.1', resolve);
});
const providerEndpoint = `http://127.0.0.1:${server.address().port}`;

async function availablePort() {
  const listener = createServer();
  await new Promise(resolve => listener.listen(0, '127.0.0.1', resolve));
  const port = listener.address().port;
  await new Promise((resolve, reject) => listener.close(error => error ? reject(error) : resolve()));
  return port;
}

async function waitForApi(baseUrl, headers) {
  const deadline = Date.now() + 30000;
  while (Date.now() < deadline) {
    try {
      const response = await fetch(`${baseUrl}/runs`, { headers, signal: AbortSignal.timeout(750) });
      if (response.ok) return;
    } catch {}
    await new Promise(resolve => setTimeout(resolve, 200));
  }
  throw new Error('The installed desktop API did not become ready.');
}

async function getRunEvents() {
  const response = await fetch(`${connection.base_url}/runs/${encodeURIComponent(runId)}/events`, { headers: { Authorization: `Bearer ${connection.token}` } });
  if (!response.ok) throw new Error(`Could not read run events: ${response.status}`);
  return response.json();
}

async function waitForBrowserIntent() {
  const deadline = Date.now() + 20000;
  while (Date.now() < deadline) {
    const events = await getRunEvents();
    const intent = events.find(event => event.event_type === 'tool.intended' && event.payload.tool === 'browser.run');
    if (intent) return intent.payload.call_id;
    await new Promise(resolve => setTimeout(resolve, 100));
  }
  throw new Error('The browser tool did not start within the acceptance deadline.');
}

async function cleanLaunchCredentials() {
  const latest = launchCredentials.at(-1);
  if (!latest) return true;
  const source = [
    'import sys',
    'from xaa.credentials import API_LAUNCH_TOKEN_PROFILE, API_LAUNCH_URL_PROFILE, CredentialStore',
    'store = CredentialStore()',
    'clean = True',
    'for profile, expected in ((API_LAUNCH_TOKEN_PROFILE, sys.argv[1]), (API_LAUNCH_URL_PROFILE, sys.argv[2])):',
    '    current = store.get(profile)',
    '    if current == expected: store.delete(profile)',
    '    elif current is not None: clean = False',
    'print(clean)',
  ].join('\n');
  try {
    const result = execFileSync('uv', ['run', 'python', '-c', source, latest.token, latest.base_url], {
      windowsHide: true, encoding: 'utf8', timeout: 30000, env: process.env,
    }).trim();
    return result === 'True';
  } catch { return false; }
}

try {
  const configuredDebugPort = Number(process.env.XAA_NATIVE_BROWSER_CANCEL_DEBUG_PORT);
  const debugPort = Number.isInteger(configuredDebugPort) && configuredDebugPort > 0 && configuredDebugPort <= 65535
    ? configuredDebugPort
    : await availablePort();
  const launchEnvironment = Object.fromEntries(Object.entries(process.env)
    .filter(([key]) => !['PATH', 'PYTHONPATH', 'VIRTUAL_ENV'].includes(key.toUpperCase())));
  Object.assign(launchEnvironment, {
    PATH: path.join(process.env.SystemRoot, 'System32'),
    APPDATA: roamingAppData,
    LOCALAPPDATA: localAppData,
    LM_STUDIO_URL: providerEndpoint,
    XAA_DATA_DIR: dataDirectory,
    XAA_TEST_CONFIG_DIR: configDirectory,
    XAA_TEST_LOG_DIR: logs,
    WEBVIEW2_USER_DATA_FOLDER: path.join(profile, 'webview-profile'),
    WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS: `--remote-debugging-port=${debugPort}`,
  });
  const oldLog = await readFile(engineLog, 'utf8').catch(() => '');
  desktop = spawn(desktopExecutable, [], { windowsHide: true, stdio: ['ignore', 'pipe', 'pipe'], env: launchEnvironment });
  desktop.stdout.on('data', chunk => { stdout = (stdout + chunk.toString()).slice(-16000); });
  desktop.stderr.on('data', chunk => { stderr = (stderr + chunk.toString()).slice(-16000); });

  const debugDeadline = Date.now() + 60000;
  let debugEndpoint;
  while (Date.now() < debugDeadline) {
    try {
      const response = await fetch(`http://127.0.0.1:${debugPort}/json/version`, { signal: AbortSignal.timeout(750) });
      const metadata = await response.json();
      if (!response.ok || typeof metadata.webSocketDebuggerUrl !== 'string') throw new Error('WebView debug metadata is unavailable');
      const endpoint = new URL(metadata.webSocketDebuggerUrl);
      if (endpoint.protocol !== 'ws:' || !['127.0.0.1', 'localhost', '[::1]'].includes(endpoint.hostname) || endpoint.port !== String(debugPort)) throw new Error('Unexpected WebView debug endpoint');
      endpoint.hostname = '127.0.0.1';
      debugEndpoint = endpoint.toString();
      break;
    } catch (error) {
      if (desktop.exitCode !== null) throw new Error(`Installed desktop exited during startup: ${String(error)}; stdout=${stdout}; stderr=${stderr}`);
      await new Promise(resolve => setTimeout(resolve, 200));
    }
  }
  if (!debugEndpoint) throw new Error('The installed WebView debugging endpoint did not become available.');
  browser = await chromium.connectOverCDP(debugEndpoint, { timeout: 5000 });
  const pageDeadline = Date.now() + 20000;
  while (!page && Date.now() < pageDeadline) {
    page = browser.contexts().flatMap(context => context.pages())[0];
    if (!page) await new Promise(resolve => setTimeout(resolve, 100));
  }
  if (!page) throw new Error('The installed desktop did not expose its WebView page.');
  await expect(page.getByRole('heading', { name: 'Xyst AI Agent', exact: true })).toBeVisible({ timeout: 20000 });
  connection = await page.evaluate(() => window.__TAURI_INTERNALS__.invoke('engine_connection'));
  launchCredentials.push(connection);
  const headers = { Authorization: `Bearer ${connection.token}` };
  await waitForApi(connection.base_url, headers);

  const providerStatus = page.locator('section[aria-labelledby="provider-setup-title"] [role="status"]').first();
  await expect(providerStatus).not.toHaveText('Checking the local model server…', { timeout: 30000 });
  await page.locator('#lm-studio-endpoint').fill(providerEndpoint);
  await page.getByRole('button', { name: 'Save address and connect' }).click();
  await expect(providerStatus).toHaveText('Connected. Found 1 model.', { timeout: 15000 });
  await expect(page.locator('#local-model').locator('option', { hasText: fakeModel })).toHaveCount(1, { timeout: 15000 });
  await page.locator('#local-model').selectOption(fakeModel);
  const diagnosticResponse = page.waitForResponse(response => new URL(response.url()).pathname === '/providers/diagnostics', { timeout: 15000 });
  await page.getByRole('button', { name: 'Run harmless tool-use diagnostic' }).click();
  const diagnosticResult = await diagnosticResponse;
  if (!diagnosticResult.ok()) throw new Error(`Native harmless diagnostic failed: ${await diagnosticResult.text()}`);
  const diagnosticBody = await diagnosticResult.json();
  if (diagnosticBody.tool_round_trip_verified !== true) throw new Error(`Native harmless diagnostic was not verified: ${JSON.stringify(diagnosticBody)}`);
  await expect(page.getByText(/Tool-use diagnostic passed/)).toBeVisible({ timeout: 15000 });

  await page.locator('#approved-web-hosts').fill('httpbin.org');
  await page.getByLabel('Allow managed browser reads for this next run').check();
  await page.getByLabel('Workspace path').fill(workspace);
  await page.locator('#run-objective').fill('Open the approved delayed page and summarize its visible content.');
  const created = page.waitForResponse(response => response.request().method() === 'POST'
    && new URL(response.url()).pathname === '/runs', { timeout: 15000 });
  await page.getByRole('button', { name: 'Start objective', exact: true }).click();
  const createResponse = await created;
  if (!createResponse.ok()) throw new Error(`Desktop did not create the browser run: ${await createResponse.text()}`);
  const run = await createResponse.json();
  runId = run.id;
  await expect(page.getByRole('region', { name: 'Active run' })).toBeVisible({ timeout: 10000 });
  browserCallId = await waitForBrowserIntent();

  let cancelRequestSeen = false;
  page.on('request', request => {
    const url = new URL(request.url());
    if (request.method() === 'POST' && url.pathname === `/runs/${encodeURIComponent(runId)}/cancel`) cancelRequestSeen = true;
  });
  await new Promise(resolve => setTimeout(resolve, 500));
  await page.getByRole('button', { name: 'cancel', exact: true }).click();

  const cancelDeadline = Date.now() + 15000;
  let finalRun;
  let toolResult;
  while (Date.now() < cancelDeadline) {
    const runResponse = await fetch(`${connection.base_url}/runs/${encodeURIComponent(runId)}`, { headers });
    if (runResponse.ok) finalRun = await runResponse.json();
    const resultResponse = await fetch(`${connection.base_url}/runs/${encodeURIComponent(runId)}/tools/${encodeURIComponent(browserCallId)}/result`, { headers });
    if (resultResponse.ok) {
      const body = await resultResponse.json();
      if (typeof body.result === 'string') {
        try { toolResult = JSON.parse(body.result); } catch {}
      }
    }
    if (finalRun?.state === 'cancelled' && toolResult?.status === 'cancelled') break;
    await new Promise(resolve => setTimeout(resolve, 100));
  }
  if (!cancelRequestSeen) throw new Error('The desktop UI did not send the authenticated run cancellation request.');
  if (finalRun?.state !== 'cancelled') throw new Error(`The desktop run did not reach cancelled: ${finalRun?.state ?? 'missing'}`);
  if (toolResult?.status !== 'cancelled') throw new Error(`Browser cancellation result was not durably saved: ${JSON.stringify(toolResult)}`);
  if (fakeState.browserProposals !== 1 || fakeState.browserResultFollowups !== 0) throw new Error('The fake provider observed an unexpected browser proposal or post-cancellation inference.');

  await page.evaluate(() => window.__TAURI_INTERNALS__.invoke('quit_application')).catch(() => {});
  const exitDeadline = Date.now() + 15000;
  while (desktop.exitCode === null && Date.now() < exitDeadline) await new Promise(resolve => setTimeout(resolve, 100));
  if (desktop.exitCode === null) throw new Error('The installed desktop did not stop cleanly after cancellation acceptance.');
  report = {
    passed: true,
    desktop_executable: desktopExecutable,
    model_inference_requested: false,
    provider: 'deterministic loopback OpenAI-compatible fixture',
    user_action: 'cancel button in installed desktop WebView',
    cancel_request_observed: cancelRequestSeen,
    run_id: runId,
    browser_call_id: browserCallId,
    final_run_state: finalRun.state,
    saved_browser_result: toolResult,
    browser_proposals: fakeState.browserProposals,
    post_cancel_model_followups: fakeState.browserResultFollowups,
    desktop_exit_code: desktop.exitCode,
    profile_root: profile,
  };
  await writeFile(path.join(root, 'report.json'), `${JSON.stringify(report, null, 2)}\n`);
} catch (error) {
  const log = await readFile(engineLog, 'utf8').catch(() => '');
  const tail = log.slice(Math.max(0, log.length - 20000));
  await writeFile(path.join(root, 'desktop-stdout.log'), stdout).catch(() => {});
  await writeFile(path.join(root, 'desktop-stderr.log'), stderr).catch(() => {});
  await writeFile(path.join(root, 'engine-startup.log'), tail).catch(() => {});
  await page?.screenshot({ path: path.join(root, 'failure.png'), fullPage: true, timeout: 5000 }).catch(() => {});
  const providerStatusText = await page?.locator('section[aria-labelledby="provider-setup-title"] [role="status"]').first().innerText().catch(() => null);
  const providerErrorText = await page?.locator('section[aria-labelledby="provider-setup-title"] [role="alert"]').first().innerText().catch(() => null);
  await writeFile(path.join(root, 'report.json'), `${JSON.stringify({
    passed: false,
    error: String(error),
    provider_status: providerStatusText,
    provider_error: providerErrorText,
    run_id: runId,
    browser_call_id: browserCallId,
    provider_state: fakeState,
    model_inference_requested: false,
  }, null, 2)}\n`).catch(() => {});
  throw error;
} finally {
  await browser?.close().catch(() => {});
  if (desktop?.exitCode === null && desktop) {
    try { execFileSync('taskkill', ['/PID', String(desktop.pid), '/T', '/F'], { windowsHide: true, stdio: 'ignore' }); } catch {}
  }
  if (server) await new Promise(resolve => server.close(() => resolve()));
  const credentialsClean = await cleanLaunchCredentials();
  if (report) {
    report.launch_credentials_clean = credentialsClean;
    await writeFile(path.join(root, 'report.json'), `${JSON.stringify(report, null, 2)}\n`);
  }
}

if (!report?.passed || !report.launch_credentials_clean) throw new Error('Native browser cancellation acceptance did not finish cleanly.');
console.log(JSON.stringify({ ...report, report: path.join(root, 'report.json') }, null, 2));
