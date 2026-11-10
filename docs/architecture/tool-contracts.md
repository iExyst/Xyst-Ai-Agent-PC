# Workspace tool contracts

`engine/xaa/runtime/tool_contracts.py` is the coordinator's source of truth for the
public workspace file tools. Each versioned contract records its input/output
schema, permission, effect class, timeout, cancellation behavior, retry rule,
resource limits, audit fields, and whether the coordinator may expose it to a
model. The coordinator builds model-facing schemas and chooses the filesystem
worker operation and timeout from these contracts.

The initial registry covers directory listing, file reading, bounded search,
file creation, patching, text replacement, and conditional file writing. File
deletion has a contract for internal recovery, but is not exposed as a model
tool. All these operations use the owned filesystem worker with a ten-second
maximum, additionally bounded by the run deadline. The worker is terminated and
joined when the timeout or cancellation boundary is reached; uncertain effects
must be reconciled from the durable journal before any retry.

The registry is a scoped first slice, not the contract inventory for every
coordinator tool. Browser, web, command, Git, MCP, specialist, and messaging
contracts still need the same consistent treatment. Their existing behavior and
limits remain documented with their respective implementations until those
slices are migrated.
