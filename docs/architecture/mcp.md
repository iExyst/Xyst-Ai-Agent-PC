# MCP stdio integrations

Updated 2026-11-05. This documents the initial MCP client slice; it does not claim full Hermes MCP or plug-in parity.

## Configuration and lifecycle

The authenticated loopback API stores a user-selected server name, executable, argument array, optional working directory, and names of credential variables. It does not accept shell command strings. Connecting is an explicit desktop action. The app launches the configured process using the official Python MCP SDK stdio transport, initializes a session, and discovers tools. Connections live only for the app process lifetime and are closed on disconnect or shutdown; the user must reconnect after restart.

Each connection's AnyIO stdio context is entered and exited by its own background owner task. API requests can then discover, call, disconnect, or shut down the connection without transferring an AnyIO cancel scope across tasks. Calls on one server are serialized. Initialization is bounded to 15 seconds, calls to 45 seconds, listings to 256 tools/eight pages, input schemas to 128 KiB, and results to 256 KiB.

Credentials are configured per declared environment variable and stored through the Windows OS credential store. Only the configured credential values are added to the MCP stdio environment; API responses expose configured booleans, never values. Server stderr is discarded. Diagnostics use bounded generic messages and redact configured secrets.

## Permissions and trust

Discovered tools are namespaced by server ID. Their JSON schemas are validated, external `$ref` targets are rejected, and a digest covers schema, description, title, and annotations. A new or changed schema resets permission to `none`. The user can classify each tool as disabled, read, or write. Read tools can be offered to runs; write tools are only offered when the user separately selects them for that run. Tool intents and results are journaled, and results are labelled `untrusted_mcp_output`. System instructions state that server metadata and output are untrusted.

A disconnect before execution returns a known `not_connected` status after recording a completed result for the already-proposed call; it does not leave an unresolved intent or fail the run. This path is tested through `Coordinator._execute_tool` with a real fixture server disconnected after discovery. A timeout, transport failure, server-reported error, or output overflow during a write is journaled as `unknown_outcome`, drops the connection when transport health is uncertain, and is never retried automatically because a remote side effect may have happened. The system prompt instructs the model to reconcile externally before proposing another write. Cancellation does not replay calls. The stdio process itself is explicitly configured by the local user and runs with the user's process privileges; this slice does not provide OS/container isolation for MCP servers.

## Verified scope and remaining work

`tests/integration/test_mcp_stdio.py` launches the MCP SDK fixture as a real subprocess and verifies initialize, discovery, default-deny, permission grant, a tool round trip, output trust label, disconnect, authenticated API control, and secret redaction/deletion using an in-memory credential-store substitute. This proves stdio protocol behavior against the fixture, not compatibility with every third-party MCP server or Windows Credential Manager live acceptance.

Remaining work includes cancellation and unknown-outcome fault-injection acceptance, reconnection policy, sampling/resources/prompts, HTTP MCP transports, package/manifest discovery, per-server process resource controls, broader native desktop acceptance, and third-party server compatibility. No full MCP parity is claimed.
