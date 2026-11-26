import { expect, test } from '@playwright/test';

test.use({
  permissions: ['microphone'],
  launchOptions: { args: ['--use-fake-device-for-media-stream', '--use-fake-ui-for-media-stream'] },
});

test('voice attachment submits WAV and the selected local transcription endpoint', async ({ page }) => {
  const run = { id: 'voice-run', objective: 'Summarize this recording', state: 'verifying', revision: 1, acceptance_criteria: [], model: 'fixture/model', limits: { max_iterations: 2, max_seconds: 60 } };
  const audio = Buffer.from('RIFF\x24\0\0\0WAVEfmt \x10\0\0\0\x01\0\x01\0\x80\x3e\0\0\0}\0\0\x02\0\x10\0data\0\0\0\0', 'binary');
  let submitted: Record<string, unknown> | undefined;
  await page.route('http://127.0.0.1:8765/**', async route => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    const headers = { 'access-control-allow-origin': 'http://127.0.0.1:5173', 'access-control-allow-headers': 'authorization,content-type,last-event-id', 'access-control-allow-methods': 'GET,POST,OPTIONS' };
    if (request.method() === 'OPTIONS') return route.fulfill({ status: 204, headers });
    if (path === '/models') return route.fulfill({ json: [{ id: 'fixture/model' }], headers });
    if (path === '/specialists/profiles') return route.fulfill({ json: [], headers });
    if (path.startsWith('/memory')) return route.fulfill({ json: [], headers });
    if (path === '/runs' && request.method() === 'GET') return route.fulfill({ json: { items: [], next_cursor: null }, headers });
    if (path.endsWith('/plan')) return route.fulfill({ json: { tasks: [], ready: [], blocked: [] }, headers });
    if (path.endsWith('/events/stream')) return route.fulfill({ headers, contentType: 'text/event-stream', body: 'event: done\ndata: {}\n\n' });
    if (path === '/runs' && request.method() === 'POST') {
      submitted = request.postDataJSON();
      return route.fulfill({ json: run, headers });
    }
    if (path.endsWith('/usage')) return route.fulfill({ json: { inference_attempts: 0, prompt_tokens: 0, completion_tokens: 0, total_tokens: 0, unmeasured_calls: 0, outstanding_reserved_tokens: 0, outstanding_reservations: 0 }, headers });
    return route.fulfill({ json: run, headers });
  });

  const voiceModuleRequests: string[] = [];
  page.on('request', request => { if (request.url().includes('VoiceCapture')) voiceModuleRequests.push(request.url()); });
  await page.goto('/');
  await expect.poll(() => voiceModuleRequests).toHaveLength(0);
  await page.getByLabel('Engine token').fill('x'.repeat(32));
  await page.getByRole('button', { name: 'Connect', exact: true }).click();
  await page.getByRole('navigation',{name:'Main sections'}).getByRole('link',{name:'Model setup'}).click();
  await page.getByLabel('Local model').selectOption('fixture/model');
  await page.getByRole('navigation',{name:'Main sections'}).getByRole('link',{name:'New objective'}).click();
  await page.getByLabel('Workspace path').fill('C:\\fixture');
  await page.locator('details.advanced-options').evaluate(element => { (element as HTMLDetailsElement).open=true; });
  await expect.poll(() => voiceModuleRequests.length).toBeGreaterThan(0);
  await page.getByLabel('Objective', { exact: true }).fill(run.objective);
  await page.getByLabel('Voice input (PCM WAV, up to 4 MiB and 10 minutes)').setInputFiles({ name: 'request.wav', mimeType: 'audio/wav', buffer: audio });
  await expect(page.getByLabel('Speech transcription')).toHaveValue('openai_compatible');
  await expect(page.getByLabel('Local speech service URL')).toBeVisible();
  await page.getByLabel('Local speech service URL').fill('http://127.0.0.1:8765/v1/audio/transcriptions');
  await expect(page.getByText('Attached: request.wav. Audio is processed with the selected local speech recognizer. Transcript text is untrusted reference material.', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'Start objective', exact: true }).click();
  await expect.poll(() => submitted).toBeDefined();
  expect(submitted).toMatchObject({
    objective: run.objective,
    audio: `data:audio/wav;base64,${audio.toString('base64')}`,
    transcription_provider: 'openai_compatible',
    transcription_url: 'http://127.0.0.1:8765/v1/audio/transcriptions',
  });
  expect(submitted).not.toHaveProperty('image');
  expect(submitted).not.toHaveProperty('document');
});

