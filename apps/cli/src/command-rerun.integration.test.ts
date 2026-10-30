import assert from "node:assert/strict";
import test from "node:test";
import { createServer } from "node:http";
import type { AddressInfo } from "node:net";
import { mkdtemp, mkdir, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { spawn } from "node:child_process";

type MockToolCall = { id: string; name: string; args: Record<string, unknown> };
type CliResult = { code: number | null; stdout: string; stderr: string };

function resultPayload(stdout: string): unknown {
  return stdout.trim().split(/\r?\n/).flatMap((line) => {
    try { const value = JSON.parse(line) as { type?: unknown; data?: unknown }; return value.type === "result" ? [value.data] : []; }
    catch { return []; }
  }).at(-1);
}

test("real CLI resumes an externally edited command only after explicit rerun authorization", { timeout: 30_000 }, async () => {
  const scratch = await mkdtemp(join(tmpdir(), "xyst-cli-command-rerun-"));
  const workspace = join(scratch, "workspace"); const appData = join(scratch, "appdata");
  await mkdir(workspace); await mkdir(appData);
  await writeFile(join(workspace, "phase.txt"), "PHASE_A\n", "utf8");
  await writeFile(join(workspace, "probe.mjs"), 'import { readFileSync } from "node:fs"; process.stdout.write(readFileSync("phase.txt", "utf8"));\n', "utf8");

  const modelTurns: MockToolCall[] = [
    { id: "call-run-initial", name: "run_process", args: { executable: process.execPath, args: ["probe.mjs"], cwd: "." } },
    { id: "call-complete-initial", name: "task_complete", args: { evidence_tool_call_ids: ["call-run-initial"] } },
    { id: "call-run-rerun", name: "run_process", args: { executable: process.execPath, args: ["probe.mjs"], cwd: "." } },
    { id: "call-read-rerun", name: "file_read", args: { path: "phase.txt" } },
    { id: "call-complete-rerun", name: "task_complete", args: { evidence_tool_call_ids: ["call-run-rerun", "call-read-rerun"] } }
  ];
  let turn = 0; let modelLoadRequests = 0;
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
  const invoke = (args: string[]) => new Promise<CliResult>((resolve, reject) => {
    const child = spawn(process.execPath, ["--import", import.meta.resolve("tsx"), cliEntry, ...args], {
      cwd: workspace,
      env: { ...process.env, APPDATA: appData, XDG_DATA_HOME: appData, HOME: appData, XYST_ROOT: workspace, LM_STUDIO_BASE_URL: `http://127.0.0.1:${port}`, LM_API_TOKEN: "fixture-token" },
      windowsHide: true,
      stdio: ["ignore", "pipe", "pipe"]
    });
    let stdout = ""; let stderr = "";
    const timer = setTimeout(() => { child.kill(); reject(new Error(`CLI invocation timed out: ${args.join(" ")}\n${stderr}`)); }, 15_000);
    child.stdout.setEncoding("utf8"); child.stderr.setEncoding("utf8");
    child.stdout.on("data", (chunk: string) => { stdout += chunk; }); child.stderr.on("data", (chunk: string) => { stderr += chunk; });
    child.once("error", (error) => { clearTimeout(timer); reject(error); });
    child.once("close", (code) => { clearTimeout(timer); resolve({ code, stdout, stderr }); });
  });

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
    assert.equal(turn, 5, `expected five deterministic CLI inference requests; got ${turn}`);
    assert.equal(modelLoadRequests, 0, "the test must never load a local model");
  } finally {
    if (server.listening) await close();
    await rm(scratch, { recursive: true, force: true });
  }
});
