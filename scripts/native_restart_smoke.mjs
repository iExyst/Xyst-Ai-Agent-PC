import { chromium, expect } from '@playwright/test';
import { createHash, randomUUID } from 'node:crypto';
import { createServer } from 'node:http';
import { mkdir, readFile, writeFile } from 'node:fs/promises';
import { spawn, execFileSync } from 'node:child_process';
import path from 'node:path';

if (process.platform !== 'win32') throw new Error('Native restart acceptance requires Windows.');

const desktopExecutable = path.resolve(process.argv[2] ?? 'build/native-dev/xaa-desktop.exe');
const specialistMode = process.argv.includes('--specialist');
const root = path.resolve('.acceptance', `${specialistMode ? 'native-specialist-restart' : 'native-restart'}-${randomUUID()}`);
const profile = path.join(root, 'profile');
const localAppData = path.join(profile, 'localappdata');
const appData = path.join(profile, 'appdata');
const logDirectory = path.join(localAppData, 'dev.xyst.xaa', 'logs');
const database = path.join(localAppData, 'XAA', 'xaa.sqlite3');
const fixtureWorkspace = path.join(root, 'workspace');
const engineLog = path.join(logDirectory, 'engine.log');
const fakeModel = 'xaa-native-restart-fixture';
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
  allowSpecialistResponse: false,
  rolePromptChecks: 0,
};
let diagnosticsNonce;
const specialistNonce = `native-specialist-evidence-${randomUUID()}`;
const specialistIdentity = `Evidence Reader ${randomUUID().slice(0, 8)}`;
const specialistInstructions = `Use workspace_read_file and quote this saved result exactly for ${specialistNonce}.`;

await Promise.all([root, localAppData, appData, logDirectory, fixtureWorkspace].map(directory => mkdir(directory, { recursive: true })));
await writeFile(path.join(fixtureWorkspace, 'fixture.txt'), specialistMode ? specialistNonce : 'native restart fixture remains unchanged\n');

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
    await expect(page.getByRole('heading', { name: 'Xyst AI Agent', exact: true })).toBeVisible({ timeout: 20000 });
    const connection = await page.evaluate(() => window.__TAURI_INTERNALS__.invoke('engine_connection'));
    launchCredentials.push(connection);
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
    const blockedDeadline = Date.now() + 30000;
    while (!fakeState.specialistResultBlocked && Date.now() < blockedDeadline) await new Promise(resolve => setTimeout(resolve, 50));
    if (!fakeState.specialistResultBlocked || fakeState.rolePromptChecks !== 1) throw new Error('The native specialist did not read the fixture and reach the saved-result boundary with its snapshotted role prompt.');
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
    fakeState.allowSpecialistResponse = true;
    const resume = await fetch(`${second.connection.base_url}/runs/${encodeURIComponent(run.id)}/tasks/reader/recovery`, {
      method: 'POST', headers: { ...second.headers, 'Content-Type': 'application/json' },
      body: JSON.stringify({ decision: 'resume' }),
    });
    if (resume.status !== 202) throw new Error(`Could not resume the saved specialist journal: ${await resume.text()}`);
    const candidateDeadline = Date.now() + 45000;
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
    if (readResults.length !== 1 || recoveryDecisions.length !== 1 || recoveryDecisions[0].payload.decision !== 'resume' || fakeState.specialistReadProposals !== 1 || fakeState.specialistResultRequests !== 2 || fakeState.rolePromptChecks !== 2) {
      throw new Error('Specialist recovery replayed the workspace read, lost its saved role prompt, or omitted the explicit recovery decision.');
    }
    const fixtureAfter = await readFile(path.join(fixtureWorkspace, 'fixture.txt'), 'utf8');
    if (fixtureAfter !== specialistNonce) throw new Error('The read-only specialist recovery fixture changed.');
    await second.page.screenshot({ path: path.join(root, 'after-restart.png'), fullPage: true });
    report = {
      passed: true,
      scenario: 'Windows Tauri desktop and bundled engine process death during named specialist inference, same-profile restart, explicit task recovery and candidate review',
      provider: 'deterministic local OpenAI-compatible fixture; no LM Studio or external provider',
      run_id: run.id,
      task_id: 'reader',
      specialist_identity: specialistIdentity,
      specialist_route: fakeModel,
      specialist_instructions_snapshot_verified: true,
      profile_mutation_did_not_change_saved_task: true,
      task_read_results: readResults.length,
      task_read_proposals: fakeState.specialistReadProposals,
      task_result_followups: fakeState.specialistResultRequests,
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
    await writeFile('build/installer/native-specialist-restart-acceptance.json', JSON.stringify(report, null, 2));
    console.log(JSON.stringify({ ...report, evidence: root }, null, 2));
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
    launch_credentials_cleaned: false,
    desktop_sha256: createHash('sha256').update(await readFile(desktopExecutable)).digest('hex'),
    engine_sha256: createHash('sha256').update(await readFile(path.join(path.dirname(desktopExecutable), 'xaa-engine.exe'))).digest('hex'),
  };
  await writeFile(path.join(root, 'report.json'), JSON.stringify(report, null, 2));
  await mkdir('build/installer', { recursive: true });
  await writeFile('build/installer/native-restart-acceptance.json', JSON.stringify(report, null, 2));
  console.log(JSON.stringify({ ...report, evidence: root }, null, 2));
  }
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
  const credentialsCleaned = cleanTestLaunchCredentials();
  if (report && credentialsCleaned !== report.launch_credentials_cleaned) {
    report.launch_credentials_cleaned = credentialsCleaned;
    const serialized = JSON.stringify(report, null, 2);
    await writeFile(path.join(root, 'report.json'), serialized);
    await writeFile(`build/installer/${specialistMode ? 'native-specialist-restart' : 'native-restart'}-acceptance.json`, serialized);
  }
  if (!credentialsCleaned) throw new Error('The native restart acceptance could not clean its own API launch credential pair.');
  await new Promise(resolve => modelServer.close(resolve));
}