test('Windows offline speech is an explicit local option and sends no service URL', async ({ page }) => {
  const run = { id: 'windows-voice-run', objective: 'Transcribe this recording', state: 'verifying', revision: 1, acceptance_criteria: [], model: 'fixture/model', limits: { max_iterations: 2, max_seconds: 60 } };
  const audio = Buffer.from('RIFF\x24\0\0\0WAVEfmt \x10\0\0\0\x01\0\x01\0\x80\x3e\0\0\0}\0\0\x02\0\x10\0data\0\0\0\0', 'binary');
  let submitted: Record<string, unknown> | undefined;
  await page.route('http://127.0.0.1:8765/**', async route => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    const headers = { 'access-control-allow-origin': 'http://127.0.0.1:5173', 'access-control-allow-headers': 'authorization,content-type,last-event-id', 'access-control-allow-methods': 'GET,POST,OPTIONS' };
    if (request.method() === 'OPTIONS') return route.fulfill({ status: 204, headers });
    if (path === '/models') return route.fulfill({ json: [{ id: 'fixture/model' }], headers });
    if (path === '/specialists/profiles') return route.fulfill({ json: [], headers });
    if (path.startsWith('/memory')) return route.fulfill({ json: [], headers });
    if (path === '/runs' && request.method() === 'GET') return route.fulfill({ json: { items: [], next_cursor: null }, headers });
    if (path.endsWith('/plan')) return route.fulfill({ json: { tasks: [], ready: [], blocked: [] }, headers });
    if (path.endsWith('/events/stream')) return route.fulfill({ headers, contentType: 'text/event-stream', body: 'event: done\ndata: {}\n\n' });
    if (path === '/runs' && request.method() === 'POST') {
      submitted = request.postDataJSON();
      return route.fulfill({ json: run, headers });
    }
    if (path.endsWith('/usage')) return route.fulfill({ json: { inference_attempts: 0, prompt_tokens: 0, completion_tokens: 0, total_tokens: 0, unmeasured_calls: 0, outstanding_reserved_tokens: 0, outstanding_reservations: 0 }, headers });
    return route.fulfill({ json: run, headers });
  });
  await page.goto('/');
  await page.getByLabel('Engine token').fill('x'.repeat(32));
  await page.getByRole('button', { name: 'Connect', exact: true }).click();
  await page.getByRole('navigation',{name:'Main sections'}).getByRole('link',{name:'Model setup'}).click();
  await page.getByLabel('Local model').selectOption('fixture/model');
  await page.getByRole('navigation',{name:'Main sections'}).getByRole('link',{name:'New objective'}).click();
  await page.getByLabel('Workspace path').fill('C:\\fixture');
  await page.locator('details.advanced-options').evaluate(element => { (element as HTMLDetailsElement).open=true; });
  await page.getByLabel('Objective', { exact: true }).fill(run.objective);
  await page.getByLabel('Voice input (PCM WAV, up to 4 MiB and 10 minutes)').setInputFiles({ name: 'request.wav', mimeType: 'audio/wav', buffer: audio });
  await page.getByLabel('Speech transcription').selectOption('windows_offline');
  await expect(page.getByText(/speech recognizer installed in Windows/)).toBeVisible();
  await expect(page.getByLabel('Local speech service URL')).toHaveCount(0);
  await page.getByRole('button', { name: 'Start objective', exact: true }).click();
  await expect.poll(() => submitted).toBeDefined();
  expect(submitted).toMatchObject({
    objective: run.objective,
    audio: `data:audio/wav;base64,${audio.toString('base64')}`,
    transcription_provider: 'windows_offline',
  });
  expect(submitted).not.toHaveProperty('transcription_url');
});

