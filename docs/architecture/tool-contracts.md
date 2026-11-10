# Workspace tool contracts

`engine/xaa/runtime/tool_contracts.py` is the coordinator's source of truth for the
public workspace file tools and bounded command tool. The bounded Git inspection
contract is declared beside its implementation in `git_tools.py`. Each versioned
contract records its input/output schema, permission, effect class, timeout,
cancellation behavior, retry rule, resource limits, and audit fields. The
coordinator builds model-facing schemas and chooses the filesystem worker
operation and timeout from these contracts; Git inspection uses its declared
timeout for the owned process.

The filesystem registry covers directory listing, file reading, bounded search,
file creation, patching, text replacement, and conditional file writing. File
deletion has a contract for internal recovery, but is not exposed as a model
tool. All these operations use the owned filesystem worker with a ten-second
maximum, additionally bounded by the run deadline. The worker is terminated and
joined when the timeout or cancellation boundary is reached; uncertain effects
must be reconciled from the durable journal before any retry. Git inspection is
read-only, kills and joins its process on cancellation or timeout, and caps the
saved output artifact at 8 MiB.

The registry is a scoped first slice, not the contract inventory for every
coordinator tool. The command entry is a typed execution contract used for its
model-facing input schema; the coordinator still enforces its saved grant and
run deadline, while the command controller owns snapshot, execution, output,
cancellation, and recovery behavior. Browser, web, MCP, specialist, and
messaging contracts still need inventory and consistency review. Their existing
behavior and limits remain documented with their respective implementations.
