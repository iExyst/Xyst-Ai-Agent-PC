import { useEffect, useState } from 'react';
import { EngineClient, type Run } from './api/client.ts';
import type { components } from './generated/api.js';

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
  const runEnd = isTerminal ? Date.parse(run.updated_at) : clockNow;
  const runStart = Date.parse(run.created_at);
  const elapsedSeconds = Number.isFinite(runStart) && Number.isFinite(runEnd)
    ? Math.max(0, Math.floor((runEnd - runStart) / 1000))
    : null;
  const remainingSeconds = elapsedSeconds === null || !run.limits
    ? null
    : Math.max(0, run.limits.max_seconds - elapsedSeconds);
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
  return <section aria-label="Run resources"><h2>Run resources</h2><p>Model: {run.model ?? 'Not selected'}</p>
    {run.procedure_trial&&<p>{run.procedure_trial.purpose==='active_use'?'Active procedure':'Procedure trial'}: {run.procedure_trial.name} &middot; version {run.procedure_trial.version}. This run does not activate or independently evaluate the procedure.</p>}
    {run.limits && <><p>{run.limits.max_iterations} {run.limits.max_iterations===1?'iteration':'iterations'} · {run.limits.max_seconds} {run.limits.max_seconds===1?'second':'seconds'}</p>
      <p>Inference iterations remaining: {iterationAllowanceRemaining}</p>
      <p>Wall-clock allowance remaining: {timeRemainingLabel}</p>
      <p>Reported token threshold: {run.limits.max_total_tokens ?? 'Not set'} · Output tokens per request: {run.limits.max_output_tokens ?? 'Server default'}</p>
      <p>Token allowance remaining: {tokenAllowanceRemaining}</p>
      <p>Pre-inference token reservation: {run.limits.pre_inference_token_reservations ? 'Required; exact LM Studio count' : 'Off'}</p></>}
    {usage ? <p>Measured tokens: {usage.total_tokens} ({usage.prompt_tokens} input, {usage.completion_tokens} output). Calls without measured usage: {usage.unmeasured_calls}.</p> : <p>{unavailable ? 'Token usage unavailable.' : 'Loading token usage…'}</p>}
    {usage && <><p>Reserved tokens awaiting measured results: {usage.outstanding_reserved_tokens} across {usage.outstanding_reservations} {usage.outstanding_reservations === 1 ? 'request' : 'requests'}.</p>
      {usage.outstanding_reservations > 0 && <p>These holds include input and the output allowance. Interrupted requests keep their hold until a measured result is saved.</p>}</>}
    {unavailable && <button onClick={()=>setRetry(value=>value+1)}>Retry token usage</button>}
    <p>Measured usage covers the parent, specialists, and run-triggered capability diagnostics.</p>
  </section>;
}
