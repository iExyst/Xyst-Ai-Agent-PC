import assert from "node:assert/strict";
import test from "node:test";
import { AgentEventStore } from "./agent-event-store.js";

test("agent event store retains only the latest 80 events", () => {
  const store = new AgentEventStore();
  for (let index = 0; index < 82; index++) store.publish({ type: "agent.text", data: { index } });
  const events = store.getSnapshot();
  assert.equal(events.length, 80);
  assert.equal(events[0]?.data?.index, 2);
  assert.equal(events.at(-1)?.data?.index, 81);
});

test("clearing agent events notifies subscribers and empty clears are no-ops", () => {
  const store = new AgentEventStore();
  let updates = 0;
  const unsubscribe = store.subscribe(() => { updates++; });
  store.clear();
  assert.equal(updates, 0);
  store.publish({ type: "agent.started" });
  assert.equal(updates, 1);
  store.clear();
  assert.equal(store.getSnapshot().length, 0);
  assert.equal(updates, 2);
  store.clear();
  assert.equal(updates, 2);
  unsubscribe();
  store.publish({ type: "agent.finished" });
  assert.equal(updates, 2);
});
