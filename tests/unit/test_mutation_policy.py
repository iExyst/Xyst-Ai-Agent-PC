import hashlib
import os
import subprocess
from pathlib import Path

import pytest
from xaa.runtime import mutation_policy
from xaa.runtime.executor import ToolPreconditionError, WorkspaceExecutor
from xaa.runtime.file_handles import open_existing_for_edit
from xaa.storage.runs import RunStore


def run_in(root):
    store = RunStore(root / "db.sqlite3")
    run = store.create_run(
        objective="Guard private state",
        acceptance_criteria=[],
        workspace=str(root),
        mode="workspace",
        model=None,
    )
    return store, run["id"], store.acquire_lease(run["id"], "worker")


@pytest.mark.parametrize("directory", ["artifacts", "backups"])
def test_executor_refuses_private_creation_and_edit(workspace_tmp_path, directory):
    root = workspace_tmp_path
    private = root / directory
    private.mkdir()
    existing = private / "existing.txt"
    existing.write_bytes(b"preserve")
    store, run_id, token = run_in(root)
    executor = WorkspaceExecutor(store)
    with pytest.raises(ToolPreconditionError):
        executor.create_file(
            run_id,
            token,
            "new",
            {"path": f"{directory}/new.txt", "content": "bad"},
            idempotency_key="new",
        )
    assert not (private / "new.txt").exists()
    with pytest.raises(ToolPreconditionError):
        executor.write_file(
            run_id,
            token,
            "edit",
            {
                "path": f"{directory}/existing.txt",
                "content": "bad",
                "expected_sha256": hashlib.sha256(b"preserve").hexdigest(),
            },
            idempotency_key="edit",
        )
    assert existing.read_bytes() == b"preserve"
    assert not store.unresolved_tool_calls(run_id)


@pytest.mark.parametrize("suffix", ["", "-wal", "-shm", "-journal"])
def test_database_family_is_reserved(workspace_tmp_path, suffix):
    database = workspace_tmp_path / "db.sqlite3"
    with pytest.raises(PermissionError):
        mutation_policy.require_mutable_destination(Path(str(database) + suffix), database)
    mutation_policy.require_mutable_destination(workspace_tmp_path / "project.txt", database)


def test_opened_runtime_target_checked_before_write(workspace_tmp_path, monkeypatch):
    runtime = workspace_tmp_path / "python"
    runtime.mkdir()
    target = runtime / "module.py"
    target.write_bytes(b"preserve")
    monkeypatch.setattr(mutation_policy.sys, "prefix", str(runtime))
    with (
        pytest.raises(PermissionError),
        open_existing_for_edit(target, database=workspace_tmp_path / "db.sqlite3") as destination,
    ):
        destination.write(b"bad")
    assert target.read_bytes() == b"preserve"


def test_live_engine_and_entrypoint_are_reserved(workspace_tmp_path):
    engine = Path(mutation_policy.__file__).resolve().parents[2]
    for target in (
        engine / "new_shadow.py",
        engine.parent / "scripts" / "engine_entry.py",
        engine.parent / "apps" / "desktop" / "src" / "main.tsx",
    ):
        with pytest.raises(PermissionError):
            mutation_policy.require_mutable_destination(target, workspace_tmp_path / "db.sqlite3")


def test_frozen_executable_and_extraction_roots_reserved(workspace_tmp_path, monkeypatch):
    installed = workspace_tmp_path / "installed"
    extracted = workspace_tmp_path / "extracted"
    monkeypatch.setattr(mutation_policy.sys, "frozen", True, raising=False)
    monkeypatch.setattr(mutation_policy.sys, "_MEIPASS", str(extracted), raising=False)
    monkeypatch.setattr(mutation_policy.sys, "executable", str(installed / "engine.exe"))
    for root in (installed, extracted):
        with pytest.raises(PermissionError):
            mutation_policy.require_mutable_destination(root / "code.py", workspace_tmp_path / "db")


@pytest.mark.skipif(os.name != "nt", reason="Windows junction alias")
def test_private_directory_alias_target_still_reserved(workspace_tmp_path):
    actual = workspace_tmp_path / "actual"
    actual.mkdir()
    data = workspace_tmp_path / "data"
    data.mkdir()
    subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(data / "artifacts"), str(actual)],
        check=True,
        capture_output=True,
    )
    with pytest.raises(PermissionError):
        mutation_policy.require_mutable_destination(actual / "new.txt", data / "db.sqlite3")


@pytest.mark.skipif(os.name != "nt", reason="Windows UNC path semantics")
@pytest.mark.parametrize("server", ["wsl$", "WSL.LOCALHOST"])
def test_wsl_private_roots_reserved(workspace_tmp_path, server):
    for private in ("var/lib/xaa-execution-manager/claim", "tmp/xaa-executions/input"):
        with pytest.raises(PermissionError, match="private state"):
            mutation_policy.require_mutable_destination(
                Path(f"//{server}/Ubuntu-24.04/{private}"), workspace_tmp_path / "db.sqlite3"
            )
