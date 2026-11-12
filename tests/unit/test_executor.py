import hashlib
import os
import subprocess

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


def test_authorized_automation_can_write_only_to_its_selected_workspace(workspace_tmp_path):
    root = workspace_tmp_path / "project"
    root.mkdir()
    store = RunStore(workspace_tmp_path / "runs.sqlite3")
    observe_id = store.create_run(
        objective="Observe", acceptance_criteria=[], workspace=str(root), mode="observe", model=None
    )["id"]
    observe_token = store.acquire_lease(observe_id, "observe-worker")
    assert observe_token
    with pytest.raises(PermissionError):
        WorkspaceExecutor(store).create_file(
            observe_id,
            observe_token,
            "observe-create",
            {"path": "denied.txt", "content": "no"},
            idempotency_key="observe-create",
        )
    assert not (root / "denied.txt").exists()

    authorized_id = store.create_run(
        objective="Automate",
        acceptance_criteria=[],
        workspace=str(root),
        mode="authorized_automation",
        model=None,
    )["id"]
    authorized_token = store.acquire_lease(authorized_id, "automation-worker")
    assert authorized_token
    result = WorkspaceExecutor(store).create_file(
        authorized_id,
        authorized_token,
        "automation-create",
        {"path": "notes.txt", "content": "scheduled update"},
        idempotency_key="automation-create",
    )
    assert result["created"] is True
    assert (root / "notes.txt").read_text(encoding="utf-8") == "scheduled update"


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


def test_file_reads_and_edits_reject_junctions_to_outside_workspace(workspace_tmp_path):
    root = workspace_tmp_path / "project"
    outside = workspace_tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    sentinel = outside / "private.txt"
    original = b"private outside data\r\n"
    sentinel.write_bytes(original)
    junction = root / "external"
    if os.name == "nt":
        subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(junction), str(outside)],
            check=True,
            capture_output=True,
        )
    else:
        junction.symlink_to(outside, target_is_directory=True)
    try:
        store = RunStore(workspace_tmp_path / "runs.sqlite3")
        run_id = store.create_run(
            objective="Check workspace containment",
            acceptance_criteria=[],
            workspace=str(root),
            mode="workspace",
            model=None,
        )["id"]
        token = store.acquire_lease(run_id, "worker")
        executor = WorkspaceExecutor(store)
        with pytest.raises(PermissionError, match="outside the selected workspace"):
            executor.read_file(
                run_id,
                token,
                "junction-read",
                {"path": "external/private.txt"},
                idempotency_key="junction-read",
            )
        with pytest.raises(PermissionError, match="outside the selected workspace"):
            executor.write_file(
                run_id,
                token,
                "junction-edit",
                {
                    "path": "external/private.txt",
                    "content": "overwrite attempt",
                    "expected_sha256": hashlib.sha256(original).hexdigest(),
                },
                idempotency_key="junction-edit",
            )
        with pytest.raises(PermissionError, match="outside the selected workspace"):
            executor.delete_file(
                run_id,
                token,
                "junction-delete",
                {
                    "path": "external/private.txt",
                    "expected_sha256": hashlib.sha256(original).hexdigest(),
                },
                idempotency_key="junction-delete",
            )
        assert sentinel.read_bytes() == original
        assert store.unresolved_tool_calls(run_id) == []
    finally:
        if os.name == "nt":
            junction.rmdir()
        else:
            junction.unlink()
