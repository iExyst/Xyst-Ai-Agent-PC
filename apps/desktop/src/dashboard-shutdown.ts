import type { ChildProcess } from "node:child_process";

type BeforeQuitEvent = { preventDefault(): void };

/** Keep Electron open until every dashboard worker tree is confirmed stopped. */
export function createDashboardQuitGuard(
  stopTree: (pid: number) => Promise<boolean>,
  quit: () => void,
  reportCleanupFailure: () => void
) {
  let state: "idle" | "stopping" | "confirmed" = "idle";

  return (event: BeforeQuitEvent, children: Iterable<ChildProcess>) => {
    if (state === "confirmed") return;
    if (state === "stopping") { event.preventDefault(); return; }

    const pending = [...children].filter((child) => child.pid && child.exitCode === null && child.signalCode === null);
    if (!pending.length) return;

    event.preventDefault();
    state = "stopping";
    void Promise.allSettled(pending.map((child) => stopTree(child.pid!))).then((results) => {
      if (results.every((result) => result.status === "fulfilled" && result.value)) {
        state = "confirmed";
        quit();
      } else {
        state = "idle";
        reportCleanupFailure();
      }
    });
  };
}
