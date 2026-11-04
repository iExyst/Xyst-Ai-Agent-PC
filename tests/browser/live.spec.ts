import { test, expect } from '@playwright/test';
import { spawn } from 'node:child_process';
import { randomUUID, randomBytes } from 'node:crypto';
import { mkdir, writeFile } from 'node:fs/promises';
import path from 'node:path';

test('live browser objective reads a fresh file through LM Studio', async ({ page, request }) => {
  test.skip(process.env.XAA_LIVE !== '1', 'Explicit live-model acceptance only');
  test.setTimeout(90000);
  const root = path.resolve('.acceptance', randomUUID());
  await mkdir(root, { recursive: true });
  const code = `XAA-${randomUUID()}`;
  await writeFile(path.join(root, 'evidence.txt'), code);
  const token = randomBytes(32).toString('hex');
  const headers = { Authorization: `Bearer ${token}` };
  const engine = spawn(path.resolve('.venv/Scripts/python.exe'), ['-m', 'xaa.api'], { windowsHide: true, stdio: 'ignore', env: { ...process.env, XAA_API_TOKEN: token, XAA_DATA_DIR: root, XAA_UI_ORIGIN: 'http://127.0.0.1:5173' } });
  let instance: string | undefined;
  try {
    await expect.poll(async () => { try { return (await request.get('http://127.0.0.1:8765/models', { headers })).status(); } catch { return 0; } }, { timeout: 20000 }).toBe(200);
    const loaded = await request.post('http://127.0.0.1:8765/models/load', { headers, data: { model: 'qwen3-0.6b', context_length: 8192 } });
    expect(loaded.ok()).toBeTruthy();
    instance = (await loaded.json()).instance_id;
    await page.goto('/');
    await page.getByLabel('Engine token').fill(token);
    await page.getByRole('button', { name: 'Connect', exact: true }).click();
    await page.getByLabel('Local model').selectOption('qwen3-0.6b');
    await page.getByLabel('Workspace path').fill(root);
    await page.getByLabel('Objective', { exact: true }).fill('Use workspace_read_file to read evidence.txt. Report the exact code stored in the file.');
    await page.getByRole('button', { name: 'Start objective' }).click();
    await expect(page.getByRole('article')).toContainText(code, { timeout: 45000 });
    await expect(page.getByRole('heading', { name: 'Active run verifying' })).toBeVisible();
    await page.screenshot({ path: path.join(root, 'live-ui.png'), fullPage: true });
    await writeFile(path.join(root, 'report.json'), JSON.stringify({ passed: true, code, model: 'qwen3-0.6b', workflow: 'browser-engine-model-file-result' }, null, 2));
  } finally {
    if (instance) await request.post('http://127.0.0.1:8765/models/unload', { headers, data: { instance_id: instance } });
    engine.kill();
    await new Promise<void>(resolve => { if (engine.exitCode !== null) resolve(); else engine.once('exit', () => resolve()); });
  }
});
