import assert from "node:assert/strict";
import { EventEmitter } from "node:events";
import type { ChildProcess } from "node:child_process";
import test from "node:test";
import { SnapshotWorker } from "./snapshot-worker.js";

class Child extends EventEmitter {
  unrefCalls = 0;
  channel = { unrefCalls: 0, unref() { this.unrefCalls++; } };
  unref() { this.unrefCalls++; return this; }
  requests: any[] = [];
  send(message: unknown, callback: (error: Error | null) => void) { this.requests.push(message); callback(null); return true; }
  result(data: unknown) { this.emit("message", { type: "desktop:result", id: this.requests.at(-1).id, data }); }
}
const tick = () => new Promise<void>((resolve) => setImmediate(resolve));

test("repeated dashboard reads reuse one child and concurrent readers share one request", async () => {
  const child = new Child(); let starts = 0;
  const reader = new SnapshotWorker(() => { starts++; setImmediate(() => child.emit("message", { type: "desktop:ready" })); return child as unknown as ChildProcess; }, async () => true);
  const first = reader.read(); const overlap = reader.read();
  assert.equal(first, overlap);
  await tick(); await tick(); assert.equal(child.requests.length, 1);
  child.result({ revision: 1 }); assert.deepEqual(await first, { revision: 1 });
  const second = reader.read(); await tick(); child.result({ revision: 2 });
  assert.deepEqual(await second, { revision: 2 }); assert.equal(starts, 1);
});

test("process output reads reuse the dashboard worker and serialize with snapshots", async () => {
  const child = new Child(); let starts = 0;
  const reader = new SnapshotWorker(() => { starts++; setImmediate(() => child.emit("message", { type: "desktop:ready" })); return child as unknown as ChildProcess; }, async () => true);
  const snapshot = reader.read();
  const processRead = reader.showProcess("job-1");
  const duplicateProcessRead = reader.showProcess("job-1");
  assert.equal(processRead, duplicateProcessRead);
  await tick(); await tick();
  assert.deepEqual(child.requests.map(({ type }) => type), ["desktop:snapshot"]);
  child.result({ revision: 1 }); assert.deepEqual(await snapshot, { revision: 1 });
  await tick();
  assert.deepEqual(child.requests.map(({ type }) => type), ["desktop:snapshot", "desktop:process-show"]);
  assert.equal(child.requests[1].jobId, "job-1");
  child.result({ id: "job-1", details: { stdout: "hello" } });
  assert.deepEqual(await processRead, { id: "job-1", details: { stdout: "hello" } });
  assert.equal(starts, 1);
});

test("timed-out dashboard cannot respawn while process-tree cleanup remains pending", async () => {
  let starts = 0; let timedOutChild!: Child; let release!: (confirmed: boolean) => void;
  const stopped = new Promise<boolean>((resolve) => { release = resolve; });
  const reader = new SnapshotWorker(() => { starts++; timedOutChild = new Child(); return timedOutChild as unknown as ChildProcess; }, () => stopped, 10);
  const first = reader.read(); void first.catch(() => undefined);
  await new Promise((resolve) => setTimeout(resolve, 30));
  assert.equal(reader.read(), first); assert.equal(starts, 1);
  release(true); await assert.rejects(first, /timed out/);
  // The child was stopped before it emitted close/exit, so cleanup must release
  // both process and IPC references itself.
  await tick(); assert.equal(timedOutChild.unrefCalls, 1);
  assert.equal(timedOutChild.channel.unrefCalls, 1);
});

test("worker exit rejects pending read and permits a clean replacement", async () => {
  const children: Child[] = [];
  const reader = new SnapshotWorker(() => { const child = new Child(); children.push(child); return child as unknown as ChildProcess; }, async () => true);
  const first = reader.read(); await tick(); children[0].emit("exit", 1);
  await assert.rejects(first, /exited/);
  const next = reader.read(); await tick(); children[1].emit("message", { type: "desktop:ready" }); children[1].result("ready");
  assert.equal(await next, "ready"); assert.equal(children.length, 2);
});

test("unconfirmed cleanup reports failure and blocks replacement workers", async () => {
  let starts = 0;
  const reader = new SnapshotWorker(() => { starts++; return new Child() as unknown as ChildProcess; }, async () => false, 5);
  await assert.rejects(reader.read(), /cleanup could not be confirmed/);
  await assert.rejects(reader.read(), /automatic restart is blocked/);
  assert.equal(starts, 1);
});
