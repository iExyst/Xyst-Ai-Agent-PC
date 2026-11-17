import type { Run } from './api/client.ts';

export function runElapsedSeconds(run: Run, now = Date.now()): number | null {
  const start = run.started_at === null ? null : Date.parse(run.started_at ?? run.created_at);
  const end = ['completed', 'failed', 'cancelled'].includes(run.state)
    ? Date.parse(run.updated_at)
    : now;
  if (start === null) return 0;
  return Number.isFinite(start) && Number.isFinite(end)
    ? Math.max(0, Math.floor((end - start) / 1000))
    : null;
}

export function runRemainingSeconds(run: Run, now = Date.now()): number | null {
  const elapsed = runElapsedSeconds(run, now);
  return elapsed === null || !run.limits
    ? null
    : Math.max(0, run.limits.max_seconds - elapsed);
}
