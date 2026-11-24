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
    selected_skill_script_paths,
    specialist_procedure_script_paths,
)
from xaa.runtime.states import RunState
from xaa.runtime.task_graph import TaskDefinition
from xaa.runtime.workspace_snapshot import SnapshotFile, WorkspaceSnapshot, capture_workspace
from xaa.storage.commands import CommandJournal
from xaa.storage.runs import LeaseLost, RunStore
from xaa.storage.skills import SkillAsset, SkillDraft, SkillStore, snapshot_digest
from xaa.storage.specialist_profiles import SpecialistProfileStore
from xaa.storage.tasks import TaskStore


def test_procedure_credential_grant_is_part_of_the_selected_snapshot_digest():
    snapshot = {
        "content": "Use a declared credential only when approved.",
        "required_tools": [],
        "required_permissions": [],
        "credential_names": ["API_TOKEN"],
    }
    no_grant = {**snapshot, "credential_names": []}
    assert snapshot_digest(snapshot) != snapshot_digest(no_grant)
    legacy_snapshot = {key: value for key, value in no_grant.items() if key != "credential_names"}
    assert snapshot_digest(legacy_snapshot) == snapshot_digest(no_grant)


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
            {
                "path": "scripts/check.mjs",
                "media_type": "text/javascript",
                "content_base64": base64.b64encode(
                    b"import { readFileSync } from 'node:fs';\nconsole.log(readFileSync('fixture.txt', 'utf8'));\n"
                ).decode("ascii"),
                "sha256": hashlib.sha256(
                    b"import { readFileSync } from 'node:fs';\nconsole.log(readFileSync('fixture.txt', 'utf8'));\n"
                ).hexdigest(),
            },
            {
                "path": "scripts/check.cjs",
                "media_type": "application/javascript",
                "content_base64": base64.b64encode(
                    b"const fs = require('node:fs');\nconsole.log(fs.readFileSync('fixture.txt', 'utf8'));\n"
                ).decode("ascii"),
                "sha256": hashlib.sha256(
                    b"const fs = require('node:fs');\nconsole.log(fs.readFileSync('fixture.txt', 'utf8'));\n"
                ).hexdigest(),
            },
            {
                "path": "scripts/check.ps1",
                "media_type": "text/plain",
                "content_base64": base64.b64encode(b"Get-Content fixture.txt\n").decode("ascii"),
                "sha256": hashlib.sha256(b"Get-Content fixture.txt\n").hexdigest(),
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
    assert paths["__xaa_selected_skill__/fixture-skill/v2/scripts/check.mjs"].startswith(
        b"import { readFileSync }"
    )
    assert paths["__xaa_selected_skill__/fixture-skill/v2/scripts/check.cjs"].startswith(
        b"const fs = require"
    )
    assert paths["__xaa_selected_skill__/fixture-skill/v2/scripts/check.ps1"] == (
        b"Get-Content fixture.txt\n"
    )
    assert selected_skill_script_paths(selected_trial()) == [
        "__xaa_selected_skill__/fixture-skill/v2/scripts/check.py",
        "__xaa_selected_skill__/fixture-skill/v2/scripts/check.sh",
        "__xaa_selected_skill__/fixture-skill/v2/scripts/check.mjs",
        "__xaa_selected_skill__/fixture-skill/v2/scripts/check.cjs",
        "__xaa_selected_skill__/fixture-skill/v2/scripts/check.ps1",
    ]
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


def test_assigned_specialist_script_is_staged_from_frozen_task_snapshot(workspace_tmp_path):
    store = RunStore(workspace_tmp_path / "specialist-script.sqlite3")
    profiles = SpecialistProfileStore(store)
    script = b"print('specialist script')\n"
    skill = SkillStore(store).create(
        SkillDraft(
            name="Specialist script fixture",
            content="Run the test script when authorized.",
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
    with store.connection() as connection:
        connection.execute(
            "UPDATE skills SET lifecycle_state='active',active_version=1 WHERE id=?",
            (skill["id"],),
        )
    profiles.set_procedure("executor", skill["id"])
    run = store.create_run(
        objective="Run assigned check",
        acceptance_criteria=[],
        workspace=None,
        mode="workspace",
        model=None,
    )
    owner = store.acquire_lease(run["id"], "fixture")
    TaskStore(store).save_plan(
        run["id"], owner, [TaskDefinition(id="check", objective="Run check", role="executor")]
    )
    procedure = TaskStore(store).records(run["id"])[0]["procedure_snapshot"]
    assert procedure is not None
    source = WorkspaceSnapshot((SnapshotFile("input.txt", b"unchanged"),), (), ())
    staged = add_selected_skill_scripts(source, None, procedure)
    path = f"__xaa_selected_skill__/{skill['id']}/v1/scripts/check.py"
    assert {item.path: item.content for item in staged.files}[path] == script
    assert specialist_procedure_script_paths(procedure) == [path]
