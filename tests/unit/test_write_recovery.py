import hashlib

import pytest
from xaa.runtime.executor import WorkspaceExecutor
from xaa.runtime.tool_worker import execute_filesystem
from xaa.storage.runs import RunStore


@pytest.mark.parametrize(
    "current,outcome",
    [(b"after", "applied"), (b"before", "unchanged"), (b"partial", "conflicting")],
)
@pytest.mark.parametrize("owned", [False, True], ids=["direct", "worker"])
async def test_write_effect_is_reconciled_without_repeating_it(
    workspace_tmp_path, monkeypatch, current, outcome, owned
):
    target = workspace_tmp_path / "file.txt"
    target.write_bytes(b"before")
    store = RunStore(workspace_tmp_path / "runs.sqlite3")
    run_id = store.create_run(
        objective="Edit",
        acceptance_criteria=[],
        workspace=str(workspace_tmp_path),
        mode="workspace",
        model=None,
    )["id"]
    token = store.acquire_lease(run_id, "old-worker")

    def interrupted(*args, **kwargs):
        raise RuntimeError("Simulated interruption after fsync")

    monkeypatch.setattr(store, "record_tool_result", interrupted)
    with pytest.raises(RuntimeError, match="interruption"):
        WorkspaceExecutor(store).write_file(
            run_id,
            token,
            "write-1",
            {
                "path": "file.txt",
                "content": "after",
                "expected_sha256": hashlib.sha256(b"before").hexdigest(),
            },
            idempotency_key="write-1",
        )
    assert target.read_bytes() == b"after"
    if current != b"after":
        target.write_bytes(current)
    modified = target.stat().st_mtime_ns
    store.release_lease(run_id, token)
    recovered = RunStore(store.path)
    new_token = recovered.acquire_lease(run_id, "new-worker")
    result = (
        await execute_filesystem(
            recovered,
            run_id,
            new_token,
            "reconcile",
            "reconcile",
            {},
            idempotency_key="reconcile",
            internal=True,
        )
        if owned
        else WorkspaceExecutor(recovered).reconcile_writes(run_id, new_token)
    )
    assert result == {"write-1": outcome}
    assert target.stat().st_mtime_ns == modified
    assert len(recovered.unresolved_tool_calls(run_id)) == (0 if outcome == "applied" else 1)
