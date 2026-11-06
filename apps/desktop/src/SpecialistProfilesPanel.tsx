import { useEffect, useState } from 'react';
import type { EngineClient, SpecialistProfile } from './api/client.ts';

type Props = { client: EngineClient };

export function SpecialistProfilesPanel({ client }: Props) {
  const [profiles, setProfiles] = useState<SpecialistProfile[]>([]);
  const [models, setModels] = useState<{ id: string }[]>([]);
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const [identityDrafts, setIdentityDrafts] = useState<Record<string, string>>({});
  const [instructionDrafts, setInstructionDrafts] = useState<Record<string, string>>({});
  const [memoryDrafts, setMemoryDrafts] = useState<Record<string, string>>({});
  const [memories, setMemories] = useState<Record<string, Awaited<ReturnType<EngineClient['specialistMemory']>>>>({});
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
    setIdentityDrafts(Object.fromEntries(nextProfiles.map(profile => [profile.role, profile.display_name])));
    setInstructionDrafts(Object.fromEntries(nextProfiles.map(profile => [profile.role, profile.system_instructions])));
    const roleMemories = await Promise.all(nextProfiles.map(async profile => [profile.role, await client.specialistMemory(profile.role)] as const));
    setMemories(Object.fromEntries(roleMemories));
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

  const saveIdentity = async (profile: SpecialistProfile) => {
    setBusyRole(profile.role);
    setError('');
    try {
      const saved = await client.setSpecialistIdentity(profile.role, identityDrafts[profile.role] ?? '');
      setProfiles(current => current.map(item => item.role === saved.role ? saved : item));
      setIdentityDrafts(current => ({ ...current, [saved.role]: saved.display_name }));
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

  const saveMemory = async (profile: SpecialistProfile) => {
    setBusyRole(profile.role);
    setError('');
    try {
      await client.saveSpecialistMemory(profile.role, memoryDrafts[profile.role] ?? '');
      setMemoryDrafts(current => ({ ...current, [profile.role]: '' }));
      const next = await client.specialistMemory(profile.role);
      setMemories(current => ({ ...current, [profile.role]: next }));
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : String(failure));
    } finally {
      setBusyRole('');
    }
  };

  const deleteMemory = async (profile: SpecialistProfile, id: string) => {
    setBusyRole(profile.role);
    setError('');
    try {
      await client.deleteSpecialistMemory(profile.role, id);
      setMemories(current => ({ ...current, [profile.role]: (current[profile.role] ?? []).filter(item => item.id !== id) }));
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
      <label>{profile.role} specialist name
        <input
          aria-label={`${profile.role} specialist name`}
          maxLength={64}
          value={identityDrafts[profile.role] ?? profile.display_name}
          disabled={busyRole === profile.role}
          onChange={event => setIdentityDrafts(current => ({ ...current, [profile.role]: event.target.value }))}
        />
      </label>
      <button
        type="button"
        disabled={busyRole === profile.role || (identityDrafts[profile.role] ?? profile.display_name).trim() === profile.display_name}
        onClick={() => void saveIdentity(profile)}
      >Save identity</button>
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
      <label>{profile.display_name} saved memories
        <textarea
          aria-label={`${profile.display_name} saved memory`}
          maxLength={16384}
          value={memoryDrafts[profile.role] ?? ''}
          disabled={busyRole === profile.role}
          onChange={event => setMemoryDrafts(current => ({ ...current, [profile.role]: event.target.value }))}
        />
      </label>
      <button type="button" disabled={busyRole === profile.role || !(memoryDrafts[profile.role] ?? '').trim()} onClick={() => void saveMemory(profile)}>Save role memory</button>
      <ul aria-label={`${profile.display_name} saved memory list`}>
        {(memories[profile.role] ?? []).map(memory => <li key={memory.id}>{memory.content} <button type="button" disabled={busyRole === profile.role} onClick={() => void deleteMemory(profile, memory.id)}>Delete memory</button></li>)}
      </ul>
    </div>)}
  </section>;
}
