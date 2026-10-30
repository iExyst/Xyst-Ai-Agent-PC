import assert from "node:assert/strict";
import { spawn, spawnSync } from "node:child_process";
import { createServer } from "node:http";
import { access, mkdir, mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { resolve, join, relative, isAbsolute, basename } from "node:path";

if (process.platform !== "win32") throw new Error("The packaged Windows UI smoke only runs on Windows.");

const root = process.cwd();
const version = JSON.parse(await readFile(join(root, "package.json"), "utf8")).version;
const packageDir = resolve(process.env.XYST_PACKAGE_DIR ?? join(root, "release"));
const installer = join(packageDir, `Xyst Setup ${version}.exe`);
const scratch = await mkdtemp(join(tmpdir(), "xyst-clean-install-"));
const scratchRoot = resolve(tmpdir());
const scratchPath = resolve(scratch);
const scratchRelative = relative(scratchRoot, scratchPath);
if (!scratchRelative || scratchRelative.startsWith("..") || isAbsolute(scratchRelative) || !basename(scratchPath).startsWith("xyst-clean-install-")) {
  throw new Error(`Refusing to use an unexpected clean-install scratch directory: ${scratchPath}`);
}
const installDir = join(scratch, "installed-app");
const installedExe = join(installDir, "Xyst.exe");
const dataDir = join(scratch, "data");
const workDir = join(scratch, "workspace");
const env = {
  ...process.env,
  LOCALAPPDATA: join(scratch, "localappdata"),
  APPDATA: join(scratch, "appdata"),
  XYST_ROOT: dataDir,
  LM_STUDIO_BASE_URL: "http://127.0.0.1:9",
  LM_API_TOKEN: "packaged-smoke-token"
};
let app;
let fixtureTurn = 0;
let modelLoadRequests = 0;
let pauseRerunResponse = false;
let notifyRerunRequest;
let releaseRerunResponse;
let pauseCrashResponse = false;
let notifyCrashRequest;
let releaseCrashResponse;
const rerunRequestReceived = new Promise((resolveRequest) => { notifyRerunRequest = resolveRequest; });
const rerunResponseGate = new Promise((resolveResponse) => { releaseRerunResponse = resolveResponse; });
const crashRequestReceived = new Promise((resolveRequest) => { notifyCrashRequest = resolveRequest; });
const crashResponseGate = new Promise((resolveResponse) => { releaseCrashResponse = resolveResponse; });
const fixtureTurns = [
  { id: "desktop-smoke-run-initial", name: "run_process", args: { executable: process.execPath, args: ["probe.mjs"], cwd: "." } },
  { id: "desktop-smoke-complete-initial", name: "task_complete", args: { evidence_tool_call_ids: ["desktop-smoke-run-initial"] } },
  { id: "desktop-smoke-run-rerun", name: "run_process", args: { executable: process.execPath, args: ["probe.mjs"], cwd: "." } },
  { id: "desktop-smoke-read-rerun", name: "file_read", args: { path: "phase.txt" } },
  { id: "desktop-smoke-complete-rerun", name: "task_complete", args: { evidence_tool_call_ids: ["desktop-smoke-run-rerun", "desktop-smoke-read-rerun"] } },
  { id: "desktop-smoke-run-crash", name: "run_process", args: { executable: process.execPath, args: ["probe.mjs"], cwd: "." } },
  { id: "desktop-smoke-run-retry", name: "run_process", args: { executable: process.execPath, args: ["probe.mjs"], cwd: "." } },
  { id: "desktop-smoke-read-retry", name: "file_read", args: { path: "phase.txt" } },
  { id: "desktop-smoke-complete-retry", name: "task_complete", args: { evidence_tool_call_ids: ["desktop-smoke-run-retry", "desktop-smoke-read-retry"] } }
];
const provider = createServer((request, response) => {
  if (request.method === "GET" && request.url === "/api/v1/models") {
    response.writeHead(200, { "content-type": "application/json" });
    response.end(JSON.stringify({ models: [{ type: "llm", publisher: "fixture", key: "packaged-smoke-model", display_name: "Packaged Smoke Model", size_bytes: 1, max_context_length: 32768, loaded_instances: [{ id: "packaged-smoke-model", config: { context_length: 32768 } }], capabilities: { trained_for_tool_use: true } }] }));
    return;
  }
  if (request.method === "GET" && request.url === "/v1/models") {
    response.writeHead(200, { "content-type": "application/json" }); response.end(JSON.stringify({ data: [{ id: "packaged-smoke-model" }] })); return;
  }
  if (request.url?.startsWith("/api/v1/models/load")) {
    modelLoadRequests++; response.writeHead(500); response.end("packaged smoke must not load a model"); return;
  }
  if (request.method === "POST" && request.url === "/v1/chat/completions") {
    request.resume();
    request.once("end", async () => {
      const index = fixtureTurn++;
      if (index === 2 && pauseRerunResponse) { notifyRerunRequest(); await rerunResponseGate; }
      if (index === 5 && pauseCrashResponse) { notifyCrashRequest(); await crashResponseGate; }
      const call = fixtureTurns[index];
      if (!call) { response.writeHead(500); response.end("Unexpected packaged smoke inference request"); return; }
      const chunk = { id: `packaged-smoke-${index}`, choices: [{ index: 0, delta: { tool_calls: [{ index: 0, id: call.id, type: "function", function: { name: call.name, arguments: JSON.stringify(call.args) } }] }, finish_reason: "tool_calls" }] };
      response.writeHead(200, { "content-type": "text/event-stream", "cache-control": "no-cache" });
      response.end(`data: ${JSON.stringify(chunk)}\n\ndata: [DONE]\n\n`);
    });
    return;
  }
  response.writeHead(404); response.end("Unexpected packaged smoke provider endpoint");
});

function resultPayload(stdout) {
  return stdout.trim().split(/\r?\n/).flatMap((line) => {
    try { const value = JSON.parse(line); return value.type === "result" ? [value.data] : []; }
    catch { return []; }
  }).at(-1);
}

function runCli(nodeBinary, cliEntry, args, timeoutMs = 45_000) {
  const child = spawn(nodeBinary, ["--experimental-wasm-modules", cliEntry, "--json", ...args], { cwd: dataDir, env, windowsHide: true, stdio: ["ignore", "pipe", "pipe"] });
  let stdout = ""; let stderr = "";
  child.stdout.setEncoding("utf8"); child.stderr.setEncoding("utf8");
  child.stdout.on("data", (chunk) => { stdout += chunk; }); child.stderr.on("data", (chunk) => { stderr += chunk; });
  return new Promise((resolveResult, rejectResult) => {
    const timer = setTimeout(() => {
      if (child.pid) spawnSync("taskkill.exe", ["/PID", String(child.pid), "/T", "/F"], { stdio: "ignore", windowsHide: true });
      rejectResult(new Error(`Packaged smoke CLI timed out: ${args.join(" ")}\n${stderr}`));
    }, timeoutMs);
    child.once("error", (error) => { clearTimeout(timer); rejectResult(error); });
    child.once("close", (code) => { clearTimeout(timer); resolveResult({ code, stdout, stderr }); });
  });
}

async function waitForTarget(port, timeoutMs = 45_000) {
  const deadline = Date.now() + timeoutMs;
  let lastError;
  while (Date.now() < deadline) {
    try {
      const response = await fetch(`http://127.0.0.1:${port}/json`);
      if (response.ok) {
        const targets = await response.json();
        const page = targets.find((target) => target.type === "page" && target.webSocketDebuggerUrl);
        if (page) return page;
      }
    } catch (error) { lastError = error; }
    await new Promise((resolveWait) => setTimeout(resolveWait, 400));
  }
  throw new Error(`Packaged Xyst renderer did not expose its debug target in time: ${String(lastError ?? "no page target")}`);
}

async function cdpCommand(page, method, params, id) {
  const socket = new WebSocket(page.webSocketDebuggerUrl);
  try {
    await new Promise((resolveOpen, rejectOpen) => {
      const timeout = setTimeout(() => rejectOpen(new Error("Timed out connecting to packaged renderer")), 10_000);
      socket.addEventListener("open", () => { clearTimeout(timeout); resolveOpen(); }, { once: true });
      socket.addEventListener("error", () => { clearTimeout(timeout); rejectOpen(new Error("Could not connect to packaged renderer")); }, { once: true });
    });
    const message = new Promise((resolveMessage, rejectMessage) => {
      const timeout = setTimeout(() => rejectMessage(new Error(`Timed out running ${method} in packaged renderer`)), 10_000);
      const onMessage = (event) => {
        const packet = JSON.parse(String(event.data));
        if (packet.id !== id) return;
        clearTimeout(timeout);
        socket.removeEventListener("message", onMessage);
        if (packet.error) rejectMessage(new Error(packet.error.message));
        else resolveMessage(packet.result);
      };
      socket.addEventListener("message", onMessage);
    });
    socket.send(JSON.stringify({ id, method, params }));
    return await message;
  } finally { socket.close(); }
}

async function evaluate(page, expression, id) {
  const result = await cdpCommand(page, "Runtime.evaluate", { expression, returnByValue: true }, id);
  if (result?.exceptionDetails) throw new Error(result.exceptionDetails.text ?? "Renderer evaluation failed");
  return result?.result?.value ?? result?.result;
}

async function waitForUi(page, timeoutMs = 45_000) {
  const deadline = Date.now() + timeoutMs;
  let snapshot;
  while (Date.now() < deadline) {
    try {
      snapshot = JSON.parse(await evaluate(page, "JSON.stringify({ title: document.title, buttons: document.querySelectorAll('button').length, width: window.innerWidth, height: window.innerHeight, body: document.body.innerText.slice(0, 4000) })", 1));
      if (snapshot.title === "Xyst Control Room" && snapshot.buttons >= 10 && snapshot.body.includes("RUNTIME READY")) return { title: snapshot.title, buttons: snapshot.buttons, width: snapshot.width, height: snapshot.height };
    } catch {}
    await new Promise((resolveWait) => setTimeout(resolveWait, 500));
  }
  throw new Error(`Packaged renderer did not reach the expected control room UI: ${JSON.stringify(snapshot ?? {})}`);
}

async function pressKey(page, key, virtualKeyCode, id) {
  const params = { key, code: key, windowsVirtualKeyCode: virtualKeyCode, nativeVirtualKeyCode: virtualKeyCode };
  await cdpCommand(page, "Input.dispatchKeyEvent", { ...params, type: "keyDown" }, id);
  await cdpCommand(page, "Input.dispatchKeyEvent", { ...params, type: "keyUp" }, id + 1);
}

async function verifyKeyboardSkipLink(page) {
  await cdpCommand(page, "Page.bringToFront", {}, 27);
  const pageHasFocus = await evaluate(page, "document.hasFocus()", 28);
  assert.equal(pageHasFocus, true, "The packaged renderer must be the active window for keyboard visibility checks");
  await evaluate(page, "if (document.activeElement instanceof HTMLElement && document.activeElement !== document.body) document.activeElement.blur()", 29);
  await pressKey(page, "Tab", 9, 30);
  const focusedLink = JSON.parse(await evaluate(page, "JSON.stringify({ text: document.activeElement?.textContent?.trim(), top: document.activeElement?.getBoundingClientRect().top })", 32));
  assert.equal(focusedLink.text, "Skip to main content", `Expected Tab to focus the skip link, got ${JSON.stringify(focusedLink)}`);
  const visibilityDeadline = Date.now() + 2_000;
  let visibleLink = focusedLink;
  while (visibleLink.top < 0 && Date.now() < visibilityDeadline) {
    await new Promise((resolveWait) => setTimeout(resolveWait, 50));
    visibleLink = JSON.parse(await evaluate(page, "JSON.stringify({ text: document.activeElement?.textContent?.trim(), top: document.activeElement?.getBoundingClientRect().top })", 36));
    assert.equal(visibleLink.text, "Skip to main content", `The skip link lost keyboard focus while becoming visible: ${JSON.stringify(visibleLink)}`);
  }
  assert.ok(visibleLink.top >= 0, `The keyboard-focused skip link should become visible: ${JSON.stringify(visibleLink)}`);
  await pressKey(page, "Enter", 13, 33);
  const targetId = await evaluate(page, "document.activeElement?.id ?? ''", 35);
  assert.equal(targetId, "main-content", `Expected Enter on the skip link to focus main content, got ${targetId}`);
  return { firstTabTarget: focusedLink.text, enterTarget: targetId };
}

async function verifyAppearanceAccessibility(page) {
  await evaluate(page, "[...document.querySelectorAll('.sidebar .nav-item')].find(button => button.textContent.includes('Settings'))?.click()", 37);
  const before = JSON.parse(await evaluate(page, "JSON.stringify((() => { const root=document.documentElement; const item=document.querySelector('.nav-item'); const navLabel=document.querySelector('.nav-label'); const probe=document.createElement('div'); probe.className='spinner'; probe.dataset.xystSmokeProbe='true'; document.body.append(probe); return { original:{ largeText:root.dataset.largeText==='true', highContrast:root.dataset.highContrast==='true', reducedMotion:root.dataset.reducedMotion==='true' }, before:item.getBoundingClientRect().height, borderBefore:getComputedStyle(root).getPropertyValue('--border').trim(), textBefore:getComputedStyle(navLabel).color, motionBefore:getComputedStyle(probe).animationDuration }; })())", 38));
  const toggle = async (label, id) => evaluate(page, `(() => { const row=[...document.querySelectorAll('.toggle-row')].find(item=>item.querySelector('strong')?.textContent?.trim()===${JSON.stringify(label)}); const button=row?.querySelector('button[role=switch]'); if(!button) throw new Error('Could not find ${label} toggle'); button.click(); return button.getAttribute('aria-checked'); })()`, id);
  for (const [index, label] of ["Larger text", "High contrast", "Reduced motion"].entries()) {
    if (!before.original[{ "Larger text": "largeText", "High contrast": "highContrast", "Reduced motion": "reducedMotion" }[label]]) await toggle(label, 39 + index);
  }
  await new Promise((resolveWait) => setTimeout(resolveWait, 350));
  const measurements = JSON.parse(await evaluate(page, "JSON.stringify((() => { const root=document.documentElement; const item=document.querySelector('.nav-item'); const navLabel=document.querySelector('.nav-label'); const probe=document.querySelector('[data-xyst-smoke-probe]'); return { after:item.getBoundingClientRect().height, zoom:Number(getComputedStyle(root).zoom), borderAfter:getComputedStyle(root).getPropertyValue('--border').trim(), textAfter:navLabel.style.color || getComputedStyle(navLabel).color, motionAfter:getComputedStyle(probe).animationDuration }; })())", 43));
  for (const [index, label] of ["Larger text", "High contrast", "Reduced motion"].entries()) {
    if (!before.original[{ "Larger text": "largeText", "High contrast": "highContrast", "Reduced motion": "reducedMotion" }[label]]) await toggle(label, 44 + index);
  }
  await evaluate(page, "document.querySelector('[data-xyst-smoke-probe]')?.remove(); [...document.querySelectorAll('.sidebar .nav-item')].find(button => button.textContent.includes('Overview'))?.click()", 47);
  Object.assign(measurements, before);
  const seconds = (value) => value.endsWith("ms") ? Number.parseFloat(value) / 1000 : Number.parseFloat(value);
  assert.ok(measurements.zoom >= 1.14 && measurements.after >= measurements.before * 1.14, `Larger text should enlarge the renderer: ${JSON.stringify(measurements)}`);
  assert.notEqual(measurements.borderAfter, measurements.borderBefore, `High contrast should change panel borders: ${JSON.stringify(measurements)}`);
  assert.notEqual(measurements.textAfter, measurements.textBefore, `High contrast should brighten muted text: ${JSON.stringify(measurements)}`);
  assert.ok(seconds(measurements.motionBefore) <= 0.00001 || seconds(measurements.motionAfter) < seconds(measurements.motionBefore) / 100, `Reduced motion should suppress spinner animation unless the OS already suppresses it: ${JSON.stringify(measurements)}`);
  return measurements;
}

function stopInstalledCopies(executable) {
  const escaped = executable.replace(/'/g, "''");
  const command = `$target = '${escaped}'; Get-CimInstance Win32_Process -Filter "Name='Xyst.exe'" | Where-Object { $_.ExecutablePath -eq $target } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }`;
  return spawnSync("powershell.exe", ["-NoProfile", "-EncodedCommand", Buffer.from(command, "utf16le").toString("base64")], { stdio: "ignore", windowsHide: true });
}

function waitForSnapshotWorkerPriority(parentPid, timeoutMs = 10_000) {
  if (!Number.isInteger(parentPid) || parentPid < 1) throw new Error("Packaged Xyst did not expose its process ID");
  const command = `$parent = ${parentPid}; $deadline = [DateTime]::UtcNow.AddMilliseconds(${timeoutMs}); do { $worker = Get-CimInstance Win32_Process -Filter "ParentProcessId=$parent" | Where-Object { $_.CommandLine -match 'desktop\\s+snapshot\\s+--worker' } | Select-Object -First 1; if ($worker) { (Get-Process -Id $worker.ProcessId).PriorityClass; exit 0 }; Start-Sleep -Milliseconds 100 } while ([DateTime]::UtcNow -lt $deadline); exit 1`;
  const result = spawnSync("powershell.exe", ["-NoProfile", "-EncodedCommand", Buffer.from(command, "utf16le").toString("base64")], { encoding: "utf8", timeout: timeoutMs + 2_000, windowsHide: true });
  if (result.error) throw result.error;
  const priority = result.stdout.trim();
  if (result.status !== 0 || priority !== "BelowNormal") {
    throw new Error(`Packaged dashboard snapshot worker should run at BelowNormal priority; got ${JSON.stringify({ status: result.status, priority, stderr: result.stderr.trim() })}`);
  }
  return priority;
}

function waitForAgentResumePriority(parentPid, timeoutMs = 10_000) {
  if (!Number.isInteger(parentPid) || parentPid < 1) throw new Error("Packaged Xyst did not expose its process ID");
  const command = `$parent = ${parentPid}; $deadline = [DateTime]::UtcNow.AddMilliseconds(${timeoutMs}); do { $worker = Get-CimInstance Win32_Process -Filter "ParentProcessId=$parent" | Where-Object { $_.CommandLine -match 'agent\\s+resume' } | Select-Object -First 1; if ($worker) { (Get-Process -Id $worker.ProcessId).PriorityClass; exit 0 }; Start-Sleep -Milliseconds 100 } while ([DateTime]::UtcNow -lt $deadline); exit 1`;
  const result = spawnSync("powershell.exe", ["-NoProfile", "-EncodedCommand", Buffer.from(command, "utf16le").toString("base64")], { encoding: "utf8", timeout: timeoutMs + 2_000, windowsHide: true });
  if (result.error) throw result.error;
  const priority = result.stdout.trim();
  if (result.status !== 0 || priority !== "BelowNormal") {
    throw new Error(`Packaged resumed-agent worker should run at BelowNormal priority; got ${JSON.stringify({ status: result.status, priority, stderr: result.stderr.trim() })}`);
  }
  return priority;
}

function waitForAgentResumeProcess(parentPid, timeoutMs = 10_000) {
  if (!Number.isInteger(parentPid) || parentPid < 1) throw new Error("Packaged Xyst did not expose its process ID");
  const command = `$parent = ${parentPid}; $deadline = [DateTime]::UtcNow.AddMilliseconds(${timeoutMs}); do { $worker = Get-CimInstance Win32_Process -Filter "ParentProcessId=$parent" | Where-Object { $_.CommandLine -match 'agent\\s+resume' } | Select-Object -First 1; if ($worker) { $priority=(Get-Process -Id $worker.ProcessId).PriorityClass; \"$($worker.ProcessId)|$priority\"; exit 0 }; Start-Sleep -Milliseconds 100 } while ([DateTime]::UtcNow -lt $deadline); exit 1`;
  const result = spawnSync("powershell.exe", ["-NoProfile", "-EncodedCommand", Buffer.from(command, "utf16le").toString("base64")], { encoding: "utf8", timeout: timeoutMs + 2_000, windowsHide: true });
  if (result.error) throw result.error;
  const [pidText, priority] = result.stdout.trim().split("|");
  const pid = Number(pidText);
  if (result.status !== 0 || !Number.isInteger(pid) || pid < 1 || priority !== "BelowNormal") {
    throw new Error(`Packaged resumed-agent process should be discoverable at BelowNormal priority; got ${JSON.stringify({ status: result.status, stdout: result.stdout.trim(), stderr: result.stderr.trim() })}`);
  }
  return { pid, priority };
}

let rendererEvalId = 100;
async function waitForRenderer(page, expression, description, timeoutMs = 20_000) {
  const deadline = Date.now() + timeoutMs;
  let last;
  while (Date.now() < deadline) {
    try { last = await evaluate(page, expression, rendererEvalId++); if (last) return last; } catch {}
    await new Promise((resolveWait) => setTimeout(resolveWait, 200));
  }
  throw new Error(`Timed out waiting for packaged renderer ${description}; last value: ${JSON.stringify(last)}`);
}

async function withTimeout(promise, timeoutMs, message) {
  let timer;
  try { return await Promise.race([promise, new Promise((_, reject) => { timer = setTimeout(() => reject(new Error(message)), timeoutMs); })]); }
  finally { clearTimeout(timer); }
}

async function waitForSessionCallStatus(nodeBinary, cliEntry, sessionId, callId, expectedStatus, timeoutMs = 15_000) {
  const deadline = Date.now() + timeoutMs;
  let lastStatus = "missing";
  while (Date.now() < deadline) {
    const calls = await runCli(nodeBinary, cliEntry, ["session", "calls", sessionId], 5_000);
    if (calls.code !== 0) throw new Error("Could not inspect packaged session call " + callId + ": " + (calls.stderr || calls.stdout));
    const rows = resultPayload(calls.stdout) ?? [];
    const row = rows.find((item) => item.id === callId);
    lastStatus = row?.status ?? "missing";
    if (lastStatus === expectedStatus) return row;
    await new Promise((resolveWait) => setTimeout(resolveWait, 100));
  }
  throw new Error("Timed out waiting for packaged call " + callId + " to become " + expectedStatus + "; last status=" + lastStatus);
}

async function waitForCrashCallReady(nodeBinary, cliEntry, sessionId, probePath, expectedOwnerPid, timeoutMs = 20_000) {
  const deadline = Date.now() + timeoutMs;
  let lastStatus;
  while (Date.now() < deadline) {
    const calls = await runCli(nodeBinary, cliEntry, ["session", "calls", sessionId], 5_000);
    if (calls.code !== 0) throw new Error(`Could not inspect packaged crash-smoke calls: ${calls.stderr || calls.stdout}`);
    const rows = resultPayload(calls.stdout) ?? [];
    lastStatus = rows.find((row) => row.id === "desktop-smoke-run-crash")?.status;
    try {
      const ownerPid = Number((await readFile(probePath, "utf8")).trim());
      if (lastStatus === "running" && ownerPid === expectedOwnerPid) return;
      if (ownerPid !== expectedOwnerPid) throw new Error(`Crash probe captured owner PID ${ownerPid}, expected ${expectedOwnerPid}`);
    } catch (error) {
      if (error?.code !== "ENOENT") throw error;
    }
    await new Promise((resolveWait) => setTimeout(resolveWait, 100));
  }
  throw new Error(`Packaged crash probe did not become ready under its owning CLI process; status=${lastStatus}`);
}

try {
  await access(installer);
  await mkdir(workDir, { recursive: true });
  await mkdir(dataDir, { recursive: true });
  await mkdir(env.APPDATA, { recursive: true });
  const setup = spawnSync(installer, ["/S", `/D=${installDir}`], { cwd: root, env, stdio: "inherit", timeout: 180_000, windowsHide: true });
  if (setup.error) throw setup.error;
  if (setup.status !== 0) throw new Error(`Silent clean installation failed with exit code ${setup.status}`);

  // NSIS can launch the app after installation. Stop that copy, then start the
  // installed executable with an isolated profile and DevTools enabled.
  stopInstalledCopies(installedExe);
  await new Promise((resolveWait) => setTimeout(resolveWait, 800));
  let executable;
  try { await access(installedExe); executable = installedExe; } catch {}
  assert.ok(executable, `Installed Xyst.exe was not found under ${process.env.LOCALAPPDATA}`);

  const packagedRoot = join(installDir, "resources", "app.asar.unpacked");
  const nodeBinary = join(packagedRoot, "apps", "desktop", "runtime", "node.exe");
  const cliEntry = join(packagedRoot, "apps", "cli", "dist", "index.js");
  await access(nodeBinary); await access(cliEntry);
  await writeFile(join(dataDir, "phase.txt"), "PHASE_A\n", "utf8");
  await writeFile(join(dataDir, "probe.mjs"), 'import { existsSync, readFileSync, writeFileSync } from "node:fs";\nconst mode = existsSync("crash-mode.txt") ? readFileSync("crash-mode.txt", "utf8").trim() : "";\nif (mode === "armed") { const ownerPid = process.ppid; writeFileSync("crash-probe-ready.txt", String(ownerPid)); await new Promise((resolve) => setTimeout(resolve, 5000)); try { process.kill(ownerPid, 0); writeFileSync("crash-effect.txt", "applied"); } catch { process.exit(0); } }\nif (mode === "retry") writeFileSync("crash-effect.txt", "applied");\nprocess.stdout.write(readFileSync("phase.txt", "utf8"));\n', "utf8");
  await new Promise((resolveListen, rejectListen) => { provider.once("error", rejectListen); provider.listen(0, "127.0.0.1", resolveListen); });
  env.LM_STUDIO_BASE_URL = `http://127.0.0.1:${provider.address().port}`;
  for (const [key, value] of [["model.default", JSON.stringify("packaged-smoke-model")], ["autonomy.externalActionApproval", "false"]]) {
    const configured = await runCli(nodeBinary, cliEntry, ["config", "set", key, value]);
    assert.equal(configured.code, 0, configured.stderr || configured.stdout);
  }
  const desktopObjective = `Packaged desktop external-edit rerun ${Date.now()}`;
  const initial = await runCli(nodeBinary, cliEntry, ["agent", "run", "--model", "packaged-smoke-model", "--max-turns", "4", desktopObjective]);
  assert.equal(initial.code, 0, initial.stderr || initial.stdout);
  const initialResult = resultPayload(initial.stdout);
  assert.equal(initialResult?.status, "completed", initial.stdout);
  assert.equal(typeof initialResult?.sessionId, "string", initial.stdout);
  const sessionId = initialResult.sessionId;
  const initialCalls = await runCli(nodeBinary, cliEntry, ["session", "calls", sessionId]);
  assert.equal(initialCalls.code, 0, initialCalls.stderr || initialCalls.stdout);
  const initialRows = resultPayload(initialCalls.stdout);
  assert.equal(initialRows?.find((row) => row.id === "desktop-smoke-run-initial")?.status, "completed", initialCalls.stdout);
  await writeFile(join(dataDir, "phase.txt"), "PHASE_B\n", "utf8");

  const port = 9222;
  app = spawn(executable, [`--remote-debugging-port=${port}`, "--remote-allow-origins=*", "--disable-gpu"], {
    cwd: workDir,
    env,
    windowsHide: true,
    stdio: "ignore"
  });
  const appError = new Promise((_, reject) => app.once("error", reject));
  const page = await Promise.race([waitForTarget(port), appError]);
  const snapshot = await waitForUi(page);
  const snapshotWorkerPriority = waitForSnapshotWorkerPriority(app.pid);
  const keyboard = await verifyKeyboardSkipLink(page);
  const appearanceAccessibility = await verifyAppearanceAccessibility(page);
  await evaluate(page, "(() => { const button=[...document.querySelectorAll('.nav-item')].find((item)=>item.textContent.includes('Sessions')); if(!button) throw new Error('Sessions navigation is missing'); button.click(); return true; })()", 50);
  await waitForRenderer(page, "document.querySelectorAll('.session-item').length === 1", "the seeded saved session");
  await evaluate(page, "document.querySelector('.session-item')?.click()", 51);
  await waitForRenderer(page, "[...document.querySelectorAll('.tool-log-name')].some((item)=>item.textContent.trim()==='run_process')", "the saved process call");
  await evaluate(page, "(() => { window.confirm=()=>true; const row=[...document.querySelectorAll('details.tool-log-item')].find((item)=>item.querySelector('.tool-log-name')?.textContent.trim()==='run_process'); if(!row) throw new Error('Saved process call is missing'); row.open=true; const button=[...row.querySelectorAll('button')].find((item)=>item.textContent.includes('Authorize one fresh execution')); if(!button) throw new Error('Desktop rerun authorization action is missing'); button.click(); return true; })()", 52);
  await waitForRenderer(page, "!!document.querySelector('textarea[aria-label=\"Continue saved session\"]')", "the saved-session reply form");
  await evaluate(page, "document.querySelector('textarea[aria-label=\"Continue saved session\"]')?.focus()", 53);
  const desktopPrompt = "The workspace file changed to PHASE_B outside Xyst. Run the authorized command again, read the current file, and verify the new output.";
  await cdpCommand(page, "Input.insertText", { text: desktopPrompt }, 54);
  assert.equal(await evaluate(page, "document.querySelector('textarea[aria-label=\"Continue saved session\"]')?.value", 55), desktopPrompt, "Desktop reply text must be entered before resuming");
  await waitForRenderer(page, "document.querySelector('.session-reply button.primary-button')?.disabled === false", "the authorized Continue control");
  pauseRerunResponse = true;
  await evaluate(page, "document.querySelector('.session-reply button.primary-button')?.click()", 56);
  await withTimeout(rerunRequestReceived, 20_000, "Desktop did not resume the saved session through its provider");
  const agentResumePriority = waitForAgentResumePriority(app.pid);
  releaseRerunResponse(); pauseRerunResponse = false;
  const desktopRerunVisible = await waitForRenderer(page, "(() => { const rows=[...document.querySelectorAll('details.tool-log-item')].filter((item)=>item.querySelector('.tool-log-name')?.textContent.trim()==='run_process'); for(const row of rows) row.open=true; return rows.length >= 2 && rows.map((row)=>row.innerText).join(' ').includes('PHASE_B'); })()", "the resumed PHASE_B process result", 30_000);
  assert.equal(desktopRerunVisible, true);
  const finalCalls = await runCli(nodeBinary, cliEntry, ["session", "calls", sessionId]);
  assert.equal(finalCalls.code, 0, finalCalls.stderr || finalCalls.stdout);
  const finalRows = resultPayload(finalCalls.stdout);
  const rerunRow = finalRows?.find((row) => row.id === "desktop-smoke-run-rerun");
  assert.equal(rerunRow?.status, "completed", finalCalls.stdout);
  assert.equal(rerunRow?.is_rerun, 1, finalCalls.stdout);
  assert.match(JSON.parse(rerunRow.output_json).stdout, /PHASE_B/);
  assert.equal(await readFile(join(dataDir, "phase.txt"), "utf8"), "PHASE_B\n");
  await writeFile(join(dataDir, "crash-mode.txt"), "armed\n", "utf8");
  await evaluate(page, "(() => { const rows=[...document.querySelectorAll('details.tool-log-item')].filter((item)=>item.querySelector('.tool-log-name')?.textContent.trim()==='run_process'); const row=rows.find((item)=>item.innerText.includes('PHASE_B')); if(!row) throw new Error('Completed phase-B command is missing'); row.open=true; window.confirm=()=>true; const button=[...row.querySelectorAll('button')].find((item)=>item.textContent.includes('Authorize one fresh execution')); if(!button) throw new Error('Second Desktop rerun authorization action is missing'); button.click(); return true; })()", 57);
  const crashPrompt = "Run the authorized command again and verify the current phase file.";
  await cdpCommand(page, "Input.insertText", { text: crashPrompt }, 58);
  assert.equal(await evaluate(page, "document.querySelector('textarea[aria-label=\"Continue saved session\"]')?.value", 59), crashPrompt, "The Desktop crash-smoke prompt must be entered before resuming");
  await waitForRenderer(page, "document.querySelector('.session-reply button.primary-button')?.disabled === false", "the second authorized Continue control");
  pauseCrashResponse = true;
  await evaluate(page, "document.querySelector('.session-reply button.primary-button')?.click()", 60);
  await withTimeout(crashRequestReceived, 20_000, "Desktop did not start the owner-crash recovery session");
  const crashWorker = waitForAgentResumeProcess(app.pid);
  releaseCrashResponse(); pauseCrashResponse = false;
  const crashProbePath = join(dataDir, "crash-probe-ready.txt");
  await waitForCrashCallReady(nodeBinary, cliEntry, sessionId, crashProbePath, crashWorker.pid);
  const killedOwner = spawnSync("taskkill.exe", ["/PID", String(crashWorker.pid), "/F"], { encoding: "utf8", windowsHide: true });
  assert.equal(killedOwner.status, 0, `Could not terminate the exact Desktop-owned CLI process: ${killedOwner.stderr || killedOwner.stdout}`);
  await new Promise((resolveWait) => setTimeout(resolveWait, 6_000));
  await assert.rejects(access(join(dataDir, "crash-effect.txt")), { code: "ENOENT" }, "The abandoned Desktop command must not perform its side effect after owner exit");

  await evaluate(page, "document.querySelector('.session-item.selected')?.click()", 61);
  await waitForRenderer(page, "(() => [...document.querySelectorAll('details.tool-log-item')].some((row)=>row.querySelector('.tool-log-name')?.textContent.trim()==='run_process' && [...row.querySelectorAll('button')].some((button)=>button.textContent.includes('Review abandoned rerun') && !button.disabled)))()", "the recoverable abandoned Desktop rerun", 30_000);
  await evaluate(page, "(() => { window.confirm=()=>true; const row=[...document.querySelectorAll('details.tool-log-item')].find((item)=>item.querySelector('.tool-log-name')?.textContent.trim()==='run_process' && [...item.querySelectorAll('button')].some((button)=>button.textContent.includes('Review abandoned rerun'))); const button=[...row.querySelectorAll('button')].find((item)=>item.textContent.includes('Review abandoned rerun')); if(!button) throw new Error('Desktop abandoned-rerun review action is missing'); button.click(); return true; })()", 62);
  await waitForRenderer(page, "(() => [...document.querySelectorAll('details.tool-log-item')].some((row)=>row.querySelector('.tool-log-name')?.textContent.trim()==='run_process' && [...row.querySelectorAll('button')].some((button)=>button.textContent.includes('Confirm no effect and allow retry') && !button.disabled)))()", "the uncertain Desktop rerun reconciliation", 30_000);
  await waitForSessionCallStatus(nodeBinary, cliEntry, sessionId, "desktop-smoke-run-crash", "uncertain");
  await evaluate(page, "(() => { window.confirm=()=>true; const row=[...document.querySelectorAll('details.tool-log-item')].find((item)=>item.querySelector('.tool-log-name')?.textContent.trim()==='run_process' && [...item.querySelectorAll('button')].some((button)=>button.textContent.includes('Confirm no effect and allow retry'))); const button=[...row.querySelectorAll('button')].find((item)=>item.textContent.includes('Confirm no effect and allow retry')); if(!button) throw new Error('Desktop no-effect reconciliation action is missing'); button.click(); return true; })()", 63);
  await waitForSessionCallStatus(nodeBinary, cliEntry, sessionId, "desktop-smoke-run-crash", "retry_allowed");
  await writeFile(join(dataDir, "crash-mode.txt"), "retry\n", "utf8");
  await waitForRenderer(page, "!!document.querySelector('textarea[aria-label=\"Continue saved session\"]')", "the crash-recovered session reply form");
  await evaluate(page, "document.querySelector('textarea[aria-label=\"Continue saved session\"]')?.focus()", 64);
  await cdpCommand(page, "Input.dispatchKeyEvent", { type: "keyDown", key: "a", code: "KeyA", modifiers: 2, windowsVirtualKeyCode: 65, nativeVirtualKeyCode: 65 }, 65);
  await cdpCommand(page, "Input.dispatchKeyEvent", { type: "keyUp", key: "a", code: "KeyA", modifiers: 2, windowsVirtualKeyCode: 65, nativeVirtualKeyCode: 65 }, 66);
  const retryPrompt = "The abandoned command had no effect. Retry it once, read the current phase file, and verify completion.";
  await cdpCommand(page, "Input.insertText", { text: retryPrompt }, 67);
  assert.equal(await evaluate(page, "document.querySelector('textarea[aria-label=\"Continue saved session\"]')?.value", 68), retryPrompt, "The crash-recovery prompt must replace the failed response");
  await waitForRenderer(page, "document.querySelector('.session-reply button.primary-button')?.disabled === false", "the crash-recovered Continue control");
  await evaluate(page, "document.querySelector('.session-reply button.primary-button')?.click()", 69);
  await waitForSessionCallStatus(nodeBinary, cliEntry, sessionId, "desktop-smoke-complete-retry", "completed", 30_000);
  const retryVisible = await waitForRenderer(page, "(() => { const rows=[...document.querySelectorAll('details.tool-log-item')].filter((item)=>item.querySelector('.tool-log-name')?.textContent.trim()==='run_process'); for(const row of rows) row.open=true; return rows.length >= 4 && rows.some((row)=>row.innerText.includes('PHASE_B') && row.querySelector('.status-pill')?.textContent.trim().toLowerCase().startsWith('completed')); })()", "the retried Desktop command result", 10_000);
  assert.equal(retryVisible, true);
  const crashFinalCalls = await runCli(nodeBinary, cliEntry, ["session", "calls", sessionId]);
  assert.equal(crashFinalCalls.code, 0, crashFinalCalls.stderr || crashFinalCalls.stdout);
  const crashFinalRows = resultPayload(crashFinalCalls.stdout);
  const abandonedRow = crashFinalRows?.find((row) => row.id === "desktop-smoke-run-crash");
  const retryRow = crashFinalRows?.find((row) => row.id === "desktop-smoke-run-retry");
  assert.equal(abandonedRow?.status, "retry_consumed", crashFinalCalls.stdout);
  assert.equal(retryRow?.status, "completed", crashFinalCalls.stdout);
  assert.equal(retryRow?.is_rerun, 1, crashFinalCalls.stdout);
  assert.match(JSON.parse(retryRow.output_json).stdout, /PHASE_B/);
  assert.equal(crashFinalRows?.find((row) => row.id === "desktop-smoke-read-retry")?.status, "completed", crashFinalCalls.stdout);
  assert.equal(crashFinalRows?.find((row) => row.id === "desktop-smoke-complete-retry")?.status, "completed", crashFinalCalls.stdout);
  assert.equal(await readFile(join(dataDir, "crash-effect.txt"), "utf8"), "applied");
  const desktopCrashRecovery = { ownerPid: crashWorker.pid, priority: crashWorker.priority, abandonedStatus: abandonedRow.status, retryStatus: retryRow.status, crashChildEffect: "blocked", retryEffect: await readFile(join(dataDir, "crash-effect.txt"), "utf8") };
  assert.equal(fixtureTurn, 9, `expected nine deterministic provider calls; got ${fixtureTurn}`);
  assert.equal(modelLoadRequests, 0, "the packaged session smoke must not load a model");
  const duplicate = spawn(executable, [`--remote-debugging-port=${port}`, "--remote-allow-origins=*", "--disable-gpu"], {
    cwd: workDir,
    env,
    windowsHide: true,
    stdio: "ignore"
  });
  const duplicateExit = await new Promise((resolveExit, rejectExit) => {
    const timer = setTimeout(() => resolveExit(undefined), 10_000);
    duplicate.once("error", (error) => { clearTimeout(timer); rejectExit(error); });
    duplicate.once("exit", (code, signal) => { clearTimeout(timer); resolveExit({ code, signal }); });
  });
  if (!duplicateExit) {
    if (duplicate.pid) spawnSync("taskkill.exe", ["/PID", String(duplicate.pid), "/T", "/F"], { stdio: "ignore", windowsHide: true });
    throw new Error("A second Xyst process stayed alive instead of using the existing single instance");
  }
  assert.equal(duplicateExit.code, 0, `Second Xyst process should exit cleanly: ${JSON.stringify(duplicateExit)}`);
  assert.equal(app.exitCode, null, "The original Xyst process should remain alive after the duplicate launch");
  await waitForTarget(port, 5_000);
  const screenshot = await cdpCommand(page, "Page.captureScreenshot", { format: "png", fromSurface: true }, 2);
  assert.ok(screenshot?.data, "Packaged renderer did not return a screenshot");
  await writeFile(join(packageDir, "windows-gui-smoke.png"), Buffer.from(screenshot.data, "base64"));
  process.stdout.write(`Clean Windows install UI smoke passed: ${JSON.stringify({ ...snapshot, snapshotWorkerPriority, agentResumePriority, desktopRerun: { sessionId, status: rerunRow.status, output: JSON.parse(rerunRow.output_json).stdout }, desktopCrashRecovery, keyboard, appearanceAccessibility })}\n`);
} finally {
  releaseRerunResponse();
  releaseCrashResponse();
  if (app?.pid) spawnSync("taskkill.exe", ["/PID", String(app.pid), "/T", "/F"], { stdio: "ignore", windowsHide: true });
  if (app?.pid) stopInstalledCopies(installedExe);
  provider.closeAllConnections?.();
  if (provider.listening) await new Promise((resolveClose) => provider.close(resolveClose));
  const finalScratchPath = resolve(scratch);
  const finalScratchRelative = relative(scratchRoot, finalScratchPath);
  if (finalScratchPath !== scratchPath || !finalScratchRelative || finalScratchRelative.startsWith("..") || isAbsolute(finalScratchRelative) || !basename(finalScratchPath).startsWith("xyst-clean-install-")) {
    throw new Error(`Refusing to remove an unexpected clean-install scratch directory: ${finalScratchPath}`);
  }
  await rm(finalScratchPath, { recursive: true, force: true, maxRetries: 5, retryDelay: 200 });
}
