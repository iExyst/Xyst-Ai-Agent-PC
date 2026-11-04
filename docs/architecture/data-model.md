# Initial data model

All records use stable IDs, UTC timestamps, schema versions, foreign keys and indexes. User time zones are stored separately for display and schedule interpretation. State changes and corresponding events are committed transactionally.

Tool journals distinguish completed results, known failed preconditions and unresolved intended actions. A write records `tool.executing` before touching bytes. Exclusive-open, preflight-read and hash-comparison failures may resolve as `failed` with `effect=not_started` only before that event. Failure results and events commit together and replay as tool responses under the original provider call ID. An error after execution begins remains unresolved until destination reconciliation; an exception class alone cannot establish that no effect occurred.

| Record | Key fields / purpose |
|---|---|
| sessions | identity, channel binding, title, timestamps |
| messages | session, role, content/artifact refs, sequence |
| runs | objective, state, budgets, permissions, lease, checkpoint |
| tasks | parent run/task, dependencies, role/model route, criteria, workspace, budgets, state/result |
| run_events | monotonic per-run sequence, event type, payload, timestamp |
| tool_calls | tool/version, validated arguments, idempotency key, state, result/error |
| artifacts | content address/path, media type, size, provenance |
| approvals | scope, requested action, decision, expiry |
| memories | kind, scope, provenance, confidence, lifecycle timestamps |
| skills | lifecycle state, declared tools/permissions |
| skill_versions | immutable versioned content and evaluation evidence |
| schedules | recurrence, timezone, missed-run and overlap policy, destination |
| schedule_executions | durable occurrence ID, outcome and delivery evidence |
| channel_bindings | account/channel identity mapping and authorization |
| provider_profiles | adapter, endpoint, model selection, capability snapshot; secret references only |

Large artifact bodies are held in managed storage; SQLite stores metadata. Secrets belong in the OS credential store, never in these records.

Schema version 3 adds `conversation_fts`, a derived FTS5 index synchronized with run objectives and public assistant text in run events. See `../releases/database-migrations.md` for backfill, deletion, privacy boundaries and downgrade behavior.
