import { chromium, expect } from '@playwright/test';
import { spawn, execFileSync } from 'node:child_process';
import { createHash, randomUUID } from 'node:crypto';
import { mkdir, readFile, writeFile } from 'node:fs/promises';
import net from 'node:net';
import path from 'node:path';

const root = path.resolve('.acceptance', `native-skill-readiness-${randomUUID()}`);
await mkdir(root, { recursive: true });
const logDirectory = path.join(root, 'logs');
await mkdir(logDirectory, { recursive: true });
const webviewProfile = path.join(root, 'webview-profile');
const localAppData = path.join(root, 'local-app-data');
const appData = path.join(root, 'roaming-app-data');
await mkdir(localAppData, { recursive: true });
await mkdir(appData, { recursive: true });

async function freePort() {
  return await new Promise((resolve, reject) => {
    const server = net.createServer();
    server.once('error', reject);
    server.listen(0, '127.0.0.1', () => {
      const address = server.address();
      if (!address || typeof address === 'string') return reject(new Error('Could not allocate a debug port.'));
      server.close((error) => error ? reject(error) : resolve(address.port));
    });
  });
}

const stage = process.env.XAA_NATIVE_STAGE || 'native-dev';
if (!['native-dev', 'native-release'].includes(stage)) throw new Error(`Unsupported native stage: ${stage}`);
const stageRoot = path.resolve(process.env.XAA_NATIVE_STAGE_ROOT || path.join('build', stage));
const desktop = path.resolve(process.env.XAA_NATIVE_DESKTOP_PATH || path.join(stageRoot, 'xaa-desktop.exe'));
const engine = path.resolve(process.env.XAA_NATIVE_ENGINE_PATH || path.join(stageRoot, 'xaa-engine.exe'));
const manifestPath = path.resolve(process.env.XAA_NATIVE_MANIFEST_PATH || path.join(stageRoot, 'manifest.json'));
const manifest = JSON.parse(await readFile(manifestPath, 'utf8'));
const desktopHash = createHash('sha256').update(await readFile(desktop)).digest('hex');
const engineHash = createHash('sha256').update(await readFile(engine)).digest('hex');
if (manifest['xaa-desktop.exe'] !== desktopHash || manifest['xaa-engine.exe'] !== engineHash) {
  throw new Error('The native desktop and engine do not match the stage manifest.');
}

const debugPort = await freePort();
const distro = process.env.XAA_WSL_DISTRIBUTION || 'Ubuntu-24.04';
const pluginRoot = path.join(root, 'plugins');
const skillDirectory = path.join(pluginRoot, 'readiness-check');
await mkdir(skillDirectory, { recursive: true });
await writeFile(path.join(skillDirectory, 'SKILL.md'), `---
name: readiness-check
description: Fixture for installed native WSL dependency readiness.
dependencies:
  python: ["requests>=2.0", "packaging>=26.3,<27"]
  python3: ["xaa-readiness-missing-fixture-20261113==99.0"]
  npm: ["left-pad>=1.0", "https://example.test/private-token"]
---
# Readiness fixture
`, 'utf8');

const inheritedEnvironmentKeys = [
  'SystemRoot', 'WINDIR', 'USERPROFILE', 'USERNAME', 'TEMP', 'TMP', 'HOMEDRIVE',
  'HOMEPATH', 'PROCESSOR_ARCHITECTURE', 'NUMBER_OF_PROCESSORS', 'COMSPEC', 'SystemDrive',
];
const environment = Object.fromEntries(inheritedEnvironmentKeys
  .filter((key) => process.env[key] !== undefined)
  .map((key) => [key, process.env[key]]));
environment.PATH = path.join(process.env.SystemRoot, 'System32');
environment.XAA_TEST_HIDDEN = '1';
environment.XAA_TEST_LOG_DIR = logDirectory;
environment.XAA_DATA_DIR = root;
environment.LOCALAPPDATA = localAppData;
environment.APPDATA = appData;
environment.WEBVIEW2_USER_DATA_FOLDER = webviewProfile;
environment.WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS = `--remote-debugging-port=${debugPort}`;

