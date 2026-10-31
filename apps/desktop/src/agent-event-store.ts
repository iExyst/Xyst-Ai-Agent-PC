export type AgentEvent = { type?: string; event?: string; data?: Record<string, any>; sessionId?: string; at?: string };

const MAX_AGENT_EVENTS = 80;
const RENDER_INTERVAL_MS = 100;

export class AgentEventStore {
  private events: AgentEvent[] = [];
  private pendingEvents: AgentEvent[] = [];
  private publishTimer?: ReturnType<typeof setTimeout>;
  private readonly subscribers = new Set<() => void>();

  readonly getSnapshot = (): AgentEvent[] => this.events;

  readonly subscribe = (listener: () => void): (() => void) => {
    this.subscribers.add(listener);
    return () => { this.subscribers.delete(listener); };
  };

  readonly publish = (event: AgentEvent): void => {
    this.pendingEvents.push(event);
    if (this.publishTimer) return;
    this.publishTimer = setTimeout(() => this.flush(), RENDER_INTERVAL_MS);
  };

  readonly clear = (): void => {
    if (this.publishTimer) clearTimeout(this.publishTimer);
    this.publishTimer = undefined;
    this.pendingEvents = [];
    if (!this.events.length) return;
    this.events = [];
    this.notify();
  };

  private flush(): void {
    this.publishTimer = undefined;
    if (!this.pendingEvents.length) return;
    const incoming = this.pendingEvents.splice(-MAX_AGENT_EVENTS);
    this.pendingEvents = [];
    this.events = [...this.events, ...incoming].slice(-MAX_AGENT_EVENTS);
    this.notify();
  }

  private notify(): void {
    for (const listener of this.subscribers) listener();
  }
}

export const agentEventStore = new AgentEventStore();
