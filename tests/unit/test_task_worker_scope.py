from contextlib import contextmanager

import pytest
from pydantic import ValidationError
from xaa.runtime import executor as module
from xaa.runtime.executor import WorkspaceExecutor
from xaa.runtime.task_graph import TaskDefinition
from xaa.runtime.tool_worker import FilesystemWorkerError, execute_filesystem
from xaa.storage.runs import RunStore, TaskReadScope
from xaa.storage.tasks import TaskStore


def setup(root):
    project = root / "project"
    project.mkdir()
    (project / "input.txt").write_text("private task evidence")
    store = RunStore(root / "data" / "db.sqlite3")
    run = store.create_run(
        objective="Parent",
        acceptance_criteria=[],
        workspace=str(project),
        mode="workspace",
        model="fixture",
        command_grant={"distribution": "Ubuntu", "profile_sha256": "a" * 64},
    )
    owner = store.acquire_lease(run["id"], "parent", seconds=300)
    tasks = TaskStore(store)
    tasks.save_plan(
        run["id"],
        owner,
        [
            TaskDefinition(
                id=name,
                objective=name,
                max_iterations=2,
                permissions=["workspace.read", "workspace.write"],
            )
            for name in ("a", "b")
        ],
    )
    a = tasks.claim_ready(run["id"], owner)
    b = tasks.claim_ready(run["id"], owner)
    return store, run["id"], owner, a, b, project


@pytest.mark.parametrize("fault", ["expired", "sibling", "scope", "permission", "status", "parent"])
async def test_actual_worker_requires_its_own_current_task_scope(workspace_tmp_path, fault):
    store, run_id, owner, a, b, project = setup(workspace_tmp_path)
    result = await execute_filesystem(
        store,
        run_id,
        owner,
        "first",
        "read",
        {"path": "input.txt"},
        idempotency_key="first",
        internal=True,
        task_id="a",
        task_token=a["token"],
    )
    assert result["text"] == "private task evidence"
    token = a["token"]
    with store.connection() as c:
        if fault == "expired":
            c.execute(
                "UPDATE task_leases SET expires_at='2000-01-01T00:00:00+00:00' WHERE task_id=?",
                (run_id + ":a",),
            )
        if fault == "scope":
            c.execute(
                "UPDATE tasks SET workspace=? WHERE id=?", (str(project.parent), run_id + ":a")
            )
        if fault == "permission":
            c.execute("UPDATE tasks SET permission_scope='[]' WHERE id=?", (run_id + ":a",))
        if fault == "status":
            c.execute("UPDATE tasks SET status='verifying' WHERE id=?", (run_id + ":a",))
    if fault == "sibling":
        token = b["token"]
    if fault == "parent":
        store.release_lease(run_id, owner)
    with pytest.raises(FilesystemWorkerError):
        await execute_filesystem(
            store,
            run_id,
            owner,
            "refused",
            "read",
            {"path": "input.txt"},
            idempotency_key="refused",
            internal=True,
            task_id="a",
            task_token=token,
        )
    assert not store.unresolved_tool_calls(run_id)
    assert not any(
        event["event_type"] == "tool.intended" and event["payload"].get("call_id") == "refused"
        for event in store.list_events(run_id)
    )


async def test_task_protocol_and_store_cannot_inherit_parent_write_or_command_grants(
    workspace_tmp_path,
):
    store, run_id, owner, a, _, project = setup(workspace_tmp_path)
    scoped = RunStore(store.path, task_scope=TaskReadScope(run_id, owner, "a", a["token"]))
    assert scoped.get_run(run_id)["mode"] == "observe"
    assert scoped.get_run(run_id)["command_grant"] is None
    assert store.get_run(run_id)["command_grant"] is not None
    with pytest.raises(PermissionError):
        WorkspaceExecutor(scoped).create_file(
            run_id,
            owner,
            "create",
            {"path": "new.txt", "content": "forbidden"},
            idempotency_key="create",
        )
    with pytest.raises(PermissionError):
        await execute_filesystem(
            store,
            run_id,
            owner,
            "forbidden",
            "create",
            {"path": "new.txt", "content": "forbidden"},
            idempotency_key="forbidden",
            internal=True,
            task_id="a",
            task_token=a["token"],
        )
    with pytest.raises(ValidationError):
        await execute_filesystem(
            store,
            run_id,
            owner,
            "partial",
            "read",
            {"path": "input.txt"},
            idempotency_key="partial",
            internal=True,
            task_id="a",
        )
    assert not (project / "new.txt").exists()
    assert not any(event["event_type"] == "tool.intended" for event in store.list_events(run_id))


def test_expiry_after_open_refuses_result_commit_and_publication(workspace_tmp_path, monkeypatch):
    store, run_id, owner, a, _, _ = setup(workspace_tmp_path)
    scoped = RunStore(store.path, task_scope=TaskReadScope(run_id, owner, "a", a["token"]))
    original = module.open_for_read

    @contextmanager
    def expire_after_open(target):
        with original(target) as source:
            yield source
        with store.connection() as c:
            c.execute(
                "UPDATE task_leases SET expires_at='2000-01-01T00:00:00+00:00' WHERE task_id=?",
                (run_id + ":a",),
            )

    monkeypatch.setattr(module, "open_for_read", expire_after_open)
    with pytest.raises(PermissionError):
        WorkspaceExecutor(scoped).read_file(
            run_id,
            owner,
            "interrupted",
            {"path": "input.txt"},
            idempotency_key="interrupted",
            internal=True,
        )
    assert [item["id"] for item in store.unresolved_tool_calls(run_id)] == ["interrupted"]
    with pytest.raises(PermissionError):
        TaskStore(store).record_read_worker_event(
            run_id,
            owner,
            "a",
            a["token"],
            "task.tool.result",
            {
                "task_id": "a",
                "journal_id": "interrupted",
                "result": {"text": "must not be published"},
            },
        )
    assert not any(
        event["event_type"] in ("tool.completed", "task.tool.result")
        for event in store.list_events(run_id)
    )
    store.append_worker_event(run_id, owner, "fixture.parent_still_owned", {})


async def test_task_directory_listing_uses_the_same_live_scope(workspace_tmp_path):
    store, run_id, owner, a, _, _ = setup(workspace_tmp_path)
    result = await execute_filesystem(
        store,
        run_id,
        owner,
        "list",
        "list",
        {"path": "."},
        idempotency_key="list",
        internal=True,
        task_id="a",
        task_token=a["token"],
    )
    assert "input.txt" in str(result)
    with store.connection() as c:
        c.execute("UPDATE tasks SET status='verifying' WHERE id=?", (run_id + ":a",))
    with pytest.raises(FilesystemWorkerError):
        await execute_filesystem(
            store,
            run_id,
            owner,
            "stale-list",
            "list",
            {"path": "."},
            idempotency_key="stale-list",
            internal=True,
            task_id="a",
            task_token=a["token"],
        )
    assert not any(
        event["event_type"] == "tool.intended" and event["payload"].get("call_id") == "stale-list"
        for event in store.list_events(run_id)
    )
