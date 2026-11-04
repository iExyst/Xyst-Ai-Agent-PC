import time

import pytest
from pydantic import ValidationError
from xaa.runtime import snapshot_worker
from xaa.runtime.executor import ReconciliationRequired
from xaa.runtime.snapshot_worker import SnapshotRequest, capture_command_snapshot
from xaa.runtime.states import RunState
from xaa.runtime.workspace_snapshot import capture_workspace
from xaa.storage.commands import CommandJournal
from xaa.storage.runs import LeaseLost, RunStore


def setup(root):
    project = root / "project"
    project.mkdir()
    store = RunStore(root / "data" / "db.sqlite3")
    run = store.create_run(
        objective="Capture command inputs",
        acceptance_criteria=[],
        workspace=str(project),
        mode="workspace",
        model=None,
        command_grant={"distribution": "Ubuntu", "profile_sha256": "a" * 64},
    )
    token = store.acquire_lease(run["id"], "worker", seconds=60)
    store.transition(run["id"], RunState.PLANNING, owner=token)
    store.transition(run["id"], RunState.RUNNING, owner=token)
    CommandJournal(store).reserve(
        run["id"],
        token,
        "command",
        {"argv": ["/usr/bin/true"]},
        idempotency_key="command",
    )
    return store, run["id"], token, project


async def test_real_worker_captures_binary_unicode_without_project_imports(
    workspace_tmp_path, monkeypatch
):
    store, run_id, token, project = setup(workspace_tmp_path)
    (project / "\u96ea.txt").write_bytes(b"\x00\xffunicode fixture")
    shadow = project / "xaa"
    shadow.mkdir()
    (shadow / "__init__.py").write_text("raise RuntimeError('unsafe import')", encoding="utf-8")
    monkeypatch.chdir(project)
    monkeypatch.setenv("PYTHONPATH", str(project))
    expected = capture_workspace(project)
    actual = await capture_command_snapshot(
        store, run_id, token, "command", deadline=time.monotonic() + 10
    )
    assert actual.sha256 == expected.sha256
    assert {entry.path: entry.content for entry in actual.files} == {
        entry.path: entry.content for entry in expected.files
    }
    assert not store.list_artifacts(run_id)
    assert not any(event["event_type"] == "tool.executing" for event in store.list_events(run_id))


async def test_maximum_binary_contents_cross_owned_pipe(workspace_tmp_path):
    store, run_id, token, project = setup(workspace_tmp_path)
    for number in range(4):
        (project / f"binary{number}.dat").write_bytes(b"\x00\xff" * (1024 * 1024))
    snapshot = await capture_command_snapshot(
        store, run_id, token, "command", deadline=time.monotonic() + 10
    )
    assert sum(len(entry.content) for entry in snapshot.files) == 8 * 1024 * 1024
    assert snapshot.sha256 == capture_workspace(project).sha256


async def test_new_owner_cannot_capture_old_intent(workspace_tmp_path, monkeypatch):
    store, run_id, token, _ = setup(workspace_tmp_path)
    store.release_lease(run_id, token)
    replacement = store.acquire_lease(run_id, "replacement")

    async def forbidden(*args, **kwargs):
        pytest.fail("Replacement owner must reconcile, never spawn a snapshot worker")

    monkeypatch.setattr(snapshot_worker, "request_process", forbidden)
    with pytest.raises(ReconciliationRequired):
        await capture_command_snapshot(
            store, run_id, replacement, "command", deadline=time.monotonic() + 10
        )


async def test_revocation_during_capture_rejects_returned_bytes(workspace_tmp_path, monkeypatch):
    store, run_id, token, project = setup(workspace_tmp_path)
    data = capture_workspace(project).to_bytes()

    async def revoke(*args, **kwargs):
        store.transition(run_id, RunState.PAUSED)
        return data

    monkeypatch.setattr(snapshot_worker, "request_process", revoke)
    with pytest.raises(LeaseLost):
        await capture_command_snapshot(
            store, run_id, token, "command", deadline=time.monotonic() + 10
        )


def test_snapshot_protocol_cannot_supply_workspace_path():
    with pytest.raises(ValidationError):
        SnapshotRequest(
            database="db", run_id="run", owner="token", call_id="call", workspace="other"
        )
