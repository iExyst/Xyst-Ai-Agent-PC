export type AgentEvent = { type?: string; event?: string; data?: Record<string, any>; sessionId?: string; at?: string };

const MAX_AGENT_EVENTS = 80;

export class AgentEventStore {
  private events: AgentEvent[] = [];
  private readonly subscribers = new Set<() => void>();

  readonly getSnapshot = (): AgentEvent[] => this.events;

  readonly subscribe = (listener: () => void): (() => void) => {
    this.subscribers.add(listener);
    return () => { this.subscribers.delete(listener); };
  };

  readonly publish = (event: AgentEvent): void => {
    this.events = [...this.events.slice(-(MAX_AGENT_EVENTS - 1)), event];
    for (const listener of this.subscribers) listener();
  };

  readonly clear = (): void => {
    if (!this.events.length) return;
    this.events = [];
    for (const listener of this.subscribers) listener();
  };
}

export const agentEventStore = new AgentEventStore();
