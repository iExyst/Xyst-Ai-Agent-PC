const { spawn } = require("node:child_process");
const { createRequire } = require("node:module");
const { createServer: createNetServer } = require("node:net");
const { resolve } = require("node:path");

const requireFromHere = createRequire(__filename);
const workspaceRoot = resolve(__dirname, "..");

function findAvailablePort() {
  return new Promise((resolvePort, reject) => {
    const probe = createNetServer();
    probe.once("error", reject);
    probe.listen(0, "127.0.0.1", () => {
      const address = probe.address();
      if (!address || typeof address === "string") {
        probe.close(() => reject(new Error("Could not determine an available renderer port")));
        return;
      }
      probe.close((error) => error ? reject(error) : resolvePort(address.port));
    });
  });
}

async function main() {
  const { createServer } = await import("vite");
  const port = await findAvailablePort();
  const server = await createServer({
    clearScreen: false,
    server: { host: "127.0.0.1", port, strictPort: true, open: false }
  });
  let child;
  let serverClose;

  const closeServer = () => serverClose ??= server.close();

  const close = async () => {
    if (child && child.exitCode === null && child.signalCode === null) child.kill();
    await closeServer();
  };

  try {
    await server.listen();
    const rendererUrl = server.resolvedUrls?.local?.[0];
    if (!rendererUrl) throw new Error("Vite started without a local renderer URL");

    console.log(`[Xyst] Renderer listening at ${rendererUrl}`);
    const electronPath = requireFromHere("electron");
    child = spawn(electronPath, [workspaceRoot], {
      cwd: workspaceRoot,
      env: { ...process.env, VITE_DEV_SERVER_URL: rendererUrl },
      stdio: "inherit"
    });

    process.once("SIGINT", close);
    process.once("SIGTERM", close);
    child.once("error", async (error) => {
      console.error("[Xyst] Could not start Electron:", error);
      await close();
      process.exitCode = 1;
    });
    child.once("exit", async (code, signal) => {
      await closeServer();
      process.exitCode = code ?? (signal ? 1 : 0);
    });
  } catch (error) {
    await closeServer();
    throw error;
  }
}

main().catch((error) => {
  console.error("[Xyst] Desktop development startup failed:", error);
  process.exitCode = 1;
});
