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

export function VoiceCapture({ onRecorded, onCaptureBusy, disabled }: { onRecorded: (dataUrl: string) => void; onCaptureBusy: (busy: boolean) => void; disabled: boolean }) {
  const [recording, setRecording] = useState(false);
  const [processing, setProcessing] = useState(false);
  const [error, setError] = useState('');
  const [microphones, setMicrophones] = useState<MediaDeviceInfo[]>([]);
  const [microphoneId, setMicrophoneId] = useState('');
  const [microphoneStatus, setMicrophoneStatus] = useState('');
  const streamRef = useRef<MediaStream | undefined>(undefined);
  const recorderRef = useRef<MediaRecorder | undefined>(undefined);
  const chunksRef = useRef<Blob[]>([]);
  const timeoutRef = useRef<number | undefined>(undefined);
  const discardRef = useRef(false);
  const mountedRef = useRef(false);

  function releaseMicrophone() {
    if (timeoutRef.current !== undefined) window.clearTimeout(timeoutRef.current);
    timeoutRef.current = undefined;
    streamRef.current?.getTracks().forEach(track => track.stop());
    streamRef.current = undefined;
    recorderRef.current = undefined;
    if (mountedRef.current) setRecording(false);
  }

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      discardRef.current = true;
      if (recorderRef.current?.state === 'recording') recorderRef.current.stop();
      streamRef.current?.getTracks().forEach(track => track.stop());
      if (timeoutRef.current !== undefined) window.clearTimeout(timeoutRef.current);
      onCaptureBusy(false);
    };
  }, []);

  useEffect(() => {
    if (!disabled) return;
    discardRef.current = true;
    if (recorderRef.current?.state === 'recording') recorderRef.current.stop();
    else releaseMicrophone();
    onCaptureBusy(false);
  }, [disabled]);

  async function start() {
    setError('');
    discardRef.current = false;
    chunksRef.current = [];
    onCaptureBusy(true);
    try {
      if (!navigator.mediaDevices?.getUserMedia || !window.MediaRecorder) {
        throw new Error('Microphone recording is not available in this desktop environment.');
      }
      const audio: MediaTrackConstraints = microphoneId
        ? { deviceId: { exact: microphoneId } }
        : {};
      const stream = await navigator.mediaDevices.getUserMedia({ audio });
      if (!mountedRef.current || discardRef.current) {
        stream.getTracks().forEach(track => track.stop());
        onCaptureBusy(false);
        return;
      }
      streamRef.current = stream;
      const recorder = new MediaRecorder(stream);
      recorderRef.current = recorder;
      recorder.ondataavailable = event => { if (event.data.size) chunksRef.current.push(event.data); };
      recorder.onerror = () => {
        discardRef.current = true;
        releaseMicrophone();
        onCaptureBusy(false);
        setError('Microphone recording failed. Check the device and try again.');
      };
      recorder.onstop = () => {
        releaseMicrophone();
        if (!mountedRef.current || discardRef.current || chunksRef.current.length === 0) {
          onCaptureBusy(false);
          return;
        }
        setProcessing(true);
        void (async () => {
          let context: AudioContext | undefined;
          try {
            const recordingBlob = new Blob(chunksRef.current, { type: recorder.mimeType || 'audio/webm' });
            context = new AudioContext();
            const decoded = await context.decodeAudioData(await recordingBlob.arrayBuffer());
            const wav = encodePcmWav(decoded);
            if (mountedRef.current && !discardRef.current) onRecorded(toDataUrl(wav));
          } catch (failure) {
            setError(failure instanceof Error && failure.message.includes('4 MiB')
              ? failure.message
              : 'Could not convert the recording to PCM WAV. Try a shorter recording or attach a WAV file.');
          } finally {
            await context?.close();
            if (mountedRef.current) setProcessing(false);
            onCaptureBusy(false);
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
      onCaptureBusy(false);
      if (!mountedRef.current) return;
      setError(failure instanceof Error && failure.name === 'NotAllowedError'
        ? 'Microphone permission was denied. Allow microphone access in Windows settings or attach a WAV file.'
        : failure instanceof Error ? failure.message : 'Could not start microphone recording.');
    }
  }

  function stop() {
    if (recorderRef.current?.state === 'recording') recorderRef.current.stop();
  }

  async function refreshMicrophones() {
    setError('');
    setMicrophoneStatus('');
    let temporaryStream: MediaStream | undefined;
    try {
      if (!navigator.mediaDevices?.getUserMedia || !navigator.mediaDevices.enumerateDevices) {
        throw new Error('Microphone selection is not available in this desktop environment.');
      }
      // Device names and IDs are hidden until microphone permission is granted.
      // Use a temporary stream only for enumeration, then release it immediately.
      temporaryStream = await navigator.mediaDevices.getUserMedia({ audio: true });
      temporaryStream.getTracks().forEach(track => track.stop());
      temporaryStream = undefined;
      const devices = await navigator.mediaDevices.enumerateDevices();
      const inputs = devices.filter(device => device.kind === 'audioinput');
      setMicrophones(inputs);
      setMicrophoneId(current => inputs.some(device => device.deviceId === current) ? current : '');
      setMicrophoneStatus(inputs.length
        ? `Found ${inputs.length} microphone${inputs.length === 1 ? '' : 's'}.`
        : 'No microphones were found.');
    } catch (failure) {
      temporaryStream?.getTracks().forEach(track => track.stop());
      setError(failure instanceof Error && failure.name === 'NotAllowedError'
        ? 'Microphone permission was denied. Allow microphone access in Windows settings or attach a WAV file.'
        : failure instanceof Error ? failure.message : 'Could not list microphones.');
    }
  }

  return <section className="voice-capture" aria-label="Microphone voice capture">
    <header className="voice-capture-header">
      <div><span className="voice-capture-kicker">VOICE INPUT</span><strong>Record from this device</strong><p>Choose a microphone or use the system default. Audio stays on this computer until you submit.</p></div>
      <span className={`voice-capture-state${recording ? ' is-recording' : ''}`}><i aria-hidden="true" />{recording ? 'Recording' : processing ? 'Preparing audio' : 'Ready'}</span>
    </header>
    <div className="voice-capture-controls">
      <div className="voice-capture-device">
        <button type="button" className="secondary-button" disabled={disabled || recording || processing} onClick={() => void refreshMicrophones()}>{microphones.length ? 'Refresh devices' : 'List microphones'}</button>
        {microphones.length > 0 && <label htmlFor="voice-microphone">Microphone
          <select id="voice-microphone" value={microphoneId} disabled={disabled || recording || processing} onChange={event => setMicrophoneId(event.target.value)}>
            <option value="">System default</option>
            {microphones.map((device, index) => <option key={device.deviceId} value={device.deviceId}>{device.label || `Microphone ${index + 1}`}</option>)}
          </select>
        </label>}
      </div>
      {!recording
        ? <button type="button" className="voice-capture-record" disabled={disabled || processing} onClick={() => void start()}><span aria-hidden="true">●</span>{processing ? 'Converting recording…' : 'Record voice'}</button>
        : <button type="button" className="voice-capture-stop" disabled={disabled} onClick={stop}><span aria-hidden="true">■</span>Stop recording</button>}
    </div>
    {microphoneStatus && <p className="voice-capture-note" role="status">{microphoneStatus} Choose a device or keep System default.</p>}
    {recording && <p className="voice-capture-note is-recording" role="status">Recording locally. Stop when you’re done; the 4 MiB limit stops capture automatically.</p>}
    {error && <p className="voice-capture-error" role="alert">{error}</p>}
  </section>;
}