test('microphone capture is converted locally to a bounded PCM WAV attachment', async ({ page }) => {
  const run = { id: 'voice-capture-run', objective: 'Summarize this recording', state: 'verifying', revision: 1, acceptance_criteria: [], model: 'fixture/model', limits: { max_iterations: 2, max_seconds: 60 } };
  const oldAudio = Buffer.from('RIFF\x24\0\0\0WAVEfmt \x10\0\0\0\x01\0\x01\0\x80\x3e\0\0\0}\0\0\x02\0\x10\0data\0\0\0\0', 'binary');
  let submitted: Record<string, unknown> | undefined;
  await page.addInitScript(() => {
    const audioContextPrototype = AudioContext.prototype;
    const decodeAudioData = audioContextPrototype.decodeAudioData;
    const windowWithAudioFixture = window as Window & { __xaaForceSilentDecode?: boolean };
    audioContextPrototype.decodeAudioData = function (...args: Parameters<typeof decodeAudioData>) {
      if (windowWithAudioFixture.__xaaForceSilentDecode) {
        return Promise.resolve(new AudioBuffer({ length: 16_000, numberOfChannels: 1, sampleRate: 16_000 }));
      }
      return decodeAudioData.apply(this, args);
    };
    const getUserMedia = navigator.mediaDevices.getUserMedia.bind(navigator.mediaDevices);
    let deniedOnce = false;
    const microphoneRequests: MediaStreamConstraints[] = [];
    (window as Window & { __xaaMicrophoneRequests?: MediaStreamConstraints[] }).__xaaMicrophoneRequests = microphoneRequests;
    Object.defineProperty(navigator.mediaDevices, 'getUserMedia', {
      configurable: true,
      value: (constraints: MediaStreamConstraints) => {
        microphoneRequests.push(constraints);
        if (!deniedOnce) {
          deniedOnce = true;
          return Promise.reject(new DOMException('Permission denied by acceptance fixture', 'NotAllowedError'));
        }
        // The fixture exposes its own stable IDs for selection coverage; the
        // browser's fake device only has its built-in synthetic input.
        return getUserMedia({ audio: true }).then(stream => {
          (window as Window & { __xaaMicrophoneStream?: MediaStream }).__xaaMicrophoneStream = stream;
          return stream;
        });
      },
    });
    Object.defineProperty(navigator.mediaDevices, 'enumerateDevices', {
      configurable: true,
      value: async () => [
        { deviceId: 'builtin-mic', groupId: 'fixture', kind: 'audioinput', label: 'Built-in microphone', toJSON: () => ({}) },
        { deviceId: 'usb-headset', groupId: 'fixture', kind: 'audioinput', label: 'USB headset microphone', toJSON: () => ({}) },
      ],
    });
  });
  await page.route('http://127.0.0.1:8765/**', async route => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    const headers = { 'access-control-allow-origin': 'http://127.0.0.1:5173', 'access-control-allow-headers': 'authorization,content-type,last-event-id', 'access-control-allow-methods': 'GET,POST,OPTIONS' };
    if (request.method() === 'OPTIONS') return route.fulfill({ status: 204, headers });
    if (path === '/models') return route.fulfill({ json: [{ id: 'fixture/model' }], headers });
    if (path === '/specialists/profiles') return route.fulfill({ json: [], headers });
    if (path.startsWith('/memory')) return route.fulfill({ json: [], headers });
    if (path === '/runs' && request.method() === 'GET') return route.fulfill({ json: { items: [], next_cursor: null }, headers });
    if (path.endsWith('/plan')) return route.fulfill({ json: { tasks: [], ready: [], blocked: [] }, headers });
    if (path.endsWith('/events/stream')) return route.fulfill({ headers, contentType: 'text/event-stream', body: 'event: done\ndata: {}\n\n' });
    if (path === '/runs' && request.method() === 'POST') {
      submitted = request.postDataJSON();
      return route.fulfill({ json: run, headers });
    }
    if (path.endsWith('/usage')) return route.fulfill({ json: { inference_attempts: 0, prompt_tokens: 0, completion_tokens: 0, total_tokens: 0, unmeasured_calls: 0, outstanding_reserved_tokens: 0, outstanding_reservations: 0 }, headers });
    return route.fulfill({ json: run, headers });
  });

  await page.goto('/');
  await page.getByLabel('Engine token').fill('x'.repeat(32));
  await page.getByRole('button', { name: 'Connect', exact: true }).click();
  await page.getByRole('navigation',{name:'Main sections'}).getByRole('link',{name:'Model setup'}).click();
  await page.getByLabel('Local model').selectOption('fixture/model');
  await page.getByRole('navigation',{name:'Main sections'}).getByRole('link',{name:'New objective'}).click();
  await page.getByLabel('Workspace path').fill('C:\\fixture');
  await page.locator('details.advanced-options').evaluate(element => { (element as HTMLDetailsElement).open=true; });
  await page.getByLabel('Objective', { exact: true }).fill(run.objective);
  await page.getByLabel('Voice input (PCM WAV, up to 4 MiB and 10 minutes)').setInputFiles({ name: 'old.wav', mimeType: 'audio/wav', buffer: oldAudio });
  await expect(page.getByText(/Attached: old\.wav/)).toBeVisible();
  await page.getByRole('button', { name: 'Record voice' }).click();
  await expect(page.getByRole('alert')).toContainText('Microphone permission was denied');
  await expect(page.getByRole('button', { name: 'Record voice' })).toBeEnabled();
  await page.getByRole('button', { name: 'List microphones' }).click();
  await expect(page.getByRole('status').filter({ hasText: 'Found 2 microphones.' })).toBeVisible();
  await page.getByRole('combobox', { name: 'Microphone', exact: true }).selectOption('usb-headset');
  await page.getByRole('button', { name: 'Record voice' }).click();
  await expect.poll(() => page.evaluate(() => {
    return (window as Window & { __xaaMicrophoneRequests?: MediaStreamConstraints[] }).__xaaMicrophoneRequests ?? [];
  })).toContainEqual({ audio: { deviceId: { exact: 'usb-headset' } } });
  await expect(page.getByText(/Attached: old\.wav/)).not.toBeVisible();
  await expect(page.getByRole('button', { name: 'Start objective', exact: true })).toBeDisabled();
  await expect(page.getByLabel('Voice input (PCM WAV, up to 4 MiB and 10 minutes)')).toBeDisabled();
  await expect(page.getByText(/Recording locally/)).toBeVisible();
  const inputMeter = page.getByRole('meter', { name: 'Microphone input level' });
  await expect(inputMeter).toBeVisible();
  await expect.poll(() => inputMeter.evaluate(element => element.value)).toBeGreaterThan(0);
  await expect(page.getByText(/Microphone signal detected|No input yet/)).toBeVisible();
  // Give the browser more than one MediaRecorder timeslice to deliver data
  // before stopping; sub-second recordings were flaky on hosted Windows Edge.
  await page.waitForTimeout(1500);
  await page.getByRole('button', { name: 'Stop recording' }).click();
  await expect(page.getByText(/Attached: Microphone recording\.wav/)).toBeVisible();
  await expect.poll(() => page.evaluate(() => {
    const tracks = (window as Window & { __xaaMicrophoneStream?: MediaStream }).__xaaMicrophoneStream?.getTracks() ?? [];
    return tracks.length > 0 && tracks.every(track => track.readyState === 'ended');
  })).toBe(true);
  await expect(page.getByRole('button', { name: 'Start objective', exact: true })).toBeEnabled();
  await page.getByLabel('Local speech service URL').fill('http://127.0.0.1:8765/v1/audio/transcriptions');
  await page.getByRole('button', { name: 'Start objective', exact: true }).click();
  await expect.poll(() => submitted).toBeDefined();
  const dataUrl = submitted?.audio;
  expect(typeof dataUrl).toBe('string');
  const encoded = (dataUrl as string).replace('data:audio/wav;base64,', '');
  const wav = Buffer.from(encoded, 'base64');
  expect(wav.subarray(0, 4).toString()).toBe('RIFF');
  expect(wav.subarray(8, 12).toString()).toBe('WAVE');
  expect(wav.readUInt16LE(22)).toBe(1);
  expect(wav.readUInt32LE(24)).toBe(16_000);
  expect(wav.readUInt16LE(34)).toBe(16);
  expect(wav.length).toBeLessThanOrEqual(4 * 1024 * 1024);
  expect(submitted).toMatchObject({ transcription_url: 'http://127.0.0.1:8765/v1/audio/transcriptions' });

  await page.getByRole('navigation', { name: 'Main sections' }).getByRole('link', { name: 'New objective' }).click();
  await page.locator('details.advanced-options').evaluate(element => { (element as HTMLDetailsElement).open = true; });
  await page.evaluate(() => { (window as Window & { __xaaForceSilentDecode?: boolean }).__xaaForceSilentDecode = true; });
  await page.getByRole('button', { name: 'Record voice' }).click();
  await expect(page.getByText(/Recording locally/)).toBeVisible();
  await page.waitForTimeout(1500);
  await page.getByRole('button', { name: 'Stop recording' }).click();
  await expect(page.getByRole('alert')).toContainText('No microphone signal was captured');
});

