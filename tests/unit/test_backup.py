import hashlib
import os
import zipfile

import pytest
from xaa.storage.backup_bundle import create_backup_bundle, restore_backup_bundle
from xaa.storage.restore import restore_database
from xaa.storage.runs import DATABASE_SCHEMA_VERSION, RunStore


def test_backup_reopens_with_exact_run_and_events(workspace_tmp_path):
    store = RunStore(workspace_tmp_path / "live.sqlite3")
    run = store.create_run(
        objective="Preserve progress",
        acceptance_criteria=["Recover it"],
        workspace=None,
        mode="observe",
        model=None,
    )
    snapshot = store.backup()
    restored = RunStore(snapshot)
    assert restored.get_run(run["id"]) == store.get_run(run["id"])
    assert restored.list_events(run["id"]) == store.list_events(run["id"])
    later = store.create_run(
        objective="After snapshot",
        acceptance_criteria=[],
        workspace=None,
        mode="observe",
        model=None,
    )
    assert restored.get_run(later["id"]) is None


def test_offline_restore_preserves_records_and_refuses_overwrite(workspace_tmp_path):
    store = RunStore(workspace_tmp_path / "source.sqlite3")
    run = store.create_run(
        objective="Preserve", acceptance_criteria=[], workspace=None, mode="observe", model=None
    )
    snapshot = store.backup()
    destination = workspace_tmp_path / "restored" / "xaa.sqlite3"
    restore_database(snapshot, destination)
    assert RunStore(destination).get_run(run["id"]) == store.get_run(run["id"])
    before = destination.read_bytes()
    with pytest.raises(FileExistsError):
        restore_database(snapshot, destination)
    assert destination.read_bytes() == before


def test_bundle_restores_database_and_verified_artifact_bytes(workspace_tmp_path):
    store = RunStore(workspace_tmp_path / "live.sqlite3")
    run = store.create_run(
        objective="Keep output", acceptance_criteria=[], workspace=None, mode="observe", model=None
    )
    artifact_root = store.path.parent / "artifacts"
    artifact_root.mkdir()
    payload = b"saved command output\n"
    key = "saved-output.txt"
    (artifact_root / key).write_bytes(payload)
    with store.connection() as connection:
        connection.execute(
            "INSERT INTO artifacts VALUES (?,?,?,?,?,?,?,?,?)",
            (
                "artifact-1",
                run["id"],
                None,
                "text/plain",
                key,
                len(payload),
                hashlib.sha256(payload).hexdigest(),
                "test",
                "2026-11-05T00:00:00+00:00",
            ),
        )

    bundle = create_backup_bundle(store)
    destination = workspace_tmp_path / "recovered"
    restored_db = restore_backup_bundle(bundle, destination)
    restored = RunStore(restored_db)
    assert restored.get_run(run["id"]) == store.get_run(run["id"])
    assert (destination / "artifacts" / key).read_bytes() == payload
    assert restored.get_artifact("artifact-1")["sha256"] == hashlib.sha256(payload).hexdigest()
    with pytest.raises(FileExistsError):
        restore_backup_bundle(bundle, destination)


def test_bundle_restore_rejects_archive_path_traversal(workspace_tmp_path):
    store = RunStore(workspace_tmp_path / "live.sqlite3")
    bundle = create_backup_bundle(store)
    malicious = workspace_tmp_path / "malicious.xaa-backup"
    with zipfile.ZipFile(bundle) as source, zipfile.ZipFile(malicious, "w") as target:
        for item in source.infolist():
            target.writestr(item.filename, source.read(item.filename))
        target.writestr("artifacts/../../escape.txt", b"no")
    with pytest.raises(ValueError, match="entries do not match"):
        restore_backup_bundle(malicious, workspace_tmp_path / "recovered")
    assert not (workspace_tmp_path / "escape.txt").exists()


def test_previous_schema_backup_migrates_after_restore(workspace_tmp_path):
    store = RunStore(workspace_tmp_path / "previous-schema.sqlite3")
    with store.connection() as connection:
        connection.execute("ALTER TABLE mcp_servers DROP COLUMN plugin_sha256")
        connection.execute("PRAGMA user_version=24")
    bundle = create_backup_bundle(store)

    restored_path = restore_backup_bundle(bundle, workspace_tmp_path / "previous-schema-restored")
    restored = RunStore(restored_path)
    with restored.connection() as connection:
        columns = {row["name"] for row in connection.execute("PRAGMA table_info(mcp_servers)")}
        version = connection.execute("PRAGMA user_version").fetchone()[0]
    assert "plugin_sha256" in columns
    assert version == DATABASE_SCHEMA_VERSION


def test_bundle_publication_retries_transient_windows_directory_lock(
    workspace_tmp_path, monkeypatch
):
    if os.name != "nt":
        pytest.skip("Windows directory publication lock behavior")
    store = RunStore(workspace_tmp_path / "live.sqlite3")
    bundle = create_backup_bundle(store)
    real_rename = os.rename
    attempts = 0

    def rename_with_transient_lock(source, destination):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            failure = PermissionError(5, "Access is denied")
            failure.winerror = 5
            raise failure
        real_rename(source, destination)

    monkeypatch.setattr(os, "rename", rename_with_transient_lock)
    result = restore_backup_bundle(bundle, workspace_tmp_path / "recovered")
    assert result.is_file()
    assert attempts == 2
