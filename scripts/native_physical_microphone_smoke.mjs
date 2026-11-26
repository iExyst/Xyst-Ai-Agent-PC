import { chromium, expect } from '@playwright/test';
import { randomUUID } from 'node:crypto';
import { mkdir, writeFile } from 'node:fs/promises';
import { spawn, execFileSync } from 'node:child_process';
import path from 'node:path';

if (process.platform !== 'win32') throw new Error('Physical microphone acceptance requires Windows.');

const executable = path.resolve(process.argv[2] ?? 'build/native-dev/xaa-desktop.exe');
const root = path.resolve('.acceptance', `physical-microphone-${randomUUID()}`);
const debugPort = 19347;
const desktop = spawn(executable, [], {
  windowsHide: true,
  stdio: 'ignore',
  env: {
    ...process.env,
    APPDATA: path.join(root, 'appdata'),
    LOCALAPPDATA: path.join(root, 'localappdata'),
    XAA_DATA_DIR: path.join(root, 'data'),
    XAA_TEST_HIDDEN: '1',
    WEBVIEW2_USER_DATA_FOLDER: path.join(root, 'webview'),
    WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS: `--remote-debugging-port=${debugPort} --use-fake-ui-for-media-stream`,
  },
});
let browser;
let report;

try {
  await mkdir(root, { recursive: true });
  let connected = false;
  for (let attempt = 0; attempt < 120; attempt++) {
    try {
      browser = await chromium.connectOverCDP(`http://127.0.0.1:${debugPort}`, { timeout: 1000 });
      connected = true;
      break;
    } catch {
      if (desktop.exitCode !== null) throw new Error(`The native desktop exited with ${desktop.exitCode}.`);
      await new Promise(resolve => setTimeout(resolve, 250));
    }
  }
  if (!connected || !browser) throw new Error('The native desktop WebView did not become available.');
  const page = browser.contexts()[0].pages()[0];
  await expect(page.getByRole('navigation', { name: 'Main sections' })).toBeVisible({ timeout: 20000 });
  await page.evaluate(() => {
    const original = navigator.mediaDevices.getUserMedia.bind(navigator.mediaDevices);
    window.__xaaPhysicalMicStreams = [];
    Object.defineProperty(navigator.mediaDevices, 'getUserMedia', {
      configurable: true,
      value: async constraints => {
        const stream = await original(constraints);
        window.__xaaPhysicalMicStreams.push({ constraints, stream });
        return stream;
      },
    });
  });
  await page.getByRole('link', { name: /New objective/ }).click();
  await page.locator('summary').filter({ hasText: 'Permissions, attachments and run limits' }).click();
  await page.getByRole('button', { name: 'List microphones', exact: true }).click();
  const microphone = page.getByRole('combobox', { name: 'Microphone', exact: true });
  await expect(microphone).toBeVisible({ timeout: 10000 });
  const choices = await microphone.locator('option').count();
  if (choices < 2) throw new Error('Windows did not expose a physical microphone to the native desktop.');
  const selectedId = await microphone.locator('option').nth(1).getAttribute('value');
  if (!selectedId) throw new Error('The physical microphone selection had no device identifier.');
  await microphone.selectOption(selectedId);
  await page.getByRole('button', { name: 'Record voice', exact: true }).click();
  await expect(page.getByText(/Recording locally/)).toBeVisible({ timeout: 10000 });
  const activeCapture = await page.evaluate(expectedId => {
    const entry = window.__xaaPhysicalMicStreams?.at(-1);
    return Boolean(entry
      && entry.constraints?.audio?.deviceId?.exact === expectedId
      && entry.stream.getAudioTracks().length > 0
      && entry.stream.getAudioTracks().every(track => track.readyState === 'live'));
  }, selectedId);
  if (!activeCapture) throw new Error('The selected physical microphone did not produce a live capture track.');
  await new Promise(resolve => setTimeout(resolve, 2000));
  await page.getByRole('button', { name: 'Stop recording', exact: true }).click();
  await expect(page.getByText(/Attached: Microphone recording\.wav/)).toBeVisible({ timeout: 10000 });
  const tracksStopped = await page.evaluate(() => {
    const tracks = window.__xaaPhysicalMicStreams?.at(-1)?.stream.getTracks() ?? [];
    return tracks.length > 0 && tracks.every(track => track.readyState === 'ended');
  });
  if (!tracksStopped) throw new Error('The microphone stream remained active after recording stopped.');
  report = {
    passed: true,
    physical_device_enumerated: true,
    selected_device_used_for_capture: true,
    live_audio_track_observed: true,
    recording_duration_ms: 2000,
    local_wav_attachment_created: true,
    capture_tracks_stopped: true,
    audio_submitted_to_transcription: false,
    model_inference_requested: false,
    device_name_or_id_recorded: false,
  };
} catch (error) {
  report = { passed: false, error: String(error), audio_submitted_to_transcription: false };
  throw error;
} finally {
  await mkdir(root, { recursive: true });
  if (report) await writeFile(path.join(root, 'report.json'), JSON.stringify(report, null, 2));
  await browser?.close().catch(() => {});
  if (desktop.exitCode === null) {
    try { execFileSync('taskkill', ['/PID', String(desktop.pid), '/T', '/F'], { windowsHide: true, stdio: 'ignore' }); } catch {}
  }
  console.log(JSON.stringify({ ...report, evidence_directory: root }, null, 2));
}
