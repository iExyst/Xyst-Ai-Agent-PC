import { useEffect, useState } from 'react';
import { EngineClient, type Run } from './api/client.ts';
import type { components } from './generated/api.js';

type Usage = components['schemas']['UsageReport'];
export function RunResources({client, run, refresh}: {client: EngineClient; run: Run; refresh: number}) {
  const [usage, setUsage] = useState<Usage>();
  const [unavailable, setUnavailable] = useState(false);
  const [retry, setRetry] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    setUsage(undefined); setUnavailable(false);
    void client.usage(run.id, controller.signal).then(value => {
      if (!['prompt_tokens','completion_tokens','total_tokens','unmeasured_calls','outstanding_reserved_tokens','outstanding_reservations'].every(key => {
        const count = value[key as keyof Usage]; return Number.isInteger(count) && count >= 0;
      })) throw new Error('Invalid usage response');
      if (!controller.signal.aborted) setUsage(value);
    }).catch(() => { if (!controller.signal.aborted) setUnavailable(true); });
    return () => controller.abort();
  }, [client, run.id, run.revision, run.state, refresh, retry]);
  return <section aria-label="Run resources"><h2>Run resources</h2><p>Model: {run.model ?? 'Not selected'}</p>
    {run.procedure_trial&&<p>Procedure trial: {run.procedure_trial.name} &middot; version {run.procedure_trial.version}. This run does not activate or independently evaluate the procedure.</p>}
    {run.limits && <><p>{run.limits.max_iterations} iterations · {run.limits.max_seconds} seconds</p>
      <p>Reported token threshold: {run.limits.max_total_tokens ?? 'Not set'} · Output tokens per request: {run.limits.max_output_tokens ?? 'Server default'}</p></>}
    {usage ? <p>Measured tokens: {usage.total_tokens} ({usage.prompt_tokens} input, {usage.completion_tokens} output). Calls without measured usage: {usage.unmeasured_calls}.</p> : <p>{unavailable ? 'Token usage unavailable.' : 'Loading token usage…'}</p>}
    {usage && <><p>Reserved tokens awaiting measured results: {usage.outstanding_reserved_tokens} across {usage.outstanding_reservations} {usage.outstanding_reservations === 1 ? 'request' : 'requests'}.</p>
      {usage.outstanding_reservations > 0 && <p>These holds include input and the output allowance. Interrupted requests keep their hold until a measured result is saved.</p>}</>}
    {unavailable && <button onClick={()=>setRetry(value=>value+1)}>Retry token usage</button>}
    <p>Measured usage covers the parent, specialists, and run-triggered capability diagnostics.</p>
  </section>;
}
