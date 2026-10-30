import assert from "node:assert/strict";
import { EventEmitter, once } from "node:events";
import { fork } from "node:child_process";
import { mkdtempSync, mkdirSync, writeFileSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";
import { serveDesktopSnapshots } from "./desktop-worker.js";

class Parent extends EventEmitter {
  connected = true;
  messages: any[] = [];
  send(message: unknown, callback: () => void) { this.messages.push(message); callback(); return true; }
}
const settle = () => new Promise<void>((resolve) => setImmediate(resolve));

test("dashboard worker reuses its process while rejecting overlapping reads", async () => {
  const parent = new Parent(); let reads = 0; let release!: () => void;
  const gate = new Promise<void>((resolve) => { release = resolve; });
  const controller = new AbortController();
  const serving = serveDesktopSnapshots(async () => { reads++; await gate; return { reads }; }, controller, parent as unknown as NodeJS.Process);
  assert.equal(parent.messages[0].type, "desktop:ready");
  parent.emit("message", { type: "desktop:snapshot", id: "first" });
  parent.emit("message", { type: "desktop:snapshot", id: "overlap" });
  await settle(); assert.equal(reads, 1);
  assert.equal(parent.messages.find((m) => m.id === "overlap").type, "desktop:error");
  release(); await settle();
  parent.emit("message", { type: "desktop:snapshot", id: "next" }); await settle();
  assert.equal(reads, 2);
  assert.equal(parent.messages.find((m) => m.id === "next").data.reads, 2);
  parent.connected = false; parent.emit("disconnect"); await serving;
  assert.equal(controller.signal.aborted, true);
});

test("parent exit aborts a pending dashboard operation before worker shutdown", async () => {
  const parent = new Parent(); const controller = new AbortController(); let stopped = false;
  const serving = serveDesktopSnapshots(() => new Promise((resolve) => {
    controller.signal.addEventListener("abort", () => { stopped = true; resolve({}); }, { once: true });
  }), controller, parent as unknown as NodeJS.Process);
  parent.emit("message", { type: "desktop:snapshot", id: "pending" }); await settle();
  parent.connected = false; parent.emit("disconnect"); await serving;
  assert.equal(stopped, true);
  assert.equal(parent.messages.some((m) => m.type === "desktop:result"), false);
});

test("actual CLI IPC worker returns a one-shot corruption report without modifying storage", { timeout: 20_000 }, async () => {
  const root = mkdtempSync(resolve(tmpdir(), "xyst-dashboard-corrupt-worker-"));
  const data = resolve(root, "data"); const database = resolve(data, "xyst/xyst.sqlite");
  mkdirSync(dirname(database), { recursive: true });
  const bytes = Buffer.from("deliberately corrupt SQLite fixture"); writeFileSync(database, bytes);
  const cwd = resolve(dirname(fileURLToPath(import.meta.url)), "../../..");
  const child = fork(fileURLToPath(new URL("./index.ts", import.meta.url)), ["--json", "desktop", "snapshot", "--worker"], {
    cwd, execArgv: ["--import", "tsx"], stdio: ["ignore", "ignore", "ignore", "ipc"],
    env: { ...process.env, XYST_ROOT: root, APPDATA: data, XDG_DATA_HOME: data, XDG_CONFIG_HOME: resolve(root, "config") }
  });
  try {
    const [ready] = await once(child, "message"); assert.equal(ready.type, "desktop:ready");
    const exited = once(child, "exit");
    const response = once(child, "message"); child.send({ type: "desktop:snapshot", id: "corrupt" });
    const [result] = await response; assert.equal(result.id, "corrupt"); assert.equal(result.data.status.database, "corrupt");
    assert.deepEqual(readFileSync(database), bytes);
    const [code] = await exited; // One-shot corruption reports allow a later refresh to detect repaired storage.
    assert.equal(code, 0);
  } finally {
    if (child.exitCode === null) child.kill();
    child.unref(); child.channel?.unref(); rmSync(root, { recursive: true, force: true });
  }
});
