import { chromium, expect } from '@playwright/test';
import { createHash, randomUUID } from 'node:crypto';
import { createServer } from 'node:http';
import { mkdir, readFile, writeFile } from 'node:fs/promises';
import { spawn, execFileSync } from 'node:child_process';
import path from 'node:path';

if (process.platform !== 'win32') throw new Error('Native restart acceptance requires Windows.');

const desktopExecutable = path.resolve(process.argv.slice(2).find(argument => !argument.startsWith('--')) ?? 'build/native-dev/xaa-desktop.exe');
const liveSpecialistMode = process.argv.includes('--live-specialist');
const specialistMode = process.argv.includes('--specialist') || liveSpecialistMode;
const onboardingMode = process.argv.includes('--onboarding');
if (onboardingMode && specialistMode) throw new Error('Native onboarding acceptance cannot be combined with specialist recovery modes.');
const acceptanceName = onboardingMode ? 'native-onboarding-acceptance.json' : liveSpecialistMode ? 'native-live-specialist-restart-acceptance.json' : specialistMode ? 'native-specialist-restart-acceptance.json' : 'native-restart-acceptance.json';
const root = path.resolve('.acceptance', `${onboardingMode ? 'native-onboarding' : liveSpecialistMode ? 'native-live-specialist-restart' : specialistMode ? 'native-specialist-restart' : 'native-restart'}-${randomUUID()}`);
const profile = path.join(root, 'profile');
const localAppData = path.join(profile, 'localappdata');
const appData = path.join(profile, 'appdata');
const logDirectory = path.join(localAppData, 'dev.xyst.xaa', 'logs');
const database = path.join(root, 'XAA', 'xaa.sqlite3');
const fixtureWorkspace = path.join(root, 'workspace');
const engineLog = path.join(logDirectory, 'engine.log');
const fakeModel = liveSpecialistMode ? (process.env.XAA_NATIVE_LIVE_MODEL ?? 'qwen3-0.6b') : onboardingMode ? 'xaa-native-onboarding-fixture' : 'xaa-native-restart-fixture';
const onboardingProjectIdentity = `Aurora Ledger ${randomUUID().slice(0, 8)}`;
const onboardingMarker = `NATIVE-ONBOARDING-${randomUUID()}`;
const fakeState = {
  runStarted: false,
  allowRunResponse: false,
  accepts: 0,
  diagnostics: 0,
  requests: 0,
  streamedCandidates: 0,
  specialistReadProposals: 0,
  specialistResultRequests: 0,
  specialistResultBlocked: false,
  chatContinuationRequests: 0,
  chatContextVerified: false,
  allowSpecialistResponse: false,
  rolePromptChecks: 0,
  liveToolProposals: 0,
  liveResultBlocked: false,
  liveResumeAllowed: false,
  liveResultFollowups: 0,
  liveSavedResultResumePrompts: 0,
  onboardingReadProposals: 0,
  onboardingResultFollowups: 0,
};
const liveProviderEndpoint = 'http://127.0.0.1:1234';
let diagnosticsNonce;
const specialistNonce = `native-specialist-evidence-${randomUUID()}`;
const restoredCommandOutput = `NATIVE-REPLAY-OUTPUT-${randomUUID()}`;
const specialistIdentity = `Evidence Reader ${randomUUID().slice(0, 8)}`;
const specialistInstructions = liveSpecialistMode
  ? `Call workspace_read_file for fixture.txt exactly once. After it returns, do not call tools again. Quote the exact text field from that saved result, including ${specialistNonce}. If the conversation already contains a tool result for this file, use it immediately and do not reread.`
  : `Use workspace_read_file and quote this saved result exactly for ${specialistNonce}.`;

await Promise.all([root, localAppData, appData, logDirectory, fixtureWorkspace].map(directory => mkdir(directory, { recursive: true })));
await writeFile(path.join(fixtureWorkspace, 'fixture.txt'), specialistMode ? specialistNonce : 'native restart fixture remains unchanged\n');
if (onboardingMode) await writeFile(path.join(fixtureWorkspace, 'README.md'), `# ${onboardingProjectIdentity}\n\n${onboardingMarker}\n\nA desktop ledger for community gardens.\n\n## Setup\nRun the packaged desktop application and open the selected workspace.\n`);

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

function streamedToolCall(response, callId, name, argumentsJson) {
  fakeState.accepts += 1;
  response.writeHead(200, { 'content-type': 'text/event-stream', 'cache-control': 'no-cache' });
  response.write(`data: ${JSON.stringify({ choices: [{ index: 0, delta: { role: 'assistant' }, finish_reason: null }] })}\n\n`);
  response.write(`data: ${JSON.stringify({ choices: [{ index: 0, delta: { tool_calls: [{ index: 0, id: callId, type: 'function', function: { name, arguments: argumentsJson } }] }, finish_reason: null }] })}\n\n`);
  response.write(`data: ${JSON.stringify({ choices: [{ index: 0, delta: {}, finish_reason: 'tool_calls' }], usage: { prompt_tokens: 32, completion_tokens: 9, total_tokens: 41 } })}\n\n`);
  response.end('data: [DONE]\n\n');
}

