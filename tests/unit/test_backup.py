import pytest
from xaa.storage.restore import restore_database
from xaa.storage.runs import RunStore


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
