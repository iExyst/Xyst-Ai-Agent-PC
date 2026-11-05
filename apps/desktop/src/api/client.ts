import type { components } from '../generated/api.js';
import { decodeRunEvents, EventStreamDisconnected } from './events.ts';

function reconnectDelay(milliseconds: number, signal?: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    const abort = () => { clearTimeout(timer); reject(signal?.reason ?? new DOMException('Aborted', 'AbortError')); };
    const timer = setTimeout(() => { signal?.removeEventListener('abort', abort); resolve(); }, milliseconds);
    signal?.addEventListener('abort', abort, { once: true });
    if (signal?.aborted) abort();
  });
}

export type Run = components['schemas']['RunRecord'];
export type CreateRun = components['schemas']['CreateRunRequest'];
export type Artifact = components['schemas']['ArtifactRecord'];
export type Memory = components['schemas']['MemoryRecord'];
export type Plan = components['schemas']['PlanRecord'];
export type Candidate = components['schemas']['TaskCandidate'];
export type ReviewDecision = components['schemas']['TaskReviewDecision'];
export type RunReviewDecision = components['schemas']['RunReviewDecision'];
export type ProjectValidation = components['schemas']['ProjectValidationSummary'];
export type RunHistory = components['schemas']['RunHistory'];

export class EngineRequestError extends Error {
  readonly status: number;
  constructor(status: number, message: string) { super(message); this.status = status; }
}

export class EngineClient {
  #token: string;
  #base: URL;
  #fetch: typeof fetch;

  constructor(baseUrl: string, token: string, fetcher: typeof fetch = fetch) {
    const base = new URL(baseUrl);
    if (base.protocol !== 'http:' || !['127.0.0.1', 'localhost', '[::1]'].includes(base.hostname) || base.username || base.password) {
      throw new Error('The engine address must be an HTTP loopback address.');
    }
    if (token.length < 32) throw new Error('Engine authentication token is missing or too short.');
    this.#base = base;
    this.#token = token;
    this.#fetch = (input, init) => fetcher(input, init);
  }

