import test from "node:test";
import assert from "node:assert/strict";
import { constants } from "node:os";
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
