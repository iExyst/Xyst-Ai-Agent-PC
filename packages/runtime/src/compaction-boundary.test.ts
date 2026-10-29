import test from "node:test";
import assert from "node:assert/strict";
import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { isAbsolute, join, relative } from "node:path";
import { SessionCompactor } from "./session-compaction.js";
import { SessionStore, XystStore } from "./storage.js";
import { estimateRequiredContextTokens } from "./model-selection.js";

async function withStore(run: (store: XystStore, sessions: SessionStore, sessionId: string) => Promise<void>) {
  const root = await mkdtemp(join(tmpdir(), "xyst-compaction-review-"));
  const relativeRoot = relative(tmpdir(), root);
  if (!relativeRoot || relativeRoot.startsWith("..") || isAbsolute(relativeRoot)) throw new Error("Unexpected temporary fixture path");
  const store = new XystStore(join(root, "test.sqlite"));
  try { await run(store, new SessionStore(store), new SessionStore(store).create("Compaction review")); }
  finally { store.close(); await rm(root, { recursive: true, force: true, maxRetries: 5, retryDelay: 100 }); }
}

function seedToolChain(sessions: SessionStore, sessionId: string, largeFirstOutput = false) {
  const add = (role: "user" | "assistant" | "tool", value: Record<string, unknown>) => sessions.addMessage(sessionId, role, `__xyst_api_message__:${JSON.stringify(value)}`);
  add("user", { role: "user", content: "Inspect the workspace." });
  for (let i = 0; i < 6; i++) {
    const id = `call-${i}`;
    add("assistant", { role: "assistant", content: null, tool_calls: [{ id, type: "function", function: { name: "file_read", arguments: "{}" } }] });
    add("tool", { role: "tool", tool_call_id: id, content: largeFirstOutput && i === 0 ? "X".repeat(30_000) : `result ${i}` });
  }
}

const response = { choices: [{ message: { content: JSON.stringify({ decisions: [], dependencies: [], completed_work: [], outstanding_actions: [], durable_facts: [] }) } }] };

test("compaction crosses a large completed tool output while retaining the raw record", async () => withStore(async (store, sessions, sessionId) => {
  seedToolChain(sessions, sessionId, true);
  let history: Array<{ role: string; tool_call_id?: string; content: string }> = [];
  const provider = {
    isLocalServer: () => true, redact: (value: string) => value,
    models: async () => [{ type: "llm", key: "fixture", max_context_length: 8192, loaded_instances: [{ id: "fixture", config: { context_length: 8192 } }] }],
    structuredOutput: async (request: { messages: Array<{ content: string }> }) => {
      history = (JSON.parse(request.messages[1]!.content) as { completed_turn_history: typeof history }).completed_turn_history;
      return response;
    }
  };
  const result = await new SessionCompactor(store, sessions, provider as never).compact(sessionId, "fixture");
  assert.equal(result.compactedMessages, 9);
  assert.match(history.find((entry) => entry.tool_call_id === "call-0")?.content ?? "", /characters omitted from this summary excerpt/);
  assert.equal(history.find((entry) => entry.tool_call_id === "call-0")?.content.length, 1013);
  const raw = sessions.messages(sessionId) as Array<{ content: string }>;
  assert.ok(raw.some((row) => row.content.includes("X".repeat(30_000))));
  assert.equal(sessions.contextMessages(sessionId).length, 6, "includes a synthetic user continuation for the retained completed tool-only tail");
}));

test("automatic compaction requires the originally selected resident instance in fresh inventory", async () => withStore(async (store, sessions, sessionId) => {
  seedToolChain(sessions, sessionId);
  let inferenceCalls = 0; let instances = [{ id: "another-instance", config: { context_length: 8192 } }];
  const provider = {
    isLocalServer: () => true, redact: (value: string) => value,
    models: async () => [{ type: "llm", key: "fixture", max_context_length: 8192, loaded_instances: instances }],
    structuredOutput: async () => { inferenceCalls++; return response; }
  };
  const compactor = new SessionCompactor(store, sessions, provider as never);
  await assert.rejects(() => compactor.compact(sessionId, "fixture", undefined, { requireLoadedInstanceId: "original-instance" }), /resident compaction instance.*no longer loaded/);
  assert.equal(inferenceCalls, 0); assert.equal(sessions.compactions(sessionId).length, 0);
  instances = [];
  await compactor.compact(sessionId, "fixture");
  assert.equal(inferenceCalls, 1, "explicit manual compaction keeps its established behavior");
}));

