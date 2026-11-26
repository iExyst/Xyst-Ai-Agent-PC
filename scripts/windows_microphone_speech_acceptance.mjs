import { spawn } from 'node:child_process';
import { mkdir, writeFile } from 'node:fs/promises';
import { randomUUID } from 'node:crypto';
import path from 'node:path';

if (process.platform !== 'win32') throw new Error('Windows microphone speech acceptance requires Windows.');

const root = path.resolve('.acceptance', `windows-microphone-speech-${randomUUID()}`);
await mkdir(root, { recursive: true });
const systemRoot = process.env.SystemRoot || 'C:\\Windows';
const powershell = path.join(systemRoot, 'System32', 'WindowsPowerShell', 'v1.0', 'powershell.exe');
const environment = Object.fromEntries(Object.entries(process.env).filter(([key]) => [
  'SYSTEMROOT', 'WINDIR', 'TEMP', 'TMP', 'USERPROFILE', 'HOMEDRIVE', 'HOMEPATH', 'USERNAME', 'USERDOMAIN',
].includes(key.toUpperCase())));
environment.PATH = path.dirname(powershell);

const program = String.raw`
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
Add-Type -AssemblyName System.Speech
$recognizerInfo = [System.Speech.Recognition.SpeechRecognitionEngine]::InstalledRecognizers() | Select-Object -First 1
if ($null -eq $recognizerInfo) { throw 'No Windows speech recognizer is installed.' }
$recognizer = [System.Speech.Recognition.SpeechRecognitionEngine]::new($recognizerInfo)
try {
    $recognizer.LoadGrammar([System.Speech.Recognition.DictationGrammar]::new())
    $recognizer.InitialSilenceTimeout = [System.TimeSpan]::FromSeconds(20)
    $recognizer.BabbleTimeout = [System.TimeSpan]::FromSeconds(12)
    $recognizer.EndSilenceTimeout = [System.TimeSpan]::FromSeconds(1)
$script:audioMetrics = [System.Collections.Hashtable]::Synchronized(@{events=0; peak_level=0})
$audioHandlerBlock = {
    param($sender, $eventArgs)
    $script:audioMetrics.events = [int]$script:audioMetrics.events + 1
    $script:audioMetrics.peak_level = [Math]::Max([int]$script:audioMetrics.peak_level, [int]$eventArgs.AudioLevel)
}.GetNewClosure()
$audioHandler = [System.EventHandler[System.Speech.Recognition.AudioLevelUpdatedEventArgs]]$audioHandlerBlock
$recognizer.add_AudioLevelUpdated($audioHandler)
    $recognizer.SetInputToDefaultAudioDevice()
    [Console]::Error.WriteLine('Listening locally. Say: Hello world.')
    $result = $recognizer.Recognize([System.TimeSpan]::FromSeconds(35))
if ($null -eq $result -or [string]::IsNullOrWhiteSpace($result.Text)) {
    $failure = if ($script:audioMetrics.peak_level -eq 0) { 'silent_input' } else { 'no_speech_recognized' }
    [Console]::Out.Write((@{failure=$failure; audio_events=$script:audioMetrics.events; peak_input_level=$script:audioMetrics.peak_level} | ConvertTo-Json -Compress))
    exit 3
}
[Console]::Out.Write((@{text=$result.Text; culture=$recognizerInfo.Culture.Name; confidence=$result.Confidence; audio_events=$script:audioMetrics.events; peak_input_level=$script:audioMetrics.peak_level} | ConvertTo-Json -Compress))
}
finally { $recognizer.Dispose() }
`;
const encoded = Buffer.from(program, 'utf16le').toString('base64');
console.log('The microphone will be listened to locally for up to 35 seconds. Say “Hello world” once the listening prompt appears. The report retains only the transcript, confidence, and aggregate input-level diagnostics, not microphone audio.');

let result;
let failureCode;
try {
  result = await new Promise((resolve, reject) => {
    const child = spawn(powershell, ['-NoLogo', '-NoProfile', '-NonInteractive', '-EncodedCommand', encoded], {
      windowsHide: true,
      stdio: ['ignore', 'pipe', 'pipe'],
      env: environment,
    });
    const output = [];
    const errors = [];
    child.stdout.on('data', chunk => output.push(chunk));
    child.stderr.on('data', chunk => {
      errors.push(chunk);
      process.stderr.write(chunk);
    });
    const timeout = setTimeout(() => {
      child.kill();
      reject(new Error('Windows microphone recognition exceeded its 45-second process limit.'));
    }, 45_000);
    child.once('error', error => { clearTimeout(timeout); reject(error); });
    child.once('close', code => {
      clearTimeout(timeout);
      let parsed;
      try { parsed = JSON.parse(Buffer.concat(output).toString('utf8')); }
      catch {
        reject(new Error(`Windows microphone recognizer returned invalid output: ${Buffer.concat(errors).toString('utf8').slice(-1000)}`));
        return;
      }
      if (code === 3 && ['silent_input', 'no_speech_recognized'].includes(parsed.failure)) resolve(parsed);
      else if (code !== 0) reject(new Error(`Windows microphone recognition failed: ${Buffer.concat(errors).toString('utf8').slice(-1000)}`));
      else resolve(parsed);
    });
  });
} catch (error) {
  const message = error instanceof Error ? error.message : String(error);
  failureCode = 'recognizer_error';
}
if (failureCode === undefined && ['silent_input', 'no_speech_recognized'].includes(result?.failure)) {
  failureCode = result.failure;
}
const normalize = value => value.toLowerCase().replace(/[^a-z]+/g, ' ').trim();
const transcript = result?.text;
const report = {
  passed: transcript !== undefined && normalize(transcript) === 'hello world',
  source: 'installed Windows default microphone',
  recognizer: 'installed Windows System.Speech recognizer',
  expected_phrase: 'Hello world',
  ...(transcript === undefined ? {} : { transcript }),
  normalized_exact_match: transcript !== undefined && normalize(transcript) === 'hello world',
  ...(result === undefined ? {} : {
    audio_level_event_count: result.audio_events,
    peak_input_level_percent: result.peak_input_level,
  }),
  ...(result === undefined ? {} : {
    confidence: result.confidence,
    recognizer_language: result.culture,
  }),
  ...(failureCode === undefined ? {} : { failure_code: failureCode }),
  microphone_audio_retained: false,
  network_used: false,
  model_inference_requested: false,
};
await writeFile(path.join(root, 'report.json'), JSON.stringify(report, null, 2));
console.log(JSON.stringify({ ...report, evidence_directory: root }, null, 2));
if (!report.passed) process.exitCode = 1;
