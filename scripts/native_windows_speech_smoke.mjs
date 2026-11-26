import { spawn, execFileSync } from 'node:child_process';
import { createServer } from 'node:net';
import { copyFile, mkdir, readFile, writeFile } from 'node:fs/promises';
import { createHash, randomUUID } from 'node:crypto';
import path from 'node:path';

if (process.platform !== 'win32') throw new Error('Frozen Windows speech acceptance requires Windows.');

const engineArgument = process.argv[2];
const reportRootArgument = process.argv[3];
const installedPackage = process.argv[4] === '--installed-package';
if (process.argv.length > 5 || (process.argv[4] !== undefined && !installedPackage) || (installedPackage && !engineArgument)) {
  throw new Error('Usage: native_windows_speech_smoke.mjs [engine-path [report-directory [--installed-package]]].');
}
const root = reportRootArgument
  ? path.resolve(reportRootArgument)
  : path.resolve('.acceptance', `native-windows-speech-${randomUUID()}`);
const bundle = path.join(root, 'bundle');
const data = path.join(root, 'data');
const systemRoot = process.env.SystemRoot || 'C:\\Windows';
const powershell = path.join(systemRoot, 'System32', 'WindowsPowerShell', 'v1.0', 'powershell.exe');
await Promise.all([root, bundle, data].map(directory => mkdir(directory, { recursive: true })));

const minimalEnvironment = Object.fromEntries(Object.entries(process.env).filter(([key]) => [
  'SYSTEMROOT', 'WINDIR', 'TEMP', 'TMP', 'USERPROFILE', 'HOMEDRIVE', 'HOMEPATH', 'USERNAME', 'USERDOMAIN',
].includes(key.toUpperCase())));
minimalEnvironment.PATH = path.join(systemRoot, 'System32');
const ttsScript = String.raw`
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.ASCIIEncoding]::new()
Add-Type -AssemblyName System.Speech
$synthesizer = [System.Speech.Synthesis.SpeechSynthesizer]::new()
$stream = [System.IO.MemoryStream]::new()
try {
    $synthesizer.SetOutputToWaveStream($stream)
    $synthesizer.Speak('Hello world.')
}
finally { $synthesizer.Dispose() }
[Console]::Out.Write([System.Convert]::ToBase64String($stream.ToArray()))
$stream.Dispose()
`;
const synthesis = await new Promise((resolve, reject) => {
  const child = spawn(powershell, ['-NoLogo', '-NoProfile', '-NonInteractive', '-Command', ttsScript], {
    windowsHide: true, stdio: ['ignore', 'pipe', 'ignore'], env: minimalEnvironment,
  });
  const chunks = [];
  child.stdout.on('data', chunk => chunks.push(chunk));
  const timeout = setTimeout(() => { child.kill(); reject(new Error('Local synthetic speech generation timed out.')); }, 60_000);
  child.once('error', error => { clearTimeout(timeout); reject(error); });
  child.once('close', code => {
    clearTimeout(timeout);
    if (code !== 0) reject(new Error('Windows could not generate the local synthetic speech fixture.'));
    else resolve(Buffer.concat(chunks));
  });
});
const audio = Buffer.from(synthesis.toString('ascii'), 'base64');
if (audio.length < 44 || audio.toString('ascii', 0, 4) !== 'RIFF' || audio.toString('ascii', 8, 12) !== 'WAVE') {
  throw new Error('Windows speech synthesis did not produce a valid WAV.');
}
await writeFile(path.join(root, 'synthetic-hello.wav'), audio);

const sourceEngine = path.resolve('apps/desktop/src-tauri/binaries/xaa-engine-x86_64-pc-windows-msvc.exe');
const engine = engineArgument ? path.resolve(engineArgument) : path.join(bundle, 'xaa-engine.exe');
if (engineArgument) {
  await readFile(engine);
} else {
  await copyFile(sourceEngine, engine);
}
const manifest = { 'xaa-engine.exe': createHash('sha256').update(await readFile(engine)).digest('hex') };
await writeFile(path.join(bundle, 'manifest.json'), JSON.stringify(manifest, null, 2));

