# Pinned Hermes memory source audit

Reviewed 2026-11-11 against the local Hermes checkout at `1e208b280deb18f01d015b0acdf3ef3552d08039`. This is a source-level comparison, not an upstream runtime test or a claim of parity. SHA-256 values below bind the findings to the reviewed files.

## Reviewed implementation

| Pinned source | SHA-256 | Findings |
|---|---|---|
| `agent/memory_manager.py` | `b181fbcd01bf88d6ee6a8fc2f5e4f58b3bbe58c6dfcadde00f6cea5f88405304` | Owns provider lifecycle, prompt blocks, prefetch, turn synchronization, tool dispatch, async hooks, session boundaries, compression checkpoints and provider-health/stuck-call handling. Provider instances are bound to a profile to prevent cross-profile state reuse. |
| `agent/memory_provider.py` | `ac81dcf489c698891f0edebfe26845a75c50d2bde77ec3be8d7938a85c79f1fc` | Defines the adapter contract for initialization, availability, prompt context, retrieval, turn sync, tools, recall status, identity and shutdown hooks. Third-party providers are optional and must honor profile scoping. |
| `agent/memory_manager_tools.py` | `dbd96b5e8aeae41afa6bbec48ad1a837315a268be2eedcf43c7cc609540c480d` | Adds provider tool schemas only when the selected toolset enables them; normalizes and checks tool names before registration. |
| `tools/memory_tool.py` | `00c86420cb4d824dbfe2806b1f748a1fc182b8c11ea2f902e8b77d3d035624dd` | Built-in `memory` tool targets personal notes or user profile, supports add/patch/remove and batches, and routes writes through approval staging. Destructive writes from unattended reviews are staged for owner review. |
| `tools/memory_tool_store.py` | `0f665c95fcf44e3115c7a625622885e20254418b414a92a1f8570d1300e9fc31` | Implements bounded file-backed entries and profile flags for `MEMORY.md` and `USER.md`; store limits are checked on writes. |
| `agent/memory_context.py` | `3ed4a7bb2d7b5d8394e204c911c7983e4e1804a797083fffc87a071767b3851f` | Sanitizes provider context fences and injected memory markers before context is sent onward, and removes duplicate recall lines without merging provenance. |

## Behavioral comparison

- Hermes combines a built-in file store with an optional provider lifecycle. Retrieval can be prefetched by query, providers can synchronize a turn asynchronously, and providers may add their own tools. Provider unavailability and stalled calls are surfaced through manager status paths.
- The built-in Hermes store distinguishes personal notes from a user profile and permits model proposed edits through the memory tool's write gate. Background review cannot silently patch or remove entries; those changes are staged for the owner.
- X.A.A. stores explicit preferences and user-sourced knowledge in SQLite. Records carry kind, scope, content, provenance, confidence, creation/update times, and an optional source URI. FTS5 powers owner search; owner controls support edit, export and deletion, and index triggers track changes transactionally.
- X.A.A. recalls bounded, most-recent explicit preferences and sourced knowledge into coordinator/specialist context. It labels sourced content as unverified and untrusted. It does not yet expose a pluggable memory-provider lifecycle, provider-controlled prefetch/sync hooks, provider-contributed tools, profile-specific health status, or model-proposed memory writes. Its own query-ranked FTS5 recall uses the current parent or specialist objective, with the existing byte limit. These remain explicit parity gaps; they are not prerequisites for the objective's safer explicit-memory gate.
- The objective requires conversation history, preferences, task outcomes, sourced knowledge and procedural skills as distinct records, provenance and owner controls, and prohibits promoting arbitrary tool/web output into trusted instructions. Current run/session, skill and memory stores keep those categories separate. The evidence establishes selected flows only, not broad retrieval quality or full memory parity.

## Boundaries and next work

No external provider was enabled, no network service was contacted, and no upstream test was run. X.A.A. does not need to copy Hermes's automatic turn synchronization or plugin tools to meet the objective. If added later, they need profile binding, bounded provider timeouts, provenance retention, untrusted-context handling, explicit write authorization, and recoverable owner review before model-suggested edits can be considered.

The immediately actionable parity work is to keep the matrix explicit about query retrieval and provider lifecycle, then audit the adjacent skill source paths against X.A.A.'s lifecycle. Do not mark memory or Phase 0 complete from this one capability audit.
