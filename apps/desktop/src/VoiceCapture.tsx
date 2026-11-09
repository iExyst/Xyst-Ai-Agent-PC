import { useEffect, useRef, useState } from 'react';

const SAMPLE_RATE = 16_000;
const MAX_WAV_BYTES = 4 * 1024 * 1024;
const MAX_DURATION_SECONDS = Math.min(600, Math.floor((MAX_WAV_BYTES - 44) / (SAMPLE_RATE * 2)));

function encodePcmWav(buffer: AudioBuffer): Uint8Array {
  const frameCount = Math.floor(buffer.duration * SAMPLE_RATE);
  const byteLength = 44 + frameCount * 2;
  if (frameCount < 1 || byteLength > MAX_WAV_BYTES) {
    throw new Error('Recording is too large for a 4 MiB WAV. Record a shorter clip.');
  }

  const output = new ArrayBuffer(byteLength);
  const view = new DataView(output);
  const writeText = (offset: number, text: string) => {
    for (let index = 0; index < text.length; index++) view.setUint8(offset + index, text.charCodeAt(index));
  };
  writeText(0, 'RIFF');
  view.setUint32(4, byteLength - 8, true);
  writeText(8, 'WAVE');
  writeText(12, 'fmt ');
  view.setUint32(16, 16, true);
  view.setUint16(20, 1, true);
  view.setUint16(22, 1, true);
  view.setUint32(24, SAMPLE_RATE, true);
  view.setUint32(28, SAMPLE_RATE * 2, true);
  view.setUint16(32, 2, true);
  view.setUint16(34, 16, true);
  writeText(36, 'data');
  view.setUint32(40, frameCount * 2, true);

  const channels = Array.from({ length: buffer.numberOfChannels }, (_, channel) => buffer.getChannelData(channel));
  for (let frame = 0; frame < frameCount; frame++) {
    const start = Math.floor((frame * buffer.sampleRate) / SAMPLE_RATE);
    const end = Math.min(buffer.length, Math.max(start + 1, Math.floor(((frame + 1) * buffer.sampleRate) / SAMPLE_RATE)));
    let sample = 0;
    let count = 0;
    for (const channel of channels) {
      for (let source = start; source < end; source++) {
        sample += channel[source] ?? 0;
        count++;
      }
    }
    const value = Math.max(-1, Math.min(1, count ? sample / count : 0));
    view.setInt16(44 + frame * 2, value < 0 ? value * 0x8000 : value * 0x7fff, true);
  }
  return new Uint8Array(output);
}

function toDataUrl(bytes: Uint8Array): string {
  let binary = '';
  const chunkSize = 0x8000;
  for (let offset = 0; offset < bytes.length; offset += chunkSize) {
    binary += String.fromCharCode(...bytes.subarray(offset, Math.min(offset + chunkSize, bytes.length)));
  }
  return `data:audio/wav;base64,${btoa(binary)}`;
}

export function VoiceCapture({ onRecorded, disabled }: { onRecorded: (dataUrl: string) => void; disabled: boolean }) {
  const [recording, setRecording] = useState(false);
  const [error, setError] = useState('');
  const streamRef = useRef<MediaStream | undefined>(undefined);
  const recorderRef = useRef<MediaRecorder | undefined>(undefined);
  const chunksRef = useRef<Blob[]>([]);
  const timeoutRef = useRef<number | undefined>(undefined);
  const discardRef = useRef(false);

  function releaseMicrophone() {
    if (timeoutRef.current !== undefined) window.clearTimeout(timeoutRef.current);
    timeoutRef.current = undefined;
    streamRef.current?.getTracks().forEach(track => track.stop());
    streamRef.current = undefined;
    recorderRef.current = undefined;
    setRecording(false);
  }

  useEffect(() => () => {
    discardRef.current = true;
    if (recorderRef.current?.state === 'recording') recorderRef.current.stop();
    streamRef.current?.getTracks().forEach(track => track.stop());
    if (timeoutRef.current !== undefined) window.clearTimeout(timeoutRef.current);
  }, []);

  async function start() {
    setError('');
    discardRef.current = false;
    chunksRef.current = [];
    try {
      if (!navigator.mediaDevices?.getUserMedia || !window.MediaRecorder) {
        throw new Error('Microphone recording is not available in this desktop environment.');
      }
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      streamRef.current = stream;
      const recorder = new MediaRecorder(stream);
      recorderRef.current = recorder;
      recorder.ondataavailable = event => { if (event.data.size) chunksRef.current.push(event.data); };
      recorder.onerror = () => {
        discardRef.current = true;
        releaseMicrophone();
        setError('Microphone recording failed. Check the device and try again.');
      };
      recorder.onstop = () => {
        releaseMicrophone();
        if (discardRef.current || chunksRef.current.length === 0) return;
        void (async () => {
          let context: AudioContext | undefined;
          try {
            const recordingBlob = new Blob(chunksRef.current, { type: recorder.mimeType || 'audio/webm' });
            context = new AudioContext();
            const decoded = await context.decodeAudioData(await recordingBlob.arrayBuffer());
            const wav = encodePcmWav(decoded);
            onRecorded(toDataUrl(wav));
          } catch (failure) {
            setError(failure instanceof Error && failure.message.includes('4 MiB')
              ? failure.message
              : 'Could not convert the recording to PCM WAV. Try a shorter recording or attach a WAV file.');
          } finally {
            await context?.close();
          }
        })();
      };
      recorder.start(500);
      setRecording(true);
      timeoutRef.current = window.setTimeout(() => {
        if (recorder.state === 'recording') recorder.stop();
      }, MAX_DURATION_SECONDS * 1000);
    } catch (failure) {
      releaseMicrophone();
      setError(failure instanceof Error && failure.name === 'NotAllowedError'
        ? 'Microphone permission was denied. Allow microphone access in Windows settings or attach a WAV file.'
        : failure instanceof Error ? failure.message : 'Could not start microphone recording.');
    }
  }

  function stop() {
    if (recorderRef.current?.state === 'recording') recorderRef.current.stop();
  }

  return <div aria-label="Microphone voice capture">
    {!recording
      ? <button type="button" disabled={disabled} onClick={() => void start()}>Record voice</button>
      : <button type="button" disabled={disabled} onClick={stop}>Stop recording</button>}
    {recording && <p role="status">Recording on this computer. The microphone stops when you finish or reach the 4 MiB recording limit.</p>}
    {error && <p role="alert">{error}</p>}
  </div>;
}
