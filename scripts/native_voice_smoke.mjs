import { chromium, expect } from '@playwright/test';
import { spawn, execFileSync } from 'node:child_process';
import { mkdir, readFile, writeFile } from 'node:fs/promises';
import { createHash, randomUUID } from 'node:crypto';
import { createServer } from 'node:http';
import path from 'node:path';

if (process.platform !== 'win32') throw new Error('Native voice acceptance requires Windows.');

const root = path.resolve('.acceptance', `native-voice-${randomUUID()}`);
const localAppData = path.join(root, 'localappdata');
const appData = path.join(root, 'appdata');
const workspace = path.join(root, 'workspace');
const input = path.join(root, 'input');
const debugPort = Number(process.env.XAA_NATIVE_DEBUG_PORT || 19229);
const model = process.env.XAA_NATIVE_VOICE_MODEL || 'qwen2.5-0.5b-instruct';
const marker = `VOICE-${randomUUID().replaceAll('-', '').slice(0, 12).toUpperCase()}`;
const transcript = `The spoken verification code is ${marker}.`;
await Promise.all([root, localAppData, appData, workspace, input].map(p => mkdir(p, { recursive: true })));

function wavFixture() {
  const samples = 1600;
  const bytes = Buffer.alloc(44 + samples * 2);
  bytes.write('RIFF', 0); bytes.writeUInt32LE(bytes.length - 8, 4); bytes.write('WAVE', 8);
  bytes.write('fmt ', 12); bytes.writeUInt32LE(16, 16); bytes.writeUInt16LE(1, 20);
  bytes.writeUInt16LE(1, 22); bytes.writeUInt32LE(16000, 24); bytes.writeUInt32LE(32000, 28);
  bytes.writeUInt16LE(2, 32); bytes.writeUInt16LE(16, 34); bytes.write('data', 36);
  bytes.writeUInt32LE(samples * 2, 40);
  return bytes;
}

const audio = wavFixture();
const audioPath = path.join(input, 'voice-check.wav');
await writeFile(audioPath, audio);
let receivedAudio = false;
let receivedBodyBytes = 0;
const speechServer = createServer((request, response) => {
  const chunks = [];
  let size = 0;
  request.on('data', chunk => {
    size += chunk.length;
    if (size <= 1024 * 1024) chunks.push(chunk);
  });
  request.on('end', () => {
    const body = Buffer.concat(chunks);
    receivedBodyBytes = size;
    const valid = request.method === 'POST'
      && request.url === '/v1/audio/transcriptions'
      && (request.headers['content-type'] || '').includes('multipart/form-data')
      && body.includes(Buffer.from('filename="audio.wav"'))
      && body.includes(audio)
      && body.includes(Buffer.from('whisper-1'));
    receivedAudio = valid;
    const result = Buffer.from(JSON.stringify(valid ? { text: transcript } : { error: 'Invalid fixture request' }));
    response.writeHead(valid ? 200 : 400, { 'Content-Type': 'application/json', 'Content-Length': result.length });
    response.end(result);
  });
});
await new Promise((resolve, reject) => {
  speechServer.once('error', reject);
  speechServer.listen(0, '127.0.0.1', resolve);
});
const speechAddress = speechServer.address();
if (!speechAddress || typeof speechAddress === 'string') throw new Error('Local transcription fixture did not bind.');
const transcriptionUrl = `http://127.0.0.1:${speechAddress.port}/v1/audio/transcriptions`;

const nativeManifest = JSON.parse(await readFile('build/native-dev/manifest.json', 'utf8'));
const nativeEnv = Object.fromEntries(Object.entries(process.env).filter(([key]) => !['PATH', 'PYTHONPATH', 'VIRTUAL_ENV'].includes(key.toUpperCase())));
nativeEnv.PATH = path.join(process.env.SystemRoot, 'System32');
const desktop = spawn(path.resolve('build/native-dev/xaa-desktop.exe'), [], {
  windowsHide: true, stdio: 'ignore',
  env: { ...nativeEnv, APPDATA: appData, LOCALAPPDATA: localAppData, XAA_TEST_HIDDEN: '1', XAA_DATA_DIR: root,
    WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS: `--remote-debugging-port=${debugPort}` },
});
let browser, connection, ownedInstance, report;

async function api(pathname, init = {}) {
  const response = await fetch(`${connection.base_url}${pathname}`, {
    ...init, headers: { Authorization: `Bearer ${connection.token}`, ...(init.headers ?? {}) },
  });
  if (!response.ok) throw new Error(`Native voice API ${pathname} returned ${response.status}: ${await response.text()}`);
  return response;
}

async function waitForModels() {
  let lastError;
  for (let attempt = 0; attempt < 120; attempt++) {
    try { return await (await api('/models')).json(); }
    catch (error) {
      lastError = error;
      await new Promise(resolve => setTimeout(resolve, 500));
    }
  }
  throw new Error(`Bundled engine API did not become ready: ${String(lastError)}`);
}

