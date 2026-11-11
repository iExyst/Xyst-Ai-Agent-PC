import { chromium, expect } from '@playwright/test';
import { randomUUID } from 'node:crypto';
import { createServer } from 'node:http';
import { mkdir, writeFile } from 'node:fs/promises';
import { spawn, execFileSync } from 'node:child_process';
import path from 'node:path';

if (process.platform !== 'win32') throw new Error('Native memory acceptance requires Windows.');

const desktopExecutable = path.resolve(process.argv[2] ?? 'build/native-dev/xaa-desktop.exe');
const root = path.resolve('.acceptance', `native-memory-panel-${randomUUID()}`);
const profile = path.join(root, 'profile');
const appData = path.join(profile, 'appdata');
const localAppData = path.join(profile, 'localappdata');
const logDirectory = path.join(localAppData, 'dev.xyst.xaa', 'logs');
const unique = `native-memory-${randomUUID()}`;
const initialPreference = `Saved preference ${unique}: use concise explanations.`;
const updatedPreference = `Saved preference ${unique}: show one concrete example.`;
const initialKnowledge = `Sourced knowledge ${unique}: SQLite has a write-ahead log.`;
const updatedKnowledge = `Sourced knowledge ${unique}: readers can coexist with a WAL writer.`;
const initialSource = `https://example.test/${unique}`;
const updatedSource = `https://sqlite.org/wal.html#${unique}`;
let desktop;
let browser;
let connection;
let report;

await Promise.all([root, appData, localAppData, logDirectory].map(directory => mkdir(directory, { recursive: true })));

function cleanTestLaunchCredentials() {
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
      windowsHide: true,
      encoding: 'utf8',
      timeout: 30000,
      env: process.env,
    }).trim() === 'True';
  } catch {
    return false;
  }
}

