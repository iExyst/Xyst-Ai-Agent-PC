type Port = Pick<NodeJS.Process, "on" | "removeListener" | "send" | "connected">;

/** A read-only dashboard worker accepts one request at a time and exits with its parent. */
export async function serveDesktopSnapshots(read: () => Promise<unknown>, controller: AbortController, port: Port = process, maxReads = Infinity): Promise<void> {
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
      const request = message as { type?: unknown; id?: unknown };
      if (request.type !== "desktop:snapshot" || typeof request.id !== "string" || request.id.length > 100) return;
      if (busy) { send({ type: "desktop:error", id: request.id, error: "Dashboard read already pending" }); return; }
      busy = true;
      void Promise.resolve().then(read).then(
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
