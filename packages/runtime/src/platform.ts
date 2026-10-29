import { spawn, type ChildProcess } from "node:child_process";
import { delimiter, isAbsolute, normalize, resolve } from "node:path";
import { platform } from "node:os";

export function normalizeUserPath(path: string, cwd = process.cwd()): string {
  if (!path.trim()) throw new Error("Path cannot be empty");
  if (process.platform === "win32" && /^\\\\\?\\/.test(path)) return normalize(path);
  return normalize(isAbsolute(path) ? path : resolve(cwd, path));
}

export function getDataDirectory(app = "xyst"): string {
  if (process.platform === "win32") return resolve(process.env.APPDATA ?? resolve(process.env.USERPROFILE ?? process.cwd(), "AppData", "Roaming"), app);
  return resolve(process.env.XDG_DATA_HOME ?? resolve(process.env.HOME ?? process.cwd(), ".local", "share"), app);
}

export interface ProcessOptions {
  cwd?: string;
  env?: NodeJS.ProcessEnv;
  signal?: AbortSignal;
  timeoutMs?: number;
  maxOutputChars?: number;
  stdin?: boolean;
  onStdinWriter?: (write: (value: string) => void, close: () => void) => void;
  onStdout?: (chunk: string) => void;
  onStderr?: (chunk: string) => void;
}
export interface ProcessResult { code: number | null; signal: NodeJS.Signals | null; stdout: string; stderr: string; durationMs: number }

/** The OS rejected process creation; no program executed. */
export class ProcessStartError extends Error {
  constructor(cause: Error) {
    super(`Process did not start: ${cause.message}`, { cause });
    this.name = "ProcessStartError";
  }
}

export async function killProcessTree(pid: number): Promise<boolean> {
  if (!Number.isSafeInteger(pid) || pid <= 0) return false;
  if (process.platform === "win32") {
    return await new Promise<boolean>((resolveDone) => {
      let finished = false;
      let timer: NodeJS.Timeout | undefined;
      const finish = (success: boolean) => {
        if (finished) return;
        finished = true;
        if (timer) clearTimeout(timer);
        // Direct-child fallback is best-effort; it cannot confirm descendants exited.
        if (!success) { try { process.kill(pid, "SIGKILL"); } catch { /* child may already have exited */ } }
        resolveDone(success);
      };
      let killer: ChildProcess;
      const systemRoot = process.env.SystemRoot ?? process.env.WINDIR;
      const taskkill = systemRoot ? resolve(systemRoot, "System32", "taskkill.exe") : "taskkill";
      try { killer = spawn(taskkill, ["/pid", String(pid), "/T", "/F"], { windowsHide: true, stdio: "ignore" }); }
      catch { finish(false); return; }
      timer = setTimeout(() => { try { killer.kill(); } finally { finish(false); } }, 5_000);
      killer.once("close", (code) => finish(code === 0));
      killer.once("error", () => finish(false));
    });
  } else {
    let target = -pid;
    try { process.kill(target, "SIGTERM"); }
    catch (error) {
      if ((error as NodeJS.ErrnoException).code === "ESRCH") return true;
      target = pid;
      try { process.kill(target, "SIGTERM"); }
      catch (fallbackError) { return (fallbackError as NodeJS.ErrnoException).code === "ESRCH"; }
    }
    const deadline = Date.now() + 1_000;
    let targetExited = false;
    while (Date.now() < deadline) {
      try { process.kill(target, 0); }
      catch (error) {
        if ((error as NodeJS.ErrnoException).code === "ESRCH") { targetExited = true; break; }
        return false;
      }
      await new Promise((resolveDone) => setTimeout(resolveDone, Math.min(50, Math.max(1, deadline - Date.now()))));
    }
    if (!targetExited) {
      try { process.kill(target, "SIGKILL"); }
      catch (error) {
        if ((error as NodeJS.ErrnoException).code === "ESRCH") targetExited = true;
        else return false;
      }
      const killDeadline = Date.now() + 1_000;
      while (!targetExited && Date.now() < killDeadline) {
        try { process.kill(target, 0); }
        catch (error) {
          if ((error as NodeJS.ErrnoException).code === "ESRCH") { targetExited = true; break; }
          return false;
        }
        await new Promise((resolveDone) => setTimeout(resolveDone, Math.min(50, Math.max(1, killDeadline - Date.now()))));
      }
    }
    return targetExited;
  }
}

