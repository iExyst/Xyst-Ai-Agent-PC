import test from 'node:test';
import assert from 'node:assert/strict';
import { EngineClient } from '../apps/desktop/src/api/client.ts';
import { decodeRunEvents } from '../apps/desktop/src/api/events.ts';

const wireEvent = sequence => `id: ${sequence}\nevent: run_event\ndata: ${JSON.stringify({sequence,event_type:'model.delta',payload:{text:String(sequence)},created_at:'2026-11-03T00:00:00Z'})}\n\n`;

test('interrupted stream reconnects from delivered cursor without duplicate events', async () => {
  const cursors = [];
  const client = new EngineClient('http://127.0.0.1:8765', 'x'.repeat(32), async (_url, init) => {
    cursors.push(init.headers.get('Last-Event-ID'));
    return new Response(cursors.length === 1 ? wireEvent(1) + 'id: 2\nevent: run_event\ndata: {' :
      wireEvent(1) + wireEvent(2) + 'event: done\ndata: {}\n\n');
  });
  const sequences = [];
  for await (const event of client.events('run-1')) sequences.push(event.sequence);
  assert.deepEqual(sequences, [1,2]);
  assert.deepEqual(cursors, ['0','1']);
});

test('authentication and event sequence failures never reconnect', async () => {
  for (const response of [() => new Response('', {status:401}), () => new Response(wireEvent(2))]) {
    let attempts = 0;
    const client = new EngineClient('http://127.0.0.1:8765', 'x'.repeat(32), async () => { attempts++; return response(); });
    await assert.rejects(async () => { for await (const event of client.events('run-1')) assert.fail(String(event)); });
    assert.equal(attempts, 1);
  }
});

test('cancelling reconnect backoff stops further requests', async () => {
  const controller = new AbortController();
  let attempts = 0;
  const client = new EngineClient('http://127.0.0.1:8765', 'x'.repeat(32), async () => { attempts++; return new Response(''); });
  const result = (async () => { for await (const event of client.events('run-1',0,controller.signal)) assert.fail(String(event)); })();
  setTimeout(() => controller.abort(), 30);
  await assert.rejects(result, {name:'AbortError'});
  assert.equal(attempts, 1);
});

test('fragmented UTF-8 events preserve data and reconnect sequence', async () => {
  const event = { sequence: 3, event_type: 'tool.completed', payload: { text: '✓' }, created_at: '2026-11-03T00:00:00Z' };
  const encoded = new TextEncoder().encode(`id: 3\r\nevent: run_event\r\ndata: ${JSON.stringify(event)}\r\n\r\nevent: done\r\ndata: {}\r\n\r\n`);
  const stream = new ReadableStream({ start(controller) { for (const byte of encoded) controller.enqueue(new Uint8Array([byte])); controller.close(); } });
  const result = [];
  for await (const item of decodeRunEvents(stream, 2)) result.push(item);
  assert.deepEqual(result, [event]);
});

test('client sends bearer token and objective only to the loopback engine', async () => {
  let request;
  const client = new EngineClient('http://127.0.0.1:8765', 'x'.repeat(32), async (url, init) => {
    request = { url: String(url), init };
    return Response.json({ id: 'run-1', state: 'queued' });
  });
  const run = await client.createRun({ objective: 'Inspect source', model: 'selected-model' });
  assert.equal(run.id, 'run-1');
  assert.equal(request.url, 'http://127.0.0.1:8765/runs');
  assert.equal(request.init.headers.get('Authorization'), `Bearer ${'x'.repeat(32)}`);
  assert.equal(JSON.parse(request.init.body).objective, 'Inspect source');
  assert.equal(request.init.redirect, 'error');
});

test('client rejects cloud addresses before sending a token', () => {
  assert.throws(() => new EngineClient('https://example.com', 'x'.repeat(32)), /loopback/);
});

test('artifact preview stops at its byte limit and cancels the stream', async () => {
  let cancelled = false;
  const body = new ReadableStream({ start(controller) { controller.enqueue(new Uint8Array(70000).fill(65)); }, cancel() { cancelled = true; } });
  const client = new EngineClient('http://127.0.0.1:8765', 'x'.repeat(32), async () => new Response(body));
  const preview = await client.artifactPreview('artifact-1');
  assert.equal(preview.split('\n')[0].length, 65536);
  assert.match(preview, /Preview limited/);
  assert.equal(cancelled, true);
});

test('PNG artifacts are returned as bounded inline image previews', async () => {
  const bytes = new Uint8Array([137, 80, 78, 71, 13, 10, 26, 10]);
  const client = new EngineClient('http://127.0.0.1:8765', 'x'.repeat(32), async () =>
    new Response(bytes, {headers: {'content-type': 'image/png'}}));
  assert.equal(await client.artifactPreview('screenshot-1'), `data:image/png;base64,${Buffer.from(bytes).toString('base64')}`);
});