const portProbe = createServer();
await new Promise((resolve, reject) => {
  portProbe.once('error', reject);
  portProbe.listen(0, '127.0.0.1', resolve);
});
const port = portProbe.address().port;
await new Promise((resolve, reject) => portProbe.close(error => error ? reject(error) : resolve()));
const token = `xaa-windows-speech-${randomUUID()}-${randomUUID()}`;
const childEnvironment = {
  ...minimalEnvironment,
  XAA_API_TOKEN: token,
  XAA_API_PORT: String(port),
  XAA_DATA_DIR: data,
  XAA_CREDENTIAL_NAMESPACE: `speech_${randomUUID().replaceAll('-', '').slice(0, 20)}`,
};
const engineProcess = spawn(engine, [], {
  windowsHide: true,
  stdio: ['ignore', 'pipe', 'pipe'],
  env: childEnvironment,
});
let engineOutput = '';
engineProcess.stdout.on('data', chunk => { engineOutput += chunk.toString(); });
engineProcess.stderr.on('data', chunk => { engineOutput += chunk.toString(); });
const baseUrl = `http://127.0.0.1:${port}`;
const headers = { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' };
async function api(pathname, init = {}) {
  return fetch(`${baseUrl}${pathname}`, { ...init, headers: { ...headers, ...(init.headers ?? {}) } });
}

let report;
try {
  let ready = false;
  let lastError;
  for (let attempt = 0; attempt < 120; attempt++) {
    if (engineProcess.exitCode !== null) throw new Error(`Frozen engine exited at startup (${engineProcess.exitCode}): ${engineOutput.slice(-3000)}`);
    try {
      const response = await api('/runs?limit=1', { headers: { Authorization: `Bearer ${token}` } });
      if (response.ok) { ready = true; break; }
      lastError = new Error(`API readiness returned HTTP ${response.status}.`);
    } catch (error) { lastError = error; }
    await new Promise(resolve => setTimeout(resolve, 250));
  }
  if (!ready) throw new Error(`Frozen engine API did not become ready: ${String(lastError)} ${engineOutput.slice(-3000)}`);
  const response = await api('/runs', {
    method: 'POST',
    body: JSON.stringify({
      objective: 'Transcribe this locally synthesized speech fixture.',
      audio: `data:audio/wav;base64,${audio.toString('base64')}`,
      transcription_provider: 'windows_offline',
    }),
  });
  if (response.status !== 201) throw new Error(`Frozen Windows offline speech API returned ${response.status}: ${await response.text()}`);
  const run = await response.json();
  const artifactsResponse = await api(`/runs/${encodeURIComponent(run.id)}/artifacts`, { headers: { Authorization: `Bearer ${token}` } });
  if (!artifactsResponse.ok) throw new Error(`Could not list frozen speech artifacts: ${artifactsResponse.status}`);
  const artifacts = await artifactsResponse.json();
  const transcriptArtifact = artifacts.find(item => item.media_type === 'text/plain');
  const audioArtifact = artifacts.find(item => item.media_type === 'audio/wav');
  if (!transcriptArtifact || !audioArtifact || artifacts.length !== 2) throw new Error('Frozen speech API did not retain exactly the audio and transcript artifacts.');
  const [transcriptResponse, audioResponse] = await Promise.all([
    api(`/artifacts/${transcriptArtifact.id}`, { headers: { Authorization: `Bearer ${token}` } }),
    api(`/artifacts/${audioArtifact.id}`, { headers: { Authorization: `Bearer ${token}` } }),
  ]);
  if (!transcriptResponse.ok || !audioResponse.ok) throw new Error('Could not download frozen Windows speech artifacts.');
  const transcript = await transcriptResponse.text();
  const returnedAudio = Buffer.from(await audioResponse.arrayBuffer());
  if (transcript.trim().toLowerCase() !== 'hello world') throw new Error(`Frozen Windows recognizer returned an unexpected transcript: ${transcript}`);
  if (!returnedAudio.equals(audio)) throw new Error('Frozen Windows speech API changed the source WAV bytes.');
  const usageResponse = await api(`/runs/${encodeURIComponent(run.id)}/usage`, { headers: { Authorization: `Bearer ${token}` } });
  if (!usageResponse.ok) throw new Error(`Could not verify frozen speech run usage: ${usageResponse.status}`);
  const usage = await usageResponse.json();
  if (usage.inference_attempts !== 0) throw new Error(`Offline speech acceptance unexpectedly attempted model inference (${usage.inference_attempts}).`);
  report = {
    passed: true,
    package_kind: installedPackage
      ? 'Installed Windows package engine'
      : 'PyInstaller frozen Windows engine used by native desktop bundle',
    transcription_provider: 'windows_offline',
    recognizer: 'installed Windows System.Speech recognizer',
    synthetic_phrase: 'Hello world',
    transcript,
    audio_artifact_byte_match: true,
    transcript_artifact_persisted: true,
    inference_attempts: usage.inference_attempts,
    run_id: run.id,
    network_used: false,
    model_inference_requested: false,
    engine_sha256: manifest['xaa-engine.exe'],
  };
  await writeFile(path.join(root, 'report.json'), JSON.stringify(report, null, 2));
} catch (error) {
  await writeFile(path.join(root, 'report.json'), JSON.stringify({ passed: false, error: String(error), engine_output: engineOutput.slice(-3000) }, null, 2));
  throw error;
} finally {
  if (engineProcess.exitCode === null) {
    await api('/shutdown', { method: 'POST' }).catch(() => {});
    await Promise.race([
      new Promise(resolve => engineProcess.once('exit', resolve)),
      new Promise(resolve => setTimeout(resolve, 5000)),
    ]);
  }
  if (engineProcess.exitCode === null) {
    try { execFileSync('taskkill', ['/PID', String(engineProcess.pid), '/T', '/F'], { windowsHide: true, stdio: 'ignore' }); } catch {}
  }
  console.log(report ?? { passed: false, root }, root);
}
