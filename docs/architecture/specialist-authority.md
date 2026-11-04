# Specialist filesystem authority

Read-only specialist workers carry the saved task ID and opaque worker token in the private worker protocol. Model tool arguments cannot select that scope, a root directory, parent ownership or an operating mode. Both scope fields must be present together; scoped requests permit only internal read/list operations.

A scoped RunStore validates the current parent lease and exact running task/token/expiry, workspace.read permission and saved task-to-parent workspace binding. The same validation applies at filesystem intent and result transactions, not just in the coordinator before launching a child. Its get_run view uses Observe mode and omits any parent command grant. Parent operations without task scope retain their normal run permissions.

Specialist model and tool-result events are published only in a transaction that rechecks parent and task authority. Expiry/revocation after a read therefore cannot publish a completed result or candidate evidence under stale task ownership. An interrupted read can remain unresolved until existing recovery examines it; scope loss never implies successful execution.

This boundary is a prerequisite for specialist editing. Specialists still use their existing read-only tools against the saved parent workspace. Separate working copies, write proposals, conflict integration and autonomous review are not implemented by this change. Future write support must add explicit isolated-workspace authority rather than weakening this read scope or inheriting a parent's command grant.

Run `uv run pytest -q tests/unit/test_task_worker_scope.py` for deterministic scope/race cases. After building the frozen engine, run `uv run python scripts/task_worker_scope_diagnostic.py`; it requires neither WSL nor a model and restricts the child PATH to System32. `scripts/live_specialists_diagnostic.py` separately tests two real local-model specialists with the installed selected model. Evidence and the retained failed model attempt are indexed in `../releases/evidence/task-worker-scope.json`. These checks do not establish isolated editing, full native specialist workflows or complete orchestration parity.
