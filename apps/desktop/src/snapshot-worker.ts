import type { ChildProcess } from "node:child_process";
import { randomUUID } from "node:crypto";
import { singleFlight } from "./single-flight.js";

/** Reuse one IPC worker. Never replace a timed-out worker until cleanup is confirmed. */
export class SnapshotWorker {
  private child?: ChildProcess;
  private ready = false;
  private stopping = false;
  private quarantine?: Error;
  private pending?: { id: string; sent: boolean; resolve: (value: unknown) => void; reject: (error: Error) => void; timer: ReturnType<typeof setTimeout> };
  constructor(private readonly start: () => ChildProcess, private readonly stop: (child: ChildProcess) => Promise<boolean>, private readonly timeoutMs = 20_000) {}

  readonly read = singleFlight(() => new Promise<unknown>((resolve, reject) => {
    if (this.quarantine) { reject(this.quarantine); return; }
    const timer = setTimeout(() => this.terminate("Dashboard refresh timed out"), this.timeoutMs);
    this.pending = { id: randomUUID(), sent: false, resolve, reject, timer };
    try {
      if (!this.child) this.connect();
      this.dispatch();
    } catch (error) { this.finish(error instanceof Error ? error : new Error(String(error))); }
  }));

  private finish(error?: Error, value?: unknown) {
    const pending = this.pending;
    this.pending = undefined;
    if (!pending) return;
    clearTimeout(pending.timer);
    if (error) pending.reject(error); else pending.resolve(value);
  }

  private connect() {
    const child = this.start();
    this.child = child;
    this.ready = false;
    child.on("message", (message: unknown) => {
      if (this.child !== child || this.stopping || !message || typeof message !== "object") return;
      const reply = message as { type?: unknown; id?: unknown; data?: unknown; error?: unknown };
      if (reply.type === "desktop:ready") { this.ready = true; this.dispatch(); }
      else if (this.pending && reply.id === this.pending.id) {
        if (reply.type === "desktop:result") this.finish(undefined, reply.data);
        else if (reply.type === "desktop:error") this.finish(new Error(String(reply.error ?? "Dashboard read failed")));
      }
    });
    const exited = () => {
      if (this.child !== child || this.stopping) return;
      child.unref();
      child.channel?.unref();
      this.child = undefined; this.ready = false;
      this.finish(new Error("Dashboard worker exited before returning its result"));
    };
    child.once("close", exited);
    child.once("exit", exited);
    child.once("error", exited);
  }

  private dispatch() {
    if (!this.ready || !this.pending || this.pending.sent || !this.child) return;
    this.pending.sent = true;
    try {
      this.child.send!({ type: "desktop:snapshot", id: this.pending.id }, (error) => {
        if (error) this.terminate("Dashboard worker connection failed");
      });
    } catch { this.terminate("Dashboard worker connection failed"); }
  }

  private terminate(message: string) {
    const child = this.child;
    if (!child || this.stopping) return;
    this.stopping = true;
    void this.stop(child).then((confirmed) => {
      if (!confirmed) {
        this.quarantine = new Error("Dashboard worker cleanup could not be confirmed; automatic restart is blocked");
        this.finish(this.quarantine);
        return;
      }
      // The tree may be gone before Node emits its exit/close events. Release the
      // old IPC handles now so a confirmed timeout cleanup cannot keep Electron alive.
      child.unref();
      child.channel?.unref();
      if (this.child === child) { this.child = undefined; this.ready = false; }
      this.stopping = false;
      this.finish(new Error(message));
    }).catch(() => {
      this.quarantine = new Error("Dashboard worker cleanup failed; automatic restart is blocked");
      this.finish(this.quarantine);
    });
  }
}