const modelServer = createServer(async (request, response) => {
  if (liveSpecialistMode) {
    let raw = '';
    if (request.method !== 'GET' && request.method !== 'HEAD') {
      for await (const chunk of request) raw += chunk.toString();
    }
    let body;
    try { body = raw ? JSON.parse(raw) : undefined; }
    catch { reply(response, 400, { error: 'invalid JSON from the desktop engine' }); return; }
    const messages = body?.messages ?? [];
    const taskSystem = messages.find(message => message.role === 'system' && typeof message.content === 'string' && message.content.includes(specialistIdentity));
    const hasSavedToolResult = messages.some(message => message.role === 'tool');
    if (taskSystem && messages.some(message => String(message.content).includes(specialistInstructions))) {
      fakeState.rolePromptChecks += 1;
      if (hasSavedToolResult) {
        fakeState.liveResultFollowups += 1;
        if (fakeState.liveResumeAllowed && messages.filter(message => message.role === 'tool').some(message => String(message.content).includes(specialistNonce))) {
          fakeState.liveSavedResultResumePrompts += 1;
        }
        if (!fakeState.liveResumeAllowed && !fakeState.liveResultBlocked) {
          fakeState.liveResultBlocked = true;
          response.writeHead(200, { 'content-type': 'text/event-stream', 'cache-control': 'no-cache', connection: 'keep-alive' });
          return;
        }
      }
    }
    let upstream;
    try {
      upstream = await fetch(`${liveProviderEndpoint}${request.url}`, {
        method: request.method,
        headers: { 'content-type': request.headers['content-type'] ?? 'application/json' },
        ...(raw ? { body: raw } : {}),
        signal: AbortSignal.timeout(180000),
      });
      const bytes = Buffer.from(await upstream.arrayBuffer());
      const contentType = upstream.headers.get('content-type') ?? 'application/json';
      response.writeHead(upstream.status, { 'content-type': contentType, 'cache-control': 'no-store' });
      response.end(bytes);
      if (taskSystem && request.url === '/v1/chat/completions') {
        const text = bytes.toString('utf8');
        fakeState.liveToolProposals += [...text.matchAll(/workspace_read_file/g)].length;
      }
    } catch (error) {
      if (!response.headersSent) reply(response, 502, { error: `local LM Studio proxy failed: ${String(error)}` });
      else response.destroy(error instanceof Error ? error : undefined);
    }
    return;
  }
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
  if (onboardingMode) {
    const toolNames = (body.tools ?? []).map(tool => tool.function?.name);
    if (!toolNames.includes('workspace_read_file')) {
      reply(response, 409, { error: 'onboarding request omitted the read-only workspace tool' });
      return;
    }
    if (messages.some(message => message.role === 'tool')) {
      const toolResults = messages.filter(message => message.role === 'tool').map(message => String(message.content));
      if (!toolResults.some(result => result.includes(onboardingMarker))) {
        reply(response, 409, { error: 'onboarding follow-up omitted the exact README tool result' });
        return;
      }
      fakeState.onboardingResultFollowups += 1;
      if (fakeState.onboardingResultFollowups !== 1) {
        reply(response, 409, { error: 'onboarding requested an unexpected repeated model follow-up' });
        return;
      }
      const answer = `${onboardingProjectIdentity} is a desktop ledger for community gardens. The README says to run the packaged desktop application and open the selected workspace. The project marker is ${onboardingMarker}. A useful next step is to add a test for ledger entry validation. Source: README.md.`;
      if (body.stream === true) streamedCompletion(response, answer);
      else chatCompletion(response, { role: 'assistant', content: answer });
      return;
    }
    fakeState.onboardingReadProposals += 1;
    if (fakeState.onboardingReadProposals !== 1) {
      reply(response, 409, { error: 'onboarding proposed more than one workspace read' });
      return;
    }
    if (body.stream === true) {
      streamedToolCall(response, 'native-onboarding-readme-once', 'workspace_read_file', JSON.stringify({ path: 'README.md' }));
    } else {
      chatCompletion(response, {
        role: 'assistant',
        content: null,
        tool_calls: [{ id: 'native-onboarding-readme-once', type: 'function', function: { name: 'workspace_read_file', arguments: JSON.stringify({ path: 'README.md' }) } }],
      });
    }
    return;
  }
  if (!specialistMode && userText.includes('Current user turn:\nContinue it')) {
    const expectedHistory = 'Prior turns:\n[["user","Earlier question"],["assistant","Earlier reply"]]';
    if (!userText.includes(expectedHistory) || ++fakeState.chatContinuationRequests !== 1) {
      reply(response, 409, { error: 'native chat continuation omitted saved history or repeated the request' });
      return;
    }
    fakeState.chatContextVerified = true;
    streamedCompletion(response, 'Native desktop continued the saved conversation.');
    return;
  }
  if (specialistMode) {
    const toolNames = (body.tools ?? []).map(tool => tool.function?.name);
    if (!toolNames.includes('workspace_read_file')) {
      reply(response, 400, { error: 'specialist request omitted workspace_read_file' });
      return;
    }
    const systemMessage = messages.find(message => message.role === 'system');
    const systemText = typeof systemMessage?.content === 'string' ? systemMessage.content : '';
    if (systemText.includes(specialistIdentity) && systemText.includes(specialistInstructions)) {
      fakeState.rolePromptChecks += 1;
    } else {
      reply(response, 409, { error: 'specialist identity or saved instructions changed across profile edits' });
      return;
    }
    if (messages.some(message => message.role === 'tool')) {
      const toolResults = messages.filter(message => message.role === 'tool');
      if (!toolResults.some(message => String(message.content).includes(specialistNonce))) {
        reply(response, 409, { error: 'saved specialist prompt omitted the exact read result' });
        return;
      }
      fakeState.specialistResultRequests += 1;
      if (fakeState.specialistResultRequests === 1 && !fakeState.allowSpecialistResponse) {
        fakeState.specialistResultBlocked = true;
        request.on('close', () => {});
        return;
      }
      if (fakeState.specialistResultRequests > 2 || !fakeState.allowSpecialistResponse) {
        reply(response, 409, { error: 'unexpected specialist continuation request' });
        return;
      }
      fakeState.streamedCandidates += 1;
      streamedCompletion(response, `Recovered specialist candidate with exact saved file evidence: ${specialistNonce}`);
      return;
    }
    fakeState.specialistReadProposals += 1;
    if (fakeState.specialistReadProposals !== 1) {
      reply(response, 409, { error: 'the specialist repeated its file read' });
      return;
    }
    streamedToolCall(response, 'native-specialist-read-once', 'workspace_read_file', JSON.stringify({ path: 'fixture.txt' }));
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
let liveModelContextLength;

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
  const configuredDebugPort = Number(process.env.XAA_NATIVE_DEBUG_PORT);
  const debugPort = Number.isInteger(configuredDebugPort) && configuredDebugPort > 0 && configuredDebugPort < 65536
    ? configuredDebugPort
    : await availablePort();
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
        if (proc.exitCode !== null) throw new Error(`${label} desktop exited during startup: ${lastError}; stdout=${stdout}; stderr=${stderr}`);
        await new Promise(resolve => setTimeout(resolve, 200));
      }
    }
    if (!browser) throw new Error(`${label} desktop WebView did not become available: ${lastError}; stdout=${stdout}; stderr=${stderr}`);
    const pageDeadline = Date.now() + 20000;
    let page;
    while (Date.now() < pageDeadline) {
      page = browser.contexts().flatMap(context => context.pages())[0];
      if (page) break;
      if (proc.exitCode !== null) throw new Error(`${label} desktop exited before its WebView page appeared; stdout=${stdout}; stderr=${stderr}`);
      await new Promise(resolve => setTimeout(resolve, 100));
    }
    if (!page) throw new Error(`${label} desktop did not expose a WebView page; stdout=${stdout}; stderr=${stderr}`);
    await expect(page.getByRole('navigation', { name: 'Main sections' })).toBeVisible({ timeout: 20000 });
    const connection = await page.evaluate(() => window.__TAURI_INTERNALS__.invoke('engine_connection'));
    launchCredentials.push(connection);
    const headers = { Authorization: `Bearer ${connection.token}` };
    // On a clean Windows profile the frozen engine can use the desktop's
    // complete 60-second startup retry window while extracting its payload.
    const apiDeadline = Date.now() + 75000;
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
const launchCredentials = [];

function cleanTestLaunchCredentials() {
  const latest = launchCredentials.at(-1);
  if (!latest) return true;
  const cleanup = [
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
    const result = execFileSync('uv', ['run', 'python', '-c', cleanup, latest.token, latest.base_url], {
      windowsHide: true, encoding: 'utf8', timeout: 30000, env: process.env,
    }).trim();
    return result === 'True';
  } catch {
    return false;
  }
}

async function runNativeOnboarding() {
  const page = first.page;
  const headers = first.headers;
  const runsBefore = await (await fetch(`${first.connection.base_url}/runs`, { headers })).json();
  if (runsBefore.items?.length !== 0) throw new Error('The isolated onboarding profile was not clean before first launch.');
  const initial = await (await fetch(`${first.connection.base_url}/providers/lm-studio/config`, { headers })).json();
  if (initial.endpoint !== 'http://127.0.0.1:1234' || initial.selected_model !== null) {
    throw new Error(`Fresh onboarding profile did not use the default endpoint and no model: ${JSON.stringify(initial)}`);
  }

  await page.getByRole('link', { name: 'Model setup' }).click();
  await page.getByLabel('LM Studio server address').fill(providerEndpoint);
  await page.getByRole('button', { name: 'Save address and connect', exact: true }).click();
  await expect(page.getByRole('status').filter({ hasText: 'Connected. Found 1 model.' })).toBeVisible({ timeout: 20000 });
  const discovered = await (await fetch(`${first.connection.base_url}/models`, { headers })).json();
  if (discovered.length !== 1 || discovered[0].id !== fakeModel) throw new Error(`Native onboarding discovered an unexpected model inventory: ${JSON.stringify(discovered)}`);
  await page.getByLabel('Local model or compatible provider model').selectOption(fakeModel);
  await expect.poll(async () => (await (await fetch(`${first.connection.base_url}/providers/lm-studio/config`, { headers })).json()).selected_model, { timeout: 10000 }).toBe(fakeModel);
  await page.getByRole('button', { name: 'Run harmless tool-use diagnostic', exact: true }).click();
  await expect(page.getByText('The selected model completed a validated harmless tool round trip.', { exact: true })).toBeVisible({ timeout: 20000 });
  if (fakeState.diagnostics !== 1 || fakeState.accepts !== 2) throw new Error(`The harmless model diagnostic did not complete exactly one tool round trip: ${JSON.stringify(fakeState)}`);

  await page.getByRole('link', { name: 'New objective' }).click();
  await page.getByLabel('Workspace path').fill(fixtureWorkspace);
  let demoRequest;
  page.on('request', request => {
    if (request.url() === `${first.connection.base_url}/runs` && request.method() === 'POST') demoRequest = request.postDataJSON();
  });
  await page.getByRole('button', { name: 'Start read-only onboarding demonstration', exact: true }).click();
  const runDeadline = Date.now() + 60000;
  let savedRun;
  while (Date.now() < runDeadline) {
    const history = await (await fetch(`${first.connection.base_url}/runs?limit=10`, { headers })).json();
    savedRun = history.items?.find(item => item.workspace === fixtureWorkspace && item.objective?.includes('Do not modify files or run commands'));
    if (savedRun && ['verifying', 'completed', 'failed', 'blocked'].includes(savedRun.state)) break;
    await new Promise(resolve => setTimeout(resolve, 150));
  }
  if (!savedRun || savedRun.state !== 'verifying') {
    const alerts = await page.getByRole('alert').allTextContents();
    throw new Error(`The native read-only demonstration did not reach final review: ${JSON.stringify({ state: savedRun?.state, alerts, provider: fakeState })}`);
  }
  if (demoRequest?.mode !== 'observe' || demoRequest?.command_grant !== null || demoRequest?.workspace !== fixtureWorkspace
    || demoRequest?.objective !== savedRun.objective) throw new Error(`The demonstration did not preserve its read-only scope: ${JSON.stringify(demoRequest)}`);

  const events = await (await fetch(`${first.connection.base_url}/runs/${encodeURIComponent(savedRun.id)}/events`, { headers })).json();
  const intents = events.filter(event => event.event_type === 'tool.intended');
  const readIntent = intents.find(event => event.payload.tool === 'workspace.read_file');
  const readResult = readIntent && events.find(event => event.event_type === 'tool.completed' && event.payload.call_id === readIntent.payload.call_id);
  const answers = events.filter(event => event.event_type === 'model.message').map(event => event.payload.message?.content).filter(value => typeof value === 'string');
  const originalReadme = `# ${onboardingProjectIdentity}\n\n${onboardingMarker}\n\nA desktop ledger for community gardens.\n\n## Setup\nRun the packaged desktop application and open the selected workspace.\n`;
  const unchanged = await readFile(path.join(fixtureWorkspace, 'README.md'), 'utf8') === originalReadme;
  if (!readIntent || !readResult
    || !answers.some(answer => answer.includes(onboardingProjectIdentity) && answer.includes(onboardingMarker))
    || intents.length !== 1 || fakeState.onboardingReadProposals !== 1 || fakeState.onboardingResultFollowups !== 1 || !unchanged) {
    throw new Error(`The native onboarding result did not prove one exact read-only workspace task: ${JSON.stringify({ intents: intents.map(event => event.payload.tool), answerCount: answers.length, provider: fakeState, unchanged })}`);
  }

  await page.screenshot({ path: path.join(root, 'before-final-review.png'), fullPage: true });
  await page.getByLabel('I reviewed the objective, result, and available evidence').check();
  await page.getByLabel('Final review rationale').fill('Verified the README project identity, setup instructions, exact file evidence, and unchanged workspace.');
  await page.getByRole('button', { name: 'Accept and complete run', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Active run completed' })).toBeVisible({ timeout: 20000 });
  const completed = await (await fetch(`${first.connection.base_url}/runs/${encodeURIComponent(savedRun.id)}`, { headers })).json();
  if (completed.state !== 'completed') throw new Error(`The owner-reviewed native onboarding run did not complete: ${completed.state}`);
  await page.screenshot({ path: path.join(root, 'after-final-review.png'), fullPage: true });

  const result = {
    passed: true,
    scenario: 'fresh Windows Tauri profile connects through the desktop UI, completes the harmless diagnostic, reads a real workspace file with owner review, and reaches completed',
    provider: 'deterministic local OpenAI-compatible fixture; no live LM Studio or external provider inference',
    live_model_inference: false,
    initial_profile_empty: true,
    default_endpoint: initial.endpoint,
    configured_endpoint: providerEndpoint,
    discovered_model: fakeModel,
    harmless_tool_round_trip: true,
    onboarding_read_tool: readIntent.payload.tool,
    exact_read_evidence: true,
    observe_mode: demoRequest.mode,
    command_grant: demoRequest.command_grant,
    owner_final_review: true,
    final_run_state: completed.state,
    workspace_unchanged: unchanged,
    desktop_sha256: createHash('sha256').update(await readFile(desktopExecutable)).digest('hex'),
    engine_sha256: createHash('sha256').update(await readFile(path.join(path.dirname(desktopExecutable), 'xaa-engine.exe'))).digest('hex'),
    launch_credentials_cleaned: false,
  };
  await writeFile(path.join(root, 'report.json'), JSON.stringify(result, null, 2));
  await mkdir('build/installer', { recursive: true });
  await writeFile(path.join('build/installer', acceptanceName), JSON.stringify(result, null, 2));
  return result;
}

try {
  if (onboardingMode) {
    first = await startDesktop('Onboarding');
    report = await runNativeOnboarding();
  } else {
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
  if (liveSpecialistMode) {
    const liveCatalog = await (await fetch(`${liveProviderEndpoint}/api/v1/models`)).json();
    const liveModel = liveCatalog.models?.find(model => model.key === fakeModel);
    const liveInstance = liveModel?.loaded_instances?.find(instance => instance.id === fakeModel);
    liveModelContextLength = liveInstance?.config?.context_length;
    if (!Number.isInteger(liveModelContextLength) || liveModelContextLength < 8192) {
      throw new Error(`Live specialist recovery requires ${fakeModel} loaded with at least 8192 context tokens; observed ${liveModelContextLength ?? 'no loaded instance'}.`);
    }
  }
  if (specialistMode) {
    const createResponse = await fetch(`${first.connection.base_url}/runs`, {
      method: 'POST', headers: { ...first.headers, 'Content-Type': 'application/json' },
      body: JSON.stringify({
        objective: 'Run one named researcher, recover it after a desktop process death, and preserve its exact file evidence.',
        acceptance_criteria: ['The recovered researcher candidate quotes the exact saved file read.'],
        workspace: fixtureWorkspace,
        mode: 'workspace', max_iterations: 8, max_seconds: 240,
      }),
    });
    if (createResponse.status !== 201) throw new Error(`Could not create the specialist recovery run: ${await createResponse.text()}`);
    const run = await createResponse.json();
    if (!run.id) throw new Error('The native engine did not return a durable specialist run ID.');
    execFileSync('uv', ['run', 'python', 'scripts/seed_native_specialist_restart.py', database, run.id,
      fixtureWorkspace, fakeModel, specialistIdentity, specialistInstructions, 'fixture.txt'], {
      windowsHide: true, stdio: 'pipe', timeout: 30000, env: process.env,
    });
    const seededPlan = await (await fetch(`${first.connection.base_url}/runs/${encodeURIComponent(run.id)}/plan`, { headers: first.headers })).json();
    const seededTask = seededPlan.tasks.find(task => task.local_id === 'reader');
    if (!seededTask || seededTask.identity_name !== specialistIdentity || seededTask.model_route !== fakeModel) {
      throw new Error(`The saved specialist did not snapshot its configured identity and model route: ${JSON.stringify(seededTask)}`);
    }
    const dispatch = await fetch(`${first.connection.base_url}/runs/${encodeURIComponent(run.id)}/plan/dispatch`, {
      method: 'POST', headers: first.headers,
    });
    if (dispatch.status !== 202) throw new Error(`Could not dispatch the saved specialist: ${await dispatch.text()}`);
    const blockedDeadline = Date.now() + (liveSpecialistMode ? 180000 : 30000);
    while (!(liveSpecialistMode ? fakeState.liveResultBlocked : fakeState.specialistResultBlocked) && Date.now() < blockedDeadline) await new Promise(resolve => setTimeout(resolve, 50));
    if (liveSpecialistMode) {
      if (!fakeState.liveResultBlocked || fakeState.liveResultFollowups !== 1 || fakeState.rolePromptChecks !== 2 || fakeState.liveToolProposals < 1) {
        throw new Error(`The live model did not read once and reach the blocked continuation: ${JSON.stringify({ rolePromptChecks: fakeState.rolePromptChecks, liveToolProposals: fakeState.liveToolProposals, liveResultFollowups: fakeState.liveResultFollowups, liveResultBlocked: fakeState.liveResultBlocked })}`);
      }
    } else if (!fakeState.specialistResultBlocked || fakeState.rolePromptChecks !== 2) {
      throw new Error(`The native specialist missed its saved-result boundary: ${JSON.stringify({ rolePromptChecks: fakeState.rolePromptChecks, readProposals: fakeState.specialistReadProposals, resultRequests: fakeState.specialistResultRequests, resultBlocked: fakeState.specialistResultBlocked })}`);
    }
    const firstEvents = await (await fetch(`${first.connection.base_url}/runs/${encodeURIComponent(run.id)}/events`, { headers: first.headers })).json();
    const firstReadResults = firstEvents.filter(event => event.event_type === 'task.tool.result' && event.payload.task_id === 'reader');
    if (firstReadResults.length !== 1 || !JSON.stringify(firstReadResults[0].payload).includes(specialistNonce)) throw new Error('The specialist did not durably save exactly one exact fixture read before process death.');
    const identityChange = await fetch(`${first.connection.base_url}/specialists/profiles/researcher/identity`, {
      method: 'PUT', headers: { ...first.headers, 'Content-Type': 'application/json' },
      body: JSON.stringify({ display_name: `Changed ${randomUUID().slice(0, 8)}` }),
    });
    if (!identityChange.ok) throw new Error(`Could not edit the researcher profile after saving its task: ${await identityChange.text()}`);
    const instructionChange = await fetch(`${first.connection.base_url}/specialists/profiles/researcher/instructions`, {
      method: 'PUT', headers: { ...first.headers, 'Content-Type': 'application/json' },
      body: JSON.stringify({ system_instructions: 'This updated profile applies only to future task plans.' }),
    });
    if (!instructionChange.ok) throw new Error(`Could not edit the researcher instructions after saving its task: ${await instructionChange.text()}`);
    await first.page.screenshot({ path: path.join(root, 'before-restart.png'), fullPage: true });
    await first.browser.close();
    first.browser = undefined;
    execFileSync('taskkill', ['/PID', String(first.proc.pid), '/T', '/F'], { windowsHide: true, stdio: 'ignore' });
    await new Promise((resolve, reject) => {
      const deadline = Date.now() + 15000;
      const poll = () => first.proc.exitCode !== null ? resolve() : Date.now() >= deadline ? reject(new Error('The killed specialist desktop process did not exit.')) : setTimeout(poll, 100);
      poll();
    });
    let oldApiStopped = false;
    try { await fetch(`${first.connection.base_url}/runs/${encodeURIComponent(run.id)}`, { headers: first.headers, signal: AbortSignal.timeout(500) }); }
    catch { oldApiStopped = true; }
    if (!oldApiStopped) throw new Error('The original bundled API remained reachable after specialist desktop process death.');
    execFileSync('uv', ['run', 'python', '-c',
      "import sqlite3,sys; c=sqlite3.connect(sys.argv[1]); c.execute(\"UPDATE runs SET lease_expires_at='2000-01-01T00:00:00+00:00' WHERE id=?\",(sys.argv[2],)); c.execute(\"UPDATE task_leases SET expires_at='2000-01-01T00:00:00+00:00' WHERE task_id=?\",(sys.argv[2]+':reader',)); c.commit(); c.close()",
      database, run.id], { windowsHide: true, stdio: 'pipe', timeout: 30000, env: process.env });
    second = await startDesktop('Specialist restart');
    const recoveryDeadline = Date.now() + 30000;
    let recovery;
    while (Date.now() < recoveryDeadline) {
      const response = await fetch(`${second.connection.base_url}/runs/${encodeURIComponent(run.id)}/tasks/reader/recovery`, { headers: second.headers });
      if (response.ok) { recovery = await response.json(); break; }
      if (![409, 500].includes(response.status)) throw new Error(`Specialist recovery lookup failed: ${await response.text()}`);
      await new Promise(resolve => setTimeout(resolve, 150));
    }
    if (!recovery || recovery.safe_to_resume !== true || recovery.saved_tool_results !== 1) throw new Error(`The restarted desktop did not expose safe recovery from one saved read: ${JSON.stringify(recovery)}`);
    const recoveredPlan = await (await fetch(`${second.connection.base_url}/runs/${encodeURIComponent(run.id)}/plan`, { headers: second.headers })).json();
    const recoveredTask = recoveredPlan.tasks.find(task => task.local_id === 'reader');
    if (!recoveredTask || recoveredTask.identity_name !== specialistIdentity || recoveredTask.model_route !== fakeModel) {
      throw new Error(`The restarted desktop changed the saved task identity or route after profile edits: ${JSON.stringify(recoveredTask)}`);
    }
    if (liveSpecialistMode) fakeState.liveResumeAllowed = true;
    else fakeState.allowSpecialistResponse = true;
    const resume = await fetch(`${second.connection.base_url}/runs/${encodeURIComponent(run.id)}/tasks/reader/recovery`, {
      method: 'POST', headers: { ...second.headers, 'Content-Type': 'application/json' },
      body: JSON.stringify({ decision: 'resume' }),
    });
    if (resume.status !== 202) throw new Error(`Could not resume the saved specialist journal: ${await resume.text()}`);
    const candidateDeadline = Date.now() + (liveSpecialistMode ? 180000 : 45000);
    let candidate;
    let recoveredRun;
    while (Date.now() < candidateDeadline) {
      const response = await fetch(`${second.connection.base_url}/runs/${encodeURIComponent(run.id)}/tasks/reader/candidate`, { headers: second.headers });
      if (response.ok) candidate = await response.json();
      const runResponse = await fetch(`${second.connection.base_url}/runs/${encodeURIComponent(run.id)}`, { headers: second.headers });
      if (runResponse.ok) recoveredRun = await runResponse.json();
      if (candidate?.candidate && recoveredRun?.state === 'verifying') break;
      await new Promise(resolve => setTimeout(resolve, 150));
    }
    if (!candidate?.candidate || !String(candidate.candidate).includes(specialistNonce) || recoveredRun?.state !== 'verifying') {
      throw new Error('The restarted native specialist did not produce one reviewable candidate using its saved file result.');
    }
    const events = await (await fetch(`${second.connection.base_url}/runs/${encodeURIComponent(run.id)}/events`, { headers: second.headers })).json();
    const readResults = events.filter(event => event.event_type === 'task.tool.result' && event.payload.task_id === 'reader');
    const recoveryDecisions = events.filter(event => event.event_type === 'task.recovery_decided' && event.payload.task_id === 'reader');
    if (recoveryDecisions.length !== 1 || recoveryDecisions[0].payload.decision !== 'resume' || (liveSpecialistMode
      ? readResults.length < 1 || fakeState.rolePromptChecks < 3 || fakeState.liveResultFollowups < 2 || fakeState.liveSavedResultResumePrompts < 1 || fakeState.liveToolProposals < 1
      : readResults.length !== 1 || fakeState.rolePromptChecks !== 3 || fakeState.specialistReadProposals !== 1 || fakeState.specialistResultRequests !== 2)) {
      throw new Error(`Specialist recovery lost its saved result or explicit decision: ${JSON.stringify({ readResults: readResults.length, rolePromptChecks: fakeState.rolePromptChecks, resultFollowups: fakeState.liveResultFollowups, savedResultResumePrompts: fakeState.liveSavedResultResumePrompts, recoveryDecisions: recoveryDecisions.length })}`);
    }
    const fixtureAfter = await readFile(path.join(fixtureWorkspace, 'fixture.txt'), 'utf8');
    if (fixtureAfter !== specialistNonce) throw new Error('The read-only specialist recovery fixture changed.');
    await second.page.screenshot({ path: path.join(root, 'after-restart.png'), fullPage: true });
    report = {
      passed: true,
      scenario: 'Windows Tauri desktop and bundled engine process death during named specialist inference, same-profile restart, explicit task recovery and candidate review',
      provider: liveSpecialistMode ? `LM Studio local model ${fakeModel} via a local interruption proxy` : 'deterministic local OpenAI-compatible fixture; no LM Studio or external provider',
      ...(liveSpecialistMode ? { model_context_length: liveModelContextLength } : {}),
      run_id: run.id,
      task_id: 'reader',
      specialist_identity: specialistIdentity,
      specialist_route: fakeModel,
      specialist_instructions_snapshot_verified: true,
      profile_mutation_did_not_change_saved_task: true,
      task_read_results: readResults.length,
      task_read_results_before_restart: firstReadResults.length,
      ...(liveSpecialistMode ? { resumed_reads: readResults.length - firstReadResults.length, resumed_prompt_contains_saved_result: fakeState.liveSavedResultResumePrompts > 0 } : {}),
      task_read_proposals: liveSpecialistMode ? fakeState.liveToolProposals : fakeState.specialistReadProposals,
      task_result_followups: liveSpecialistMode ? fakeState.liveResultFollowups : fakeState.specialistResultRequests,
      role_prompt_checks: fakeState.rolePromptChecks,
      safe_recovery_summary: recovery,
      explicit_recovery_decision: recoveryDecisions[0].payload.decision,
      candidate_contains_exact_fixture_nonce: true,
      final_run_state: recoveredRun.state,
      workspace_unchanged: true,
      desktop_sha256: createHash('sha256').update(await readFile(desktopExecutable)).digest('hex'),
      engine_sha256: createHash('sha256').update(await readFile(path.join(path.dirname(desktopExecutable), 'xaa-engine.exe'))).digest('hex'),
      launch_credentials_cleaned: false,
    };
    await writeFile(path.join(root, 'report.json'), JSON.stringify(report, null, 2));
    await mkdir('build/installer', { recursive: true });
    await writeFile(path.join('build/installer', acceptanceName), JSON.stringify(report, null, 2));
  } else {
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

  const seedOutputEvent = [
    'import base64,sys',
    'from pathlib import Path',
    'from xaa.storage.runs import RunStore',
    'store=RunStore(Path(sys.argv[1]))',
    'payload={"call_id":"native-restart-fixture","execution_id":"' + 'a'.repeat(32) + '","stream":"stdout","trust":"untrusted_command_output","chunk_b64":base64.b64encode((sys.argv[3]+"\\n").encode()).decode()}',
    'with store._connection() as connection:',
    '    connection.execute("BEGIN IMMEDIATE")',
    '    RunStore.record_event(connection,sys.argv[2],"execution.output.delta",payload)',
    '    connection.execute("UPDATE runs SET lease_expires_at=\'2000-01-01T00:00:00+00:00\' WHERE id=?",(sys.argv[2],))',
  ].join('\n');
  execFileSync('uv', ['run', 'python', '-c', seedOutputEvent, database, run.id, restoredCommandOutput], {
    windowsHide: true, stdio: 'pipe', timeout: 30000, env: process.env,
  });
  fakeState.allowRunResponse = true;
  second = await startDesktop('Restarted');
  const activeRunLink = second.page.getByRole('link', { name: /Active run/ });
  await expect(activeRunLink).toBeVisible({ timeout: 15000 });
  await activeRunLink.click();
  const restoredOutput = second.page.getByRole('region', { name: 'Streaming command output' });
  await expect(restoredOutput).toContainText(restoredCommandOutput, { timeout: 15000 });
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

  const chatSessionId = `native-chat-${randomUUID()}`;
  const chatBase = `${second.connection.base_url}/chat/sessions/${encodeURIComponent(chatSessionId)}`;
  const openedChat = await fetch(chatBase, {
    method: 'POST', headers: { ...second.headers, 'Content-Type': 'application/json' },
    body: JSON.stringify({ model: fakeModel, workspace: fixtureWorkspace, mode: 'observe' }),
  });
  if (!openedChat.ok) throw new Error(`Could not seed the shared conversation: ${await openedChat.text()}`);
  const seededTurn = await fetch(`${chatBase}/turn`, {
    method: 'POST', headers: { ...second.headers, 'Content-Type': 'application/json' },
    body: JSON.stringify({
      run_id: run.id,
      model: fakeModel,
      workspace: fixtureWorkspace,
      mode: 'observe',
      user_message: 'Earlier question',
      assistant_message: 'Earlier reply',
    }),
  });
  if (!seededTurn.ok) throw new Error(`Could not seed the shared conversation turn: ${await seededTurn.text()}`);
  await second.page.locator('a[href="#model-setup"]').click();
  await second.page.getByLabel('Local model or compatible provider model').selectOption(fakeModel);
  await second.page.getByRole('link', { name: /New objective/ }).click();
  await second.page.getByLabel('Workspace path').fill(fixtureWorkspace);
  await second.page.locator('details.saved-conversations > summary').click();
  const conversations = second.page.getByRole('region', { name: 'Shared conversation sessions' });
  await conversations.getByRole('button', { name: 'Load shared conversations' }).click();
  await conversations.getByLabel('Saved terminal and desktop sessions').selectOption(chatSessionId);
  await expect(second.page.getByRole('list', { name: 'Conversation messages' }).getByText('Earlier reply', { exact: true })).toBeVisible();
  await second.page.getByLabel('Objective', { exact: true }).fill('Continue it');
  await second.page.getByRole('button', { name: 'Send message', exact: true }).click();
  await second.page.screenshot({ path: path.join(root, 'after-chat-submit.png'), fullPage: true });
  await expect.poll(() => fakeState.chatContextVerified).toBe(true);
  const chatContinuationDeadline = Date.now() + 30000;
  let continuedChat;
  while (Date.now() < chatContinuationDeadline) {
    const response = await fetch(chatBase, { headers: second.headers });
    if (response.ok) {
      continuedChat = await response.json();
      if (continuedChat.messages.length === 4) break;
    }
    await new Promise(resolve => setTimeout(resolve, 100));
  }
  if (!continuedChat || continuedChat.messages.length !== 4
    || continuedChat.messages[2].role !== 'user'
    || continuedChat.messages[2].content !== 'Continue it'
    || continuedChat.messages[3].role !== 'assistant'
    || continuedChat.messages[3].content !== 'Native desktop continued the saved conversation.'
    || continuedChat.messages[3].run_id === run.id) {
    const visibleAlerts = await second.page.getByRole('alert').allTextContents();
    throw new Error(`The desktop continuation was not durably saved against its own coordinator run: ${JSON.stringify({ messages: continuedChat?.messages, visibleAlerts })}`);
  }
  await expect(second.page.getByRole('region', { name: 'Active run' }).getByText('Native desktop continued the saved conversation.', { exact: true })).toBeVisible();
  const chatRun = await (await fetch(`${second.connection.base_url}/runs/${encodeURIComponent(continuedChat.messages[3].run_id)}`, { headers: second.headers })).json();
  if (chatRun.state !== 'verifying' || chatRun.model !== fakeModel || chatRun.workspace !== fixtureWorkspace || chatRun.mode !== 'observe') {
    throw new Error(`The native continuation did not use its bound, durable coordinator run: ${JSON.stringify(chatRun)}`);
  }
  const chatRunEvents = await (await fetch(`${second.connection.base_url}/runs/${encodeURIComponent(chatRun.id)}/events`, { headers: second.headers })).json();
  if (!chatRunEvents.some(event => event.event_type === 'model.message' && JSON.stringify(event.payload).includes('Native desktop continued the saved conversation.'))
    || chatRunEvents.some(event => ['tool.intended', 'tool.executing', 'tool.completed'].includes(event.event_type))) {
    throw new Error('The saved desktop conversation did not produce one reviewable, effect-free coordinator answer.');
  }
  const attachmentSessionId = `native-attachment-${randomUUID()}`;
  const attachmentBytes = Buffer.from('%PDF-1.4\n% Native saved-chat attachment fixture\n', 'ascii');
  const seedAttachment = [
    'import base64,json,sys',
    'from pathlib import Path',
    'from xaa.storage.runs import RunStore',
    'from xaa.storage.cli_chat import CLIChatStore',
    'store=RunStore(Path(sys.argv[1]))',
    'run=store.create_run(objective="Retain a saved PDF attachment",acceptance_criteria=[],workspace=sys.argv[4],mode="observe",model=sys.argv[3],document_bytes=base64.b64decode(sys.argv[5]),document_media_type="application/pdf",document_text="NATIVE-SAVED-PDF-MARKER",document_filename="native-review.pdf")',
    'chat=CLIChatStore(store)',
    'chat.ensure_session(sys.argv[2],model=sys.argv[3],workspace=sys.argv[4],mode="observe")',
    'saved=chat.append_turn(sys.argv[2],run_id=run["id"],model=sys.argv[3],workspace=sys.argv[4],mode="observe",user_message="Review this saved PDF.",assistant_message="The PDF is retained with this conversation.")',
    'print(json.dumps({"run_id":run["id"],"attachment":saved["messages"][0]["attachments"][0]}))',
  ].join('\n');
  const attachmentSeed = JSON.parse(execFileSync('uv', [
    'run', 'python', '-c', seedAttachment, database, attachmentSessionId, fakeModel,
    fixtureWorkspace, attachmentBytes.toString('base64'),
  ], { windowsHide: true, stdio: 'pipe', timeout: 30000, env: process.env }).toString('utf8'));
  await second.page.getByRole('link', { name: /New objective/ }).click();
  await second.page.locator('details.saved-conversations').evaluateAll(elements => elements.forEach(element => { element.open = true; }));
  await second.page.locator('details.saved-conversations:visible #new-shared-chat-name').fill(attachmentSessionId);
  await second.page.getByRole('button', { name: 'Start or open shared conversation' }).click();
  const attachmentDownloadButton = second.page.getByRole('button', { name: 'Download PDF document' });
  await expect(attachmentDownloadButton).toBeVisible();
  const downloadEvent = second.page.waitForEvent('download', { timeout: 15000 });
  await attachmentDownloadButton.click();
  const attachmentDownload = await downloadEvent;
  if (attachmentDownload.suggestedFilename() !== 'native-review.pdf') {
    throw new Error(`Unexpected native saved-PDF filename: ${attachmentDownload.suggestedFilename()}`);
  }
  const downloadedAttachment = await readFile(await attachmentDownload.path());
  if (!downloadedAttachment.equals(attachmentBytes)) {
    throw new Error('The native saved-conversation PDF download did not preserve the artifact bytes.');
  }
  const csvSessionId = `native-csv-${randomUUID()}`;
  const csvBytes = Buffer.from('item,quantity\r\napples,2\r\n', 'utf8');
  const seedCsv = seedAttachment.replaceAll(
    'Retain a saved PDF attachment', 'Retain a saved CSV attachment',
  ).replaceAll('native-review.pdf', 'inventory.csv').replaceAll('application/pdf', 'text/csv').replaceAll(
    'NATIVE-SAVED-PDF-MARKER', 'NATIVE-SAVED-CSV-MARKER',
  ).replaceAll('Review this saved PDF.', 'Review this saved CSV.').replaceAll(
    'The PDF is retained with this conversation.', 'The CSV is retained with this conversation.',
  );
  const csvSeed = JSON.parse(execFileSync('uv', [
    'run', 'python', '-c', seedCsv, database, csvSessionId, fakeModel,
    fixtureWorkspace, csvBytes.toString('base64'),
  ], { windowsHide: true, stdio: 'pipe', timeout: 30000, env: process.env }).toString('utf8'));
  await second.page.locator('details.saved-conversations:visible #new-shared-chat-name').fill(csvSessionId);
  await second.page.getByRole('button', { name: 'Start or open shared conversation' }).click();
  const csvDownloadButton = second.page.getByRole('button', { name: 'Download CSV file' });
  await expect(csvDownloadButton).toBeVisible();
  const csvDownloadEvent = second.page.waitForEvent('download', { timeout: 15000 });
  await csvDownloadButton.click();
  const csvDownload = await csvDownloadEvent;
  if (csvDownload.suggestedFilename() !== 'inventory.csv') {
    throw new Error(`Unexpected native saved-CSV filename: ${csvDownload.suggestedFilename()}`);
  }
  const downloadedCsv = await readFile(await csvDownload.path());
  if (!downloadedCsv.equals(csvBytes)) {
    throw new Error('The native saved-conversation CSV download did not preserve the artifact bytes.');
  }
  const audioSessionId = `native-audio-${randomUUID()}`;
  const audioPayload = Buffer.from([0x34, 0x12, 0xcc, 0xed]);
  const audioBytes = Buffer.alloc(44 + audioPayload.length);
  audioBytes.write('RIFF', 0, 'ascii');
  audioBytes.writeUInt32LE(36 + audioPayload.length, 4);
  audioBytes.write('WAVEfmt ', 8, 'ascii');
  audioBytes.writeUInt32LE(16, 16);
  audioBytes.writeUInt16LE(1, 20);
  audioBytes.writeUInt16LE(1, 22);
  audioBytes.writeUInt32LE(8000, 24);
  audioBytes.writeUInt32LE(16000, 28);
  audioBytes.writeUInt16LE(2, 32);
  audioBytes.writeUInt16LE(16, 34);
  audioBytes.write('data', 36, 'ascii');
  audioBytes.writeUInt32LE(audioPayload.length, 40);
  audioPayload.copy(audioBytes, 44);
  const seedAudio = seedAttachment.replaceAll(
    'Retain a saved PDF attachment', 'Retain a saved WAV attachment',
  ).replaceAll('native-review.pdf', 'native-note.wav').replaceAll('application/pdf', 'audio/wav').replaceAll(
    'NATIVE-SAVED-PDF-MARKER', 'NATIVE-SAVED-WAV-MARKER',
  ).replaceAll('Review this saved PDF.', 'Review this saved WAV.').replaceAll(
    'The PDF is retained with this conversation.', 'The WAV is retained with this conversation.',
  );
  const audioSeed = JSON.parse(execFileSync('uv', [
    'run', 'python', '-c', seedAudio, database, audioSessionId, fakeModel,
    fixtureWorkspace, audioBytes.toString('base64'),
  ], { windowsHide: true, stdio: 'pipe', timeout: 30000, env: process.env }).toString('utf8'));
  await second.page.locator('details.saved-conversations:visible #new-shared-chat-name').fill(audioSessionId);
  await second.page.getByRole('button', { name: 'Start or open shared conversation' }).click();
  const audioDownloadButton = second.page.getByRole('button', { name: 'Download WAV audio' });
  await expect(audioDownloadButton).toBeVisible();
  const audioDownloadEvent = second.page.waitForEvent('download', { timeout: 15000 });
  await audioDownloadButton.click();
  const audioDownload = await audioDownloadEvent;
  if (audioDownload.suggestedFilename() !== 'native-note.wav') {
    throw new Error(`Unexpected native saved-WAV filename: ${audioDownload.suggestedFilename()}`);
  }
  const downloadedAudio = await readFile(await audioDownload.path());
  if (!downloadedAudio.equals(audioBytes)) {
    throw new Error('The native saved-conversation WAV download did not preserve the artifact bytes.');
  }
  const documentAttachmentFixtures = [
    { mediaType: 'text/plain', filename: 'notes.txt', label: 'Text document', bytes: Buffer.from('Native saved text attachment.\n', 'utf8') },
    { mediaType: 'text/markdown', filename: 'notes.md', label: 'Markdown document', bytes: Buffer.from('# Native notes\n\nSaved Markdown attachment.\n', 'utf8') },
    { mediaType: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document', filename: 'brief.docx', label: 'Word document', bytes: Buffer.from('PK\u0003\u0004native-docx-download-fixture', 'binary') },
    { mediaType: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', filename: 'budget.xlsx', label: 'Excel workbook', bytes: Buffer.from('PK\u0003\u0004native-xlsx-download-fixture', 'binary') },
  ];
  const nativeChatDocumentAttachments = [];
  for (const [index, fixture] of documentAttachmentFixtures.entries()) {
    const sessionId = `native-document-${index}-${randomUUID()}`;
    const fixtureMarker = `NATIVE-SAVED-DOCUMENT-${index}-MARKER`;
    const seedDocument = seedAttachment.replaceAll(
      'Retain a saved PDF attachment', `Retain a saved ${fixture.label} attachment`,
    ).replaceAll('native-review.pdf', fixture.filename).replaceAll('application/pdf', fixture.mediaType).replaceAll(
      'NATIVE-SAVED-PDF-MARKER', fixtureMarker,
    ).replaceAll('Review this saved PDF.', `Review this saved ${fixture.label}.`).replaceAll(
      'The PDF is retained with this conversation.', `The ${fixture.label} is retained with this conversation.`,
    );
    const seeded = JSON.parse(execFileSync('uv', [
      'run', 'python', '-c', seedDocument, database, sessionId, fakeModel,
      fixtureWorkspace, fixture.bytes.toString('base64'),
    ], { windowsHide: true, stdio: 'pipe', timeout: 30000, env: process.env }).toString('utf8'));
    await second.page.locator('details.saved-conversations:visible #new-shared-chat-name').fill(sessionId);
    await second.page.getByRole('button', { name: 'Start or open shared conversation' }).click();
    const downloadButton = second.page.getByRole('button', { name: `Download ${fixture.label}` });
    await expect(downloadButton).toBeVisible();
    const downloadWait = second.page.waitForEvent('download', { timeout: 15000 });
    await downloadButton.click();
    const download = await downloadWait;
    if (download.suggestedFilename() !== fixture.filename) {
      throw new Error(`Unexpected native saved-${fixture.label} filename: ${download.suggestedFilename()}`);
    }
    if (!(await readFile(await download.path())).equals(fixture.bytes)) {
      throw new Error(`The native saved-conversation ${fixture.label} download did not preserve the artifact bytes.`);
    }
    nativeChatDocumentAttachments.push({
      saved_session_id: sessionId,
      run_id: seeded.run_id,
      artifact_id: seeded.attachment.artifact_id,
      media_type: fixture.mediaType,
      size_bytes: seeded.attachment.size_bytes,
      download_filename: download.suggestedFilename(),
      artifact_bytes_verified: true,
      live_model_inference: false,
    });
  }
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
    streamed_command_output_restored_after_restart: true,
    restored_command_output_marker: restoredCommandOutput,
    candidate_count: savedAnswers.length,
    final_state: recovered.state,
    tool_effects: 0,
    native_chat_continuation: {
      prior_turn_context_verified: fakeState.chatContextVerified,
      saved_user_and_assistant_turn: true,
      coordinator_run_state: chatRun.state,
      live_provider_inference: false,
      tool_effects: 0,
    },
    native_chat_attachment: {
      saved_session_id: attachmentSessionId,
      run_id: attachmentSeed.run_id,
      artifact_id: attachmentSeed.attachment.artifact_id,
      media_type: attachmentSeed.attachment.media_type,
      size_bytes: attachmentSeed.attachment.size_bytes,
      download_filename: attachmentDownload.suggestedFilename(),
      artifact_bytes_verified: true,
      live_model_inference: false,
    },
    native_chat_audio_attachment: {
      saved_session_id: audioSessionId,
      run_id: audioSeed.run_id,
      artifact_id: audioSeed.attachment.artifact_id,
      media_type: audioSeed.attachment.media_type,
      size_bytes: audioSeed.attachment.size_bytes,
      download_filename: audioDownload.suggestedFilename(),
      artifact_bytes_verified: true,
      live_model_inference: false,
    },
    native_chat_csv_attachment: {
      saved_session_id: csvSessionId,
      run_id: csvSeed.run_id,
      artifact_id: csvSeed.attachment.artifact_id,
      media_type: csvSeed.attachment.media_type,
      size_bytes: csvSeed.attachment.size_bytes,
      download_filename: csvDownload.suggestedFilename(),
      artifact_bytes_verified: true,
      live_model_inference: false,
    },
    native_chat_document_attachments: nativeChatDocumentAttachments,
    workspace_unchanged: true,
    launch_credentials_cleaned: false,
    desktop_sha256: createHash('sha256').update(await readFile(desktopExecutable)).digest('hex'),
    engine_sha256: createHash('sha256').update(await readFile(path.join(path.dirname(desktopExecutable), 'xaa-engine.exe'))).digest('hex'),
  };
  await writeFile(path.join(root, 'report.json'), JSON.stringify(report, null, 2));
  await mkdir('build/installer', { recursive: true });
  await writeFile(path.join('build/installer', acceptanceName), JSON.stringify(report, null, 2));
  }
  }
} catch (error) {
  const detail = { passed: false, error: String(error), root, provider_requests: fakeState.accepts, diagnostic_requests: fakeState.diagnostics, live_result_followups: fakeState.liveResultFollowups, live_result_blocked: fakeState.liveResultBlocked, role_prompt_checks: fakeState.rolePromptChecks };
  await writeFile(path.join(root, 'report.json'), JSON.stringify(detail, null, 2)).catch(() => {});
  throw error;
} finally {
  await first?.browser?.close().catch(() => {});
  await second?.browser?.close().catch(() => {});
  for (const item of [first, second]) {
    if (item?.proc.exitCode === null) execFileSync('taskkill', ['/PID', String(item.proc.pid), '/T', '/F'], { windowsHide: true, stdio: 'ignore' });
  }
  const credentialsCleaned = cleanTestLaunchCredentials();
  if (report && credentialsCleaned !== report.launch_credentials_cleaned) {
    report.launch_credentials_cleaned = credentialsCleaned;
    const serialized = JSON.stringify(report, null, 2);
    await writeFile(path.join(root, 'report.json'), serialized);
    await writeFile(path.join('build/installer', acceptanceName), serialized);
  }
  if (report) console.log(JSON.stringify({ ...report, evidence: root }, null, 2));
  if (!credentialsCleaned) throw new Error('The native restart acceptance could not clean its own API launch credential pair.');
  await new Promise(resolve => modelServer.close(resolve));
}
