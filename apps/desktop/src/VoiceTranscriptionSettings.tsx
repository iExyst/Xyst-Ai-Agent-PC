export type TranscriptionProvider = 'openai_compatible' | 'windows_offline';

export function VoiceTranscriptionSettings({
  provider,
  onProviderChange,
  endpoint,
  onEndpointChange,
  model,
  onModelChange,
}: {
  provider: TranscriptionProvider;
  onProviderChange: (provider: TranscriptionProvider) => void;
  endpoint: string;
  onEndpointChange: (endpoint: string) => void;
  model: string;
  onModelChange: (model: string) => void;
}) {
  return <>
    <label htmlFor="transcription-provider">Speech transcription</label>
    <select
      id="transcription-provider"
      value={provider}
      onChange={event => onProviderChange(event.target.value as TranscriptionProvider)}
    >
      <option value="openai_compatible">Configured local speech service</option>
      <option value="windows_offline">Windows offline speech</option>
    </select>
    {provider === 'windows_offline'
      ? <p>Uses the speech recognizer installed in Windows. Audio stays on this computer and is not sent to a cloud service. A compatible offline speech language must be installed.</p>
      : <>
        <label htmlFor="transcription-url">Local speech service URL
          <input
            id="transcription-url"
            value={endpoint}
            onChange={event => onEndpointChange(event.target.value)}
            placeholder="http://127.0.0.1:8000/v1/audio/transcriptions"
          />
        </label>
        <label htmlFor="transcription-model">Local speech model
          <input
            id="transcription-model"
            value={model}
            onChange={event => onModelChange(event.target.value)}
            maxLength={256}
            autoComplete="off"
            spellCheck={false}
            placeholder="whisper-1"
          />
        </label>
        <p>Enter the model identifier your local transcription service expects. Requires an OpenAI-compatible transcription service running on this computer. X.A.A. accepts loopback addresses only; it does not download models or provide cloud fallback.</p>
      </>}
    <p>Transcript text is untrusted reference material.</p>
  </>;
}
