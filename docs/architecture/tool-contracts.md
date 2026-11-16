# Coordinator tool contracts

`engine/xaa/runtime/tool_contracts.py` is the coordinator's source of truth for
the public workspace file tools, bounded command tool, and saved-plan tool. The
bounded Git inspection contract is declared beside its implementation in
`git_tools.py`. Each versioned contract records its input/output schema,
permission, effect class, timeout,
cancellation behavior, retry rule, resource limits, and audit fields. The
coordinator builds model-facing schemas and chooses the filesystem worker
operation and timeout from these contracts; Git inspection uses its declared
timeout for the owned process.

Directory listing shares one strict output model between its runtime descriptor
and coordinator registry. It validates every returned entry and enforces its
32 KiB ceiling against the complete serialized result, including path and
wrapper fields, before journaling.

The filesystem registry covers directory listing, file reading, bounded search,
file creation, patching, text replacement, and conditional file writing. File
deletion has a contract for internal recovery, but is not exposed as a model
tool. All these operations use the owned filesystem worker with a ten-second
maximum, additionally bounded by the run deadline. The worker is terminated and
joined when the timeout or cancellation boundary is reached; uncertain effects
must be reconciled from the durable journal before any retry. Git inspection is
read-only, kills and joins its process on cancellation or timeout, and caps the
saved output artifact at 8 MiB.

`web.fetch` declares and enforces a 300 KiB serialized-result ceiling, separate
from its 512 KiB response-body limit. Its contract distinguishes character
limits for the final URL, title, and extracted text; redirect URLs receive the
same URL-length check as the initial request. Its output schema carries these
field bounds so the provider-facing declaration matches runtime behavior.

`web.search` separately caps Brave's response at 1 MiB and the serialized result
at 512 KiB. Its schema limits ten results and declares each result field's
character bound; the coordinator exposes its input schema from the same
metadata for coordinator and specialist calls. Search errors and cancellation
have a separate bounded result shape.

`browser.open` caps the serialized tool result at 1 MiB after worker processing,
in addition to the worker response cap. Its schema bounds the final URL, title,
content, form controls and options. The coordinator derives the model-facing
input schema from browser metadata for coordinator and specialist calls.
Screenshots remain managed artifacts rather than inline result bytes.

`browser.submit` declares its external POST effect separately from browser
reading. Its contract records the same-host authorization, one-form/one-submitter
behavior, URL/form/value bounds, 35-second maximum, worker and serialized-result
ceilings, cancellation uncertainty, no automatic retry, and durable call audit
fields. A cancelled or timed-out submission may have reached the site and must
be reconciled before another attempt.

MCP discovery validates and pins both tool schemas with the discovered tool
identity. `MCPManager.contract_for_tool` returns a separate app-side execution
contract for a registered tool whose permission is granted for the run. The
descriptor includes connection state, pinned schemas and hash, effective
read/write permission, effect class, timeout, cancellation and retry behavior,
output validation, limits, and audit fields; it is not added to the
provider-facing function schema. The coordinator uses this descriptor to
authorize calls and records the server ID and schema hash with the durable call
intent, including when a previously advertised server disconnects before
execution. Output schemas are capped at 128 KiB and validated against structured
call results when supplied by the server. A mismatch on a read is reported as
invalid output; a mismatch on a write remains an unknown external outcome that
requires reconciliation. MCP content, structured values, catalogs, prompts and
resource reads retain their existing 256 KiB serialized bounds. Model-facing MCP
tool descriptions include the discovered schema hash, 45-second timeout,
no-automatic-retry rule, result cap, structured-output validation, and write
reconciliation guidance; server-provided descriptions remain labeled untrusted.
The coordinator supplies the contract's expected permission and schema hash to
the executor, which rechecks both before dispatch so revocation or rediscovery
between intent journaling and execution fails closed.

`plan.save` declares the typed dependency-graph proposal, saved/rejected result shapes, `task.delegate` permission, 64-task limit, run-deadline boundary, durable-call recovery rule, and plan audit fields. The coordinator exposes its input schema directly from this contract. A saved plan is not dispatched by the same call.

`plan.run_ready` declares its workspace read/private-snapshot write behavior, persistent task result, inherited `task.delegate` authority, typed task-status result, two-task dispatch bound, per-task/run deadlines, cancellation and recovery rules, snapshot/edit ceilings, 128 KiB serialized-result ceiling, and task-status audit fields. The coordinator validates the result against the output contract and byte limit before it is durably recorded. Dispatch remains a separate call after a saved plan; cancelled work retains fenced leases for recovery rather than being replayed automatically.

`procedure.view` and `procedure.read_resource` declare the exact selected-version inputs and untrusted text outputs. Their final serialized results are capped at 512 KiB before journaling or model delivery; the resource path schema is narrowed to the selected package paths.

Model-authored `procedure.draft`, `procedure.revise`, `procedure.resource_revise`, and `procedure.resource_remove` use versioned contracts sourced from the storage validators. Revision schemas are narrowed to the run's selected procedure/version, and the resource-removal field is exposed only with its separate grant. The local SQLite transaction checks that each bounded success receipt stays within 32 KiB before committing the candidate and durable tool result together. Contracts state the grant, candidate-only effect, lock timeout, retry/reconciliation rule, limits, and audit fields.

`procedure.catalog` is a versioned read contract with a strict page schema, 50-item limit, output field bounds, ten-second timeout, audit fields and a 512 KiB final serialized-result ceiling. Oversized output is replaced with a bounded, untrusted `output_limit` result.

The registry is a scoped first slice, not the contract inventory for every
coordinator tool. MCP execution and specialist dispatch contracts are
documented above. Telegram outbound delivery declares its bounded send request,
external-message effect, authorized account-bound destination, timeout,
unknown-outcome and retry behavior, local idempotency boundary, and delivery
audit fields in `TELEGRAM_SEND_CONTRACT`; other channel adapters still need an
equivalent inventory and serialized-output consistency review. The command
entry is a typed execution contract used for its model-facing input schema; the
coordinator still enforces its saved grant and run deadline, while the command
controller owns snapshot, execution, output, cancellation, and recovery behavior.
