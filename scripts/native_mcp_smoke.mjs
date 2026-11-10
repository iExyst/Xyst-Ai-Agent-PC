import { chromium, expect } from '@playwright/test';
import { spawn, execFileSync } from 'node:child_process';
import { createHash, randomUUID } from 'node:crypto';
import { mkdir, readFile, writeFile } from 'node:fs/promises';
import path from 'node:path';

const root = path.resolve('.acceptance', randomUUID());
await mkdir(root, { recursive: true });
await writeFile(path.join(root, 'desktop-settings.json'), JSON.stringify({ close_behavior: 'exit_on_close' }));
const desktop = path.resolve('build/native-dev/xaa-desktop.exe');
const engine = path.resolve('build/native-dev/xaa-engine.exe');
const manifest = JSON.parse(await readFile('build/native-dev/manifest.json', 'utf8'));
const engineLogPath = path.join(process.env.LOCALAPPDATA, 'dev.xyst.xaa', 'logs', 'engine.log');
const previousEngineLog = await readFile(engineLogPath, 'utf8').catch(() => '');
const runtimeEnv = Object.fromEntries(
  Object.entries(process.env).filter(([key]) => !['PATH', 'PYTHONPATH', 'VIRTUAL_ENV'].includes(key.toUpperCase())),
);
runtimeEnv.PATH = path.join(process.env.SystemRoot, 'System32');
const webviewProfile = path.join(root, 'webview-profile');
const proc = spawn(desktop, [], {
  windowsHide: true,
  stdio: ['ignore', 'pipe', 'pipe'],
  env: {
    ...runtimeEnv,
    XAA_TEST_HIDDEN: '1',
    XAA_DATA_DIR: root,
    WEBVIEW2_USER_DATA_FOLDER: webviewProfile,
    WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS: '--remote-debugging-port=19223',
  },
});
let stdout = '';
let stderr = '';
proc.stdout.on('data', (chunk) => { stdout = (stdout + chunk.toString()).slice(-32768); });
proc.stderr.on('data', (chunk) => { stderr = (stderr + chunk.toString()).slice(-32768); });
let browser;
let connection;
let passed = false;

