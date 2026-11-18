import { useEffect, useState } from 'react';
import { EngineClient, type Run } from './api/client.ts';
import type { components } from './generated/api.js';
import {runRemainingSeconds} from './run-time.ts';
import './run-resources.css';

type Usage = components['schemas']['UsageReport'];
export function RunResources({client, run, refresh}: {client: EngineClient; run: Run; refresh: number}) {
  const [usage, setUsage] = useState<Usage>();
  const [unavailable, setUnavailable] = useState(false);
  const [retry, setRetry] = useState(0);
  const [clockNow, setClockNow] = useState(() => Date.now());
  const isTerminal = ['completed','failed','cancelled'].includes(run.state);
  useEffect(() => {
    const controller = new AbortController();
    setUsage(undefined); setUnavailable(false);
    void client.usage(run.id, controller.signal).then(value => {
      if (!['inference_attempts','prompt_tokens','completion_tokens','total_tokens','unmeasured_calls','outstanding_reserved_tokens','outstanding_reservations'].every(key => {
        const count = value[key as keyof Usage]; return Number.isInteger(count) && count >= 0;
      })) throw new Error('Invalid usage response');
      if (!controller.signal.aborted) setUsage(value);
    }).catch(() => { if (!controller.signal.aborted) setUnavailable(true); });
    return () => controller.abort();
  }, [client, run.id, run.revision, run.state, refresh, retry]);
  useEffect(() => {
    if (isTerminal) return;
    const timer = window.setInterval(() => setClockNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [isTerminal, run.id]);
  const remainingSeconds = runRemainingSeconds(run,clockNow);
  const timeRemainingLabel = remainingSeconds === null
    ? 'Unavailable'
    : remainingSeconds >= 3600
      ? `${Math.floor(remainingSeconds / 3600)}h ${Math.floor(remainingSeconds % 3600 / 60)}m ${remainingSeconds % 60}s`
      : remainingSeconds >= 60
        ? `${Math.floor(remainingSeconds / 60)}m ${remainingSeconds % 60}s`
        : `${remainingSeconds}s`;
  const tokenAllowanceRemaining = !run.limits
    ? 'Unavailable until run limits load.'
    : run.limits.max_total_tokens == null
    ? 'No total-token threshold set.'
    : unavailable
      ? 'Unavailable until token usage loads.'
      : !usage
        ? 'Loading token usage…'
        : usage.unmeasured_calls > 0
          ? 'Unknown because one or more calls have unmeasured usage.'
          : `${Math.max(0, run.limits.max_total_tokens - usage.total_tokens - usage.outstanding_reserved_tokens).toLocaleString()} tokens (${usage.total_tokens.toLocaleString()} measured; ${usage.outstanding_reserved_tokens.toLocaleString()} reserved).`;
  const iterationAllowanceRemaining = !run.limits
    ? 'Unavailable until run limits load.'
    : usage
      ? `${Math.max(0, run.limits.max_iterations - usage.inference_attempts)} of ${run.limits.max_iterations}`
      : unavailable
        ? 'Unavailable until usage loads.'
        : 'Loading usage…';
  return <section aria-label="Run resources" className="run-resources">
    <h2>Run resources</h2>
    <dl className="run-resource-summary">
      <div><dt>Model</dt><dd>{run.model ?? 'Not selected'}</dd></div>
      {run.limits && <>
        <div><dt>Run limits</dt><dd>{run.limits.max_iterations} {run.limits.max_iterations===1?'iteration':'iterations'} · {run.limits.max_seconds} {run.limits.max_seconds===1?'second':'seconds'}</dd></div>
        <div><dt>Iterations remaining</dt><dd>{iterationAllowanceRemaining}</dd></div>
        <div><dt>Time remaining</dt><dd>{timeRemainingLabel}</dd></div>
        <div><dt>Token threshold</dt><dd>{run.limits.max_total_tokens?.toLocaleString() ?? 'Not set'} · {run.limits.max_output_tokens?.toLocaleString() ?? 'Server default'} per request</dd></div>
      </>}
    </dl>
    {run.procedure_trial&&<p className="run-resource-procedure"><strong>{run.procedure_trial.purpose==='active_use'?'Active procedure':'Procedure trial'}: {run.procedure_trial.name} · version {run.procedure_trial.version}</strong><span>This run does not activate or independently evaluate the procedure.</span></p>}
    <details className="run-resource-details">
      <summary>Usage accounting and reservations</summary>
      <div className="run-resource-detail-content">
        {run.limits && <><p>Token allowance remaining: {tokenAllowanceRemaining}</p>
          <p>Pre-inference token reservation: {run.limits.pre_inference_token_reservations ? 'Required; exact LM Studio count' : 'Off'}</p>
          <p>Iteration allowance remaining: {iterationAllowanceRemaining}</p></>}
        {usage ? <p>Measured tokens: {usage.total_tokens.toLocaleString()} ({usage.prompt_tokens.toLocaleString()} input, {usage.completion_tokens.toLocaleString()} output). Calls without measured usage: {usage.unmeasured_calls}.</p> : <p>{unavailable ? 'Token usage unavailable.' : 'Loading token usage…'}</p>}
        {usage && <><p>Reserved tokens awaiting measured results: {usage.outstanding_reserved_tokens.toLocaleString()} across {usage.outstanding_reservations} {usage.outstanding_reservations === 1 ? 'request' : 'requests'}.</p>
          {usage.outstanding_reservations > 0 && <p>These holds include input and the output allowance. Interrupted requests keep their hold until a measured result is saved.</p>}</>}
        <p>Measured usage covers the parent, specialists, and run-triggered capability diagnostics.</p>
      </div>
    </details>
    {unavailable && <button onClick={()=>setRetry(value=>value+1)}>Retry token usage</button>}
  </section>;
}
