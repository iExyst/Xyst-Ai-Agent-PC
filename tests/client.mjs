import test from 'node:test';
import assert from 'node:assert/strict';
import { EngineClient } from '../apps/desktop/src/api/client.ts';
import { decodeRunEvents } from '../apps/desktop/src/api/events.ts';
import { suggestActiveProcedures } from '../apps/desktop/src/procedureSuggestions.ts';

test('procedure suggestions are conservative and include only active versions', () => {
  const items = [
    {id:'active',name:'Summarize Test Reports',description:'Summarize engineering test evidence',category:'testing',lifecycle_state:'active',active_version:2,latest_version:3},
    {id:'candidate',name:'Summarize Test Reports',description:'Summarize engineering test evidence',category:'testing',lifecycle_state:'candidate',active_version:null,latest_version:1},
    {id:'weak',name:'Workspace reader',description:'Read files',category:'files',lifecycle_state:'active',active_version:1,latest_version:1},
  ];
  const results = suggestActiveProcedures(items, 'Please summarize the engineering test reports');
  assert.deepEqual(results.map(result => result.item.id), ['active']);
  assert.deepEqual(results[0].matchedTerms, ['summarize','engineering','test','reports']);
  assert.deepEqual(suggestActiveProcedures(items, 'Read a workspace'), []);
});

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

test('retry budget resets after each delivered event across a long run', async () => {
  const cursors = [];
  let attempts = 0;
  const client = new EngineClient('http://127.0.0.1:8765', 'x'.repeat(32), async (_url, init) => {
    attempts++;
    cursors.push(init.headers.get('Last-Event-ID'));
    if (attempts <= 8) {
      return new Response(wireEvent(attempts) + `id: ${attempts + 1}\nevent: run_event\ndata: {`);
    }
    return new Response(wireEvent(9) + 'event: done\ndata: {}\n\n');
  });
  const sequences = [];
  for await (const event of client.events('run-long')) sequences.push(event.sequence);
  assert.deepEqual(sequences, [1,2,3,4,5,6,7,8,9]);
  assert.deepEqual(cursors, ['0','1','2','3','4','5','6','7','8']);
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

test('desktop uses the authenticated shared chat-session API', async () => {
  const requests=[];
  const client=new EngineClient('http://127.0.0.1:8765','x'.repeat(32),async(url,init)=>{
    requests.push({url:new URL(url).pathname,method:init.method??'GET',authorization:init.headers.get('Authorization'),body:init.body?JSON.parse(init.body):undefined});
    return new Response(JSON.stringify({session_id:'shared',model:'fixture/model',workspace:'C:\\fixture',mode:'observe',messages:[]}),{headers:{'content-type':'application/json'}});
  });
  await client.chatSessions();
  await client.openChatSession('shared',{model:'fixture/model',workspace:'C:\\fixture',mode:'observe'});
  await client.chatSession('shared');
  await client.appendChatTurn('shared',{run_id:'run-1',model:'fixture/model',workspace:'C:\\fixture',mode:'observe',user_message:'Continue',assistant_message:'Saved'});
  assert.deepEqual(requests.map(item=>[item.url,item.method]),[
    ['/chat/sessions','GET'],
    ['/chat/sessions/shared','POST'],
    ['/chat/sessions/shared','GET'],
    ['/chat/sessions/shared/turn','POST'],
  ]);
  assert.ok(requests.every(item=>item.authorization===`Bearer ${'x'.repeat(32)}`));
  assert.deepEqual(requests[1].body,{model:'fixture/model',workspace:'C:\\fixture',mode:'observe'});
  assert.equal(requests[3].body.user_message,'Continue');
});

test('procedure package export is authenticated and remains binary', async () => {
  let request;
  const client = new EngineClient('http://127.0.0.1:8765', 'x'.repeat(32), async (url, init) => {
    request = {url: String(url), authorization: new Headers(init.headers).get('Authorization')};
    return new Response(new Uint8Array([0x50,0x4b,0x03,0x04]), {headers:{'content-type':'application/zip'}});
  });
  const blob = await client.exportSkillPackage('skill/one', 2);
  assert.equal(request.url, 'http://127.0.0.1:8765/skills/skill%2Fone/versions/2/export');
  assert.equal(request.authorization, 'Bearer ' + 'x'.repeat(32));
  assert.deepEqual([...new Uint8Array(await blob.arrayBuffer())], [0x50,0x4b,0x03,0x04]);
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

test('PNG and JPEG artifacts are returned as bounded inline image previews', async () => {
  for (const mediaType of ['image/png', 'image/jpeg']) {
    const bytes = new Uint8Array([1, 2, 3, 4]);
    const client = new EngineClient('http://127.0.0.1:8765', 'x'.repeat(32), async () =>
      new Response(bytes, {headers: {'content-type': mediaType}}));
    assert.equal(await client.artifactPreview('image-1'), `data:${mediaType};base64,${Buffer.from(bytes).toString('base64')}`);
  }
});

test('PDF preview avoids decoding binary and directs the user to extracted text', async () => {
  let cancelled = false;
  const body = new ReadableStream({start(controller) { controller.enqueue(new Uint8Array([37, 80, 68, 70])); }, cancel() { cancelled = true; }});
  const client = new EngineClient('http://127.0.0.1:8765', 'x'.repeat(32), async () =>
    new Response(body, {headers: {'content-type': 'application/pdf'}}));
  assert.match(await client.artifactPreview('document-1'), /extracted document text/);
  assert.equal(cancelled, true);
});

test('PDF download preserves the media type and enforces the artifact byte limit', async () => {
  const client = new EngineClient('http://127.0.0.1:8765', 'x'.repeat(32), async () =>
    new Response(new Uint8Array([37, 80, 68, 70]), {headers: {'content-type': 'application/pdf'}}));
  const blob = await client.artifactDownload('document-1');
  assert.equal(blob.type, 'application/pdf');
  assert.equal(blob.size, 4);
});

test('artifact download cancels oversized responses at the configured limit', async () => {
  let cancelled = false;
  const body = new ReadableStream({
    start(controller) { controller.enqueue(new Uint8Array(8 * 1024 * 1024 + 1)); },
    cancel() { cancelled = true; },
  });
  const client = new EngineClient('http://127.0.0.1:8765', 'x'.repeat(32), async () =>
    new Response(body, {headers: {'content-type': 'application/pdf'}}));
  await assert.rejects(client.artifactDownload('large-document'), /download limit/);
  assert.equal(cancelled, true);
});
