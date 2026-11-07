import errno
from pathlib import Path

import pytest
from xaa.runtime.directory_listing import list_directory
from xaa.runtime.executor import WorkspaceExecutor
from xaa.runtime.workspace_search import search_workspace
from xaa.storage.runs import LeaseLost, RunStore


def setup_run(root: Path) -> tuple[RunStore, str, str]:
    (root / "folder").mkdir()
    (root / "folder" / "input.txt").write_text("needle", encoding="utf-8")
    store = RunStore(root / "runs.sqlite3")
    run_id = store.create_run(
        objective="Inspect files",
        acceptance_criteria=[],
        workspace=str(root),
        mode="observe",
        model=None,
    )["id"]
    return store, run_id, store.acquire_lease(run_id, "reader")


@pytest.mark.parametrize("operation", ["read", "list", "search"])
async def test_metadata_retry_keeps_one_public_intent(workspace_tmp_path, monkeypatch, operation):
    store, run_id, token = setup_run(workspace_tmp_path)
    target = workspace_tmp_path / "folder"
    if operation == "read":
        target = target / "input.txt"
    original_stat = Path.stat
    attempts = 0
    delays = []

    def flaky(path, *args, **kwargs):
        nonlocal attempts
        if path == target:
            attempts += 1
            if attempts <= 2:
                raise OSError(errno.EAGAIN, "transient metadata failure")
        return original_stat(path, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", flaky)
    monkeypatch.setattr("xaa.runtime.file_handles.time.sleep", delays.append)
    if operation == "read":
        result = WorkspaceExecutor(store).read_file(
            run_id, token, "main", {"path": "folder/input.txt"}, idempotency_key="main"
        )
        assert result["text"] == "needle"
    elif operation == "list":
        result = list_directory(store, run_id, token, "main", {"path": "folder"})
        assert any(entry["name"] == "input.txt" for entry in result["entries"])
    else:
        result = await search_workspace(
            store, run_id, token, "main", {"path": "folder", "query": "needle"}
        )
        assert result["matches"]
    assert delays == [0.05, 0.1]
    events = store.list_events(run_id)
    retries = [e["payload"] for e in events if e["event_type"] == "tool.read_retry"]
    assert [(e["stage"], e["attempt"]) for e in retries] == [("metadata", 1), ("metadata", 2)]
    assert (
        sum(
            e["event_type"] == "tool.intended" and e["payload"]["call_id"] == "main" for e in events
        )
        == 1
    )
    assert not store.unresolved_tool_calls(run_id)


@pytest.mark.parametrize(
    "error,expected", [(errno.EACCES, 1), (errno.ENOENT, 1), (errno.EAGAIN, 3)]
)
def test_metadata_errors_fail_closed_before_intent(
    workspace_tmp_path, monkeypatch, error, expected
):
    store, run_id, token = setup_run(workspace_tmp_path)
    target = workspace_tmp_path / "folder" / "input.txt"
    original_stat = Path.stat
    attempts = 0

    def fail(path, *args, **kwargs):
        nonlocal attempts
        if path == target:
            attempts += 1
            raise OSError(error, "metadata failure")
        return original_stat(path, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", fail)
    monkeypatch.setattr("xaa.runtime.file_handles.time.sleep", lambda _: None)
    with pytest.raises(OSError):
        WorkspaceExecutor(store).read_file(
            run_id, token, "main", {"path": "folder/input.txt"}, idempotency_key="main"
        )
    assert attempts == expected
    assert not store.unresolved_tool_calls(run_id)
    assert not any(e["event_type"] == "tool.intended" for e in store.list_events(run_id))


def test_metadata_retry_rechecks_lease_after_backoff(workspace_tmp_path, monkeypatch):
    store, run_id, token = setup_run(workspace_tmp_path)
    target = workspace_tmp_path / "folder" / "input.txt"
    original_stat = Path.stat
    attempts = 0

    def fail(path, *args, **kwargs):
        nonlocal attempts
        if path == target:
            attempts += 1
            raise OSError(errno.EAGAIN, "transient")
        return original_stat(path, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", fail)
    monkeypatch.setattr(
        "xaa.runtime.file_handles.time.sleep", lambda _: store.release_lease(run_id, token)
    )
    with pytest.raises(LeaseLost):
        WorkspaceExecutor(store).read_file(
            run_id, token, "main", {"path": "folder/input.txt"}, idempotency_key="main"
        )
    assert attempts == 1
    assert not store.unresolved_tool_calls(run_id)