let processHandle;
let browser;
let page;
let connection;
let readiness;
let importedSkill;
let gracefulShutdown = false;
let sandboxCheckPassed = false;
let passed = false;

try {
  processHandle = spawn(desktop, [], { windowsHide: true, stdio: ['ignore', 'pipe', 'pipe'], env: environment });
  let processOutput = '';
  processHandle.stdout.on('data', (chunk) => { processOutput = (processOutput + chunk.toString()).slice(-16_384); });
  processHandle.stderr.on('data', (chunk) => { processOutput = (processOutput + chunk.toString()).slice(-16_384); });

  const debugDeadline = Date.now() + 60_000;
  while (Date.now() < debugDeadline) {
    try {
      const response = await fetch(`http://127.0.0.1:${debugPort}/json/version`, { signal: AbortSignal.timeout(750) });
      if (!response.ok) throw new Error(`WebView debug endpoint returned HTTP ${response.status}.`);
      const metadata = await response.json();
      const endpoint = new URL(metadata.webSocketDebuggerUrl);
      if (endpoint.protocol !== 'ws:' || !['localhost', '127.0.0.1', '[::1]'].includes(endpoint.hostname)
        || endpoint.port !== String(debugPort)) throw new Error('WebView exposed an unexpected debug address.');
      endpoint.hostname = '127.0.0.1';
      browser = await chromium.connectOverCDP(endpoint.toString(), { timeout: 3000 });
      break;
    } catch (error) {
      if (processHandle.exitCode !== null) throw new Error(`Native desktop exited during startup: ${String(error)} ${processOutput}`);
      await new Promise((resolve) => setTimeout(resolve, 250));
    }
  }
  if (!browser) throw new Error('Could not attach to the native desktop WebView.');
  const pageDeadline = Date.now() + 20_000;
  while (Date.now() < pageDeadline) {
    page = browser.contexts().flatMap((context) => context.pages())[0];
    if (page) break;
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  if (!page) throw new Error('Native desktop WebView did not open an application page.');
  await expect(page.getByRole('navigation', { name: 'Main sections' })).toBeVisible({ timeout: 20_000 });
  connection = await page.evaluate(() => window.__TAURI_INTERNALS__.invoke('engine_connection'));
  const headers = { Authorization: `Bearer ${connection.token}` };
  let ready = false;
  for (let attempt = 0; attempt < 120; attempt += 1) {
    try {
      const response = await fetch(`${connection.base_url}/runs`, { headers, signal: AbortSignal.timeout(500) });
      if (response.ok) {
        const history = await response.json();
        if (history.items.length !== 0) throw new Error('Isolated native readiness database is not empty.');
        ready = true;
        break;
      }
    } catch (error) {
      if (String(error).includes('database is not empty')) throw error;
    }
    await new Promise((resolve) => setTimeout(resolve, 250));
  }
  if (!ready) throw new Error('Bundled engine API did not become ready.');
  if ((await fetch(`${connection.base_url}/runs`)).status !== 401) throw new Error('Native API did not require authentication.');

  const importedResponse = await fetch(`${connection.base_url}/extensions/packages/import-skill`, {
    method: 'POST', headers: { ...headers, 'Content-Type': 'application/json' },
    body: JSON.stringify({ root: pluginRoot, package_name: 'readiness-check', skill_name: 'readiness-check' }),
  });
  if (importedResponse.status !== 201) throw new Error(`Fixture skill import returned HTTP ${importedResponse.status}.`);
  importedSkill = await importedResponse.json();

  await page.getByRole('link', { name: 'Tools & integrations' }).click();
  const setup = page.getByRole('region', { name: 'Command sandbox setup' });
  await setup.getByText('Command sandbox setup', { exact: true }).click();
  await setup.getByRole('button', { name: 'Find installed distributions', exact: true }).click();
  const distributionPicker = setup.getByLabel('Execution distribution');
  await expect(distributionPicker).toContainText(distro, { timeout: 20_000 });
  await distributionPicker.selectOption(distro);
  await setup.getByRole('button', { name: 'Check sandbox', exact: true }).click();
  await expect(setup.getByRole('status')).toContainText('Sandbox check passed.', { timeout: 30_000 });
  sandboxCheckPassed = true;

  const procedures = page.getByText('Reusable procedures', { exact: true });
  await procedures.click();
  const skills = page.getByRole('region', { name: 'Procedure candidates' });
  await skills.getByRole('button', { name: 'Refresh procedure list', exact: true }).click();
  await skills.getByRole('button', { name: 'Inspect readiness-check', exact: true }).click();
  const dependencySection = skills.getByRole('region', { name: 'Restricted WSL Python dependency readiness' });
  const checkButton = dependencySection.getByRole('button', { name: 'Check Python dependencies in WSL', exact: true });
  if (await checkButton.isDisabled()) throw new Error('Readiness check remained disabled after the sandbox passed.');
  const [readinessResponse] = await Promise.all([
    page.waitForResponse((response) => new URL(response.url()).pathname.endsWith('/python-readiness')
      && response.request().method() === 'POST'),
    checkButton.click(),
  ]);
  if (!readinessResponse.ok()) throw new Error(`Native WSL readiness returned HTTP ${readinessResponse.status()}.`);
  readiness = await readinessResponse.json();
  await expect(dependencySection.getByRole('status')).toContainText(`Check state: ready · Python`, { timeout: 20_000 });
  await expect(dependencySection.getByRole('status')).toContainText('requests>=2.0: available (2.31.0)');
  await expect(dependencySection.getByRole('status')).toContainText('packaging>=26.3,<27: incompatible (24.0)');
  await expect(dependencySection.getByRole('status')).toContainText('xaa-readiness-missing-fixture-20261113==99.0: missing');
  await expect(dependencySection.getByRole('status')).toContainText('Other or unverified declared dependencies: 2');
  if (readiness.state !== 'ready' || readiness.unverified_dependency_count !== 2) {
    throw new Error(`Native readiness response did not match its display: ${JSON.stringify(readiness)}.`);
  }
  const finalRuns = await (await fetch(`${connection.base_url}/runs`, { headers })).json();
  if (finalRuns.items.length !== 0) throw new Error('Readiness acceptance unexpectedly created a model run.');
  await page.screenshot({ path: path.join(root, 'native-skill-readiness.png'), fullPage: true });

  await page.evaluate(() => window.__TAURI_INTERNALS__.invoke('quit_application'));
  for (let attempt = 0; attempt < 80 && processHandle.exitCode === null; attempt += 1) {
    await new Promise((resolve) => setTimeout(resolve, 250));
  }
  if (processHandle.exitCode !== 0) throw new Error('Native desktop did not exit cleanly.');
  const engineLog = await readFile(path.join(logDirectory, 'engine.log'), 'utf8');
  if (!engineLog.includes('Application shutdown complete.')) throw new Error('Native engine did not shut down gracefully.');
  gracefulShutdown = true;
  passed = true;
} finally {
  if (browser) await browser.close().catch(() => {});
  if (processHandle && processHandle.exitCode === null) {
    try {
      execFileSync(path.join(process.env.SystemRoot, 'System32', 'taskkill.exe'), ['/PID', String(processHandle.pid), '/T', '/F'], { windowsHide: true, stdio: 'ignore' });
    } catch {}
    if (processHandle.exitCode === null) {
      await Promise.race([
        new Promise((resolve) => processHandle.once('exit', resolve)),
        new Promise((_, reject) => setTimeout(() => reject(new Error('Native desktop remained after cleanup.')), 5_000)),
      ]).catch(() => {});
    }
  }
  const report = {
    passed,
    desktop_sha256: desktopHash,
    engine_sha256: engineHash,
    distribution: distro,
    imported_skill_id: importedSkill?.id ?? null,
    sandbox_check_passed: sandboxCheckPassed,
    readiness,
    model_inference_requested: false,
    model_runs_created: false,
    package_code_imported_or_executed: false,
    dependencies_installed: false,
    screenshot: 'native-skill-readiness.png',
    graceful_shutdown: gracefulShutdown,
  };
  await writeFile(path.join(root, 'report.json'), `${JSON.stringify(report, null, 2)}\n`, 'utf8');
  console.log(JSON.stringify({ ...report, report: path.join(root, 'report.json') }, null, 2));
}

if (!passed) process.exitCode = 1;
