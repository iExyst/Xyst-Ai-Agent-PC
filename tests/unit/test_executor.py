import pytest
from xaa.runtime.executor import ReconciliationRequired, WorkspaceExecutor
from xaa.runtime.file_handles import open_for_read
from xaa.storage.runs import RunStore


def test_workspace_read_is_bounded_journaled_and_not_replayed(workspace_tmp_path):
    root = workspace_tmp_path / "project"
    root.mkdir()
    (root / "input.txt").write_text("hello", encoding="utf-8")
    store = RunStore(workspace_tmp_path / "runs.sqlite3")
    run_id = store.create_run(
        objective="Read", acceptance_criteria=[], workspace=str(root), mode="observe", model=None
    )["id"]
    token = store.acquire_lease(run_id, "worker")
    assert token
    executor = WorkspaceExecutor(store)
    result = executor.read_file(
        run_id, token, "call-1", {"path": "input.txt"}, idempotency_key="read-1"
    )
    assert result["text"] == "hello"
    assert not store.unresolved_tool_calls(run_id)
    with pytest.raises(ReconciliationRequired):
        executor.read_file(run_id, token, "call-1", {"path": "input.txt"}, idempotency_key="read-1")
    with pytest.raises(PermissionError):
        executor.read_file(
            run_id, token, "escape", {"path": "../runs.sqlite3"}, idempotency_key="escape"
        )


def test_opened_destination_is_checked_before_reading(workspace_tmp_path, monkeypatch):
    root = workspace_tmp_path / "project"
    root.mkdir()
    safe = root / "input.txt"
    safe.write_text("safe", encoding="utf-8")
    outside = workspace_tmp_path / "outside.txt"
    outside.write_text("private outside data", encoding="utf-8")
    store = RunStore(workspace_tmp_path / "runs.sqlite3")
    run_id = store.create_run(
        objective="Read", acceptance_criteria=[], workspace=str(root), mode="observe", model=None
    )["id"]
    token = store.acquire_lease(run_id, "worker")
    original_open = open_for_read

    def swapped_open(path, *args, **kwargs):
        return original_open(outside if path == safe else path, *args, **kwargs)

    monkeypatch.setattr("xaa.runtime.executor.open_for_read", swapped_open)
    with pytest.raises(PermissionError, match="Opened file destination"):
        WorkspaceExecutor(store).read_file(
            run_id, token, "race", {"path": "input.txt"}, idempotency_key="race"
        )
    assert not any(event["event_type"] == "tool.completed" for event in store.list_events(run_id))
