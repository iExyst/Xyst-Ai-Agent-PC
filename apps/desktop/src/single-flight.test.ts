import assert from "node:assert/strict";
import test from "node:test";
import { singleFlight } from "./single-flight.js";

test("slow dashboard requests cannot accumulate parallel operations", async () => {
  let started = 0;
  let release!: () => void;
  const gate = new Promise<void>((resolve) => { release = resolve; });
  const read = singleFlight(async () => { started++; await gate; return started; });
  const requests = Array.from({ length: 20 }, () => read());
  await Promise.resolve();
  assert.equal(started, 1);
  assert.ok(requests.every((request) => request === requests[0]));
  release();
  assert.deepEqual(await Promise.all(requests), Array(20).fill(1));
  assert.equal(await read(), 2, "the next refresh reads new state");
});

test("a failed dashboard read releases the gate for recovery", async () => {
  let attempts = 0;
  const read = singleFlight(async () => { if (++attempts === 1) throw new Error("snapshot failed"); return "ready"; });
  await assert.rejects(read(), /snapshot failed/);
  assert.equal(await read(), "ready");
});
