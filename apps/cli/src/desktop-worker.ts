type Port = Pick<NodeJS.Process, "on" | "removeListener" | "send" | "connected">;

/** A read-only dashboard worker accepts one request at a time and exits with its parent. */
export async function serveDesktopSnapshots(
  read: () => Promise<unknown>,
  controller: AbortController,
  port: Port = process,
  maxReads = Infinity,
  readProcessJob?: (jobId: string) => Promise<unknown>
): Promise<void> {
  if (!port.connected || !port.send) throw new Error("Desktop snapshot worker requires an IPC parent");
  await new Promise<void>((resolve) => {
    let busy = false;
    let closed = false;
    let completed = 0;
    const finish = () => {
      port.removeListener("message", onMessage);
      port.removeListener("disconnect", onDisconnect);
      controller.signal.removeEventListener("abort", onAbort);
      resolve();
    };
    const send = (message: unknown) => new Promise<void>((sent) => {
      if (closed || !port.connected) { sent(); return; }
      try { port.send!(message, () => sent()); } catch { sent(); }
    });
    const onDisconnect = () => {
      closed = true;
      controller.abort();
      if (!busy) finish();
    };
    const onAbort = () => { closed = true; if (!busy) finish(); };
    const onMessage = (message: unknown) => {
      if (closed || !message || typeof message !== "object") return;
      const request = message as { type?: unknown; id?: unknown; jobId?: unknown };
      if (!["desktop:snapshot", "desktop:process-show"].includes(String(request.type)) || typeof request.id !== "string" || request.id.length > 100) return;
      if (request.type === "desktop:process-show" && (typeof request.jobId !== "string" || !request.jobId || request.jobId.length > 200)) {
        send({ type: "desktop:error", id: request.id, error: "Invalid process job ID" });
        return;
      }
      if (busy) { send({ type: "desktop:error", id: request.id, error: "Desktop worker request already pending" }); return; }
      if (request.type === "desktop:process-show" && !readProcessJob) {
        send({ type: "desktop:error", id: request.id, error: "Process inspection is unavailable" });
        return;
      }
      busy = true;
      const operation = request.type === "desktop:process-show" ? () => readProcessJob!(request.jobId as string) : read;
      void Promise.resolve().then(operation).then(
        (data) => send({ type: "desktop:result", id: request.id, data }),
        (error) => send({ type: "desktop:error", id: request.id, error: error instanceof Error ? error.message.slice(0, 1000) : "Dashboard read failed" })
      ).finally(() => { busy = false; if (++completed >= maxReads) closed = true; if (closed) finish(); });
    };
    port.on("message", onMessage);
    port.on("disconnect", onDisconnect);
    controller.signal.addEventListener("abort", onAbort, { once: true });
    if (controller.signal.aborted) onAbort();
    else send({ type: "desktop:ready" });
  });
}
