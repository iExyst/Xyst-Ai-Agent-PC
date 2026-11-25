import { chromium, expect } from '@playwright/test';
import { createHash } from 'node:crypto';
import { createServer } from 'node:net';
import { mkdir, readFile, writeFile } from 'node:fs/promises';
import { spawn, execFileSync } from 'node:child_process';
import path from 'node:path';

if (process.platform !== 'win32') throw new Error('Native SSH recovery acceptance requires Windows.');

const [desktopArgument, dataRootArgument, runId, expectedOutput] = process.argv.slice(2);
if (!desktopArgument || !dataRootArgument || !runId || !expectedOutput) {
  throw new Error('Usage: node scripts/native_ssh_recovery_smoke.mjs DESKTOP.exe DATA_ROOT RUN_ID EXPECTED_OUTPUT');
}
const desktop = path.resolve(desktopArgument);
const dataRoot = path.resolve(dataRootArgument);
const localAppData = path.join(dataRoot, 'native-profile', 'localappdata');
const appData = path.join(dataRoot, 'native-profile', 'appdata');
const logs = path.join(localAppData, 'dev.xyst.xaa', 'logs');
const reportPath = path.join(dataRoot, 'native-ssh-recovery-report.json');
await Promise.all([localAppData, appData, logs].map(directory => mkdir(directory, { recursive: true })));

async function availablePort() {
  const server = createServer();
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const address = server.address();
  if (!address || typeof address === 'string') throw new Error('Could not reserve a WebView debug port.');
  await new Promise((resolve, reject) => server.close(error => error ? reject(error) : resolve()));
  return address.port;
}

const appEnv = Object.fromEntries(Object.entries(process.env).filter(([key]) => !['PATH', 'PYTHONPATH', 'VIRTUAL_ENV'].includes(key.toUpperCase())));
appEnv.PATH = [
  path.join(process.env.SystemRoot, 'System32'),
  path.join(process.env.SystemRoot, 'System32', 'OpenSSH'),
].join(path.delimiter);
appEnv.APPDATA = appData;
appEnv.LOCALAPPDATA = localAppData;
appEnv.XAA_DATA_DIR = dataRoot;
appEnv.XAA_TEST_HIDDEN = '1';
appEnv.XAA_TEST_LOG_DIR = logs;
appEnv.WEBVIEW2_USER_DATA_FOLDER = path.join(dataRoot, 'native-profile', 'webview');

let processHandle;
let browser;
let connection;
let report;

function cleanLaunchCredentials() {
  if (!connection) return true;
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
    return execFileSync('uv', ['run', 'python', '-c', cleanup, connection.token, connection.base_url], {
      windowsHide: true, encoding: 'utf8', timeout: 30000,
    }).trim() === 'True';
  } catch {
    return false;
  }
}

