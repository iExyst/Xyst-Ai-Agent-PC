import assert from "node:assert/strict";
import test from "node:test";
import { createServer } from "node:http";
import type { AddressInfo } from "node:net";
import { existsSync } from "node:fs";
import { mkdtemp, mkdir, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { spawn, type ChildProcess } from "node:child_process";
import { XystStore } from "@xyst/runtime";

type MockToolCall = { id: string; name: string; args: Record<string, unknown> };
type CliResult = { code: number | null; stdout: string; stderr: string };

function resultPayload(stdout: string): unknown {
  return stdout.trim().split(/\r?\n/).flatMap((line) => {
    try { const value = JSON.parse(line) as { type?: unknown; data?: unknown }; return value.type === "result" ? [value.data] : []; }
    catch { return []; }
  }).at(-1);
}

test("real CLI resumes externally edited commands after explicit authorization and owner-crash reconciliation", { timeout: 60_000 }, async () => {
  const scratch = await mkdtemp(join(tmpdir(), "xyst-cli-command-rerun-"));
  const workspace = join(scratch, "workspace"); const appData = join(scratch, "appdata");
  await mkdir(workspace); await mkdir(appData);
  await writeFile(join(workspace, "phase.txt"), "PHASE_A\n", "utf8");
  await writeFile(join(workspace, "probe.mjs"), 'import { existsSync, readFileSync, writeFileSync } from "node:fs";\nconst mode = existsSync("crash-mode.txt") ? readFileSync("crash-mode.txt", "utf8").trim() : "";\nif (mode === "armed") { const ownerPid = process.ppid; writeFileSync("crash-probe-ready.txt", String(ownerPid)); await new Promise((resolve) => setTimeout(resolve, 5000)); try { process.kill(ownerPid, 0); } catch { process.exit(0); } writeFileSync("crash-effect.txt", "applied"); }\nif (mode === "retry") writeFileSync("crash-effect.txt", "applied");\nprocess.stdout.write(readFileSync("phase.txt", "utf8"));\n', "utf8");

  const modelTurns: MockToolCall[] = [
    { id: "call-run-initial", name: "run_process", args: { executable: process.execPath, args: ["probe.mjs"], cwd: "." } },
    { id: "call-complete-initial", name: "task_complete", args: { evidence_tool_call_ids: ["call-run-initial"] } },
    { id: "call-run-rerun", name: "run_process", args: { executable: process.execPath, args: ["probe.mjs"], cwd: "." } },
    { id: "call-read-rerun", name: "file_read", args: { path: "phase.txt" } },
    { id: "call-complete-rerun", name: "task_complete", args: { evidence_tool_call_ids: ["call-run-rerun", "call-read-rerun"] } },
    { id: "call-run-crash", name: "run_process", args: { executable: process.execPath, args: ["probe.mjs"], cwd: "." } },
    { id: "call-run-retry", name: "run_process", args: { executable: process.execPath, args: ["probe.mjs"], cwd: "." } },
    { id: "call-read-retry", name: "file_read", args: { path: "phase.txt" } },
    { id: "call-complete-retry", name: "task_complete", args: { evidence_tool_call_ids: ["call-run-retry", "call-read-retry"] } }
  ];
  let turn = 0; let modelLoadRequests = 0;
  const runningChildren = new Set<ChildProcess>();
  let unrelatedLiveProcess: ChildProcess | undefined;
  const server = createServer((request, response) => {
    if (request.method === "GET" && request.url === "/api/v1/models") {
      response.writeHead(200, { "content-type": "application/json" });
      response.end(JSON.stringify({ models: [{ type: "llm", publisher: "fixture", key: "mock-model", display_name: "Mock model", size_bytes: 1, max_context_length: 32768, loaded_instances: [{ id: "mock-model", config: { context_length: 32768 } }], capabilities: { trained_for_tool_use: true } }] }));
      return;
    }
    if (request.method === "GET" && request.url === "/v1/models") {
      response.writeHead(200, { "content-type": "application/json" }); response.end(JSON.stringify({ data: [{ id: "mock-model" }] })); return;
    }
    if (request.url?.startsWith("/api/v1/models/load")) {
      modelLoadRequests++; response.writeHead(500); response.end("model loading is disabled in this fixture"); return;
    }
    if (request.method === "POST" && request.url === "/v1/chat/completions") {
      request.resume();
      request.once("end", () => {
        const call = modelTurns[turn++];
        if (!call) { response.writeHead(500); response.end("Unexpected fixture inference request"); return; }
        const chunk = { id: `fixture-${turn}`, choices: [{ index: 0, delta: { tool_calls: [{ index: 0, id: call.id, type: "function", function: { name: call.name, arguments: JSON.stringify(call.args) } }] }, finish_reason: "tool_calls" }] };
        response.writeHead(200, { "content-type": "text/event-stream", "cache-control": "no-cache" });
        response.end(`data: ${JSON.stringify(chunk)}\n\ndata: [DONE]\n\n`);
      });
      return;
    }
    response.writeHead(404); response.end("Unexpected mock provider endpoint");
  });
  const listen = () => new Promise<void>((resolve, reject) => { server.once("error", reject); server.listen(0, "127.0.0.1", resolve); });
  const close = () => new Promise<void>((resolve, reject) => server.close((error) => error ? reject(error) : resolve()));
  const cliEntry = join(dirname(fileURLToPath(import.meta.url)), "index.ts");
  let port = 0;
  const startCli = (args: string[]) => {
    const child = spawn(process.execPath, ["--import", import.meta.resolve("tsx"), cliEntry, ...args], {
      cwd: workspace,
      env: { ...process.env, APPDATA: appData, XDG_DATA_HOME: appData, HOME: appData, XYST_ROOT: workspace, LM_STUDIO_BASE_URL: `http://127.0.0.1:${port}`, LM_API_TOKEN: "fixture-token" },
      windowsHide: true,
      stdio: ["ignore", "pipe", "pipe"]
    });
    runningChildren.add(child);
    child.once("close", () => runningChildren.delete(child));
    let stdout = ""; let stderr = "";
    let timer: NodeJS.Timeout;
    const result = new Promise<CliResult>((resolve, reject) => {
      timer = setTimeout(() => { child.kill("SIGKILL"); reject(new Error(`CLI invocation timed out: ${args.join(" ")}\n${stderr}`)); }, 15_000);
      child.once("error", (error) => { clearTimeout(timer); reject(error); });
      child.once("close", (code) => { clearTimeout(timer); resolve({ code, stdout, stderr }); });
    });
    child.stdout.setEncoding("utf8"); child.stderr.setEncoding("utf8");
    child.stdout.on("data", (chunk: string) => { stdout += chunk; }); child.stderr.on("data", (chunk: string) => { stderr += chunk; });
    return { child, result };
  };
  const invoke = (args: string[]) => startCli(args).result;

  try {
    await listen(); port = (server.address() as AddressInfo).port;
    const config = await invoke(["config", "set", "autonomy.externalActionApproval", "false"]);
    assert.equal(config.code, 0, config.stderr || config.stdout);
    const initial = await invoke(["--json", "agent", "run", "--model", "mock-model", "--max-turns", "4", "Run probe.mjs and report its stdout exactly."]);
    assert.equal(initial.code, 0, initial.stderr || initial.stdout);
    const initialResult = resultPayload(initial.stdout) as { sessionId?: unknown; status?: unknown } | undefined;
    assert.equal(initialResult?.status, "completed", initial.stdout);
    assert.equal(typeof initialResult?.sessionId, "string");
    const sessionId = initialResult.sessionId as string;

    const initialCalls = await invoke(["--json", "session", "calls", sessionId]); assert.equal(initialCalls.code, 0, initialCalls.stderr);
    const firstRows = resultPayload(initialCalls.stdout) as Array<{ id: string; status: string; output_json: string }>;
    const firstRun = firstRows.find((row) => row.id === "call-run-initial");
    assert.equal(firstRun?.status, "completed", JSON.stringify(firstRows)); assert.match(JSON.parse(firstRun!.output_json).stdout, /PHASE_A/);

    await writeFile(join(workspace, "phase.txt"), "PHASE_B\n", "utf8");
    const authorized = await invoke(["--json", "agent", "authorize-rerun", sessionId, "call-run-initial"]);
    assert.equal(authorized.code, 0, authorized.stderr || authorized.stdout);
    const resumed = await invoke(["--json", "agent", "resume", sessionId, "phase.txt was externally changed to PHASE_B; rerun the authorized command, read the current file, and verify both results.", "--model", "mock-model", "--max-turns", "4"]);
    assert.equal(resumed.code, 0, resumed.stderr || resumed.stdout);
    const resumedResult = resultPayload(resumed.stdout) as { status?: unknown } | undefined;
    assert.equal(resumedResult?.status, "completed", resumed.stdout);
    const finalCalls = await invoke(["--json", "session", "calls", sessionId]); assert.equal(finalCalls.code, 0, finalCalls.stderr);
    const rows = resultPayload(finalCalls.stdout) as Array<{ id: string; status: string; output_json: string; is_rerun: number }>;
    const rerun = rows.find((row) => row.id === "call-run-rerun"); const completion = rows.find((row) => row.id === "call-complete-rerun");
    assert.equal(rerun?.status, "completed", JSON.stringify(rows)); assert.equal(rerun?.is_rerun, 1, JSON.stringify(rerun));
    assert.match(JSON.parse(rerun!.output_json).stdout, /PHASE_B/);
    assert.equal(completion?.status, "completed", JSON.stringify(rows));
    assert.equal(await readFile(join(workspace, "phase.txt"), "utf8"), "PHASE_B\n");

    await writeFile(join(workspace, "crash-mode.txt"), "armed\n", "utf8");
    const crashAuthorization = await invoke(["--json", "agent", "authorize-rerun", sessionId, "call-run-rerun"]);
    assert.equal(crashAuthorization.code, 0, crashAuthorization.stderr || crashAuthorization.stdout);
    const interrupted = startCli(["--json", "agent", "resume", sessionId, "Run the next authorized command and confirm the current phase.", "--model", "mock-model", "--max-turns", "4"]);
    assert.ok(interrupted.child.pid);
    const deadline = Date.now() + 10_000;
    let crashCalls: Array<{ id: string; status: string }> = [];
    while (Date.now() < deadline) {
      const response = await invoke(["--json", "session", "calls", sessionId]);
      assert.equal(response.code, 0, response.stderr);
      crashCalls = resultPayload(response.stdout) as Array<{ id: string; status: string }>;
      if (crashCalls.some((row) => row.id === "call-run-crash" && row.status === "running") && existsSync(join(workspace, "crash-probe-ready.txt"))) break;
      await new Promise((resolve) => setTimeout(resolve, 80));
    }
    assert.equal(crashCalls.find((row) => row.id === "call-run-crash")?.status, "running", JSON.stringify(crashCalls));
    assert.equal(await readFile(join(workspace, "crash-probe-ready.txt"), "utf8"), String(interrupted.child.pid), "the delayed command must record the exact live CLI owner before it is killed");
    const databasePath = join(appData, "xyst", "xyst.sqlite");
    const ownerStore = new XystStore(databasePath);
    let crashedOwner: { host: string; pid: number; process_start_identity: string | null } | undefined;
    try {
      crashedOwner = ownerStore.db.prepare("SELECT host,pid,process_start_identity FROM command_rerun_owners WHERE tool_call_id='call-run-crash'").get() as typeof crashedOwner;
    assert.ok(crashedOwner, `the claimed command rerun must have a durable owner record (calls=${JSON.stringify(ownerStore.db.prepare("SELECT id,status,request_hash FROM tool_calls WHERE session_id=?").all(sessionId))}; heads=${JSON.stringify(ownerStore.db.prepare("SELECT * FROM command_replay_heads").all())}; auth=${JSON.stringify(ownerStore.db.prepare("SELECT * FROM command_rerun_authorizations").all())}; owners=${JSON.stringify(ownerStore.db.prepare("SELECT * FROM command_rerun_owners").all())})`);
      assert.equal(crashedOwner.pid, interrupted.child.pid, "the durable rerun owner must match the exact CLI process being terminated");
      assert.ok(crashedOwner.process_start_identity, "the durable rerun owner must carry its process creation identity");
    } finally { ownerStore.close(); }
    unrelatedLiveProcess = spawn(process.execPath, ["-e", "setInterval(() => {}, 1_000)"], { windowsHide: true, stdio: "ignore" });
    await new Promise<void>((resolve, reject) => { unrelatedLiveProcess!.once("error", reject); unrelatedLiveProcess!.once("spawn", () => resolve()); });
    assert.ok(unrelatedLiveProcess.pid && unrelatedLiveProcess.pid !== interrupted.child.pid);
    interrupted.child.kill("SIGKILL");
    await interrupted.result;
    const reusedOwnerStore = new XystStore(databasePath);
    try {
      const changed = reusedOwnerStore.db.prepare("UPDATE command_rerun_owners SET pid=? WHERE tool_call_id='call-run-crash'").run(unrelatedLiveProcess.pid);
      assert.equal(changed.changes, 1, "the fixture must preserve the crashed owner's identity while simulating its PID being reused");
    } finally { reusedOwnerStore.close(); }
    await new Promise((resolve) => setTimeout(resolve, 6_000));
    await assert.rejects(readFile(join(workspace, "crash-effect.txt"), "utf8"), { code: "ENOENT" });

    const recovered = await invoke(["--json", "agent", "recover-rerun", sessionId, "call-run-crash"]);
    assert.equal(recovered.code, 0, recovered.stderr || recovered.stdout);
    assert.match(JSON.stringify(resultPayload(recovered.stdout)), /uncertain/);
    const quarantinedCalls = await invoke(["--json", "session", "calls", sessionId]);
    assert.equal(quarantinedCalls.code, 0, quarantinedCalls.stderr);
    const quarantinedRows = resultPayload(quarantinedCalls.stdout) as Array<{ id: string; status: string }>;
    assert.equal(quarantinedRows.find((row) => row.id === "call-run-crash")?.status, "uncertain", JSON.stringify(quarantinedRows));
    const retryAuthorization = await invoke(["--json", "agent", "reconcile", sessionId, "call-run-crash", "retry"]);
    assert.equal(retryAuthorization.code, 0, retryAuthorization.stderr || retryAuthorization.stdout);
    await writeFile(join(workspace, "crash-mode.txt"), "retry\n", "utf8");
    const retried = await invoke(["--json", "agent", "resume", sessionId, "The owner-crashed command was confirmed to have no effect; run the authorized retry, read phase.txt again, and verify both results.", "--model", "mock-model", "--max-turns", "4"]);
    assert.equal(retried.code, 0, retried.stderr || retried.stdout);
    assert.equal((resultPayload(retried.stdout) as { status?: unknown } | undefined)?.status, "completed", retried.stdout);
    const crashCompletionCalls = await invoke(["--json", "session", "calls", sessionId]);
    assert.equal(crashCompletionCalls.code, 0, crashCompletionCalls.stderr);
    const recoveredRows = resultPayload(crashCompletionCalls.stdout) as Array<{ id: string; status: string; output_json: string; is_rerun: number }>;
    const crashedCall = recoveredRows.find((row) => row.id === "call-run-crash");
    const retryCall = recoveredRows.find((row) => row.id === "call-run-retry");
    assert.equal(crashedCall?.status, "retry_consumed", JSON.stringify(crashedCall));
    assert.equal(retryCall?.status, "completed", JSON.stringify(retryCall));
    assert.equal(retryCall?.is_rerun, 1, JSON.stringify(retryCall));
    assert.match(JSON.parse(retryCall!.output_json).stdout, /PHASE_B/);
    assert.equal(await readFile(join(workspace, "crash-effect.txt"), "utf8"), "applied");
    assert.equal(turn, 9, `expected nine deterministic CLI inference requests; got ${turn}`);
    assert.equal(modelLoadRequests, 0, "the test must never load a local model");
  } finally {
    const childrenToStop = [...runningChildren];
    if (unrelatedLiveProcess && unrelatedLiveProcess.exitCode === null && unrelatedLiveProcess.signalCode === null) unrelatedLiveProcess.kill("SIGKILL");
    if (unrelatedLiveProcess) childrenToStop.push(unrelatedLiveProcess);
    const closed = childrenToStop.map((child) => new Promise<void>((resolve) => {
      if (child.exitCode !== null || child.signalCode !== null) resolve();
      else child.once("close", () => resolve());
    }));
    for (const child of childrenToStop) child.kill("SIGKILL");
    await Promise.all(closed);
    if (server.listening) await close();
    await rm(scratch, { recursive: true, force: true });
  }
});
