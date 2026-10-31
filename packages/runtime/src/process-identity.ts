import { readFileSync } from "node:fs";
import { spawnSync } from "node:child_process";

const identityCache = new Map<number, { identity: string | null; expiresAt: number }>();

/** Return an OS process creation identity, or null when the platform cannot verify it. */
export function processStartIdentity(pid: number): string | null {
  if (!Number.isSafeInteger(pid) || pid <= 0) return null;
  const cached = identityCache.get(pid);
  if (cached && (pid === process.pid || cached.expiresAt > Date.now())) return cached.identity;
  let identity: string | null = null;
  try {
    if (process.platform === "linux") {
      // The process start time is field 22. The command name in field 2 may contain spaces.
      const stat = readFileSync(`/proc/${pid}/stat`, "utf8");
      const fields = stat.slice(stat.lastIndexOf(")") + 2).trim().split(/\s+/);
      const startTicks = fields[19];
      if (startTicks) identity = `linux:${startTicks}`;
    } else if (process.platform === "win32") {
      const script = `$p=Get-Process -Id ${pid} -ErrorAction Stop; $p.StartTime.ToUniversalTime().Ticks`;
      const result = spawnSync("powershell.exe", ["-NoLogo", "-NoProfile", "-NonInteractive", "-Command", script], {
        encoding: "utf8", timeout: 2_000, windowsHide: true, maxBuffer: 16 * 1024
      });
      const ticks = result.status === 0 ? result.stdout.trim() : "";
      if (/^\d+$/.test(ticks)) identity = `windows:${ticks}`;
    }
  } catch {
    identity = null;
  }
  identityCache.set(pid, { identity, expiresAt: Date.now() + 30_000 });
  return identity;
}

/** A live PID only belongs to this owner when its creation identity still matches. */
export function isRecordedProcessAlive(pid: number, expectedIdentity: string | null | undefined): boolean {
  try {
    process.kill(pid, 0);
  } catch (error) {
    return (error as NodeJS.ErrnoException).code !== "ESRCH";
  }
  // Old rows have no identity. Keep them fail-closed while the PID exists.
  if (!expectedIdentity) return true;
  const actualIdentity = processStartIdentity(pid);
  return actualIdentity === null || actualIdentity === expectedIdentity;
}
