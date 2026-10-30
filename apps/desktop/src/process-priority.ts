import { constants, setPriority } from "node:os";

type PrioritySetter = (pid: number, priority: number) => void;

/** Let Windows foreground work outrank Xyst background workers. */
export function lowerBackgroundProcessPriority(
  pid: number | undefined,
  platform = process.platform,
  apply: PrioritySetter = setPriority
): boolean {
  if (platform !== "win32" || !pid) return false;
  try {
    apply(pid, constants.priority.PRIORITY_BELOW_NORMAL);
    return true;
  } catch {
    // Priority adjustment is a best-effort responsiveness aid, not a launch requirement.
    return false;
  }
}