try {
  let connected = false;
  for (let attempt = 0; attempt < 80; attempt++) {
    try {
      browser = await chromium.connectOverCDP(`http://127.0.0.1:${debugPort}`, { timeout: 1500 });
      connected = true;
      break;
    } catch {
      if (desktop.exitCode !== null) throw new Error('Native desktop exited during voice acceptance startup.');
      await new Promise(resolve => setTimeout(resolve, 250));
    }
  }
  if (!connected || !browser) throw new Error('Native WebView did not expose its test connection.');
  const page = browser.contexts()[0].pages()[0];
  await expect(page.getByRole('navigation', { name: 'Main sections' })).toBeVisible({ timeout: 20000 });
  connection = await page.evaluate(() => window.__TAURI_INTERNALS__.invoke('engine_connection'));
  const models = await waitForModels();
  const selected = models.find(item => item.id === model);
  if (!selected) throw new Error(`Local model is not installed: ${model}`);
  if (!selected.loaded) {
    const loaded = await api('/models/load', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ model, context_length: 4096 }),
    });
    ownedInstance = (await loaded.json()).instance_id;
  }
  const selection = page.waitForResponse(response =>
    response.url().endsWith('/providers/lm-studio/selection') && response.request().method() === 'PUT');
  await page.getByLabel('Local model').selectOption(model);
  await selection;
  await page.getByLabel('Workspace path').fill(workspace);
  await page.getByLabel('Objective', { exact: true }).fill('Read the attached voice note and return only its verification code.');
  await page.locator('#run-audio').setInputFiles(audioPath);
  await expect(page.getByText('Attached: voice-check.wav. The WAV is sent only to this computer’s selected transcription service. Transcript is untrusted reference material.', { exact: true })).toBeVisible({ timeout: 10000 });
  await page.getByLabel('Local speech service URL').fill(transcriptionUrl);
  await page.getByText('Run limits', { exact: true }).click();
  await page.getByLabel('Output tokens per request (optional)').fill('512');
  await page.getByRole('button', { name: 'Start objective', exact: true }).click();

  let run;
  for (let attempt = 0; attempt < 240; attempt++) {
    const history = await (await api('/runs?limit=1')).json();
    run = history.items?.[0];
    if (run && ['verifying', 'completed', 'failed', 'blocked'].includes(run.state)) break;
    await new Promise(resolve => setTimeout(resolve, 500));
  }
  if (!receivedAudio) throw new Error('Local transcription service did not receive the expected WAV multipart request.');
  if (!run || run.state !== 'verifying') throw new Error(`Voice run did not reach review: ${run?.state ?? 'missing'}`);
  const events = await (await api(`/runs/${run.id}/events`)).json();
  const messages = events.filter(event => event.event_type === 'model.message').map(event => event.payload.message);
  const answer = messages.at(-1)?.content ?? '';
  if (!answer.includes(marker)) throw new Error(`Live model did not return the exact transcript marker: ${answer}`);
  const artifacts = await (await api(`/runs/${run.id}/artifacts`)).json();
  const expected = new Map([['user.input_document', audio], ['user.input_document_text', Buffer.from(transcript)]]);
  const actual = new Map();
  for (const artifact of artifacts) {
    const bytes = Buffer.from(await (await api(`/artifacts/${artifact.id}`)).arrayBuffer());
    actual.set(artifact.provenance, bytes);
  }
  for (const [provenance, bytes] of expected) {
    if (!actual.get(provenance)?.equals(bytes)) throw new Error(`Voice artifact integrity failed for ${provenance}.`);
  }
  if (actual.size !== 2) throw new Error(`Unexpected voice artifact set: ${[...actual.keys()].join(', ')}`);
  await expect(page.getByText(marker, { exact: false }).last()).toBeVisible({ timeout: 10000 });
  await page.screenshot({ path: path.join(root, 'voice-run.png'), fullPage: true });
  report = {
    passed: true,
    fixture_transcription_service: true,
    transcription_url_loopback: transcriptionUrl,
    multipart_audio_verified: receivedAudio,
    multipart_body_bytes: receivedBodyBytes,
    wav_sha256: createHash('sha256').update(audio).digest('hex'),
    transcript_sha256: createHash('sha256').update(transcript).digest('hex'),
    artifacts_verified: [...expected.keys()],
    selected_model: model,
    live_model_returned_transcript_marker: true,
    run_id: run.id,
    run_state: run.state,
    native_build: nativeManifest,
    model_loaded_by_test: Boolean(ownedInstance),
  };
  await writeFile(path.join(root, 'report.json'), JSON.stringify(report, null, 2));
} catch (error) {
  await writeFile(path.join(root, 'report.json'), JSON.stringify({
    passed: false, error: String(error), multipart_audio_verified: receivedAudio,
  }, null, 2));
  throw error;
} finally {
  if (ownedInstance && connection) {
    await fetch(`${connection.base_url}/models/unload`, {
      method: 'POST', headers: { Authorization: `Bearer ${connection.token}`, 'Content-Type': 'application/json' },
      body: JSON.stringify({ instance_id: ownedInstance }),
    }).catch(() => {});
  }
  if (connection && desktop.exitCode === null) {
    await fetch(`${connection.base_url}/shutdown`, {
      method: 'POST', headers: { Authorization: `Bearer ${connection.token}` },
    }).catch(() => {});
  }
  await browser?.close().catch(() => {});
  if (desktop.exitCode === null) {
    try { execFileSync('taskkill', ['/PID', String(desktop.pid), '/T', '/F'], { windowsHide: true, stdio: 'ignore' }); } catch {}
  }
  await new Promise(resolve => speechServer.close(resolve));
  console.log(report ?? { passed: false, root }, root);
}
