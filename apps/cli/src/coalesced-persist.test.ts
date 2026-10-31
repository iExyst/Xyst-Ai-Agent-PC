import assert from "node:assert/strict";
import { test } from "node:test";
import { createCoalescedPersist } from "./coalesced-persist.js";

const delay = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));
async function waitFor(predicate: () => boolean) {
  const deadline = Date.now() + 1_000;
  while (!predicate() && Date.now() < deadline) await delay(10);
  assert.ok(predicate(), "expected persistence timer to fire before timeout");
}

test("coalesced persistence bounds rapid requests and cancels pending writes", async () => {
  let writes = 0;
  const persistence = createCoalescedPersist(() => { writes += 1; }, 25);

  for (let i = 0; i < 100; i += 1) persistence.schedule();
  await waitFor(() => writes === 1);
  assert.equal(writes, 1, "the immediate leading snapshot is written once");

  for (let i = 0; i < 100; i += 1) persistence.schedule();
  await waitFor(() => writes === 2);
  assert.equal(writes, 2, "rapid chunks coalesce into one trailing snapshot");

  persistence.schedule();
  persistence.cancel();
  await delay(35);
  assert.equal(writes, 2, "cleanup prevents a delayed snapshot after process completion");
});
