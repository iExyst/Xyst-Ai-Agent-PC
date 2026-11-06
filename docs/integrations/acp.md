# Agent Client Protocol integration

X.A.A. provides an ACP stdio agent through `xaa acp`. It is a client bridge to the existing authenticated X.A.A. API: prompts create normal durable runs, the coordinator retains control of model/tool execution, and ACP cancellation requests cancel the corresponding run. The bridge does not implement a second agent loop.

## Current scope

- The user explicitly chooses the LM Studio model, workspace, and `observe` or `workspace` mode when launching the server.
- The ACP host must send that exact workspace as `session/new`'s `cwd`. Additional directories and host-provided MCP servers are refused.
- ACP currently accepts text prompts up to 2,000 UTF-8 bytes. Conversations persist in the X.A.A. chat-session store across ACP process restarts. `session/load` replays saved text turns to the host, and the API's bounded prior-turn context is reused.
- The ACP process sends its bearer token only to a numeric loopback HTTP URL. HTTPS, remote hosts, DNS names, URL credentials, query strings, and fragments are rejected.
- ACP cancellation calls the authenticated `/runs/{run_id}/cancel` route. Run completion and user-visible errors are returned through ACP session updates.

## Launching from an editor

The ACP process uses `XAA_API_TOKEN` and `XAA_API_URL` when they are present. Otherwise, while the desktop engine is running, it reads the current launch token and loopback URL from temporary Windows Credential Manager entries. The engine removes those entries at normal shutdown. Credential Manager is scoped to the logged-in Windows user, so this integration assumes ACP clients run as that same user.

For a development engine started outside Tauri, keep its API token in a protected environment; do not put it in shared editor settings or a committed file:

```powershell
$env:XAA_API_TOKEN = Read-Host "X.A.A. local API token"
uv run python -m xaa.api
```

For development, register `xaa` with arguments like these in an ACP-capable editor:

```json
{
  "command": "xaa",
  "args": [
    "acp",
    "--model",
    "qwen/qwen3.5-9b",
    "--workspace",
    "C:\\Users\\you\\Projects\\demo",
    "--mode",
    "observe"
  ]
}
```

The desktop installer bundles the same ACP entry point in `xaa-engine.exe`. Point an editor at the installed executable and pass `--cli` before the ACP arguments. For example, if installed to the default per-user location:

```json
{
  "command": "C:\\Users\\you\\AppData\\Local\\Programs\\Xyst AI Agent PC\\xaa-engine.exe",
  "args": [
    "--cli",
    "acp",
    "--model",
    "qwen/qwen3.5-9b",
    "--workspace",
    "C:\\Users\\you\\Projects\\demo",
    "--mode",
    "observe"
  ]
}
```

This packaged invocation uses the API URL and token placed in Windows Credential Manager by the running desktop engine. The editor process must run as the same Windows user.

The ACP process refuses to send the API token to nonnumeric hosts and requires a numeric loopback URL. An explicit `--api-url` argument or `XAA_API_URL` overrides the OS-vault launch URL; the selected URL must still pass this loopback check.

This is an initial ACP stdio slice. It does not yet support images/audio, host MCP servers, additional workspace roots, tool-call progress streaming, session discovery, or evaluation batch generation. The stable prompt and session flow follows the [Agent Client Protocol specification](https://agentclientprotocol.com/protocol/overview) and the [official Python SDK](https://agentclientprotocol.github.io/python-sdk/quickstart/).
