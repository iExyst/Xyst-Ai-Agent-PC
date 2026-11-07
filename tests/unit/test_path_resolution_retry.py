import errno
from pathlib import Path

import pytest
from xaa.runtime.executor import WorkspaceExecutor
from xaa.runtime.file_handles import resolve_read_path
from xaa.storage.runs import RunStore


def setup_run(root: Path) -> tuple[RunStore, str, str]:
    (root / "input.txt").write_text("verified", encoding="utf-8")
    store = RunStore(root / "runs.sqlite3")
    run_id = store.create_run(
        objective="Read a file",
        acceptance_criteria=[],
        workspace=str(root),
        mode="observe",
        model=None,
    )["id"]
    return store, run_id, store.acquire_lease(run_id, "reader")


@pytest.mark.parametrize("stage", ["workspace", "target"])
def test_read_path_resolution_retries_transient_errors_before_intent(
    workspace_tmp_path, monkeypatch, stage
):
    target = workspace_tmp_path if stage == "workspace" else workspace_tmp_path / "input.txt"
    if stage == "target":
        target.touch()
    original_resolve = Path.resolve
    attempts = 0
    delays = []

    def flaky_resolve(path, *args, **kwargs):
        nonlocal attempts
        if path == target:
            attempts += 1
            if attempts < 3:
                raise OSError(errno.EAGAIN, "transient resolution failure")
        return original_resolve(path, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", flaky_resolve)
    monkeypatch.setattr("xaa.runtime.file_handles.time.sleep", delays.append)
    if stage == "workspace":
        resolved = resolve_read_path(workspace_tmp_path)
        assert resolved == original_resolve(workspace_tmp_path)
    else:
        resolved = resolve_read_path(target)
        assert resolved == original_resolve(target)
    assert attempts == 3
    assert delays == [0.05, 0.1]


@pytest.mark.parametrize("error", [errno.EACCES, errno.ENOENT])
def test_path_resolution_does_not_retry_permanent_errors(workspace_tmp_path, monkeypatch, error):
    target = workspace_tmp_path / "input.txt"
    attempts = 0
    original_resolve = Path.resolve

    def fail(path, *args, **kwargs):
        nonlocal attempts
        if path == target:
            attempts += 1
            raise OSError(error, "permanent resolution failure")
        return original_resolve(path, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", fail)
    with pytest.raises(OSError):
        resolve_read_path(target)
    assert attempts == 1


def test_read_records_preflight_retry_and_still_journals_one_intent(
    workspace_tmp_path, monkeypatch
):
    store, run_id, token = setup_run(workspace_tmp_path)
    target = workspace_tmp_path / "input.txt"
    original_resolve = Path.resolve
    attempts = 0
    delays = []

    def flaky_resolve(path, *args, **kwargs):
        nonlocal attempts
        if path == target:
            attempts += 1
            if attempts == 1:
                raise OSError(errno.EAGAIN, "transient resolution failure")
        return original_resolve(path, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", flaky_resolve)
    monkeypatch.setattr("xaa.runtime.file_handles.time.sleep", delays.append)
    result = WorkspaceExecutor(store).read_file(
        run_id, token, "read-input", {"path": "input.txt"}, idempotency_key="read-input"
    )
    assert result["text"] == "verified"
    assert delays == [0.05]
    events = store.list_events(run_id)
    retries = [event for event in events if event["event_type"] == "tool.read_retry"]
    assert [event["payload"] for event in retries] == [
        {
            "call_id": "read-input",
            "attempt": 1,
            "delay_ms": 50,
            "stage": "path_resolution",
        }
    ]
    assert sum(event["event_type"] == "tool.intended" for event in events) == 1
    assert not store.unresolved_tool_calls(run_id)


def test_path_escape_is_not_retried(workspace_tmp_path, monkeypatch):
    workspace = workspace_tmp_path / "workspace"
    workspace.mkdir()
    store, run_id, token = setup_run(workspace)
    outside = workspace_tmp_path / "outside.txt"
    outside.write_text("private", encoding="utf-8")
    original_resolve = Path.resolve
    calls = []

    def count_resolve(path, *args, **kwargs):
        calls.append(path)
        return original_resolve(path, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", count_resolve)
    with pytest.raises(PermissionError):
        WorkspaceExecutor(store).read_file(
            run_id, token, "escape", {"path": "../outside.txt"}, idempotency_key="escape"
        )
    assert workspace / ".." / "outside.txt" in calls
    assert not any(event["event_type"] == "tool.read_retry" for event in store.list_events(run_id))
    assert not store.unresolved_tool_calls(run_id)