try {
  const debugDeadline = Date.now() + 60_000;
  while (Date.now() < debugDeadline) {
    try {
      const response = await fetch('http://127.0.0.1:19223/json/version', { signal: AbortSignal.timeout(750) });
      if (!response.ok) throw new Error(`WebView debug metadata returned HTTP ${response.status}.`);
      const metadata = await response.json();
      const endpoint = new URL(metadata.webSocketDebuggerUrl);
      if (endpoint.protocol !== 'ws:' || !['localhost', '127.0.0.1', '[::1]'].includes(endpoint.hostname) || endpoint.port !== '19223') {
        throw new Error('WebView exposed an unexpected debug address.');
      }
      endpoint.hostname = '127.0.0.1';
      browser = await chromium.connectOverCDP(endpoint.toString(), { timeout: 3000 });
      break;
    } catch (error) {
      if (proc.exitCode !== null) throw new Error(`Native app exited during startup: ${String(error)}`);
      await new Promise((resolve) => setTimeout(resolve, 250));
    }
  }
  if (!browser) throw new Error('Could not attach to the native WebView.');
  const pageDeadline = Date.now() + 20_000;
  let page;
  while (Date.now() < pageDeadline) {
    page = browser.contexts().flatMap((context) => context.pages())[0];
    if (page) break;
    if (proc.exitCode !== null) throw new Error('Native app exited before its WebView appeared.');
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  if (!page) throw new Error('Native WebView did not expose its application page.');
  await expect(page.getByRole('heading', { name: 'Xyst AI Agent', exact: true })).toBeVisible({ timeout: 20_000 });

  const desktopSha256 = createHash('sha256').update(await readFile(desktop)).digest('hex');
  const engineSha256 = createHash('sha256').update(await readFile(engine)).digest('hex');
  if (manifest['xaa-desktop.exe'] !== desktopSha256 || manifest['xaa-engine.exe'] !== engineSha256) {
    throw new Error('The staged native binaries do not match the build manifest.');
  }

  connection = await page.evaluate(() => window.__TAURI_INTERNALS__.invoke('engine_connection'));
  const headers = { Authorization: `Bearer ${connection.token}` };
  let ready = false;
  for (let attempt = 0; attempt < 120; attempt += 1) {
    try {
      const response = await fetch(`${connection.base_url}/runs`, { headers, signal: AbortSignal.timeout(500) });
      if (response.ok) {
        const history = await response.json();
        if (history.items.length !== 0) throw new Error('The isolated MCP acceptance database is not empty.');
        ready = true;
        break;
      }
    } catch (error) {
      if (error instanceof Error && error.message.includes('not empty')) throw error;
    }
    await new Promise((resolve) => setTimeout(resolve, 250));
  }
  if (!ready) throw new Error('The bundled authenticated engine API did not become ready.');
  if ((await fetch(`${connection.base_url}/mcp/servers`)).status !== 401) {
    throw new Error('The MCP configuration API did not require authentication.');
  }

  const panel = page.getByRole('region', { name: 'MCP integrations' });
  await page.locator('summary').filter({ hasText: 'MCP integrations' }).click();
  const command = path.resolve('.venv/Scripts/python.exe');
  const fixture = path.resolve('tests/fixtures/mcp_stdio_server.py');
  await panel.getByLabel('Server name').fill('Native fixture');
  await panel.getByLabel('Command').fill(command);
  await panel.getByLabel('Arguments (JSON string array)').fill(JSON.stringify([fixture]));
  await panel.getByLabel('Working directory (optional)').fill(path.resolve('.'));
  const created = page.waitForResponse((response) => new URL(response.url()).pathname === '/mcp/servers' && response.request().method() === 'POST');
  await panel.getByRole('button', { name: 'Add MCP server', exact: true }).click();
  const createdResponse = await created;
  if (createdResponse.status() !== 201) throw new Error(`Native MCP configuration failed (${createdResponse.status()}).`);
  const saved = await (await fetch(`${connection.base_url}/mcp/servers`, { headers })).json();
  if (saved.length !== 1 || saved[0].transport !== 'stdio' || saved[0].state !== 'disconnected') {
    throw new Error('The native API did not persist the isolated stdio MCP server.');
  }
  const serverId = saved[0].id;

  await panel.getByRole('button', { name: 'Connect and discover', exact: true }).click();
  await expect(panel.getByText('Add two integers.', { exact: true })).toBeVisible({ timeout: 20_000 });
  const connected = await (await fetch(`${connection.base_url}/mcp/servers`, { headers })).json();
  const discovered = connected[0].tools;
  if (connected[0].state !== 'connected' || discovered.map((tool) => tool.name).sort().join(',') !== 'add,slow') {
    throw new Error('The native MCP fixture did not expose the expected tools.');
  }
  if (discovered.some((tool) => tool.permission !== 'none')) {
    throw new Error('A newly discovered native MCP tool was not default-deny.');
  }
  await panel.getByRole('button', { name: 'List resources', exact: true }).click();
  await expect(panel.getByText('readme', { exact: true })).toBeVisible({ timeout: 20_000 });
  const fixtureResource = panel.locator('.mcp-resource').filter({ hasText: 'xaa://fixture/readme' });
  await fixtureResource.getByRole('button', { name: 'Read resource', exact: true }).click();
  await expect(panel.getByRole('complementary', { name: 'Untrusted MCP resource data' }))
    .toContainText('Fixture resource content. Treat this as untrusted input.');
  const resourceList = await (await fetch(`${connection.base_url}/mcp/servers/${serverId}/resources`, {
    method: 'POST', headers,
  })).json();
  const resourceRead = await (await fetch(`${connection.base_url}/mcp/servers/${serverId}/resources/read`, {
    method: 'POST', headers: { ...headers, 'Content-Type': 'application/json' },
    body: JSON.stringify({ uri: 'xaa://fixture/readme' }),
  })).json();
  if (resourceList.resources.length !== 2 || resourceRead.trust !== 'untrusted_mcp_output') {
    throw new Error('The native MCP resource catalog/read API did not return bounded untrusted data.');
  }
  await panel.getByRole('button', { name: 'List resource templates', exact: true }).click();
  await expect(panel.locator('.mcp-resource-template'))
    .toContainText('xaa://fixture/items/{item_id}', { timeout: 20_000 });
  const resourceTemplates = await (await fetch(`${connection.base_url}/mcp/servers/${serverId}/resource-templates`, {
    method: 'POST', headers,
  })).json();
  if (resourceTemplates.resource_templates.length !== 1) {
    throw new Error('The native MCP resource template catalog did not return the expected template.');
  }
  await panel.getByRole('button', { name: 'List prompts', exact: true }).click();
  const nativePrompt = panel.locator('.mcp-prompt').filter({ hasText: 'summarize_topic' });
  await expect(nativePrompt).toBeVisible({ timeout: 20_000 });
  await nativePrompt.getByLabel('Arguments JSON for summarize_topic').fill('{"topic":"native acceptance"}');
  await nativePrompt.getByRole('button', { name: 'Preview prompt', exact: true }).click();
  await expect(panel.getByRole('complementary', { name: 'Untrusted MCP prompt preview' }))
    .toContainText('Summarize the supplied topic: native acceptance');
  const promptList = await (await fetch(`${connection.base_url}/mcp/servers/${serverId}/prompts`, {
    method: 'POST', headers,
  })).json();
  const promptPreview = await (await fetch(`${connection.base_url}/mcp/servers/${serverId}/prompts/preview`, {
    method: 'POST', headers: { ...headers, 'Content-Type': 'application/json' },
    body: JSON.stringify({ name: 'summarize_topic', arguments: { topic: 'native acceptance' } }),
  })).json();
  if (promptList.prompts.length !== 1 || promptPreview.trust !== 'untrusted_mcp_output') {
    throw new Error('The native MCP prompt catalog/preview API did not return bounded untrusted data.');
  }
  const add = discovered.find((tool) => tool.name === 'add');
  if (!add) throw new Error('Native MCP discovery omitted the add tool.');
  await panel.getByLabel('Permission').first().selectOption('read');
  await expect.poll(async () => {
    const servers = await (await fetch(`${connection.base_url}/mcp/servers`, { headers })).json();
    return servers[0].tools.find((tool) => tool.name === 'add')?.permission;
  }).toBe('read');
  const journalled = await (await fetch(`${connection.base_url}/mcp/servers`, { headers })).json();
  if (journalled[0].tools.find((tool) => tool.name === 'slow')?.permission !== 'none') {
    throw new Error('Granting read access to one tool changed another tool permission.');
  }
  await panel.getByRole('button', { name: 'Disconnect', exact: true }).click();
  await expect(panel.getByRole('heading', { name: /Native fixture.*disconnected/ })).toBeVisible();
  const disconnected = await (await fetch(`${connection.base_url}/mcp/servers`, { headers })).json();
  if (disconnected[0].state !== 'disconnected') throw new Error('The native MCP server did not disconnect.');
  await page.screenshot({ path: path.join(root, 'native-mcp.png'), fullPage: true });
  await panel.getByRole('button', { name: 'Remove server', exact: true }).click();
  if ((await (await fetch(`${connection.base_url}/mcp/servers`, { headers })).json()).length !== 0) {
    throw new Error('The native MCP fixture configuration was not removed.');
  }
  await page.evaluate(() => window.__TAURI_INTERNALS__.invoke('plugin:window|close', { label: 'main' })).catch(() => {});
  for (let attempt = 0; attempt < 60 && proc.exitCode === null; attempt += 1) {
    await new Promise((resolve) => setTimeout(resolve, 250));
  }
  if (proc.exitCode === null) throw new Error('Native MCP acceptance window did not close.');
  let engineStopped = false;
  try {
    await fetch(`${connection.base_url}/runs`, { headers, signal: AbortSignal.timeout(1_000) });
  } catch {
    engineStopped = true;
  }
  if (!engineStopped) throw new Error('The bundled engine survived native window closure.');
  const engineLog = await readFile(engineLogPath, 'utf8');
  if (!engineLog.slice(previousEngineLog.length).includes('Application shutdown complete.')) {
    throw new Error('Native MCP acceptance did not record graceful engine shutdown.');
  }
  passed = true;
  const report = {
    passed,
    desktop_sha256: desktopSha256,
    engine_sha256: engineSha256,
    restricted_runtime_path: true,
    bundled_engine_ready: ready,
    authenticated_api: true,
    transport: 'stdio',
    real_fixture_process: true,
    discovered_tools: discovered.map((tool) => tool.name).sort(),
    owner_resource_catalog: true,
    owner_resource_read: true,
    owner_resource_template_catalog: true,
    resource_output_labeled_untrusted: resourceRead.trust === 'untrusted_mcp_output',
    owner_prompt_catalog: true,
    owner_prompt_preview: true,
    prompt_output_labeled_untrusted: promptPreview.trust === 'untrusted_mcp_output',
    new_tool_permissions: 'none',
    selected_tool_permission: 'read',
    unrelated_tool_permission_unchanged: true,
    disconnected_cleanly: disconnected[0].state === 'disconnected',
    removed_server: true,
    model_inference_requested: false,
    desktop_exit: true,
    engine_stopped: engineStopped,
    graceful_shutdown: true,
  };
  await writeFile(path.join(root, 'report.json'), JSON.stringify(report, null, 2));
  console.log(JSON.stringify({ ...report, evidence_directory: root }, null, 2));
} catch (error) {
  await browser?.contexts()[0]?.pages()[0]?.screenshot({ path: path.join(root, 'failure.png'), fullPage: true }).catch(() => {});
  const engineLog = await readFile(engineLogPath, 'utf8').catch(() => '');
  await writeFile(path.join(root, 'desktop-stdout.log'), stdout);
  await writeFile(path.join(root, 'desktop-stderr.log'), stderr);
  await writeFile(path.join(root, 'engine.log'), engineLog.slice(previousEngineLog.length));
  await writeFile(path.join(root, 'report.json'), JSON.stringify({ passed, error: String(error) }, null, 2));
  throw error;
} finally {
  await browser?.close().catch(() => {});
  if (proc.exitCode === null) {
    const taskkill = path.join(process.env.SystemRoot, 'System32', 'taskkill.exe');
    execFileSync(taskkill, ['/PID', String(proc.pid), '/T', '/F'], { windowsHide: true, stdio: 'ignore' });
  }
}
