import base64
import hashlib
import time

import pytest
from pydantic import ValidationError
from xaa.runtime import snapshot_worker
from xaa.runtime.executor import ReconciliationRequired
from xaa.runtime.snapshot_worker import (
    SnapshotRequest,
    add_selected_skill_scripts,
    capture_command_snapshot,
)
from xaa.runtime.states import RunState
from xaa.runtime.workspace_snapshot import SnapshotFile, WorkspaceSnapshot, capture_workspace
from xaa.storage.commands import CommandJournal
from xaa.storage.runs import LeaseLost, RunStore
from xaa.storage.skills import SkillAsset, SkillDraft, SkillStore, snapshot_digest


def setup(root, *, selected_script=False):
    project = root / "project"
    project.mkdir()
    store = RunStore(root / "data" / "db.sqlite3")
    procedure_trial = None
    if selected_script:
        script = b"print('selected script')\n"
        skill = SkillStore(store).create(
            SkillDraft(
                name="Selected script fixture",
                content="Run the linked check script when authorized.",
                required_tools=[],
                required_permissions=["workspace.read"],
                assets=[
                    SkillAsset(
                        path="scripts/check.py",
                        content_base64=base64.b64encode(script).decode("ascii"),
                        media_type="text/x-python",
                        sha256=hashlib.sha256(script).hexdigest(),
                    )
                ],
            )
        )
        procedure_trial = {"skill_id": skill["id"], "version": 1}
    run = store.create_run(
        objective="Capture command inputs",
        acceptance_criteria=[],
        workspace=str(project),
        mode="workspace",
        model=None,
        command_grant={"distribution": "Ubuntu", "profile_sha256": "a" * 64},
        procedure_trial=procedure_trial,
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


async def test_selected_script_flows_through_owned_snapshot_worker(workspace_tmp_path):
    store, run_id, token, project = setup(workspace_tmp_path, selected_script=True)
    (project / "evidence.txt").write_text("project input", encoding="utf-8")

    snapshot = await capture_command_snapshot(
        store, run_id, token, "command", deadline=time.monotonic() + 10
    )

    files = {entry.path: entry.content for entry in snapshot.files}
    selected = store.procedure_trial(run_id)
    assert selected is not None
    staged_path = f"__xaa_selected_skill__/{selected['skill_id']}/v1/scripts/check.py"
    assert files["evidence.txt"] == b"project input"
    assert files[staged_path] == b"print('selected script')\n"
    assert staged_path in {entry.path for entry in snapshot.files}
    assert any(path.endswith("/scripts") for path in snapshot.directories)


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


def selected_trial(path="scripts/check.py", content=b"print('selected')\n"):
    trial = {
        "skill_id": "fixture-skill",
        "version": 2,
        "content": "Run the selected check.",
        "required_tools": [],
        "required_permissions": ["workspace.read"],
        "assets": [
            {
                "path": path,
                "media_type": "text/x-python",
                "content_base64": base64.b64encode(content).decode("ascii"),
                "sha256": hashlib.sha256(content).hexdigest(),
            },
            {
                "path": "notes.txt",
                "media_type": "text/plain",
                "content_base64": base64.b64encode(b"not a script").decode("ascii"),
                "sha256": hashlib.sha256(b"not a script").hexdigest(),
            },
            {
                "path": "scripts/check.sh",
                "media_type": "text/x-shellscript",
                "content_base64": base64.b64encode(b"#!/bin/bash\nprintf 'shell'\n").decode(
                    "ascii"
                ),
                "sha256": hashlib.sha256(b"#!/bin/bash\nprintf 'shell'\n").hexdigest(),
            },
        ],
    }
    trial["snapshot_sha256"] = snapshot_digest(trial)
    return trial


def test_selected_script_is_added_to_immutable_sandbox_snapshot():
    source = WorkspaceSnapshot((SnapshotFile("project.txt", b"unchanged"),), (), ())
    staged = add_selected_skill_scripts(source, selected_trial())

    paths = {entry.path: entry.content for entry in staged.files}
    assert paths["project.txt"] == b"unchanged"
    assert paths["__xaa_selected_skill__/fixture-skill/v2/scripts/check.py"] == (
        b"print('selected')\n"
    )
    assert paths["__xaa_selected_skill__/fixture-skill/v2/scripts/check.sh"] == (
        b"#!/bin/bash\nprintf 'shell'\n"
    )
    assert "__xaa_selected_skill__/fixture-skill/v2/scripts" in staged.directories
    assert "__xaa_selected_skill__/fixture-skill/v2" in staged.directories
    assert len(source.files) == 1


def test_selected_script_overlay_rejects_tampering_and_project_collisions():
    trial = selected_trial()
    trial["snapshot_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="integrity"):
        add_selected_skill_scripts(WorkspaceSnapshot((), (), ()), trial)

    collision = WorkspaceSnapshot(
        (SnapshotFile("__xaa_selected_skill__/fixture-skill/v2/scripts/check.py", b"project"),),
        (
            "__xaa_selected_skill__",
            "__xaa_selected_skill__/fixture-skill",
            "__xaa_selected_skill__/fixture-skill/v2",
            "__xaa_selected_skill__/fixture-skill/v2/scripts",
        ),
        (),
    )
    with pytest.raises(ValueError, match="conflicts"):
        add_selected_skill_scripts(collision, selected_trial())
