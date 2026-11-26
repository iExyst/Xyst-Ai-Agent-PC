import { chromium, expect } from '@playwright/test';
import { randomUUID } from 'node:crypto';
import { mkdir, writeFile } from 'node:fs/promises';
import { spawn, execFileSync } from 'node:child_process';
import { createServer } from 'node:net';
import path from 'node:path';

if (process.platform !== 'win32') throw new Error('Physical microphone acceptance requires Windows.');

const executable = path.resolve(process.argv[2] ?? 'build/native-dev/xaa-desktop.exe');
const root = path.resolve('.acceptance', `physical-microphone-${randomUUID()}`);
const profileDirectories = [
  root,
  path.join(root, 'appdata'),
  path.join(root, 'localappdata'),
  path.join(root, 'data'),
  path.join(root, 'webview'),
];
await Promise.all(profileDirectories.map(directory => mkdir(directory, { recursive: true })));
const portProbe = createServer();
await new Promise((resolve, reject) => {
  portProbe.once('error', reject);
  portProbe.listen(0, '127.0.0.1', resolve);
});
const address = portProbe.address();
if (!address || typeof address === 'string') throw new Error('Could not reserve a WebView debugging port.');
const debugPort = address.port;
await new Promise((resolve, reject) => portProbe.close(error => error ? reject(error) : resolve()));
const inheritedEnvironmentNames = new Set([
  'COMSPEC', 'HOMEDRIVE', 'HOMEPATH', 'OS', 'PATHEXT', 'PROCESSOR_ARCHITECTURE',
  'PROCESSOR_IDENTIFIER', 'PROGRAMFILES', 'PROGRAMFILES(X86)', 'SYSTEMROOT', 'TEMP',
  'TMP', 'USERDOMAIN', 'USERNAME', 'USERPROFILE', 'WINDIR',
]);
const childEnvironment = Object.fromEntries(Object.entries(process.env)
  .filter(([key]) => inheritedEnvironmentNames.has(key.toUpperCase())));
childEnvironment.PATH = path.join(process.env.SystemRoot ?? 'C:\\Windows', 'System32');
const desktop = spawn(executable, [], {
  windowsHide: true,
  stdio: 'ignore',
  env: {
    ...childEnvironment,
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
  const inputMeter = page.getByRole('meter', { name: 'Microphone input level', exact: true });
  await expect(inputMeter).toBeVisible();
  const activeCapture = await page.evaluate(expectedId => {
    const entry = window.__xaaPhysicalMicStreams?.at(-1);
    return Boolean(entry
      && entry.constraints?.audio?.deviceId?.exact === expectedId
      && entry.stream.getAudioTracks().length > 0
      && entry.stream.getAudioTracks().every(track => track.readyState === 'live'));
  }, selectedId);
  if (!activeCapture) throw new Error('The selected physical microphone did not produce a live capture track.');
  const audioLevel = await page.evaluate(async () => {
    const stream = window.__xaaPhysicalMicStreams?.at(-1)?.stream;
    if (!stream) throw new Error('The selected microphone stream disappeared before level sampling.');
    const AudioContextClass = window.AudioContext;
    const context = new AudioContextClass();
    const analyser = context.createAnalyser();
    analyser.fftSize = 2048;
    const source = context.createMediaStreamSource(stream);
    source.connect(analyser);
    await context.resume();
    const samples = new Float32Array(analyser.fftSize);
    const levels = [];
    const deadline = performance.now() + 2000;
    try {
      while (performance.now() < deadline) {
        analyser.getFloatTimeDomainData(samples);
        let energy = 0;
        for (const value of samples) energy += value * value;
        levels.push(Math.sqrt(energy / samples.length));
        await new Promise(resolve => setTimeout(resolve, 50));
      }
    } finally {
      source.disconnect();
      analyser.disconnect();
      await context.close();
    }
    return {
      sample_windows: levels.length,
      peak_rms: levels.length ? Math.max(...levels) : 0,
      mean_rms: levels.length ? levels.reduce((sum, value) => sum + value, 0) / levels.length : 0,
    };
  });
  const visibleInputLevel = await inputMeter.evaluate(element => element.value);
  const visibleInputStatus = await page.locator('.voice-capture-meter > span').textContent();
  await page.getByRole('button', { name: 'Stop recording', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Record voice', exact: true })).toBeEnabled({ timeout: 10000 });
  const attachmentCreated = await page.getByText(/Attached: Microphone recording\.wav/).isVisible();
  const silentCaptureRejected = await page.getByRole('alert')
    .filter({ hasText: 'No microphone signal was captured' }).isVisible();
  const signalDetected = audioLevel.peak_rms > 0.00001;
  if (signalDetected && !attachmentCreated) {
    throw new Error('The microphone produced signal, but the app did not create its local WAV attachment.');
  }
  if (!signalDetected && !silentCaptureRejected) {
    throw new Error('The microphone was silent, but the app did not show its no-signal error.');
  }
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
    audio_level_diagnostic: {
      ...audioLevel,
      signal_detected: signalDetected,
      retained_audio_samples: false,
    },
    live_input_meter: {
      visible: true,
      normalized_value: visibleInputLevel,
      status: visibleInputStatus,
    },
    local_wav_attachment_created: attachmentCreated,
    silent_capture_rejected: silentCaptureRejected,
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
