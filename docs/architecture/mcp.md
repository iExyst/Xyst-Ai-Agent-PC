# MCP integrations

Updated 2026-11-05. X.A.A. has initial stdio and Streamable HTTP client paths. This is not a claim of full Hermes MCP or plug-in parity.

## Static Agent Plugins package discovery

The authenticated `POST /extensions/packages/discover` endpoint and desktop Agent plugin discovery panel inspect either one package directory or immediate child directories. Discovery bounds enumeration to 128 packages, 128 skills per package, 64 MCP servers, and 256 KiB per JSON/skill file. It validates package containment and manifest shapes, rejects duplicate JSON keys, and reports per-package diagnostics. Skill listing checks the bounded `SKILL.md` path but does not read its contents. MCP summaries omit raw arguments, environment values, HTTP headers, and endpoint paths; stdio shows only the command basename, argument count, and environment variable names. HTTP endpoints are limited to HTTPS or loopback HTTP and reject URL credentials, query and fragment.

This is metadata discovery only. It does not copy/install packages, import skill text, launch MCP processes, connect remote servers, or register tools. Semantic compatibility with the upstream manifest schema, package trust/permission review, versioned skill lifecycle integration and install/activation remain open. Tests cover static summaries/redaction, duplicate-key diagnostics and endpoint authentication; symlink escape test is skipped locally where Windows disallows symlink creation and must be exercised in CI.

## Configuration and lifecycle

The authenticated loopback API and desktop store a user-selected transport and endpoint or process command. Stdio uses an argument array and optional working directory; it never accepts a shell command string. Streamable HTTP uses an HTTPS endpoint, or plain HTTP only for loopback hosts. URL credentials, query strings and fragments are rejected. An optional bearer token is saved by its declared variable name through the OS credential store and is never returned in API responses.

Connecting is an explicit desktop action. The app initializes an MCP session, discovers tools, and keeps the transport alive for the app process lifetime. Connections are closed on disconnect or shutdown; the user reconnects after restart. Each connection has its own background owner task so AnyIO cancellation scopes stay in their creating task. Calls on one server are serialized. Initialization is bounded to 15 seconds, calls to 45 seconds, listings to 256 tools/eight pages, input schemas to 128 KiB, and results to 256 KiB. Streamable HTTP uses the official SDK client, same-origin redirect policy, certificate verification, and an authenticated request header when a token is configured.

Schema 15 adds the transport and endpoint columns while preserving older stdio records as `stdio`. A new or changed MCP tool schema resets its permission to `none`.

## Permissions and trust

Discovered tools are namespaced by server ID. Their JSON schemas are validated, external `$ref` targets are rejected, and a digest covers schema, description, title, and annotations. The user can classify each tool as disabled, read, or write. Read tools can be offered to runs; write tools are only offered when the user separately selects them for that run. Tool intents and results are journaled, and results are labelled `untrusted_mcp_output`. Server metadata and output are untrusted data.

A disconnect before execution returns a known `not_connected` status after recording a completed result for the proposed call; it does not leave an unresolved intent or fail the run. Cancelling an in-flight write closes its connection, records `cancelled_unknown_outcome`, and leaves no unresolved intent. A timeout, transport failure, server-reported error, or output overflow during a write is journaled as `unknown_outcome` and is never retried automatically because a remote side effect may have happened. The system prompt asks the model to reconcile externally before proposing another write.

Configured stdio processes run with the user's process privileges. HTTP endpoints receive only the configured bearer token and user-authorized requests. Neither transport adds OS/container isolation for MCP servers.

## Verified scope and remaining work

`tests/integration/test_mcp_stdio.py` launches the SDK fixture as a real subprocess and verifies initialize, discovery, permissions, a tool round trip, output trust, disconnect, authenticated API control, credentials, cancellation and ambiguous-write handling. `tests/integration/test_mcp_http.py` runs an actual Streamable HTTP fixture on loopback and verifies discovery, bearer-token delivery, tool invocation and disconnect; endpoint policy rejects cleartext remote endpoints and embedded credentials. `tests/browser/mcp.spec.ts` covers stdio and Streamable HTTP configuration in the desktop. Native Windows setup/discovery/permission acceptance against a real stdio fixture passed in `.acceptance/3393e4d4-03a4-42d0-b4ff-3fc11fb3aac1/`; it uses no model inference and does not prove hosted server or installed package behavior.

These fixtures verify protocol paths against the official SDK, not compatibility with every third-party MCP server or hosted provider. Remaining work includes OAuth flows, reconnect policy, resources/prompts/sampling, package installation/activation and full manifest-schema compatibility, per-server process/network resource controls, broader native desktop acceptance, and third-party compatibility. No full MCP parity is claimed.
