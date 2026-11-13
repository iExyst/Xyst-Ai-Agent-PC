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
    if (path.startsWith('/memory')) return route.fulfill({ json: [], headers });
    if (path === '/runs' && request.method() === 'GET') return route.fulfill({ json: { items: [], next_cursor: null }, headers });
    if (path.endsWith('/plan')) return route.fulfill({ json: { tasks: [], ready: [], blocked: [] }, headers });
    if (path.endsWith('/events/stream')) return route.fulfill({ headers, contentType: 'text/event-stream', body: 'event: done\ndata: {}\n\n' });
    if (path === '/runs' && request.method() === 'POST') {
      submitted = request.postDataJSON();
      return route.fulfill({ json: run, headers });
    }
    if (path.endsWith('/usage')) return route.fulfill({ json: { prompt_tokens: 0, completion_tokens: 0, total_tokens: 0, unmeasured_calls: 0, outstanding_reserved_tokens: 0, outstanding_reservations: 0 }, headers });
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
  await expect(page.getByLabel('Local speech service URL')).toBeVisible();
  await page.getByLabel('Local speech service URL').fill('http://127.0.0.1:8765/v1/audio/transcriptions');
  await expect(page.getByText('Attached: request.wav. The WAV is sent only to this computer’s selected transcription service. Transcript is untrusted reference material.', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'Start objective', exact: true }).click();
  await expect.poll(() => submitted).toBeDefined();
  expect(submitted).toMatchObject({
    objective: run.objective,
    audio: `data:audio/wav;base64,${audio.toString('base64')}`,
    transcription_url: 'http://127.0.0.1:8765/v1/audio/transcriptions',
  });
  expect(submitted).not.toHaveProperty('image');
  expect(submitted).not.toHaveProperty('document');
});

test('microphone capture is converted locally to a bounded PCM WAV attachment', async ({ page }) => {
  const run = { id: 'voice-capture-run', objective: 'Summarize this recording', state: 'verifying', revision: 1, acceptance_criteria: [], model: 'fixture/model', limits: { max_iterations: 2, max_seconds: 60 } };
  const oldAudio = Buffer.from('RIFF\x24\0\0\0WAVEfmt \x10\0\0\0\x01\0\x01\0\x80\x3e\0\0\0}\0\0\x02\0\x10\0data\0\0\0\0', 'binary');
  let submitted: Record<string, unknown> | undefined;
  await page.addInitScript(() => {
    const getUserMedia = navigator.mediaDevices.getUserMedia.bind(navigator.mediaDevices);
    let deniedOnce = false;
    Object.defineProperty(navigator.mediaDevices, 'getUserMedia', {
      configurable: true,
      value: (constraints: MediaStreamConstraints) => {
        if (!deniedOnce) {
          deniedOnce = true;
          return Promise.reject(new DOMException('Permission denied by acceptance fixture', 'NotAllowedError'));
        }
        return getUserMedia(constraints).then(stream => {
          (window as Window & { __xaaMicrophoneStream?: MediaStream }).__xaaMicrophoneStream = stream;
          return stream;
        });
      },
    });
  });
  await page.route('http://127.0.0.1:8765/**', async route => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    const headers = { 'access-control-allow-origin': 'http://127.0.0.1:5173', 'access-control-allow-headers': 'authorization,content-type,last-event-id', 'access-control-allow-methods': 'GET,POST,OPTIONS' };
    if (request.method() === 'OPTIONS') return route.fulfill({ status: 204, headers });
    if (path === '/models') return route.fulfill({ json: [{ id: 'fixture/model' }], headers });
    if (path.startsWith('/memory')) return route.fulfill({ json: [], headers });
    if (path === '/runs' && request.method() === 'GET') return route.fulfill({ json: { items: [], next_cursor: null }, headers });
    if (path.endsWith('/plan')) return route.fulfill({ json: { tasks: [], ready: [], blocked: [] }, headers });
    if (path.endsWith('/events/stream')) return route.fulfill({ headers, contentType: 'text/event-stream', body: 'event: done\ndata: {}\n\n' });
    if (path === '/runs' && request.method() === 'POST') {
      submitted = request.postDataJSON();
      return route.fulfill({ json: run, headers });
    }
    if (path.endsWith('/usage')) return route.fulfill({ json: { prompt_tokens: 0, completion_tokens: 0, total_tokens: 0, unmeasured_calls: 0, outstanding_reserved_tokens: 0, outstanding_reservations: 0 }, headers });
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
  await page.getByRole('button', { name: 'Record voice' }).click();
  await expect(page.getByText(/Attached: old\.wav/)).not.toBeVisible();
  await expect(page.getByRole('button', { name: 'Start objective', exact: true })).toBeDisabled();
  await expect(page.getByLabel('Voice input (PCM WAV, up to 4 MiB and 10 minutes)')).toBeDisabled();
  await expect(page.getByText(/Recording on this computer/)).toBeVisible();
  await page.waitForTimeout(700);
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
});