try {
  const debugPort = await availablePort();
  processHandle = spawn(desktop, [], {
    windowsHide: true,
    stdio: ['ignore', 'pipe', 'pipe'],
    env: { ...appEnv, WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS: `--remote-debugging-port=${debugPort}` },
  });
  let stdout = '';
  let stderr = '';
  processHandle.stdout.on('data', chunk => { stdout = (stdout + chunk.toString()).slice(-12000); });
  processHandle.stderr.on('data', chunk => { stderr = (stderr + chunk.toString()).slice(-12000); });

  let lastError = 'WebView debugging endpoint unavailable';
  const deadline = Date.now() + 75000;
  while (Date.now() < deadline) {
    try {
      const response = await fetch(`http://127.0.0.1:${debugPort}/json/version`, { signal: AbortSignal.timeout(750) });
      const metadata = await response.json();
      if (!response.ok || typeof metadata.webSocketDebuggerUrl !== 'string') throw new Error('Missing WebView debug metadata.');
      const endpoint = new URL(metadata.webSocketDebuggerUrl);
      if (endpoint.protocol !== 'ws:' || !['127.0.0.1', 'localhost', '[::1]'].includes(endpoint.hostname) || endpoint.port !== String(debugPort)) {
        throw new Error('Unexpected WebView debug endpoint.');
      }
      endpoint.hostname = '127.0.0.1';
      browser = await chromium.connectOverCDP(endpoint.toString(), { timeout: 3000 });
      break;
    } catch (error) {
      lastError = String(error);
      if (processHandle.exitCode !== null) throw new Error(`Desktop exited during startup: ${lastError}; stdout=${stdout}; stderr=${stderr}`);
      await new Promise(resolve => setTimeout(resolve, 200));
    }
  }
  if (!browser) throw new Error(`Desktop WebView did not become available: ${lastError}; stdout=${stdout}; stderr=${stderr}`);
  const pages = browser.contexts().flatMap(context => context.pages());
  const page = pages[0];
  if (!page) throw new Error('Desktop did not expose its WebView page.');
  await expect(page.getByRole('navigation', { name: 'Main sections' })).toBeVisible({ timeout: 20000 });
  connection = await page.evaluate(() => window.__TAURI_INTERNALS__.invoke('engine_connection'));
  const headers = { Authorization: `Bearer ${connection.token}` };
  const base = `${connection.base_url}/runs/${encodeURIComponent(runId)}`;

  let engineReady = false;
  const engineDeadline = Date.now() + 75000;
  while (Date.now() < engineDeadline) {
    try {
      const response = await fetch(`${connection.base_url}/runs`, { headers, signal: AbortSignal.timeout(750) });
      if (response.ok) {
        engineReady = true;
        break;
      }
    } catch {}
    if (processHandle.exitCode !== null) throw new Error('Desktop exited before its bundled engine API became ready.');
    await new Promise(resolve => setTimeout(resolve, 200));
  }
  if (!engineReady) throw new Error('Bundled engine API did not become ready after desktop startup.');

  let run;
  let events;
  const recoveryDeadline = Date.now() + 30000;
  while (Date.now() < recoveryDeadline) {
    const [runResponse, eventResponse] = await Promise.all([
      fetch(base, { headers }),
      fetch(`${base}/events`, { headers }),
    ]);
    if (runResponse.ok && eventResponse.ok) {
      run = await runResponse.json();
      events = await eventResponse.json();
      const recovered = events.some(event => event.event_type === 'execution.completed'
        && event.payload?.result_reconciled === true);
      const output = events.some(event => event.event_type === 'execution.output.delta'
        && event.payload?.stream === 'stdout'
        && Buffer.from(event.payload.chunk_b64 ?? '', 'base64').toString('utf8').includes(expectedOutput));
      if (recovered && output) break;
    }
    await new Promise(resolve => setTimeout(resolve, 150));
  }
  if (!run || !Array.isArray(events)) throw new Error('The retained SSH recovery run is unavailable in the restarted desktop engine.');
  const completions = events.filter(event => event.event_type === 'execution.completed');
  const outputEvents = events.filter(event => event.event_type === 'execution.output.delta'
    && event.payload?.stream === 'stdout'
    && Buffer.from(event.payload.chunk_b64 ?? '', 'base64').toString('utf8').includes(expectedOutput));
  const commandDispatches = events.filter(event => event.event_type === 'tool.executing');
  if (completions.length !== 1 || completions[0].payload?.result_reconciled !== true
    || outputEvents.length !== 1 || commandDispatches.length !== 1) {
    throw new Error(`The recovered SSH result was duplicated or changed: ${JSON.stringify({ completions: completions.length, outputEvents: outputEvents.length, commandDispatches: commandDispatches.length })}`);
  }

  await page.getByRole('link', { name: 'Saved runs' }).click();
  await expect(page.getByRole('button', { name: `Open ${run.objective}` })).toBeVisible({ timeout: 15000 });
  await page.getByRole('button', { name: `Open ${run.objective}` }).click();
  const outputPanel = page.getByRole('region', { name: 'Streaming command output' });
  await expect(outputPanel).toBeVisible({ timeout: 20000 });
  await expect(outputPanel).toContainText(expectedOutput, { timeout: 20000 });
  const visibleOutput = await outputPanel.innerText();
  const screenshotPath = path.join(dataRoot, 'native-ssh-recovery.png');
  await page.screenshot({ path: screenshotPath, fullPage: true });

  report = {
    passed: true,
    scenario: 'Windows Tauri startup recovers a pending SSH command and restores its output in the UI',
    run_id: runId,
    backend: 'ssh-wsl-bubblewrap-v1',
    final_run_state: run.state,
    desktop_engine_recovered_pending_command: true,
    command_dispatch_count: commandDispatches.length,
    recovered_execution_count: completions.length,
    matching_output_event_count: outputEvents.length,
    ui_restored_remote_output: true,
    visible_output: visibleOutput,
    screenshot: screenshotPath,
    expected_output_sha256: createHash('sha256').update(expectedOutput).digest('hex'),
    model_inference_requested_by_acceptance: false,
    desktop_sha256: createHash('sha256').update(await readFile(desktop)).digest('hex'),
    engine_sha256: createHash('sha256').update(await readFile(path.join(path.dirname(desktop), 'xaa-engine.exe'))).digest('hex'),
    launch_credentials_cleaned: false,
  };
} catch (error) {
  report = { passed: false, error: String(error), run_id: runId, data_root: dataRoot };
  throw error;
} finally {
  await browser?.close().catch(() => {});
  if (processHandle?.exitCode === null) {
    execFileSync('taskkill', ['/PID', String(processHandle.pid), '/T', '/F'], { windowsHide: true, stdio: 'ignore' });
  }
  const credentialsCleaned = cleanLaunchCredentials();
  if (report) {
    report.launch_credentials_cleaned = credentialsCleaned;
    await writeFile(reportPath, JSON.stringify(report, null, 2));
    console.log(JSON.stringify({ ...report, evidence: reportPath }, null, 2));
  }
  if (!credentialsCleaned) throw new Error('Native SSH recovery acceptance could not clean its own launch credential pair.');
}