test("compacted tool-only resumes receive a user continuation only after all tool calls are paired", async () => withStore(async (store, sessions, sessionId) => {
  seedToolChain(sessions, sessionId);
  const provider = {
    isLocalServer: () => true, redact: (value: string) => value,
    models: async () => [{ type: "llm", key: "fixture", max_context_length: 8192, loaded_instances: [{ id: "fixture", config: { context_length: 8192 } }] }],
    structuredOutput: async () => response
  };
  await new SessionCompactor(store, sessions, provider as never).compact(sessionId, "fixture");
  const context = sessions.contextMessages(sessionId);
  assert.equal(context.at(-1)?.role, "user");
  assert.match(context.at(-1)?.content ?? "", /Continue the saved task[\s\S]*Inspect the workspace/);

  sessions.addMessage(sessionId, "assistant", `__xyst_api_message__:${JSON.stringify({ role: "assistant", content: null, tool_calls: [{ id: "unresolved-call", type: "function", function: { name: "file_read", arguments: "{}" } }] })}`);
  const unresolved = sessions.contextMessages(sessionId);
  assert.notEqual(unresolved.at(-1)?.role, "user", "do not append a synthetic prompt after an unresolved tool call");
}));

test("compaction omits the prior compacted call journal from the next summary prompt", async () => withStore(async (store, sessions, sessionId) => {
  seedToolChain(sessions, sessionId);
  const first = sessions.messages(sessionId)[0] as { id: string };
  const compactedCallJournal = Array.from({ length: 40 }, (_, index) => ({ id: `old-${index}`, input: "x".repeat(900), result: "y".repeat(900) }));
  sessions.addCompaction(sessionId, first.id, {
    version: 1, primary_goal: "Inspect the workspace.", user_goals: ["Inspect the workspace."],
    decisions: ["Keep prior decisions."], dependencies: [], completed_work: [], outstanding_actions: [], durable_facts: [], compacted_tool_calls: compactedCallJournal
  });
  let promptPayload: Record<string, unknown> | undefined; let estimatedTokens = 0;
  const provider = {
    isLocalServer: () => true, redact: (value: string) => value,
    models: async () => [{ type: "llm", key: "fixture", max_context_length: 8192, loaded_instances: [{ id: "fixture", config: { context_length: 8192 } }] }],
    structuredOutput: async (request: { messages: Array<{ content: string }> }) => {
      promptPayload = JSON.parse(request.messages[1]!.content) as Record<string, unknown>;
      estimatedTokens = estimateRequiredContextTokens(request.messages, 1800);
      return response;
    }
  };
  const result = await new SessionCompactor(store, sessions, provider as never).compact(sessionId, "fixture");
  assert.ok(estimatedTokens <= 8192, `summary request estimate ${estimatedTokens} must fit the resident context`);
  const previous = JSON.parse(String(promptPayload?.previous_compaction)) as Record<string, unknown>;
  assert.equal("compacted_tool_calls" in previous, false);
  assert.deepEqual(previous.decisions, ["Keep prior decisions."]);
  assert.equal((result.summary as { compacted_tool_calls: unknown[] }).compacted_tool_calls.length, compactedCallJournal.length + 4, "full prior and newly compacted call evidence remains durable");
}));

test("compaction shortens its safe history prefix when tool-pair metadata would overflow context", async () => withStore(async (store, sessions, sessionId) => {
  seedToolChain(sessions, sessionId);
  for (let i = 6; i < 24; i++) {
    const id = `large-call-${i}`;
    sessions.addMessage(sessionId, "assistant", `__xyst_api_message__:${JSON.stringify({ role: "assistant", content: null, tool_calls: [{ id, type: "function", function: { name: "file_read", arguments: JSON.stringify({ path: `file-${i}.txt` }) } }] })}`);
    sessions.addMessage(sessionId, "tool", `__xyst_api_message__:${JSON.stringify({ role: "tool", tool_call_id: id, content: `result-${i}-` + "x".repeat(900) })}`);
  }
  let estimatedTokens = 0;
  const provider = {
    isLocalServer: () => true, redact: (value: string) => value,
    models: async () => [{ type: "llm", key: "fixture", max_context_length: 8192, loaded_instances: [{ id: "fixture", config: { context_length: 8192 } }] }],
    structuredOutput: async (request: { messages: Array<{ content: string }> }) => {
      estimatedTokens = estimateRequiredContextTokens(request.messages, 1800);
      return response;
    }
  };
  const result = await new SessionCompactor(store, sessions, provider as never).compact(sessionId, "fixture");
  assert.ok(estimatedTokens <= 8192, `selected prompt estimate ${estimatedTokens} must fit the resident context`);
  const compactedMessages = Number(result.compactedMessages);
  assert.ok(compactedMessages < 30, `fallback should select a smaller safe prefix, got ${compactedMessages}`);
}));