test('a microphone permission response arriving after leaving the composer is immediately stopped', async ({ page }) => {
  await page.addInitScript(() => {
    const nativeGetUserMedia = navigator.mediaDevices.getUserMedia.bind(navigator.mediaDevices);
    let resolveRequest: ((stream: MediaStream) => void) | undefined;
    Object.defineProperty(navigator.mediaDevices, 'getUserMedia', {
      configurable: true,
      value: () => new Promise<MediaStream>(resolve => { resolveRequest = resolve; }),
    });
    Object.defineProperty(window, '__xaaGrantLateMicrophone', {
      configurable: true,
      value: async () => {
        const stream = await nativeGetUserMedia({ audio: true });
        (window as Window & { __xaaLateMicrophone?: MediaStream }).__xaaLateMicrophone = stream;
        resolveRequest?.(stream);
      },
    });
  });
  await page.route('http://127.0.0.1:8765/**', async route => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    const headers = { 'access-control-allow-origin': 'http://127.0.0.1:5173', 'access-control-allow-headers': 'authorization,content-type,last-event-id', 'access-control-allow-methods': 'GET,POST,OPTIONS' };
    if (request.method() === 'OPTIONS') return route.fulfill({ status: 204, headers });
    if (path === '/models') return route.fulfill({ json: [{ id: 'fixture/model' }], headers });
    if (path === '/specialists/profiles') return route.fulfill({ json: [], headers });
    if (path.startsWith('/memory')) return route.fulfill({ json: [], headers });
    if (path === '/runs' && request.method() === 'GET') return route.fulfill({ json: { items: [], next_cursor: null }, headers });
    if (path.endsWith('/plan')) return route.fulfill({ json: { tasks: [], ready: [], blocked: [] }, headers });
    return route.fulfill({ json: [], headers });
  });
  await page.goto('/');
  await page.getByLabel('Engine token').fill('x'.repeat(32));
  await page.getByRole('button', { name: 'Connect', exact: true }).click();
  await page.getByRole('navigation', { name: 'Main sections' }).getByRole('link', { name: 'Model setup' }).click();
  await page.getByLabel('Local model').selectOption('fixture/model');
  await page.getByRole('navigation', { name: 'Main sections' }).getByRole('link', { name: 'New objective' }).click();
  await page.getByLabel('Workspace path').fill('C:\\fixture');
  await page.locator('details.advanced-options').evaluate(element => { (element as HTMLDetailsElement).open = true; });
  await page.getByRole('button', { name: 'Record voice' }).click();
  await page.getByRole('navigation', { name: 'Main sections' }).getByRole('link', { name: 'Model setup' }).click();
  await page.evaluate(async () => {
    await (window as Window & { __xaaGrantLateMicrophone: () => Promise<void> }).__xaaGrantLateMicrophone();
  });
  await expect.poll(() => page.evaluate(() => {
    const tracks = (window as Window & { __xaaLateMicrophone?: MediaStream }).__xaaLateMicrophone?.getTracks() ?? [];
    return tracks.length > 0 && tracks.every(track => track.readyState === 'ended');
  })).toBe(true);
});