try {
  const configuredDebugPort = Number(process.env.XAA_NATIVE_DEBUG_PORT);
  const debugServer = Number.isInteger(configuredDebugPort) && configuredDebugPort > 0 && configuredDebugPort < 65536
    ? configuredDebugPort
    : await new Promise((resolve, reject) => {
      const server = createServer();
      server.once('error', reject);
      server.listen(0, '127.0.0.1', () => {
        const address = server.address();
        if (!address || typeof address === 'string') return reject(new Error('No debug port was allocated.'));
        const port = address.port;
        server.close(error => error ? reject(error) : resolve(port));
      });
    });
  const webviewProfile = path.join(root, 'webview-profile');
  const env = Object.fromEntries(Object.entries(process.env).filter(([key]) => !['PATH', 'PYTHONPATH', 'VIRTUAL_ENV'].includes(key.toUpperCase())));
  env.PATH = path.join(process.env.SystemRoot, 'System32');
  env.APPDATA = appData;
  env.LOCALAPPDATA = localAppData;
  env.XAA_DATA_DIR = root;
  env.XAA_TEST_HIDDEN = '1';
  env.XAA_TEST_LOG_DIR = logDirectory;
  env.WEBVIEW2_USER_DATA_FOLDER = webviewProfile;
  env.WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS = `--remote-debugging-port=${debugServer}`;
  desktop = spawn(desktopExecutable, [], { windowsHide: true, stdio: ['ignore', 'pipe', 'pipe'], env });
  let stdout = '';
  let stderr = '';
  desktop.stdout.on('data', chunk => { stdout = (stdout + chunk.toString()).slice(-12000); });
  desktop.stderr.on('data', chunk => { stderr = (stderr + chunk.toString()).slice(-12000); });

  const debugDeadline = Date.now() + 60000;
  let endpoint;
  while (Date.now() < debugDeadline) {
    try {
      const response = await fetch(`http://127.0.0.1:${debugServer}/json/version`, { signal: AbortSignal.timeout(750) });
      const metadata = await response.json();
      if (!response.ok || typeof metadata.webSocketDebuggerUrl !== 'string') throw new Error('WebView debug endpoint is unavailable');
      endpoint = metadata.webSocketDebuggerUrl;
      break;
    } catch (error) {
      if (desktop.exitCode !== null) throw new Error(`Native desktop exited during startup: ${String(error)}; stdout=${stdout}; stderr=${stderr}`);
      await new Promise(resolve => setTimeout(resolve, 200));
    }
  }
  if (!endpoint) throw new Error('Native desktop WebView did not become available.');
  browser = await chromium.connectOverCDP(endpoint, { timeout: 5000 });
  const page = browser.contexts().flatMap(context => context.pages())[0];
  if (!page) throw new Error('Native desktop did not expose a WebView page.');
  await expect(page.getByRole('navigation', { name: 'Main sections' })).toBeVisible({ timeout: 20000 });
  connection = await page.evaluate(() => window.__TAURI_INTERNALS__.invoke('engine_connection'));
  const headers = { Authorization: `Bearer ${connection.token}` };
  const apiDeadline = Date.now() + 30000;
  let apiReady = false;
  while (Date.now() < apiDeadline) {
    try {
      const response = await fetch(`${connection.base_url}/memory`, { headers, signal: AbortSignal.timeout(750) });
      if (response.ok) { apiReady = true; break; }
    } catch {}
    if (desktop.exitCode !== null) throw new Error('Native desktop exited before its memory API became ready.');
    await new Promise(resolve => setTimeout(resolve, 200));
  }
  if (!apiReady) throw new Error('Authenticated native memory API did not become ready.');

  await page.getByRole('link', { name: /Tools & integrations/ }).click();
  await page.getByText('Memory preferences', { exact: true }).click();
  await page.locator('#memory-preference').fill(initialPreference);
  await page.getByRole('button', { name: 'Save preference', exact: true }).click();
  let preferenceRow = page.locator('li').filter({ hasText: unique });
  await expect(preferenceRow).toContainText(initialPreference);
  let stored = await (await fetch(`${connection.base_url}/memory?q=${encodeURIComponent(unique)}`, { headers })).json();
  if (stored.length !== 1 || stored[0].content !== initialPreference) throw new Error('Native UI save did not persist one preference to the authenticated API.');
  const preferenceId = stored[0].id;

  await preferenceRow.getByRole('button', { name: 'Edit preference', exact: true }).click();
  await page.locator('#memory-preference').fill(updatedPreference);
  await page.getByRole('button', { name: 'Update preference', exact: true }).click();
  preferenceRow = page.locator('li').filter({ hasText: unique });
  await expect(preferenceRow).toContainText(updatedPreference);
  stored = await (await fetch(`${connection.base_url}/memory?q=${encodeURIComponent(unique)}`, { headers })).json();
  if (stored.length !== 1 || stored[0].id !== preferenceId || stored[0].content !== updatedPreference) throw new Error('Native UI edit did not preserve the preference identity and updated content.');

  await preferenceRow.getByRole('button', { name: 'Delete memory', exact: true }).click();
  await expect(page.locator('li').filter({ hasText: unique })).toHaveCount(0);
  const searchAfterDelete = await (await fetch(`${connection.base_url}/memory?q=${encodeURIComponent(unique)}`, { headers })).json();
  const exportAfterDelete = await (await fetch(`${connection.base_url}/memory/export`, { headers })).json();
  if (searchAfterDelete.length !== 0 || exportAfterDelete.some(item => item.id === preferenceId)) {
    throw new Error('Deleted native preference remained in memory search or export.');
  }
  await page.locator('#memory-knowledge').fill(initialKnowledge);
  await page.locator('#memory-source').fill(initialSource);
  await page.getByRole('button', { name: 'Save sourced knowledge', exact: true }).click();
  let knowledgeRow = page.locator('li').filter({ hasText: unique });
  await expect(knowledgeRow).toContainText(initialKnowledge);
  await expect(knowledgeRow).toContainText(initialSource);
  let storedKnowledge = await (await fetch(`${connection.base_url}/memory?q=${encodeURIComponent(unique)}`, { headers })).json();
  if (storedKnowledge.length !== 1 || storedKnowledge[0].kind !== 'knowledge' || storedKnowledge[0].source_uri !== initialSource) {
    throw new Error('Native sourced-knowledge save did not preserve its source reference in the authenticated API.');
  }
  const knowledgeId = storedKnowledge[0].id;
  await knowledgeRow.getByRole('button', { name: 'Edit sourced knowledge', exact: true }).click();
  await page.locator('#memory-knowledge').fill(updatedKnowledge);
  await page.locator('#memory-source').fill(updatedSource);
  await page.getByRole('button', { name: 'Update sourced knowledge', exact: true }).click();
  knowledgeRow = page.locator('li').filter({ hasText: unique });
  await expect(knowledgeRow).toContainText(updatedKnowledge);
  await expect(knowledgeRow).toContainText(updatedSource);
  storedKnowledge = await (await fetch(`${connection.base_url}/memory?q=${encodeURIComponent(unique)}`, { headers })).json();
  if (storedKnowledge.length !== 1 || storedKnowledge[0].id !== knowledgeId || storedKnowledge[0].source_uri !== updatedSource) {
    throw new Error('Native sourced-knowledge edit did not preserve identity and update source metadata.');
  }
  await knowledgeRow.getByRole('button', { name: 'Delete memory', exact: true }).click();
  await expect(page.locator('li').filter({ hasText: unique })).toHaveCount(0);
  const knowledgeSearchAfterDelete = await (await fetch(`${connection.base_url}/memory?q=${encodeURIComponent(unique)}`, { headers })).json();
  const knowledgeExportAfterDelete = await (await fetch(`${connection.base_url}/memory/export`, { headers })).json();
  if (knowledgeSearchAfterDelete.length !== 0 || knowledgeExportAfterDelete.some(item => item.id === knowledgeId)) {
    throw new Error('Deleted sourced knowledge remained in memory search or export.');
  }
  report = {
    passed: true,
    scenario: 'native Tauri preference and sourced-knowledge save, edit, search, delete and retrieval-index verification',
    preference_id: preferenceId,
    initial_preference_saved: true,
    edit_preserved_identity: true,
    post_edit_search_verified: true,
    deleted_preference_absent_from_search: true,
    deleted_preference_absent_from_export: true,
    sourced_knowledge_id: knowledgeId,
    sourced_knowledge_create_edit_search_delete_verified: true,
    source_reference_edit_preserved_record_identity: true,
    deleted_knowledge_absent_from_search_and_export: true,
    model_inference_requested: false,
  };
  await writeFile(path.join(root, 'report.json'), JSON.stringify(report, null, 2));
  console.log(JSON.stringify({ ...report, evidence: root }, null, 2));
} catch (error) {
  const detail = { passed: false, error: String(error), root };
  await writeFile(path.join(root, 'report.json'), JSON.stringify(detail, null, 2)).catch(() => {});
  throw error;
} finally {
  await browser?.close().catch(() => {});
  if (desktop?.exitCode === null) execFileSync('taskkill', ['/PID', String(desktop.pid), '/T', '/F'], { windowsHide: true, stdio: 'ignore' });
  if (!cleanTestLaunchCredentials()) throw new Error('Native memory acceptance could not clean its own API launch credential pair.');
}
