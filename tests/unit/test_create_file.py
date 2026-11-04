import os
import subprocess
from concurrent.futures import ThreadPoolExecutor

import pytest
from xaa.runtime.create_handles import creation_parts, open_new_file
from xaa.runtime.executor import ReconciliationRequired, ToolPreconditionError, WorkspaceExecutor
from xaa.storage.runs import RunStore


def setup_run(root, mode="workspace"):
    store = RunStore(root / "db.sqlite3")
    run = store.create_run(
        objective="Create", acceptance_criteria=[], workspace=str(root), mode=mode, model=None
    )
    return store, run["id"], store.acquire_lease(run["id"], "worker")


@pytest.mark.parametrize(
    "path",
    [
        "../x",
        "a/../x",
        "/x",
        "C:x",
        "C:\\x",
        "x:stream",
        "NUL.txt",
        "a/CON",
        "x.",
        "x ",
        "a//x",
        "a/./x",
    ],
)
def test_creation_rejects_aliases_and_traversal(path):
    with pytest.raises(PermissionError):
        creation_parts(path)


@pytest.mark.skipif(os.name != "nt", reason="Windows creation backend")
def test_create_is_exclusive_and_preserves_existing_file(workspace_tmp_path):
    (workspace_tmp_path / "nested").mkdir()
    store, run_id, token = setup_run(workspace_tmp_path)
    executor = WorkspaceExecutor(store)
    result = executor.create_file(
        run_id,
        token,
        "new",
        {"path": "nested/new.txt", "content": "hello\n"},
        idempotency_key="new",
    )
    assert result["created"]
    assert (workspace_tmp_path / "nested/new.txt").read_bytes() == b"hello\n"
    with pytest.raises(ReconciliationRequired):
        executor.create_file(
            run_id,
            token,
            "new",
            {"path": "nested/new.txt", "content": "hello\n"},
            idempotency_key="new",
        )
    with pytest.raises(ToolPreconditionError) as caught:
        executor.create_file(
            run_id,
            token,
            "collision",
            {"path": "nested/new.txt", "content": "overwrite"},
            idempotency_key="collision",
        )
    assert caught.value.result["effect"] == "not_started"
    assert (workspace_tmp_path / "nested/new.txt").read_bytes() == b"hello\n"
    assert not store.unresolved_tool_calls(run_id)


@pytest.mark.skipif(os.name != "nt", reason="Windows creation backend")
def test_parent_handles_deny_rename_and_competing_creators(workspace_tmp_path):
    nested = workspace_tmp_path / "nested"
    nested.mkdir()
    with open_new_file(workspace_tmp_path, ("nested", "new")) as file:
        with pytest.raises(OSError):
            nested.rename(workspace_tmp_path / "moved")
        file.write(b"first")

    def attempt(content):
        try:
            with open_new_file(workspace_tmp_path, ("race",)) as file:
                file.write(content)
            return True
        except OSError:
            return False

    with ThreadPoolExecutor(2) as pool:
        winners = list(pool.map(attempt, [b"one", b"two"]))
    assert winners.count(True) == 1
    assert (workspace_tmp_path / "race").read_bytes() in {b"one", b"two"}


@pytest.mark.skipif(os.name != "nt", reason="Windows creation backend")
def test_creation_denies_junctions_missing_parents_and_observe_mode(workspace_tmp_path):
    outside = workspace_tmp_path / "outside"
    outside.mkdir()
    root = workspace_tmp_path / "workspace"
    root.mkdir()
    link = root / "link"
    subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(link), str(outside)], check=True, capture_output=True
    )
    store, run_id, token = setup_run(root)
    executor = WorkspaceExecutor(store)
    for identity, path in [("junction", "link/escape"), ("missing", "absent/new")]:
        with pytest.raises(ToolPreconditionError):
            executor.create_file(
                run_id, token, identity, {"path": path, "content": "deny"}, idempotency_key=identity
            )
    assert not (outside / "escape").exists()
    assert not store.unresolved_tool_calls(run_id)
    observed = store.create_run(
        objective="Observe", acceptance_criteria=[], workspace=str(root), mode="observe", model=None
    )
    observed_token = store.acquire_lease(observed["id"], "worker")
    with pytest.raises(PermissionError):
        executor.create_file(
            observed["id"],
            observed_token,
            "denied",
            {"path": "new", "content": "deny"},
            idempotency_key="denied",
        )
    assert not (root / "new").exists()


@pytest.mark.skipif(os.name != "nt", reason="Windows creation backend")
@pytest.mark.parametrize("destination", ["applied", "conflicting", "missing"])
def test_creation_fsync_failure_reconciles_without_rewriting(
    workspace_tmp_path, monkeypatch, destination
):
    store, run_id, token = setup_run(workspace_tmp_path)
    executor = WorkspaceExecutor(store)

    def fail(_descriptor):
        raise OSError("injected flush failure")

    with monkeypatch.context() as patch:
        patch.setattr("xaa.runtime.executor.os.fsync", fail)
        with pytest.raises(OSError, match="flush"):
            executor.create_file(
                run_id, token, "new", {"path": "new.txt", "content": "saved"}, idempotency_key="new"
            )
    target = workspace_tmp_path / "new.txt"
    if destination == "conflicting":
        target.write_bytes(b"changed externally")
    elif destination == "missing":
        target.unlink()
    if destination != "applied":
        assert executor.reconcile_writes(run_id, token) == {"new": destination}
        assert store.unresolved_tool_calls(run_id)
        assert target.exists() == (destination == "conflicting")
        return
    stamp = target.stat().st_mtime_ns
    assert store.unresolved_tool_calls(run_id)
    assert WorkspaceExecutor(RunStore(store.path)).reconcile_writes(run_id, token) == {
        "new": "applied"
    }
    assert not store.unresolved_tool_calls(run_id)
    assert target.stat().st_mtime_ns == stamp
    assert target.read_bytes() == b"saved"
