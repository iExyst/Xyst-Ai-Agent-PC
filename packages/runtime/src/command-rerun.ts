import { createHash, randomUUID } from "node:crypto";
import { hostname } from "node:os";
import { isRecordedProcessAlive, processStartIdentity } from "./process-identity.js";
import type { XystStore } from "./storage.js";

/** Record an explicit one-use authorization without changing the original action. */
export function authorizeCommandRerun(store: XystStore, sessionId: string, toolCallId: string): string {
  return store.db.transaction(() => {
    const action = store.db.prepare("SELECT id,name,status,execution_scope,request_hash FROM tool_calls WHERE id=? AND session_id=? AND effect='external'").get(toolCallId, sessionId) as { id: string; name: string; status: string; execution_scope: string | null; request_hash: string | null } | undefined;
    if (!action || action.name !== "run_process" || action.status !== "completed" || !action.execution_scope || !action.request_hash) throw new Error("Only a completed process action in this session can be authorized for rerun");
    const currentHead = store.db.prepare("SELECT execution_scope,request_hash FROM command_replay_heads WHERE tool_call_id=?").get(action.id) as { execution_scope: string; request_hash: string } | undefined;
    const priorAuthorization = store.db.prepare("SELECT execution_scope,request_hash FROM command_rerun_authorizations WHERE prior_tool_call_id=? OR new_tool_call_id=? ORDER BY rowid DESC LIMIT 1").get(action.id, action.id) as { execution_scope: string; request_hash: string } | undefined;
    const key = currentHead ?? priorAuthorization ?? { execution_scope: action.execution_scope, request_hash: action.request_hash };
    const head = store.db.prepare("SELECT tool_call_id FROM command_replay_heads WHERE execution_scope=? AND request_hash=?").get(key.execution_scope, key.request_hash) as { tool_call_id: string } | undefined;
    if (head && head.tool_call_id !== action.id) throw new Error("This process result is no longer the current replay head");
    store.db.prepare("INSERT OR IGNORE INTO command_replay_heads(execution_scope,request_hash,tool_call_id) VALUES(?,?,?)").run(key.execution_scope, key.request_hash, action.id);
    const id = randomUUID();
    store.db.prepare("INSERT INTO command_rerun_authorizations(id,execution_scope,request_hash,prior_tool_call_id,created_at) VALUES(?,?,?,?,?)").run(id, key.execution_scope, key.request_hash, action.id, new Date().toISOString());
    return id;
  }).immediate();
}

/** Consume one authorization and record the replacement intent in the same transaction. */
export function consumeCommandRerun(store: XystStore, scope: string, requestHash: string, call: { id: string; sessionId: string; inputJson: string }): boolean {
  return store.db.transaction(() => {
    const pending = store.db.prepare("SELECT a.id,a.prior_tool_call_id FROM command_rerun_authorizations a JOIN command_replay_heads h ON h.execution_scope=a.execution_scope AND h.request_hash=a.request_hash AND h.tool_call_id=a.prior_tool_call_id JOIN tool_calls t ON t.id=h.tool_call_id WHERE a.execution_scope=? AND a.request_hash=? AND a.consumed_at IS NULL AND t.name='run_process' AND t.status='completed'").get(scope, requestHash) as { id: string; prior_tool_call_id: string } | undefined;
    if (!pending) return false;
    const now = new Date().toISOString();
    const executionHash = createHash("sha256").update(`command-rerun:${pending.id}`).digest("hex");
    store.db.prepare("INSERT INTO tool_calls(id,session_id,name,input_json,status,created_at,effect,execution_scope,request_hash) VALUES(?,?,'run_process',?,'running',?,'external',?,?)").run(call.id, call.sessionId, call.inputJson, now, scope, executionHash);
    store.db.prepare("INSERT INTO command_rerun_owners(tool_call_id,host,pid,process_start_identity) VALUES(?,?,?,?)").run(call.id, hostname(), process.pid, processStartIdentity(process.pid));
    store.db.prepare("UPDATE command_replay_heads SET tool_call_id=? WHERE execution_scope=? AND request_hash=? AND tool_call_id=?").run(call.id, scope, requestHash, pending.prior_tool_call_id);
    store.db.prepare("UPDATE command_rerun_authorizations SET consumed_at=?,new_tool_call_id=? WHERE id=? AND consumed_at IS NULL").run(now, call.id, pending.id);
    return true;
  }).immediate();
}

