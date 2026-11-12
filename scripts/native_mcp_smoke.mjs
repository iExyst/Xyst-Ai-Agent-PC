import { chromium, expect } from '@playwright/test';
import { spawn, execFileSync } from 'node:child_process';
import { createHash, randomUUID } from 'node:crypto';
import { mkdir, readFile, writeFile } from 'node:fs/promises';
import net from 'node:net';
import path from 'node:path';

const root = path.resolve('.acceptance', randomUUID());
await mkdir(root, { recursive: true });
const acceptanceLogDirectory = path.join(root, 'logs');
await mkdir(acceptanceLogDirectory, { recursive: true });
await writeFile(path.join(root, 'desktop-settings.json'), JSON.stringify({ close_behavior: 'exit_on_close' }));

async function clickForResponse(page, locator, predicate) {
  const [response] = await Promise.all([page.waitForResponse(predicate), locator.click()]);
  return response;
}

async function removeMcpServer(page, button) {
  const deletion = await clickForResponse(page, button, (response) => {
    const pathname = new URL(response.url()).pathname;
    return pathname.startsWith('/mcp/servers/') && response.request().method() === 'DELETE';
  });
  if (deletion.status() !== 204) {
    throw new Error(`MCP server removal returned HTTP ${deletion.status()}.`);
  }
}
const stage = process.env.XAA_NATIVE_STAGE || 'native-dev';
if (!['native-dev', 'native-release'].includes(stage)) throw new Error(`Unsupported native stage: ${stage}`);
const stageRoot = path.resolve(process.env.XAA_NATIVE_STAGE_ROOT || path.join('build', stage));
const desktop = path.resolve(process.env.XAA_NATIVE_DESKTOP_PATH || path.join(stageRoot, 'xaa-desktop.exe'));
const engine = path.resolve(process.env.XAA_NATIVE_ENGINE_PATH || path.join(stageRoot, 'xaa-engine.exe'));
const manifestPath = path.resolve(process.env.XAA_NATIVE_MANIFEST_PATH || path.join(stageRoot, 'manifest.json'));
const manifest = JSON.parse(await readFile(manifestPath, 'utf8'));
const expectedDesktopHash = manifest.artifacts?.desktop?.sha256 ?? manifest['xaa-desktop.exe'];
const expectedEngineHash = manifest.artifacts?.engine?.sha256 ?? manifest['xaa-engine.exe'];
const desktopSha256 = createHash('sha256').update(await readFile(desktop)).digest('hex');
const engineSha256 = createHash('sha256').update(await readFile(engine)).digest('hex');
if (expectedDesktopHash !== desktopSha256 || expectedEngineHash !== engineSha256) {
  throw new Error('The native desktop and engine do not match the build manifest.');
}
const engineLogPath = path.join(acceptanceLogDirectory, 'engine.log');
const previousEngineLog = await readFile(engineLogPath, 'utf8').catch(() => '');
const runtimeEnv = Object.fromEntries(
  Object.entries(process.env).filter(([key]) => ![
    'PATH', 'PYTHONPATH', 'VIRTUAL_ENV',
    'XAA_NATIVE_DESKTOP_PATH', 'XAA_NATIVE_ENGINE_PATH', 'XAA_NATIVE_MANIFEST_PATH', 'XAA_NATIVE_STAGE_ROOT',
  ].includes(key.toUpperCase())),
);
runtimeEnv.PATH = path.join(process.env.SystemRoot, 'System32');
const webviewProfile = path.join(root, 'webview-profile');
const proc = spawn(desktop, [], {
  windowsHide: true,
  stdio: ['ignore', 'pipe', 'pipe'],
  env: {
    ...runtimeEnv,
    XAA_TEST_HIDDEN: '1',
    XAA_TEST_LOG_DIR: acceptanceLogDirectory,
    XAA_DATA_DIR: root,
    WEBVIEW2_USER_DATA_FOLDER: webviewProfile,
    WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS: `--remote-debugging-port=${process.env.XAA_NATIVE_DEBUG_PORT || 19223}`,
  },
});
let stdout = '';
let stderr = '';
proc.stdout.on('data', (chunk) => { stdout = (stdout + chunk.toString()).slice(-32768); });
proc.stderr.on('data', (chunk) => { stderr = (stderr + chunk.toString()).slice(-32768); });
let browser;
let connection;
let oauthFixture;
let isolationListener;
let fixtureOutput = '';
let passed = false;

