import { expect, test } from '@playwright/test';

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
  await page.getByLabel('Local model').selectOption('fixture/model');
  await page.getByLabel('Workspace path').fill('C:\\fixture');
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