/** Preserve an uncertain head when the user has explicitly authorized retry. */
export function consumeCommandHeadRetry(store: XystStore, scope: string, requestHash: string, call: { id: string; sessionId: string; inputJson: string }): boolean {
  return store.db.transaction(() => {
    const head = store.db.prepare("SELECT t.id FROM command_replay_heads h JOIN tool_calls t ON t.id=h.tool_call_id WHERE h.execution_scope=? AND h.request_hash=? AND t.name='run_process' AND t.status='retry_allowed'").get(scope, requestHash) as { id: string } | undefined;
    if (!head) return false;
    const now = new Date().toISOString();
    const hash = createHash("sha256").update(`command-head-retry:${call.id}`).digest("hex");
    store.db.prepare("INSERT INTO tool_calls(id,session_id,name,input_json,status,created_at,effect,execution_scope,request_hash) VALUES(?,?,'run_process',?,'running',?,'external',?,?)").run(call.id, call.sessionId, call.inputJson, now, scope, hash);
    store.db.prepare("INSERT INTO command_rerun_owners(tool_call_id,host,pid,process_start_identity) VALUES(?,?,?,?)").run(call.id, hostname(), process.pid, processStartIdentity(process.pid));
    store.db.prepare("UPDATE tool_calls SET status='retry_consumed' WHERE id=? AND status='retry_allowed'").run(head.id);
    store.db.prepare("UPDATE command_replay_heads SET tool_call_id=? WHERE execution_scope=? AND request_hash=? AND tool_call_id=?").run(call.id, scope, requestHash, head.id);
    return true;
  }).immediate();
}

/** Quarantine a rerun abandoned by its owning process. Never infer that the command had no effect. */
export function quarantineAbandonedCommandRerun(store: XystStore, sessionId: string, toolCallId: string): void {
  store.db.transaction(() => {
    const action = store.db.prepare(`SELECT t.status,o.host,o.pid,o.process_start_identity FROM tool_calls t
      JOIN command_rerun_owners o ON o.tool_call_id=t.id
      JOIN command_replay_heads h ON h.tool_call_id=t.id
      WHERE t.id=? AND t.session_id=? AND t.name='run_process' AND t.effect='external'`).get(toolCallId, sessionId) as { status: string; host: string; pid: number; process_start_identity: string | null } | undefined;
    if (!action || action.status !== "running") throw new Error("This call is not a running command rerun head in the session");
    if (action.host !== hostname()) throw new Error("The command owner is on another host; inspect it there before recovery");
    if (isRecordedProcessAlive(action.pid, action.process_start_identity)) throw new Error("A process currently has the recorded owner PID and its creation identity matches or cannot be verified; recovery is blocked until the owner can be verified exited");
    const changed = store.db.prepare("UPDATE tool_calls SET status='uncertain',output_json=?,finished_at=? WHERE id=? AND status='running'")
      .run(JSON.stringify({ error: "The command owner exited before recording an outcome. Inspect external effects before reconciliation." }), new Date().toISOString(), toolCallId);
    if (changed.changes !== 1) throw new Error("The command outcome changed during recovery");
  }).immediate();
}

export type CommandReplayResolution =
  | { kind: "untracked" }
  | { kind: "claimed" }
  | { kind: "head"; id: string; status: string; outputJson: string | null; tracked: boolean };

/** Serialize authorization consumption and reading the current head. */
export function resolveCommandReplay(store: XystStore, scope: string, requestHash: string, call: { id: string; sessionId: string; inputJson: string }, legacy?: { requestHash: string; workspaceRevision: number }): CommandReplayResolution {
  return store.db.transaction((): CommandReplayResolution => {
    type Row = { id: string; status: string; output_json: string | null; rowid: number };
    const findHead = (key: string) => store.db.prepare("SELECT t.id,t.status,t.output_json,t.rowid FROM command_replay_heads h JOIN tool_calls t ON t.id=h.tool_call_id WHERE h.execution_scope=? AND h.request_hash=?").get(scope, key) as Row | undefined;
    const findOriginal = (key: string) => store.db.prepare("SELECT id,status,output_json,rowid FROM tool_calls WHERE execution_scope=? AND request_hash=? AND effect='external'").get(scope, key) as Row | undefined;
    let key = requestHash;
    let head = findHead(key);
    let tracked = Boolean(head);
    if (!head) head = findOriginal(key);
    if (!head && legacy) {
      const original = findOriginal(legacy.requestHash);
      if (original && legacy.workspaceRevision <= original.rowid) {
        key = legacy.requestHash;
        head = findHead(key) ?? original;
        tracked = head.id !== original.id || Boolean(findHead(key));
      }
    }
    if (!head) return { kind: "untracked" };
    if (tracked && head.status === "completed" && consumeCommandRerun(store, scope, key, call)) return { kind: "claimed" };
    if (tracked && head.status === "retry_allowed" && consumeCommandHeadRetry(store, scope, key, call)) return { kind: "claimed" };
    return { kind: "head", id: head.id, status: head.status, outputJson: head.output_json, tracked };
  }).immediate();
}
