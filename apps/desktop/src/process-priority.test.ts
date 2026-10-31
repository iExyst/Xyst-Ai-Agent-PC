import test from "node:test";
import assert from "node:assert/strict";
import { constants } from "node:os";
import { readFileSync } from "node:fs";
import { lowerBackgroundProcessPriority } from "./process-priority.js";

test("background priority is lowered on Windows", () => {
  const calls: Array<[number, number]> = [];
  assert.equal(lowerBackgroundProcessPriority(123, "win32", (pid, priority) => calls.push([pid, priority])), true);
  assert.deepEqual(calls, [[123, constants.priority.PRIORITY_BELOW_NORMAL]]);
});

test("background priority is unchanged off Windows or without a PID", () => {
  let calls = 0;
  const apply = () => { calls++; };
  assert.equal(lowerBackgroundProcessPriority(123, "linux", apply), false);
  assert.equal(lowerBackgroundProcessPriority(undefined, "win32", apply), false);
  assert.equal(calls, 0);
});

test("priority errors do not prevent launching background work", () => {
  assert.equal(lowerBackgroundProcessPriority(123, "win32", () => { throw new Error("access denied"); }), false);
});

test("every Desktop CLI child runs below normal priority so mission work cannot outrank window input", () => {
  const source = readFileSync(new URL("./main.ts", import.meta.url), "utf8");
  const start = source.indexOf("function invokeCli(");
  assert.notEqual(start, -1, "invokeCli should exist in the Desktop main process");
  const end = source.indexOf("\nfunction ", start + 1);
  const invokeCli = source.slice(start, end < 0 ? undefined : end);
  const spawn = invokeCli.indexOf("const child = spawn(");
  const lower = invokeCli.indexOf("lowerBackgroundProcessPriority(child.pid);");
  assert.ok(spawn >= 0 && lower > spawn, "lower the spawned CLI worker's priority");
  assert.doesNotMatch(invokeCli.slice(spawn, lower), /if\s*\(agentRunId\)/, "priority must also cover mission workers without an agentRunId");
});

test("interactive Desktop main and renderer processes retain normal Windows scheduling priority", () => {
  const source = readFileSync(new URL("./main.ts", import.meta.url), "utf8");
  const windowStart = source.indexOf("async function createWindow()");
  const windowEnd = source.indexOf("\nasync function ", windowStart + 1);
  const createWindow = source.slice(windowStart, windowEnd < 0 ? undefined : windowEnd);
  assert.doesNotMatch(createWindow, /lowerBackgroundProcessPriority|setPriority|getOSProcessId/, "do not demote the interactive renderer process");
  const instanceLock = source.indexOf("if (!app.requestSingleInstanceLock())");
  const appSetup = source.slice(instanceLock);
  assert.doesNotMatch(appSetup, /lowerBackgroundProcessPriority\(process\.pid\)|setPriority\(process\.pid/, "do not demote the interactive Electron main process");
});
