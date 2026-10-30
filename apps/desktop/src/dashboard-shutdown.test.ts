import assert from "node:assert/strict";
import test from "node:test";
import type { ChildProcess } from "node:child_process";
import { createDashboardQuitGuard } from "./dashboard-shutdown.js";

function child(pid: number): ChildProcess {
  return { pid, exitCode: null, signalCode: null } as ChildProcess;
}
function event() {
  let prevented = false;
  return { preventDefault() { prevented = true; }, get prevented() { return prevented; } };
}
const tick = () => new Promise<void>((resolve) => setImmediate(resolve));

test("dashboard shutdown blocks quit until worker-tree cleanup is confirmed", async () => {
  const outcomes = [false, true]; let quits = 0; let reports = 0; const stopped: number[] = [];
  const guard = createDashboardQuitGuard(async (pid) => { stopped.push(pid); return outcomes.shift()!; }, () => { quits++; }, () => { reports++; });

  const first = event(); guard(first, [child(41)]);
  assert.equal(first.prevented, true);
  const overlapping = event(); guard(overlapping, [child(41)]);
  assert.equal(overlapping.prevented, true);
  await tick();
  assert.equal(quits, 0);
  assert.equal(reports, 1);

  const retry = event(); guard(retry, [child(41)]);
  assert.equal(retry.prevented, true);
  await tick();
  assert.equal(quits, 1);
  const internalQuit = event(); guard(internalQuit, [child(41)]);
  assert.equal(internalQuit.prevented, false);
  assert.deepEqual(stopped, [41, 41]);
});

test("dashboard shutdown allows quit when no worker process remains", () => {
  let stops = 0; let quits = 0; let reports = 0;
  const guard = createDashboardQuitGuard(async () => { stops++; return false; }, () => { quits++; }, () => { reports++; });
  const alreadyExited = { pid: 42, exitCode: 0, signalCode: null } as ChildProcess;
  const noPid = { pid: undefined, exitCode: null, signalCode: null } as ChildProcess;
  const allowed = event(); guard(allowed, [alreadyExited, noPid]);
  assert.equal(allowed.prevented, false);
  assert.deepEqual({ stops, quits, reports }, { stops: 0, quits: 0, reports: 0 });
});
