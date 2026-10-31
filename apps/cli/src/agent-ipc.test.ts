import assert from "node:assert/strict";
import test from "node:test";
import { disconnectAgentParent } from "./agent-ipc.js";

test("completed desktop agent run disconnects its IPC parent channel", () => {
  let disconnects = 0;
  disconnectAgentParent({ connected: true, disconnect: () => { disconnects++; } });
  assert.equal(disconnects, 1);
});

test("agent run does not disconnect an absent IPC parent", () => {
  let disconnects = 0;
  disconnectAgentParent({ connected: false, disconnect: () => { disconnects++; } });
  assert.equal(disconnects, 0);
});
