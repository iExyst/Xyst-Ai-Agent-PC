import type { CreateRun } from './api/client.ts';

export type LimitInputs = { max_iterations: string; max_seconds: string; max_total_tokens: string; max_output_tokens: string };
const fields = [
  ['max_iterations', 'Iteration limit', 100, false],
  ['max_seconds', 'Time limit (seconds)', 86400, false],
  ['max_total_tokens', 'Reported token threshold (optional)', 100000000, true],
  ['max_output_tokens', 'Output tokens per request (optional)', 1000000, true],
] as const;

export function parseLimits(inputs: LimitInputs): Pick<CreateRun, keyof LimitInputs> | undefined {
  for (const [key, , maximum, optional] of fields) {
    if (optional && inputs[key] === '') continue;
    const value = Number(inputs[key]);
    if (!Number.isInteger(value) || value < 1 || value > maximum) return undefined;
  }
  return { max_iterations: Number(inputs.max_iterations), max_seconds: Number(inputs.max_seconds),
    max_total_tokens: inputs.max_total_tokens === '' ? null : Number(inputs.max_total_tokens),
    max_output_tokens: inputs.max_output_tokens === '' ? null : Number(inputs.max_output_tokens) };
}

export function ResourceLimits({inputs, onChange}: {inputs: LimitInputs; onChange: (value: LimitInputs) => void}) {
  return <details><summary>Run limits</summary><fieldset><legend>Limits for the new objective</legend>
    {fields.map(([key, label, maximum, optional]) => <label key={key} htmlFor={`limit-${key}`}>{label}<input id={`limit-${key}`} type="number" min={1} max={maximum} step={1} required={!optional} value={inputs[key]} onChange={event => onChange({...inputs, [key]: event.target.value})}/></label>)}
    <p>Iterations and time cover the parent and specialists. The reported token threshold stops further work after usage arrives; a request may cross it. Output tokens limit each response. Token totals include run-triggered diagnostics. The selected output cap applies to task responses; diagnostics use a separate 512-token cap. Leave optional values empty to use the current defaults.</p>
    {!parseLimits(inputs) && <p role="alert">Enter whole numbers within the displayed limits.</p>}
  </fieldset></details>;
}
