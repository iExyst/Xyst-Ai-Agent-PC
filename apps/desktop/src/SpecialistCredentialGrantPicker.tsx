import { useEffect, useState } from 'react';
import type { CreateRun, EngineClient, SpecialistProfile } from './api/client.ts';

type Grant = NonNullable<CreateRun['specialist_credential_grants']>[number];
type CredentialStatus = Awaited<ReturnType<EngineClient['skillCredentialStatus']>>;
type RoleCredentials = { profile: SpecialistProfile; status: CredentialStatus };

export function SpecialistCredentialGrantPicker({
  client,
  enabled,
  grants,
  onChange,
}: {
  client: EngineClient;
  enabled: boolean;
  grants: Grant[];
  onChange: (grants: Grant[]) => void;
}) {
  const [roles, setRoles] = useState<RoleCredentials[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  useEffect(() => {
    let active = true;
    setLoading(true);
    setError('');
    void client.specialistProfiles().then(async (profiles) => {
      const configured = profiles.filter((profile) => profile.procedure_skill_id && profile.procedure_version);
      const results = await Promise.all(configured.map(async (profile) => ({
        profile,
        status: await client.skillCredentialStatus(profile.procedure_skill_id!, profile.procedure_version!),
      })));
      if (active) setRoles(results.filter((result) => result.status.credentials.length > 0));
    }).catch((failure) => {
      if (active) setError(String(failure));
    }).finally(() => {
      if (active) setLoading(false);
    });
    return () => { active = false; };
  }, [client]);

  function toggle(role: RoleCredentials, envName: string, checked: boolean) {
    const { profile } = role;
    const next = grants.filter((grant) => grant.role !== profile.role);
    const selected = role.status.credentials
      .filter((item) => item.configured && (item.env_name === envName ? checked :
        grants.find((grant) => grant.role === profile.role)?.credential_names.includes(item.env_name)))
      .map((item) => item.env_name);
    if (selected.length && profile.procedure_skill_id && profile.procedure_version) {
      next.push({
        role: profile.role as Grant['role'],
        skill_id: profile.procedure_skill_id,
        version: profile.procedure_version,
        credential_names: selected,
      });
    }
    onChange(next);
  }

  if (loading) return <p role="status">Checking specialist procedure credentials…</p>;
  if (error) return <p role="alert">Could not load specialist credential status: {error}</p>;
  if (!roles.length) return null;

  return <section className="procedure-credential-grant" aria-label="Specialist credential access">
    <h3>Specialist procedure credentials</h3>
    <p>Each choice applies only to that role’s selected procedure version for this run. Credentials remain in the OS vault; command output is withheld while they are available. No specialist receives credentials unless you select them here.</p>
    {!enabled && <p role="status">Select workspace mode and grant access to the restricted WSL command sandbox to enable these choices.</p>}
    {roles.map((role) => {
      const { profile, status } = role;
      const current = grants.find((grant) => grant.role === profile.role &&
        grant.skill_id === profile.procedure_skill_id && grant.version === profile.procedure_version);
      return <fieldset key={profile.role} disabled={!enabled || !status.credential_store_available}>
        <legend>{profile.display_name} · {profile.procedure_name} v{profile.procedure_version}</legend>
        {!status.credential_store_available && <p role="status">OS credential storage is unavailable.</p>}
        {status.credentials.map((item) => <label className="procedure-permission" key={item.env_name}>
          <input
            type="checkbox"
            checked={current?.credential_names.includes(item.env_name) ?? false}
            disabled={!item.configured}
            onChange={(event) => toggle(role, item.env_name, event.target.checked)}
          />
          Allow <code>{item.env_name}</code> for the {profile.role} specialist {item.required ? '(required)' : '(optional)'}
          {!item.configured && ' — not saved'}
        </label>)}
      </fieldset>;
    })}
  </section>;
}
