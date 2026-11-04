import errno

import pytest
from xaa.runtime.executor import WorkspaceExecutor
from xaa.runtime.file_handles import open_for_read
from xaa.storage.runs import LeaseLost, RunStore


def fixture(root):
    target = root / "input.txt"
    target.write_bytes(b"verified content")
    store = RunStore(root / "db.sqlite3")
    run = store.create_run(
        objective="Read", acceptance_criteria=[], workspace=str(root), mode="observe", model=None
    )
    token = store.acquire_lease(run["id"], "reader")
    return target, store, run["id"], token


def test_transient_reads_retry_twice_with_one_intent(workspace_tmp_path, monkeypatch):
    target, store, run_id, token = fixture(workspace_tmp_path)
    original = open_for_read
    attempts = []
    delays = []

    def flaky(path, *args, **kwargs):
        if path == target:
            attempts.append(path)
            if len(attempts) < 3:
                raise OSError(errno.EAGAIN, "Transient read")
        return original(path, *args, **kwargs)

    monkeypatch.setattr("xaa.runtime.executor.open_for_read", flaky)
    monkeypatch.setattr("xaa.runtime.executor.time.sleep", delays.append)
    result = WorkspaceExecutor(store).read_file(
        run_id, token, "read", {"path": "input.txt"}, idempotency_key="read"
    )
    assert result["text"] == "verified content"
    assert len(attempts) == 3
    assert delays == [0.05, 0.1]
    events = store.list_events(run_id)
    assert sum(e["event_type"] == "tool.intended" for e in events) == 1
    assert sum(e["event_type"] == "tool.read_retry" for e in events) == 2
    assert not store.unresolved_tool_calls(run_id)


@pytest.mark.parametrize(
    "error,attempt_count", [(errno.EAGAIN, 3), (errno.EACCES, 1), (errno.ENOENT, 1)]
)
def test_read_failure_retry_policy_is_bounded(
    workspace_tmp_path, monkeypatch, error, attempt_count
):
    target, store, run_id, token = fixture(workspace_tmp_path)
    original = open_for_read
    attempts = []

    def fail(path, *args, **kwargs):
        if path == target:
            attempts.append(path)
            raise OSError(error, "Injected read failure")
        return original(path, *args, **kwargs)

    monkeypatch.setattr("xaa.runtime.executor.open_for_read", fail)
    monkeypatch.setattr("xaa.runtime.executor.time.sleep", lambda _delay: None)
    with pytest.raises(OSError):
        WorkspaceExecutor(store).read_file(
            run_id, token, "read", {"path": "input.txt"}, idempotency_key="read"
        )
    assert len(attempts) == attempt_count
    assert len(store.unresolved_tool_calls(run_id)) == 1
    assert not any(e["event_type"] == "tool.completed" for e in store.list_events(run_id))


def test_retry_rechecks_opened_destination(workspace_tmp_path, monkeypatch):
    root = workspace_tmp_path / "workspace"
    root.mkdir()
    target, store, run_id, token = fixture(root)
    outside = workspace_tmp_path / "outside.txt"
    outside.write_bytes(b"private")
    original = open_for_read
    attempts = []

    def swapped(path, *args, **kwargs):
        if path == target:
            attempts.append(path)
            if len(attempts) == 1:
                raise OSError(errno.EAGAIN, "Transient")
            return original(outside, *args, **kwargs)
        return original(path, *args, **kwargs)

    monkeypatch.setattr("xaa.runtime.executor.open_for_read", swapped)
    monkeypatch.setattr("xaa.runtime.executor.time.sleep", lambda _delay: None)
    with pytest.raises(PermissionError, match="escaped"):
        WorkspaceExecutor(store).read_file(
            run_id, token, "read", {"path": "input.txt"}, idempotency_key="read"
        )
    assert len(attempts) == 2
    assert not any(e["event_type"] == "tool.completed" for e in store.list_events(run_id))


def test_retry_stops_if_lease_is_revoked(workspace_tmp_path, monkeypatch):
    target, store, run_id, token = fixture(workspace_tmp_path)
    original = open_for_read
    attempts = []

    def fail(path, *args, **kwargs):
        if path == target:
            attempts.append(path)
            raise OSError(errno.EAGAIN, "Transient")
        return original(path, *args, **kwargs)

    monkeypatch.setattr("xaa.runtime.executor.open_for_read", fail)
    monkeypatch.setattr(
        "xaa.runtime.executor.time.sleep", lambda _delay: store.release_lease(run_id, token)
    )
    with pytest.raises(LeaseLost):
        WorkspaceExecutor(store).read_file(
            run_id, token, "read", {"path": "input.txt"}, idempotency_key="read"
        )
    assert len(attempts) == 1
