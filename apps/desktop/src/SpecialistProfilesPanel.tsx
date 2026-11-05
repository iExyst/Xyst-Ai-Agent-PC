import { useEffect, useState } from 'react';
import type { EngineClient, SpecialistProfile } from './api/client.ts';

type Props = { client: EngineClient };

export function SpecialistProfilesPanel({ client }: Props) {
  const [profiles, setProfiles] = useState<SpecialistProfile[]>([]);
  const [models, setModels] = useState<{ id: string }[]>([]);
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const [instructionDrafts, setInstructionDrafts] = useState<Record<string, string>>({});
  const [busyRole, setBusyRole] = useState('');
  const [error, setError] = useState('');

  const refresh = async () => {
    const [nextProfiles, nextModels] = await Promise.all([
      client.specialistProfiles(),
      client.models(),
    ]);
    if (!Array.isArray(nextProfiles) || !Array.isArray(nextModels)) {
      throw new Error('Specialist profile or model list response was invalid.');
    }
    setProfiles(nextProfiles);
    setModels(nextModels.map(model => ({ id: model.id })));
    setDrafts(Object.fromEntries(nextProfiles.map(profile => [profile.role, profile.model_route ?? ''])));
    setInstructionDrafts(Object.fromEntries(nextProfiles.map(profile => [profile.role, profile.system_instructions])));
  };

  useEffect(() => { void refresh().catch(failure => setError(String(failure))); }, [client]);

  const save = async (profile: SpecialistProfile) => {
    setBusyRole(profile.role);
    setError('');
    try {
      const saved = await client.setSpecialistRoute(profile.role, drafts[profile.role] || null);
      setProfiles(current => current.map(item => item.role === saved.role ? saved : item));
      setDrafts(current => ({ ...current, [saved.role]: saved.model_route ?? '' }));
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : String(failure));
    } finally {
      setBusyRole('');
    }
  };

  const saveInstructions = async (profile: SpecialistProfile) => {
    setBusyRole(profile.role);
    setError('');
    try {
      const saved = await client.setSpecialistInstructions(profile.role, instructionDrafts[profile.role] ?? '');
      setProfiles(current => current.map(item => item.role === saved.role ? saved : item));
      setInstructionDrafts(current => ({ ...current, [saved.role]: saved.system_instructions }));
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : String(failure));
    } finally {
      setBusyRole('');
    }
  };

  return <section aria-label="Specialist profiles">
    <h3>Specialist identities and model routes</h3>
    <p>Choose a discovered local model for each role, or inherit the model selected for the parent run. Role instructions and routes are snapshotted when a task plan is saved, so edits only affect future plans. Tool permissions remain controlled by the application.</p>
    {error && <p role="alert">{error}</p>}
    {profiles.map(profile => <div key={profile.role} className="specialist-profile">
      <label>{profile.display_name} model
        <select
          aria-label={`${profile.display_name} model`}
          value={drafts[profile.role] ?? ''}
          disabled={busyRole === profile.role}
          onChange={event => setDrafts(current => ({ ...current, [profile.role]: event.target.value }))}
        >
          <option value="">Inherit parent run model</option>
          {models.map(model => <option key={model.id} value={model.id}>{model.id}</option>)}
        </select>
      </label>
      <button
        type="button"
        disabled={busyRole === profile.role || (drafts[profile.role] ?? '') === (profile.model_route ?? '')}
        onClick={() => void save(profile)}
      >Save route</button>
      <label>{profile.display_name} role instructions
        <textarea
          aria-label={`${profile.display_name} role instructions`}
          maxLength={4000}
          value={instructionDrafts[profile.role] ?? ''}
          disabled={busyRole === profile.role}
          onChange={event => setInstructionDrafts(current => ({ ...current, [profile.role]: event.target.value }))}
        />
      </label>
      <button
        type="button"
        disabled={busyRole === profile.role || (instructionDrafts[profile.role] ?? '') === profile.system_instructions}
        onClick={() => void saveInstructions(profile)}
      >Save role instructions</button>
    </div>)}
  </section>;
}
