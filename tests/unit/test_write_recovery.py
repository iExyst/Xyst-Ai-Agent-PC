import hashlib
import json

import pytest
from xaa.runtime.executor import WorkspaceExecutor
from xaa.runtime.tool_worker import execute_filesystem
from xaa.storage.runs import RunStore


def test_owner_reconciles_interrupted_edit_only_after_original_hash_is_restored(workspace_tmp_path):
    target = workspace_tmp_path / "file.txt"
    original = b"before"
    target.write_bytes(original)
    store = RunStore(workspace_tmp_path / "runs.sqlite3")
    run_id = store.create_run(
        objective="Edit",
        acceptance_criteria=[],
        workspace=str(workspace_tmp_path),
        mode="workspace",
        model=None,
    )["id"]
    token = store.acquire_lease(run_id, "owner-recovery")
    arguments = {
        "path": "file.txt",
        "content": "after",
        "expected_sha256": hashlib.sha256(original).hexdigest(),
    }
    assert store.record_tool_intent(
        run_id,
        token,
        call_id="edit",
        tool="workspace.write_file",
        version="1",
        arguments=arguments,
        idempotency_key="edit",
    )
    store.append_worker_event(run_id, token, "tool.executing", {"call_id": "edit"})
    target.write_bytes(b"partial")
    with pytest.raises(ValueError, match="Restore the original"):
        WorkspaceExecutor(store).reconcile_write_not_applied(run_id, token, "edit")
    assert store.unresolved_tool_calls(run_id)
    target.write_bytes(original)
    modified = target.stat().st_mtime_ns
    result = WorkspaceExecutor(store).reconcile_write_not_applied(run_id, token, "edit")
    assert result["effect"] == "not_applied_after_review"
    assert target.read_bytes() == original
    assert target.stat().st_mtime_ns == modified
    assert store.unresolved_tool_calls(run_id) == []
    assert any(event["event_type"] == "tool.reconciled" for event in store.list_events(run_id))


def test_owner_reconciles_interrupted_creation_only_when_destination_is_absent(workspace_tmp_path):
    store = RunStore(workspace_tmp_path / "runs.sqlite3")
    run_id = store.create_run(
        objective="Create",
        acceptance_criteria=[],
        workspace=str(workspace_tmp_path),
        mode="workspace",
        model=None,
    )["id"]
    token = store.acquire_lease(run_id, "owner-recovery")
    arguments = {"path": "new.txt", "content": "new content"}
    assert store.record_tool_intent(
        run_id,
        token,
        call_id="create",
        tool="workspace.create_file",
        version="1",
        arguments=arguments,
        idempotency_key="create",
    )
    target = workspace_tmp_path / "new.txt"
    target.write_bytes(b"")
    with pytest.raises(ValueError, match="destination is absent"):
        WorkspaceExecutor(store).reconcile_write_not_applied(run_id, token, "create")
    assert store.unresolved_tool_calls(run_id)
    target.unlink()
    result = WorkspaceExecutor(store).reconcile_write_not_applied(run_id, token, "create")
    assert result["effect"] == "not_applied_after_review"
    assert not target.exists()
    assert store.unresolved_tool_calls(run_id) == []


def test_owner_reconciles_interrupted_delete_only_after_original_hash_is_restored(
    workspace_tmp_path,
):
    original = b"reviewed file contents"
    target = workspace_tmp_path / "file.txt"
    target.write_bytes(original)
    store = RunStore(workspace_tmp_path / "runs.sqlite3")
    run_id = store.create_run(
        objective="Delete",
        acceptance_criteria=[],
        workspace=str(workspace_tmp_path),
        mode="workspace",
        model=None,
    )["id"]
    token = store.acquire_lease(run_id, "owner-recovery")
    arguments = {"path": "file.txt", "expected_sha256": hashlib.sha256(original).hexdigest()}
    assert store.record_tool_intent(
        run_id,
        token,
        call_id="delete",
        tool="workspace.delete_file",
        version="1",
        arguments=arguments,
        idempotency_key="delete",
        internal=True,
    )
    target.unlink()
    with pytest.raises(ValueError, match="Restore the deleted file"):
        WorkspaceExecutor(store).reconcile_write_not_applied(run_id, token, "delete")
    assert store.unresolved_tool_calls(run_id)
    target.write_bytes(original)
    result = WorkspaceExecutor(store).reconcile_write_not_applied(run_id, token, "delete")
    assert result["effect"] == "not_applied_after_review"
    assert target.read_bytes() == original
    assert store.unresolved_tool_calls(run_id) == []
    events = store.list_events(run_id)
    failed = next(event for event in events if event["event_type"] == "tool.failed")
    reconciled = next(event for event in events if event["event_type"] == "tool.reconciled")
    assert failed["payload"]["internal"] is True
    assert reconciled["payload"]["internal"] is True
    assert store.public_tool_result(run_id, "delete") is None


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
    executor = WorkspaceExecutor(store)
    executor.read_file(run_id, token, "read", {"path": "file.txt"}, idempotency_key="read")

    def interrupted(*args, **kwargs):
        raise RuntimeError("Simulated interruption after fsync")

    monkeypatch.setattr(store, "record_tool_result", interrupted)
    with pytest.raises(RuntimeError, match="interruption"):
        executor.write_file(
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


async def test_owner_confirmed_no_effect_recovery_runs_in_filesystem_worker(workspace_tmp_path):
    store = RunStore(workspace_tmp_path / "runs.sqlite3")
    run_id = store.create_run(
        objective="Create",
        acceptance_criteria=[],
        workspace=str(workspace_tmp_path),
        mode="workspace",
        model=None,
    )["id"]
    token = store.acquire_lease(run_id, "owner-recovery")
    assert store.record_tool_intent(
        run_id,
        token,
        call_id="create",
        tool="workspace.create_file",
        version="1",
        arguments={"path": "new.txt", "content": "value"},
        idempotency_key="create",
    )
    result = await execute_filesystem(
        store,
        run_id,
        token,
        "owner-reconcile",
        "reconcile_not_applied",
        {"provider_call_id": "create"},
        idempotency_key="owner-reconcile",
        internal=True,
    )
    assert result["effect"] == "not_applied_after_review"
    assert store.unresolved_tool_calls(run_id) == []
    assert (
        json.loads(store.public_tool_result(run_id, "create"))["effect"]
        == "not_applied_after_review"
    )
    assert not (workspace_tmp_path / "new.txt").exists()