try {
  const debugDeadline = Date.now() + 60_000;
  while (Date.now() < debugDeadline) {
    try {
      const debugPort = process.env.XAA_NATIVE_DEBUG_PORT || '19223';
      const response = await fetch(`http://127.0.0.1:${debugPort}/json/version`, { signal: AbortSignal.timeout(750) });
      if (!response.ok) throw new Error(`WebView debug metadata returned HTTP ${response.status}.`);
      const metadata = await response.json();
      const endpoint = new URL(metadata.webSocketDebuggerUrl);
      if (endpoint.protocol !== 'ws:' || !['localhost', '127.0.0.1', '[::1]'].includes(endpoint.hostname) || endpoint.port !== debugPort) {
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
  await expect(page.getByRole('navigation', { name: 'Main sections' })).toBeVisible({ timeout: 20_000 });
  await page.getByRole('link', { name: /Tools & integrations/ }).click();

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

  const sharedChatSessionId = `native-${randomUUID()}`;
  const openedChat = await fetch(`${connection.base_url}/chat/sessions/${sharedChatSessionId}`, {
    method: 'POST',
    headers: { ...headers, 'Content-Type': 'application/json' },
    body: JSON.stringify({ model: 'fixture/model', workspace: null, mode: 'observe' }),
  });
  if (openedChat.status !== 200) throw new Error(`Could not seed a shared conversation (${openedChat.status}).`);
  await page.getByRole('link', { name: 'New objective' }).click();
  const conversations = page.getByRole('region', { name: 'Shared conversation sessions' });
  await conversations.getByRole('button', { name: 'Load shared conversations' }).click();
  await conversations.getByLabel('Saved terminal and desktop sessions').selectOption(sharedChatSessionId);
  await expect(conversations.getByLabel('Conversation title')).toHaveValue(sharedChatSessionId);
  await conversations.getByLabel('Conversation title').fill('Native chat acceptance');
  const titleUpdate = await clickForResponse(page, conversations.getByRole('button', { name: 'Save title' }), (response) =>
    new URL(response.url()).pathname === `/chat/sessions/${sharedChatSessionId}`
      && response.request().method() === 'PATCH',
  );
  if (!titleUpdate.ok()) throw new Error('The native desktop title update request failed.');
  await expect(conversations.getByRole('button', { name: 'Save title' })).toBeDisabled();
  await expect(conversations.getByText('Native chat acceptance', { exact: true })).toBeVisible();
  await expect(conversations.getByRole('alert')).toHaveCount(0);
  const renamedChat = await (await fetch(`${connection.base_url}/chat/sessions/${sharedChatSessionId}`, { headers })).json();
  if (renamedChat.title !== 'Native chat acceptance') throw new Error('The native desktop did not persist the conversation title.');
  page.once('dialog', (dialog) => void dialog.accept());
  const chatDeletion = await clickForResponse(page, conversations.getByRole('button', { name: 'Delete shared conversation' }), (response) =>
    new URL(response.url()).pathname === `/chat/sessions/${sharedChatSessionId}`
      && response.request().method() === 'DELETE',
  );
  if (chatDeletion.status() !== 204) throw new Error('The native desktop conversation delete request failed.');
  await expect(conversations.getByLabel('Saved terminal and desktop sessions')).not.toContainText(sharedChatSessionId);
  const deletedChat = await fetch(`${connection.base_url}/chat/sessions/${sharedChatSessionId}`, { headers });
  if (deletedChat.status !== 404) throw new Error('The native desktop did not remove the shared conversation.');
  const remainingChats = await (await fetch(`${connection.base_url}/chat/sessions`, { headers })).json();
  if (remainingChats.some((session) => session.session_id === sharedChatSessionId)) {
    throw new Error('The deleted native desktop conversation remains in the shared-session list.');
  }
  await page.getByRole('link', { name: /Tools & integrations/ }).click();

  const panel = page.getByRole('region', { name: 'MCP integrations' });
  await page.locator('summary').filter({ hasText: 'MCP integrations' }).click();
  const pythonCommand = process.env.XAA_NATIVE_MCP_PYTHON || '.venv/Scripts/python.exe';
  const command = path.resolve(pythonCommand);
  const fixture = path.resolve('tests/fixtures/mcp_stdio_server.py');
  await panel.getByLabel('Server name').fill('Native fixture');
  await panel.getByLabel('Command').fill(command);
  await panel.getByLabel('Arguments (JSON string array)').fill(JSON.stringify([fixture]));
  await panel.getByLabel('Working directory (optional)').fill(path.resolve('.'));
  const createdResponse = await clickForResponse(
    page,
    panel.getByRole('button', { name: 'Add MCP server', exact: true }),
    (response) => new URL(response.url()).pathname === '/mcp/servers' && response.request().method() === 'POST',
  );
  if (createdResponse.status() !== 201) throw new Error(`Native MCP configuration failed (${createdResponse.status()}).`);
  const saved = await (await fetch(`${connection.base_url}/mcp/servers`, { headers })).json();
  if (saved.length !== 1 || saved[0].transport !== 'stdio' || saved[0].state !== 'disconnected') {
    throw new Error('The native API did not persist the isolated stdio MCP server.');
  }
  const serverId = saved[0].id;
  if (saved[0].memory_limit_mb !== 2048 || saved[0].cpu_limit_percent !== 80) {
    throw new Error('A new native MCP server did not receive its default Windows process limits.');
  }

  await panel.getByLabel('Memory limit for Native fixture (MiB; blank is unlimited)').fill('1536');
  await panel.getByLabel('CPU limit for Native fixture (percent; blank is unlimited)').fill('70');
  const resourceLimitsResponse = await clickForResponse(
    page,
    panel.getByRole('button', { name: 'Save process limits for Native fixture' }),
    (response) => new URL(response.url()).pathname === `/mcp/servers/${serverId}/resource-limits`
      && response.request().method() === 'PUT',
  );
  if (!resourceLimitsResponse.ok()) {
    throw new Error(`The native desktop could not save MCP process limits (${resourceLimitsResponse.status()}).`);
  }
  const updatedLimits = await resourceLimitsResponse.json();
  if (updatedLimits.memory_limit_mb !== 1536 || updatedLimits.cpu_limit_percent !== 70) {
    throw new Error('The native desktop resource-limit editor returned unexpected values.');
  }

  await panel.getByRole('button', { name: 'Connect and discover', exact: true }).click();
  await expect(panel.getByText('Add two integers.', { exact: true })).toBeVisible({ timeout: 20_000 });
  const connected = await (await fetch(`${connection.base_url}/mcp/servers`, { headers })).json();
  const discovered = connected[0].tools;
  if (connected[0].state !== 'connected' || discovered.map((tool) => tool.name).sort().join(',') !== 'add,slow') {
    throw new Error('The native MCP fixture did not expose the expected tools.');
  }
  if (connected[0].memory_limit_mb !== updatedLimits.memory_limit_mb
      || connected[0].cpu_limit_percent !== updatedLimits.cpu_limit_percent) {
    throw new Error('The native MCP connection did not retain the saved process limits.');
  }
  if (discovered.some((tool) => tool.permission !== 'none')) {
    throw new Error('A newly discovered native MCP tool was not default-deny.');
  }
  await panel.getByRole('button', { name: 'List resources', exact: true }).click();
  await expect(panel.getByText('readme', { exact: true })).toBeVisible({ timeout: 20_000 });
  const fixtureResource = panel.locator('.mcp-resource').filter({ hasText: 'xaa://fixture/readme' });
  await fixtureResource.getByRole('button', { name: 'Subscribe to updates', exact: true }).click();
  await expect(fixtureResource.getByRole('button', { name: 'Unsubscribe from updates', exact: true })).toBeVisible();
  await panel.getByRole('button', { name: 'Check for server updates', exact: true }).click();
  const nativeNotifications = await (await fetch(`${connection.base_url}/mcp/servers/${serverId}/resources/notifications`, {
    method: 'POST', headers,
  })).json();
  if (nativeNotifications.trust !== 'untrusted_mcp_output' || !Array.isArray(nativeNotifications.notifications)) {
    throw new Error('The native MCP notification drain did not return its bounded untrusted shape.');
  }
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
  const nativeTemplate = panel.locator('.mcp-resource-template').filter({ hasText: 'xaa://fixture/items/{item_id}' });
  await nativeTemplate.getByLabel('Arguments JSON for resource template item').fill('{"item_id":"native-acceptance"}');
  await nativeTemplate.getByRole('button', { name: 'Read template resource', exact: true }).click();
  await expect(panel.getByRole('complementary', { name: 'Untrusted MCP resource data' }))
    .toContainText('Fixture item: native-acceptance', { timeout: 20_000 });
  const templateRead = await (await fetch(`${connection.base_url}/mcp/servers/${serverId}/resource-templates/read`, {
    method: 'POST', headers: { ...headers, 'Content-Type': 'application/json' },
    body: JSON.stringify({ name: 'item', arguments: { item_id: 'native-api' } }),
  })).json();
  if (templateRead.trust !== 'untrusted_mcp_output' || templateRead.contents[0].text !== 'Fixture item: native-api') {
    throw new Error('The native MCP resource template read did not return untrusted fixture data.');
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
  await removeMcpServer(page, panel.getByRole('button', { name: 'Remove server', exact: true }));
  if ((await (await fetch(`${connection.base_url}/mcp/servers`, { headers })).json()).length !== 0) {
    throw new Error('The native MCP fixture configuration was not removed.');
  }

  isolationListener = net.createServer((socket) => socket.destroy());
  await new Promise((resolve, reject) => {
    isolationListener.once('error', reject);
    isolationListener.listen(0, '127.0.0.1', resolve);
  });
  const isolationAddress = isolationListener.address();
  if (!isolationAddress || typeof isolationAddress === 'string') {
    throw new Error('Could not allocate an AppContainer loopback fixture port.');
  }
  const parentCanReachLoopback = await new Promise((resolve) => {
    const socket = net.connect(isolationAddress.port, '127.0.0.1');
    socket.once('connect', () => { socket.destroy(); resolve(true); });
    socket.once('error', () => resolve(false));
  });
  if (!parentCanReachLoopback) throw new Error('The loopback fixture listener was unreachable from the desktop process.');

  const isolationFixture = path.resolve('tests/fixtures/mcp_appcontainer_list_probe.py');
  await panel.getByLabel('Server name').fill('Native isolation fixture');
  await panel.getByLabel('Command').fill(command);
  await panel.getByLabel('Arguments (JSON string array)').fill(JSON.stringify([isolationFixture, String(isolationAddress.port)]));
  await panel.getByLabel('Working directory (optional)').fill(root);
  await panel.getByLabel('Block network access for this MCP process (Windows)').check();
  const isolationCreatedResponse = await clickForResponse(
    page,
    panel.getByRole('button', { name: 'Add MCP server', exact: true }),
    (response) => new URL(response.url()).pathname === '/mcp/servers' && response.request().method() === 'POST',
  );
  if (isolationCreatedResponse.status() !== 201) {
    throw new Error(`Native AppContainer MCP configuration failed (${isolationCreatedResponse.status()}).`);
  }
  const isolationCreated = await isolationCreatedResponse.json();
  const isolationServerId = isolationCreated.id;
  const isolationSaved = await (await fetch(`${connection.base_url}/mcp/servers`, { headers })).json();
  const savedIsolationServer = isolationSaved.find((server) => server.id === isolationServerId);
  if (!savedIsolationServer || !savedIsolationServer.network_isolation || savedIsolationServer.state !== 'disconnected') {
    throw new Error('The installed desktop did not persist the AppContainer network-isolation choice.');
  }
  await panel.getByRole('button', { name: 'Connect and discover', exact: true }).click();
  let isolationServers;
  let isolatedConnection;
  const isolationDeadline = Date.now() + 30_000;
  while (Date.now() < isolationDeadline) {
    isolationServers = await (await fetch(`${connection.base_url}/mcp/servers`, { headers })).json();
    isolatedConnection = isolationServers.find((server) => server.id === isolationServerId);
    if (isolatedConnection?.state === 'connected') break;
    if (isolatedConnection?.state === 'error') {
      throw new Error(`Installed AppContainer MCP connection failed: ${isolatedConnection.error}`);
    }
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  if (isolatedConnection?.state !== 'connected') {
    throw new Error(`Installed AppContainer MCP connection timed out: ${JSON.stringify(isolatedConnection)}`);
  }
  isolationServers = await (await fetch(`${connection.base_url}/mcp/servers`, { headers })).json();
  isolatedConnection = isolationServers.find((server) => server.id === isolationServerId);
  const isolationTool = isolatedConnection?.tools.find((tool) => tool.name === 'loopback_status');
  if (isolatedConnection?.state !== 'connected' || !isolationTool?.description.includes('blocked:')) {
    throw new Error(`The installed AppContainer MCP process reached the local listener: ${JSON.stringify(isolationTool)}`);
  }
  await panel.getByRole('button', { name: 'Disconnect', exact: true }).click();
  await expect.poll(async () => {
    const servers = await (await fetch(`${connection.base_url}/mcp/servers`, { headers })).json();
    return servers.find((server) => server.id === isolationServerId)?.state;
  }).toBe('disconnected');
  await removeMcpServer(page, panel.getByRole('button', { name: 'Remove server', exact: true }));
  await new Promise((resolve, reject) => {
    isolationListener.close((error) => error ? reject(error) : resolve());
  });
  isolationListener = undefined;

  // Exercise the installed-package UI in the actual native WebView. The ZIP
  // arrives through the authenticated local API here; browser acceptance
  // separately covers the native file-picker button and archive selection.
  const packageName = 'native-fixture-package';
  const packageArchive = path.join(root, `${packageName}.zip`);
  execFileSync(command, ['-c', [
    'import json, pathlib, sys, zipfile',
    'archive, fixture, python = map(pathlib.Path, sys.argv[1:])',
    'with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as z:',
    ' z.writestr("plugin.json", json.dumps({"$schema":"https://agent-plugins.org/schemas/1.0.0/plugin.schema.json","name":"native-fixture-package","version":"1.0.0","description":"Native installed-package acceptance"}))',
    ' z.writestr("mcp.json", json.dumps({"$schema":"https://agent-plugins.org/schemas/1.0.0/mcp.schema.json","mcpServers":{"local-helper":{"type":"stdio","command":"cmd.exe","args":["/d","/c","${PLUGIN_ROOT}/run.cmd"],"cwd":"${PLUGIN_ROOT}"}}}))',
    ' z.writestr("run.cmd", "@echo off\\r\\n" + chr(34) + str(python) + chr(34) + " " + chr(34) + "%~dp0server.py" + chr(34) + "\\r\\n")',
    ' z.write(fixture, "server.py")',
  ].join('\n'), packageArchive, fixture, command], { windowsHide: true, stdio: 'pipe' });
  const packageInstall = await fetch(`${connection.base_url}/extensions/packages/install`, {
    method: 'POST', headers: { ...headers, 'Content-Type': 'application/json' },
    body: JSON.stringify({ archive_path: packageArchive }),
  });
  if (packageInstall.status !== 201) throw new Error(`Native package installation failed (${packageInstall.status}): ${await packageInstall.text()}`);
  const packageInstalled = await packageInstall.json();
  if (packageInstalled.package.name !== packageName) throw new Error('Native package install returned the wrong package identity.');
  await page.locator('summary').filter({ hasText: 'Agent plugin discovery' }).click();
  const plugins = page.getByRole('region', { name: 'Agent plugin package discovery' });
  await plugins.getByRole('button', { name: 'Show installed packages' }).click();
  const packageCard = plugins.locator('article').filter({ hasText: packageName });
  await expect(packageCard.getByRole('heading', { name: `${packageName} · 1.0.0` })).toBeVisible({ timeout: 10_000 });
  await expect(packageCard.getByText('local-helper', { exact: false })).toBeVisible();
  await packageCard.getByRole('button', { name: 'Add disconnected server' }).click();
  await expect(plugins.getByRole('status')).toContainText(`${packageName}:local-helper`);
  const packageServer = page.getByRole('region', { name: 'MCP integrations' })
    .locator('.mcp-server').filter({ hasText: `${packageName}:local-helper` });
  await expect(packageServer.getByRole('heading', { name: new RegExp(`${packageName}:local-helper.*disconnected`) })).toBeVisible();
  const installedServers = await (await fetch(`${connection.base_url}/mcp/servers`, { headers })).json();
  const packageServerState = installedServers.find((server) => server.name === `${packageName}:local-helper`);
  if (packageServerState?.state !== 'disconnected' || packageServerState.tools.length !== 0) {
    throw new Error('The native installed-package UI connected a server before explicit owner action.');
  }
  page.once('dialog', (dialog) => void dialog.accept());
  await packageCard.getByRole('button', { name: `Remove ${packageName}` }).click();
  await expect(plugins.getByRole('alert')).toContainText('Remove this package\'s MCP integrations before uninstalling it.');
  const retainedPackage = await (await fetch(`${connection.base_url}/extensions/packages/installed`, { headers })).json();
  if (!retainedPackage.packages.some((item) => item.name === packageName)) throw new Error('The native UI removed a referenced package.');
  await removeMcpServer(page, packageServer.getByRole('button', { name: 'Remove server' }));
  page.once('dialog', (dialog) => void dialog.accept());
  await packageCard.getByRole('button', { name: `Remove ${packageName}` }).click();
  await expect(plugins.getByRole('status')).toContainText('Imported skill versions and package data were kept');
  const removedPackages = await (await fetch(`${connection.base_url}/extensions/packages/installed`, { headers })).json();
  if (removedPackages.packages.some((item) => item.name === packageName)) throw new Error('The native UI did not remove its installed package.');

  const oauthPort = await new Promise((resolve, reject) => {
    const listener = net.createServer();
    listener.once('error', reject);
    listener.listen(0, '127.0.0.1', () => {
      const address = listener.address();
      if (!address || typeof address === 'string') return reject(new Error('Could not allocate OAuth fixture port.'));
      listener.close((error) => error ? reject(error) : resolve(address.port));
    });
  });
  const fixturePython = path.resolve(pythonCommand);
  const oauthFixturePath = path.resolve('tests/fixtures/mcp_oauth_http_server.py');
  oauthFixture = spawn(fixturePython, [oauthFixturePath, '--port', String(oauthPort)], {
    windowsHide: true,
    stdio: ['ignore', 'pipe', 'pipe'],
  });
  oauthFixture.stdout.on('data', (chunk) => { fixtureOutput = (fixtureOutput + chunk.toString()).slice(-16384); });
  oauthFixture.stderr.on('data', (chunk) => { fixtureOutput = (fixtureOutput + chunk.toString()).slice(-16384); });
  let fixtureReady = false;
  for (let attempt = 0; attempt < 100; attempt += 1) {
    try {
      const response = await fetch(`http://127.0.0.1:${oauthPort}/health`, { signal: AbortSignal.timeout(250) });
      if (response.ok) { fixtureReady = true; break; }
    } catch { /* fixture is still starting */ }
    if (oauthFixture.exitCode !== null) throw new Error(`Native OAuth fixture exited: ${fixtureOutput}`);
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  if (!fixtureReady) throw new Error(`Native OAuth fixture did not start: ${fixtureOutput}`);

  await panel.getByLabel('Transport').selectOption('streamable_http');
  await panel.getByLabel('Server name').fill('Native OAuth fixture');
  await panel.getByLabel('Streamable HTTP endpoint').fill(`http://127.0.0.1:${oauthPort}/mcp`);
  const oauthCreated = await clickForResponse(
    page,
    panel.getByRole('button', { name: 'Add MCP server', exact: true }),
    (response) => new URL(response.url()).pathname === '/mcp/servers' && response.request().method() === 'POST',
  );
  if (oauthCreated.status() !== 201) throw new Error('Native OAuth MCP configuration failed.');
  const oauthConnect = panel.getByRole('button', { name: 'Connect and discover', exact: true });
  await oauthConnect.click();
  const oauthDeadline = Date.now() + 120_000;
  let oauthState;
  while (Date.now() < oauthDeadline) {
    const servers = await (await fetch(`${connection.base_url}/mcp/servers`, { headers })).json();
    const current = servers.find((server) => server.name === 'Native OAuth fixture');
    oauthState = current?.state;
    if (oauthState === 'connected') break;
    if (oauthState === 'error') throw new Error(`Native OAuth connect failed: ${current.error}`);
    await new Promise((resolve) => setTimeout(resolve, 250));
  }
  if (oauthState !== 'connected') throw new Error('Native OAuth authorization did not finish within 120 seconds.');
  let oauthServers = await (await fetch(`${connection.base_url}/mcp/servers`, { headers })).json();
  let oauthServer = oauthServers.find((server) => server.name === 'Native OAuth fixture');
  if (!oauthServer?.oauth_configured || oauthServer.tools.map((tool) => tool.name).join(',') !== 'add') {
    throw new Error('Native desktop OAuth did not persist sign-in and discover the protected MCP tool.');
  }
  const oauthArticle = panel.locator('.mcp-server').filter({ hasText: 'Native OAuth fixture' });
  await oauthArticle.getByRole('button', { name: 'Disconnect', exact: true }).click();
  await oauthArticle.getByRole('button', { name: 'Connect and discover', exact: true }).click();
  await expect.poll(async () => {
    const servers = await (await fetch(`${connection.base_url}/mcp/servers`, { headers })).json();
    return servers.find((server) => server.name === 'Native OAuth fixture')?.state;
  }, { timeout: 30_000 }).toBe('connected');
  oauthServers = await (await fetch(`${connection.base_url}/mcp/servers`, { headers })).json();
  oauthServer = oauthServers.find((server) => server.name === 'Native OAuth fixture');
  if (!oauthServer?.oauth_configured) throw new Error('Native OAuth reconnect did not retain the saved vault token.');
  await oauthArticle.getByRole('button', { name: 'Clear sign-in', exact: true }).click();
  await expect.poll(async () => {
    const servers = await (await fetch(`${connection.base_url}/mcp/servers`, { headers })).json();
    return servers.find((server) => server.name === 'Native OAuth fixture')?.oauth_configured;
  }, { timeout: 10_000 }).toBe(false);
  oauthServers = await (await fetch(`${connection.base_url}/mcp/servers`, { headers })).json();
  oauthServer = oauthServers.find((server) => server.name === 'Native OAuth fixture');
  if (oauthServer?.oauth_configured || oauthServer.state !== 'disconnected') {
    throw new Error(`Native OAuth clear-sign-in left unexpected state: ${JSON.stringify(oauthServer)}`);
  }
  await removeMcpServer(page, oauthArticle.getByRole('button', { name: 'Remove server', exact: true }));
  await writeFile(path.join(root, 'oauth-fixture.log'), fixtureOutput);
  await page.locator('nav.side-nav a[href="#app-settings"]').click();
  const emergencyStop = page.getByRole('button', { name: 'Quit and stop the agent', exact: true });
  await emergencyStop.waitFor({ timeout: 10_000 });
  await emergencyStop.click().catch(() => {});
  for (let attempt = 0; attempt < 60 && proc.exitCode === null; attempt += 1) {
    await new Promise((resolve) => setTimeout(resolve, 250));
  }
  if (proc.exitCode === null) throw new Error('The native emergency-stop control did not stop the desktop.');
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
    oauth_transport: 'streamable_http',
    packaged_stage: process.env.XAA_NATIVE_DESKTOP_PATH ? 'explicit-binary-paths' : stage,
    manifest_hashes_verified: true,
    native_http_oauth: true,
    native_oauth_pkce_fixture: true,
    native_oauth_vault_persistence: true,
    native_oauth_reconnect: true,
    native_oauth_clear_sign_in: true,
    native_package_install_and_listing: true,
    native_package_mcp_import_stays_disconnected: packageServerState?.state === 'disconnected',
    native_package_removal_blocked_while_referenced: true,
    native_package_removed_after_integration_removal: true,
    real_fixture_process: true,
    native_process_limits: {
      default_memory_limit_mb: saved[0].memory_limit_mb,
      default_cpu_limit_percent: saved[0].cpu_limit_percent,
      edited_memory_limit_mb: updatedLimits.memory_limit_mb,
      edited_cpu_limit_percent: updatedLimits.cpu_limit_percent,
      retained_after_connection: connected[0].memory_limit_mb === updatedLimits.memory_limit_mb
        && connected[0].cpu_limit_percent === updatedLimits.cpu_limit_percent,
    },
    network_isolation: {
      persisted: savedIsolationServer.network_isolation,
      parent_loopback_reachable: parentCanReachLoopback,
      isolated_server_blocked: isolationTool.description.includes('blocked:'),
      disconnected_cleanly: true,
    },
    discovered_tools: discovered.map((tool) => tool.name).sort(),
    owner_resource_catalog: true,
    owner_resource_subscription: true,
    owner_resource_notification_check: nativeNotifications.trust === 'untrusted_mcp_output',
    owner_resource_read: true,
    owner_resource_template_catalog: true,
    owner_resource_template_read: templateRead.trust === 'untrusted_mcp_output',
    resource_output_labeled_untrusted: resourceRead.trust === 'untrusted_mcp_output',
    owner_prompt_catalog: true,
    owner_prompt_preview: true,
    prompt_output_labeled_untrusted: promptPreview.trust === 'untrusted_mcp_output',
    new_tool_permissions: 'none',
    selected_tool_permission: 'read',
    unrelated_tool_permission_unchanged: true,
    disconnected_cleanly: disconnected[0].state === 'disconnected',
    removed_server: true,
    native_shared_chat: {
      listed_from_frozen_api: true,
      opened_in_native_webview: true,
      title_update_visible_in_api: true,
      delete_removed_api_record: true,
      model_inference_requested: false,
    },
    model_inference_requested: false,
    desktop_exit: true,
    emergency_stop_control_verified: true,
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
  if (oauthFixture) await writeFile(path.join(root, 'oauth-fixture.log'), fixtureOutput ?? '');
  await writeFile(path.join(root, 'report.json'), JSON.stringify({ passed, error: String(error) }, null, 2));
  throw error;
} finally {
  if (isolationListener?.listening) {
    await new Promise((resolve) => isolationListener.close(resolve));
  }
  if (oauthFixture && oauthFixture.exitCode === null) {
    execFileSync(path.join(process.env.SystemRoot, 'System32', 'taskkill.exe'), ['/PID', String(oauthFixture.pid), '/T', '/F'], { windowsHide: true, stdio: 'ignore' });
  }
  await browser?.close().catch(() => {});
  if (proc.exitCode === null) {
    const taskkill = path.join(process.env.SystemRoot, 'System32', 'taskkill.exe');
    execFileSync(taskkill, ['/PID', String(proc.pid), '/T', '/F'], { windowsHide: true, stdio: 'ignore' });
  }
}
