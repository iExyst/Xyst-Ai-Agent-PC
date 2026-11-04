import hashlib

import pytest
from xaa.runtime.executor import ToolPreconditionError, WorkspaceExecutor
from xaa.runtime.file_handles import open_existing_for_edit
from xaa.storage.runs import RunStore


def test_edit_requires_workspace_permission_and_expected_content(workspace_tmp_path):
    target = workspace_tmp_path / "source.txt"
    target.write_text("before", encoding="utf-8")
    store = RunStore(workspace_tmp_path / "db.sqlite3")
    arguments = {
        "path": "source.txt",
        "content": "after",
        "expected_sha256": hashlib.sha256(b"before").hexdigest(),
    }
    executor = WorkspaceExecutor(store)
    for mode in ["observe", "workspace"]:
        run_id = store.create_run(
            objective="Edit",
            acceptance_criteria=[],
            workspace=str(workspace_tmp_path),
            mode=mode,
            model=None,
        )["id"]
        token = store.acquire_lease(run_id, "worker")
        if mode == "observe":
            with pytest.raises(PermissionError):
                executor.write_file(run_id, token, "edit", arguments, idempotency_key="edit")
        else:
            result = executor.write_file(run_id, token, "edit", arguments, idempotency_key="edit")
            assert target.read_text() == "after"
            assert result["sha256"] == hashlib.sha256(b"after").hexdigest()
            with pytest.raises(ValueError, match="changed"):
                executor.write_file(
                    run_id, token, "stale-edit", arguments, idempotency_key="stale-edit"
                )


def test_contending_edit_cannot_touch_locked_file(workspace_tmp_path):
    target = workspace_tmp_path / "locked.txt"
    target.write_bytes(b"unchanged")
    store = RunStore(workspace_tmp_path / "db.sqlite3")
    run_id = store.create_run(
        objective="Edit",
        acceptance_criteria=[],
        workspace=str(workspace_tmp_path),
        mode="workspace",
        model=None,
    )["id"]
    token = store.acquire_lease(run_id, "worker")
    with open_existing_for_edit(target), pytest.raises(ToolPreconditionError) as caught:
        WorkspaceExecutor(store).write_file(
            run_id,
            token,
            "write",
            {
                "path": "locked.txt",
                "content": "replacement",
                "expected_sha256": hashlib.sha256(b"unchanged").hexdigest(),
            },
            idempotency_key="write",
        )
    assert target.read_bytes() == b"unchanged"
    assert caught.value.result["effect"] == "not_started"
    assert caught.value.result["error"]["code"] == "open_failed"
    assert store.unresolved_tool_calls(run_id) == []