async function killTree(child: ChildProcess): Promise<boolean> {
  if (!child.pid) return false;
  try { return await killProcessTree(child.pid); }
  catch { child.kill("SIGKILL"); return false; }
}

export async function runProcess(command: string, args: string[], options: ProcessOptions = {}): Promise<ProcessResult> {
  const started = Date.now();
  let child: ChildProcess;
  try {
    child = spawn(command, args, { cwd: options.cwd, env: options.env, shell: false, windowsHide: true, detached: process.platform !== "win32", stdio: [options.stdin ? "pipe" : "ignore", "pipe", "pipe"] });
  } catch (error) {
    throw new ProcessStartError(error instanceof Error ? error : new Error(String(error)));
  }
  let spawned = false;
  child.once("spawn", () => { spawned = true; });
  if (options.stdin && child.stdin) options.onStdinWriter?.((value) => { if (child.stdin && !child.stdin.destroyed) child.stdin.write(value); }, () => child.stdin?.end());
  let stdout = ""; let stderr = ""; let timedOut = false;
  const maxOutputChars = options.maxOutputChars ?? 200_000;
  child.stdout?.setEncoding("utf8").on("data", (chunk: string) => { if (stdout.length < maxOutputChars) stdout += chunk.slice(0, maxOutputChars - stdout.length); options.onStdout?.(chunk); });
  child.stderr?.setEncoding("utf8").on("data", (chunk: string) => { if (stderr.length < maxOutputChars) stderr += chunk.slice(0, maxOutputChars - stderr.length); options.onStderr?.(chunk); });
  let timer: NodeJS.Timeout | undefined;
  let cancellationStarted = false;
  let rejectTermination: (error: Error) => void = () => {};
  const terminationFailure = new Promise<never>((_resolve, reject) => { rejectTermination = reject; });
  const cancel = () => {
    if (cancellationStarted) return;
    cancellationStarted = true;
    void killTree(child).then((success) => {
      if (!success) rejectTermination(new Error("Process termination could not be verified; descendants may still be running"));
    }, (error: unknown) => rejectTermination(new Error("Process termination could not be verified", { cause: error })));
  };
  if (options.signal?.aborted) cancel();
  else options.signal?.addEventListener("abort", cancel, { once: true });
  if (options.timeoutMs && options.timeoutMs > 0) timer = setTimeout(() => { timedOut = true; cancel(); }, options.timeoutMs);
  try {
    const result = await Promise.race([terminationFailure, new Promise<ProcessResult>((resolveDone, reject) => {
      child.once("error", (error) => reject(!spawned && child.pid === undefined ? new ProcessStartError(error) : error));
      child.once("close", (code, signal) => resolveDone({ code, signal, stdout, stderr, durationMs: Date.now() - started }));
    })]);
    if (timedOut) throw new Error(`Process timed out after ${options.timeoutMs} ms`);
    if (options.signal?.aborted) throw new Error("Process cancelled");
    return result;
  } finally {
    if (timer) clearTimeout(timer);
    options.signal?.removeEventListener("abort", cancel);
  }
}

export async function runShell(script: string, options: ProcessOptions = {}): Promise<ProcessResult> {
  if (process.platform === "win32") return runProcess("powershell.exe", ["-NoLogo", "-NoProfile", "-NonInteractive", "-Command", script], options);
  return runProcess("/bin/sh", ["-lc", script], options);
}

export function platformInfo() { return { os: platform(), pathDelimiter: delimiter, node: process.version }; }
