import assert from "node:assert/strict";
import test from "node:test";
import { AgentEventStore } from "./agent-event-store.js";

test("agent event store batches updates and retains only the latest 80 events", async () => {
  const store = new AgentEventStore();
  for (let index = 0; index < 82; index++) store.publish({ type: "agent.text", data: { index } });
  assert.equal(store.getSnapshot().length, 0, "pending streamed events are not rendered synchronously");
  await new Promise((resolve) => setTimeout(resolve, 120));
  const events = store.getSnapshot();
  assert.equal(events.length, 80);
  assert.equal(events[0]?.data?.index, 2);
  assert.equal(events.at(-1)?.data?.index, 81);
});

test("clearing agent events notifies subscribers and empty clears are no-ops", async () => {
  const store = new AgentEventStore();
  let updates = 0;
  const unsubscribe = store.subscribe(() => { updates++; });
  store.clear();
  assert.equal(updates, 0);
  store.publish({ type: "agent.started" });
  assert.equal(updates, 0);
  await new Promise((resolve) => setTimeout(resolve, 120));
  assert.equal(updates, 1);
  store.clear();
  assert.equal(store.getSnapshot().length, 0);
  assert.equal(updates, 2);
  store.clear();
  assert.equal(updates, 2);
  unsubscribe();
  store.publish({ type: "agent.finished" });
  await new Promise((resolve) => setTimeout(resolve, 120));
  assert.equal(updates, 2);
});

test("clearing pending agent events cancels their notification", async () => {
  const store = new AgentEventStore();
  let updates = 0;
  store.subscribe(() => { updates++; });
  store.publish({ type: "agent.text" });
  store.clear();
  await new Promise((resolve) => setTimeout(resolve, 120));
  assert.equal(store.getSnapshot().length, 0);
  assert.equal(updates, 0);
});

test("agent event store notifies subscribers once per batch", async () => {
  const store = new AgentEventStore();
  let updates = 0;
  store.subscribe(() => { updates++; });
  store.publish({ type: "agent.text", data: { token: 1 } });
  store.publish({ type: "agent.text", data: { token: 2 } });
  assert.equal(updates, 0);
  await new Promise((resolve) => setTimeout(resolve, 120));
  assert.equal(updates, 1);
  assert.deepEqual(store.getSnapshot().map((event) => event.data?.token), [1, 2]);
});
