import type { components } from '../generated/api.js';

export type RunEvent = components['schemas']['RunEvent'];

export class EventStreamDisconnected extends Error {}

export async function* decodeRunEvents(body: ReadableStream<Uint8Array>, after: number): AsyncGenerator<RunEvent> {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  let sequence = after;
  try {
    while (true) {
      const chunk = await reader.read();
      buffer += decoder.decode(chunk.value, { stream: !chunk.done });
      buffer = buffer.replace(/\r\n/g, '\n');
      if (buffer.length > 1_048_576) throw new Error('Engine event exceeds the client buffer limit.');
      let boundary;
      while ((boundary = buffer.indexOf('\n\n')) !== -1) {
        const frame = buffer.slice(0, boundary);
        buffer = buffer.slice(boundary + 2);
        const lines = frame.split('\n');
        const kind = lines.find(line => line.startsWith('event:'))?.slice(6).trim();
        if (kind === 'done') return;
        if (kind !== 'run_event') continue;
        const data = lines.filter(line => line.startsWith('data:')).map(line => line.slice(5).trimStart()).join('\n');
        const parsed: unknown = JSON.parse(data);
        if (!parsed || typeof parsed !== 'object' || !('sequence' in parsed) || !Number.isSafeInteger(parsed.sequence) || !('event_type' in parsed) || typeof parsed.event_type !== 'string' || !('payload' in parsed) || !parsed.payload || typeof parsed.payload !== 'object' || !('created_at' in parsed) || typeof parsed.created_at !== 'string') {
          throw new Error('Engine returned a malformed run event.');
        }
        const event = parsed as RunEvent;
        const wireId = lines.find(line => line.startsWith('id:'))?.slice(3).trim();
        if (wireId !== String(event.sequence)) throw new Error('Engine event ID disagrees with its sequence.');
        if (event.sequence <= sequence) continue;
        if (event.sequence !== sequence + 1) throw new Error('Engine event sequence has a gap; reconnect from the last saved cursor.');
        sequence = event.sequence;
        yield event;
      }
      if (chunk.done) {
        throw new EventStreamDisconnected('Engine event stream disconnected before completion.');
      }
    }
  } finally {
    await reader.cancel();
    reader.releaseLock();
  }
}