  async #request<T>(path: string, init: RequestInit = {}): Promise<T> {
    const headers = new Headers(init.headers);
    headers.set('Authorization', `Bearer ${this.#token}`);
    if (init.body) headers.set('Content-Type', 'application/json');
    const response = await this.#fetch(new URL(path, this.#base), { ...init, headers, redirect: 'error' });
    if (!response.ok) {
      throw new EngineRequestError(response.status, `Engine request failed (${response.status}).`);
    }
    if (response.status === 204) return undefined as T;
    return response.json() as Promise<T>;
  }

  createRun(input: CreateRun, signal?: AbortSignal): Promise<Run> {
    return this.#request('/runs', { method: 'POST', body: JSON.stringify(input), ...(signal ? { signal } : {}) });
  }

  usage(id: string, signal?: AbortSignal): Promise<components['schemas']['UsageReport']> {
    return this.#request(`/runs/${encodeURIComponent(id)}/usage`, {signal});
  }

  models(): Promise<components['schemas']['ModelInfo'][]> { return this.#request('/models'); }

  lmStudioConfig(): Promise<components['schemas']['LMStudioConfigRecord']> {
    return this.#request('/providers/lm-studio/config');
  }

  setLMStudioEndpoint(endpoint: string): Promise<components['schemas']['LMStudioConfigRecord']> {
    return this.#request('/providers/lm-studio/config', {method:'PUT',body:JSON.stringify({endpoint})});
  }

  selectLMStudioModel(model: string | null): Promise<{model:string|null}> {
    return this.#request('/providers/lm-studio/selection', {method:'PUT',body:JSON.stringify({model})});
  }

  diagnoseLMStudioModel(model: string): Promise<{model:string;tool_round_trip_verified:boolean}> {
    return this.#request('/providers/lm-studio/diagnostics', {method:'POST',body:JSON.stringify({model})});
  }

  providers(): Promise<{id:string;status:string;error?:string}[]> { return this.#request('/providers'); }

  executionDistributions(signal?: AbortSignal): Promise<components['schemas']['DistributionInventory']> {
    return this.#request('/execution/distributions', {signal});
  }

  executionReadiness(distribution: string, signal?: AbortSignal): Promise<components['schemas']['ReadinessResult']> {
    return this.#request('/execution/readiness', {method:'POST',body:JSON.stringify({distribution}),signal});
  }

  history(query='',before?:string,signal?:AbortSignal,scope:'objective'|'conversation'='objective'): Promise<RunHistory> {
    const params=new URLSearchParams({q:query,limit:'20',scope});
    if(before)params.set('before',before);
    return this.#request(`/runs?${params}`,signal?{signal}:{});
  }

  plan(runId: string, signal?: AbortSignal): Promise<Plan> {
    return this.#request(`/runs/${encodeURIComponent(runId)}/plan`, signal ? { signal } : {});
  }

  integrationPreview(runId:string,taskId:string):Promise<components['schemas']['IntegrationPreview']> {
    return this.#request(`/runs/${encodeURIComponent(runId)}/tasks/${encodeURIComponent(taskId)}/integration-preview`,{method:'POST'});
  }

  candidate(runId: string, taskId: string): Promise<Candidate> {
    return this.#request(`/runs/${encodeURIComponent(runId)}/tasks/${encodeURIComponent(taskId)}/candidate`);
  }

  dispatchPlan(runId: string): Promise<Run> {
    return this.#request(`/runs/${encodeURIComponent(runId)}/plan/dispatch`, {method:'POST'});
  }

  reviewRun(runId: string, decision: RunReviewDecision): Promise<Run> {
    return this.#request(`/runs/${encodeURIComponent(runId)}/review`, {method:'POST',body:JSON.stringify(decision)});
  }

  projectValidation(runId:string):Promise<ProjectValidation>{return this.#request(`/runs/${encodeURIComponent(runId)}/validation`);}
  runProjectValidation(runId:string,input:{request_id:string;expected_revision:number;argv:string[];max_seconds:number}):Promise<ProjectValidation>{return this.#request(`/runs/${encodeURIComponent(runId)}/validation`,{method:'POST',body:JSON.stringify(input)});}
  reconcileProjectValidation(runId:string,callId:string):Promise<ProjectValidation>{return this.#request(`/runs/${encodeURIComponent(runId)}/validation/${encodeURIComponent(callId)}/reconcile`,{method:'POST'});}

  reviewCandidate(runId: string, taskId: string, decision: ReviewDecision): Promise<Plan> {
    return this.#request(`/runs/${encodeURIComponent(runId)}/tasks/${encodeURIComponent(taskId)}/review`, {method:'POST',body:JSON.stringify(decision)});
  }

  skills(): Promise<components['schemas']['SkillSummary'][]> { return this.#request('/skills'); }
  skillVersion(id:string,version:number): Promise<components['schemas']['SkillVersion']> { return this.#request(`/skills/${encodeURIComponent(id)}/versions/${version}`); }
  saveSkill(draft:components['schemas']['SkillDraft'],id?:string,expectedVersion?:number): Promise<components['schemas']['SkillSummary']> {
    return this.#request(id?`/skills/${encodeURIComponent(id)}/versions`:'/skills',{method:'POST',body:JSON.stringify(id?{...draft,expected_version:expectedVersion}:draft)});
  }
  skillTrials(id:string,version:number): Promise<components['schemas']['SkillTrialRun'][]> { return this.#request(`/skills/${encodeURIComponent(id)}/versions/${version}/trials`); }
  evaluateSkill(id:string,version:number,review:components['schemas']['SkillEvaluation']): Promise<components['schemas']['SkillVersion']> { return this.#request(`/skills/${encodeURIComponent(id)}/versions/${version}/evaluations`,{method:'POST',body:JSON.stringify(review)}); }
  skillLifecycle(id:string): Promise<components['schemas']['SkillLifecycleEvent'][]> { return this.#request(`/skills/${encodeURIComponent(id)}/lifecycle`); }
  decideSkillLifecycle(id:string,decision:components['schemas']['SkillLifecycleDecision']): Promise<components['schemas']['SkillSummary']> { return this.#request(`/skills/${encodeURIComponent(id)}/lifecycle`,{method:'POST',body:JSON.stringify(decision)}); }
  deleteSkill(id:string): Promise<void> { return this.#request(`/skills/${encodeURIComponent(id)}`,{method:'DELETE'}); }

  memories(query = ''): Promise<Memory[]> { return this.#request(`/memory?q=${encodeURIComponent(query)}`); }
  savePreference(content: string, id?: string): Promise<Memory> { return this.#request(id ? `/memory/${encodeURIComponent(id)}` : '/memory', { method: 'POST', body: JSON.stringify({content}) }); }
  deleteMemory(id: string): Promise<void> { return this.#request(`/memory/${encodeURIComponent(id)}`, { method: 'DELETE' }); }
  exportMemory(): Promise<Memory[]> { return this.#request('/memory/export'); }

  artifacts(runId: string, signal?: AbortSignal): Promise<Artifact[]> {
    return this.#request(`/runs/${encodeURIComponent(runId)}/artifacts`, signal ? { signal } : {});
  }

  async artifactPreview(id: string, signal?: AbortSignal): Promise<string> {
    const response = await this.#fetch(new URL(`/artifacts/${encodeURIComponent(id)}`, this.#base), { headers: { Authorization: `Bearer ${this.#token}` }, redirect: 'error', ...(signal ? { signal } : {}) });
    if (!response.ok || !response.body) throw new EngineRequestError(response.status, 'Cannot read artifact.');
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let text = ''; let bytes = 0;
    try {
      while (true) {
        const chunk = await reader.read();
        if (chunk.done) return text + decoder.decode();
        const available = 65536 - bytes;
        const slice = chunk.value.subarray(0, available);
        bytes += slice.byteLength; text += decoder.decode(slice, { stream: true });
        if (bytes >= 65536) return text + decoder.decode() + '\n[Preview limited to 64 KiB]';
      }
    } finally { await reader.cancel(); reader.releaseLock(); }
  }

  getRun(id: string, signal?: AbortSignal): Promise<Run> {
    return this.#request(`/runs/${encodeURIComponent(id)}`, signal ? { signal } : {});
  }

  control(id: string, action: 'pause' | 'resume' | 'cancel'): Promise<Run> {
    return this.#request(`/runs/${encodeURIComponent(id)}/${action}`, { method: 'POST' });
  }

  async *events(id: string, after = 0, signal?: AbortSignal) {
    if (!Number.isSafeInteger(after) || after < 0) throw new Error('Event cursor must be a nonnegative integer.');
    const url = new URL(`/runs/${encodeURIComponent(id)}/events/stream`, this.#base);
    let cursor = after;
    for (let attempt = 0; ; attempt++) {
      try {
        signal?.throwIfAborted();
        const headers = new Headers({ Authorization: `Bearer ${this.#token}`, 'Last-Event-ID': String(cursor) });
        const response = await this.#fetch(url, { headers, redirect: 'error', ...(signal ? { signal } : {}) });
        if (!response.ok || !response.body) {
          await response.body?.cancel();
          throw new EngineRequestError(response.status, 'Cannot open engine event stream.');
        }
        for await (const event of decodeRunEvents(response.body, cursor)) {
          cursor = event.sequence;
          yield event;
        }
        return;
      } catch (failure) {
        if (signal?.aborted) throw failure;
        const transient = failure instanceof EventStreamDisconnected || failure instanceof TypeError ||
          (failure instanceof EngineRequestError && [408, 429, 500, 502, 503, 504].includes(failure.status));
        if (!transient || attempt >= 5) throw failure;
        await reconnectDelay(Math.min(250 * 2 ** attempt, 4000), signal);
      }
    }
  }
}
