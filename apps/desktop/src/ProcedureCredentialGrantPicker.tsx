import { useEffect, useState } from 'react';
import type { EngineClient } from './api/client.ts';
import type { components } from './generated/api.js';
import type { ProcedureTrialSelection } from './SkillsPanel.tsx';

type Status = components['schemas']['SkillCredentialStatus'];

export function ProcedureCredentialGrantPicker({
  client,
  selection,
  enabled,
  names,
  onChange,
}: {
  client: EngineClient;
  selection: ProcedureTrialSelection;
  enabled: boolean;
  names: string[];
  onChange: (names: string[]) => void;
}) {
  const [status, setStatus] = useState<Status>();
  const [error, setError] = useState('');

  useEffect(() => {
    let active = true;
    setStatus(undefined);
    setError('');
    void client.skillCredentialStatus(selection.skill_id, selection.version)
      .then((result) => { if (active) setStatus(result); })
      .catch((failure) => { if (active) setError(String(failure)); });
    return () => { active = false; };
  }, [client, selection.skill_id, selection.version]);

  if (!status && !error) return <p role="status">Checking saved procedure credentials…</p>;
  if (error) return <p role="alert">Could not load procedure credential status: {error}</p>;
  if (!status || status.credentials.length === 0) return null;

  function toggle(envName: string, checked: boolean) {
    onChange(checked ? [...new Set([...names, envName])] : names.filter((name) => name !== envName));
  }

  return <fieldset className="procedure-credential-grant" disabled={!enabled || !status.credential_store_available}>
    <legend>Procedure credential access</legend>
    <p>Choose which saved credentials this run may provide to the selected procedure’s command environment. This approval applies only to this procedure version and run. Command output is withheld from the model and saved run history while credentials are available.</p>
    {!status.credential_store_available && <p role="status">Windows Credential Manager is unavailable.</p>}
    {!enabled && <p role="status">Select the restricted WSL command sandbox and grant command permission to configure credential access for this run.</p>}
    {status.credentials.map((item) => <label className="procedure-permission" key={item.env_name}>
      <input
        type="checkbox"
        checked={names.includes(item.env_name)}
        disabled={!item.configured}
        onChange={(event) => toggle(item.env_name, event.target.checked)}
      />
      Allow <code>{item.env_name}</code> {item.required ? '(required)' : '(optional)'}
      {item.configured ? '' : ' — not saved'}
    </label>)}
  </fieldset>;
}
