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

`web.fetch` declares and enforces a 300 KiB serialized-result ceiling, separate
from its 512 KiB response-body limit. Its contract distinguishes character
limits for the final URL, title, and extracted text; redirect URLs receive the
same URL-length check as the initial request. Its output schema carries these
field bounds so the provider-facing declaration matches runtime behavior.

`web.search` separately caps Brave's response at 1 MiB and the serialized result
at 512 KiB. Its schema limits ten results and declares each result field's
character bound; the coordinator exposes its input schema from the same
metadata. Search errors and cancellation have a separate bounded result shape.

`browser.open` caps the serialized tool result at 1 MiB after worker processing,
in addition to the worker response cap. Its schema bounds the final URL, title,
content, form controls and options. The coordinator derives the model-facing
input schema from browser metadata. Screenshots remain managed artifacts rather
than inline result bytes.

The registry is a scoped first slice, not the contract inventory for every
coordinator tool. The command entry is a typed execution contract used for its
model-facing input schema; the coordinator still enforces its saved grant and
run deadline, while the command controller owns snapshot, execution, output,
cancellation, and recovery behavior. MCP, specialist, and messaging contracts
still need inventory and serialized-output consistency review. Their
existing behavior and limits remain documented with their respective implementations.
